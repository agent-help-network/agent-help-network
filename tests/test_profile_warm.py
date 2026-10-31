"""Seed pre-read of declared-policy profiles. DB tests use only the dedicated ahn_test database and stub readers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from ahn import access_profile, auto_review, background_reads, profile_warm, status_refresh
from ahn.db import connect, migrate
from ahn.official_paths import OFFICIAL_PATHS
from ahn.service import aggregate_eligible_host
from ahn.settings import Settings

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
SEEDS = ["seed-a.com", "seed-b.com", "seed-c.org", "seed-d.net", "seed-e.io"]
QUEUE_IDLE = {"claimed": 0, "done": 0, "failed": 0, "dropped": 0, "remaining": 0}
SYNC_IDLE = {"curated": 0, "added": 0, "changed": 0, "removed": 0}
ROBOTS = b"User-agent: GPTBot\nDisallow: /\n"


def test_load_seed_hosts_skips_comments_blanks_duplicates_and_ineligible():
    content = (
        "# header comment\n"
        "\n"
        "github.com\n"
        "  example.co.uk  \n"
        "github.com\n"            # duplicate
        "Example.COM\n"           # not lowercase
        "docs.github.com\n"       # tenant/sub-host, not an aggregate-eligible apex
        "localhost\n"
        "127.0.0.1\n"
        "not a host\n"
        "intranet.local\n"
        "# trailing.com\n"        # commented out
        "bbc.co.uk\n"
    )
    # tempfile rather than the tmp_path fixture: pytest's shared temp root is not always writable here.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "seeds.txt"
        path.write_text(content, encoding="utf-8")
        assert profile_warm.load_seed_hosts(path) == ["github.com", "example.co.uk", "bbc.co.uk"]


def test_shipped_seed_list_is_clean_and_starts_with_official_path_hosts():
    hosts = profile_warm.load_seed_hosts()
    assert len(hosts) >= 1000
    assert len(hosts) == len(set(hosts))
    assert all(aggregate_eligible_host(host) for host in hosts)
    official = [host for host in OFFICIAL_PATHS if host in set(hosts)]
    assert official and hosts[: len(official)] == official


TRANCO_BLOCK_MARKER = "# Hosts chosen from the Tranco top 1,000"


def test_shipped_seed_list_tranco_block_is_alphabetical_and_the_header_cites_the_list():
    # The last block comes from the Tranco list; its order must not reveal rank, and the header must say where it came from.
    lines = profile_warm.SEED_PATH.read_text(encoding="utf-8-sig").splitlines()
    marker = [index for index, line in enumerate(lines) if line.startswith(TRANCO_BLOCK_MARKER)]
    assert len(marker) == 1
    hosts_of = lambda chunk: [line.strip() for line in chunk if line.strip() and not line.startswith("#")]
    earlier, block = hosts_of(lines[: marker[0]]), hosts_of(lines[marker[0] + 1:])
    assert block and block == sorted(block) and len(block) == len(set(block))
    assert not set(block) & set(earlier)
    loaded = profile_warm.load_seed_hosts()
    eligible_block = [host for host in block if aggregate_eligible_host(host)]
    assert eligible_block and loaded[-len(eligible_block):] == eligible_block  # the warm-up reads this block last, in this order
    header = "\n".join(line for line in lines[: marker[0]] if line.startswith("#"))
    for required in ("Y83KG", "https://tranco-list.eu/list/Y83KG", "Le Pochat", "NDSS 2019", "10.14722/ndss.2019.23386",
                     "Cloudflare Radar CC BY-NC 4.0", "CrUX CC BY 4.0", "Majestic CC BY 3.0"):
        assert required in header
    assert "under its terms" not in header


@pytest.fixture
def warm_settings(monkeypatch):
    if not TEST_URL:
        pytest.skip("AHN_TEST_DATABASE_URL is required")
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE host_access_profile")
    pauses: list[float] = []
    monkeypatch.setattr(profile_warm.time, "sleep", pauses.append)
    monkeypatch.setattr(profile_warm, "load_seed_hosts", lambda path=None: list(SEEDS))
    return settings, pauses


class Reader:
    """Stub for access_profile.read_public_file: records every request, never touches the network."""

    def __init__(self, robots=("ok", ROBOTS), llms=("absent", None), raise_for=()):
        self.robots, self.llms, self.raise_for, self.calls = robots, llms, set(raise_for), []

    def __call__(self, site_host, path, *, cap_bytes, **_):
        self.calls.append((site_host, path))
        if site_host in self.raise_for:
            raise RuntimeError("simulated reader failure")
        return self.robots if path == "/robots.txt" else self.llms

    @property
    def hosts(self):
        return [host for host, path in self.calls if path == "/robots.txt"]


def stored_hosts(settings):
    with connect(settings) as conn:
        return {row["site_host"] for row in conn.execute("SELECT site_host FROM host_access_profile")}


def test_warm_profiles_reads_batches_in_order_and_never_rereads_fresh_hosts(warm_settings):
    settings, pauses = warm_settings
    now = datetime.now(timezone.utc)
    reader = Reader()

    first = profile_warm.warm_profiles(settings, batch=2, reader=reader, now=now)
    assert first == {"candidates": 3, "read": 2, "ok": 2, "errors": 0}
    assert reader.hosts == SEEDS[:2]
    assert pauses == [profile_warm.PAUSE_SECONDS]
    assert stored_hosts(settings) == set(SEEDS[:2])
    with connect(settings) as conn:
        row = conn.execute("SELECT robots_status,robots,llms_txt_status FROM host_access_profile WHERE site_host=%s", (SEEDS[0],)).fetchone()
    assert row["robots_status"] == "ok" and row["llms_txt_status"] == "absent"
    assert row["robots"]["agents_blocked"] == ["GPTBot"]

    reader.calls.clear()
    second = profile_warm.warm_profiles(settings, batch=2, reader=reader, now=now + timedelta(minutes=5))
    assert second == {"candidates": 1, "read": 2, "ok": 2, "errors": 0}
    assert reader.hosts == SEEDS[2:4]  # moved on; the first two were not read again

    reader.calls.clear()
    third = profile_warm.warm_profiles(settings, batch=2, reader=reader, now=now + timedelta(minutes=10))
    assert third == {"candidates": 0, "read": 1, "ok": 1, "errors": 0}
    assert reader.hosts == SEEDS[4:]

    reader.calls.clear()
    idle = profile_warm.warm_profiles(settings, batch=2, reader=reader, now=now + timedelta(minutes=15))
    assert idle == {"candidates": 0, "read": 0, "ok": 0, "errors": 0}
    assert reader.calls == []

    # Once the 168h window has passed the same hosts become due again, oldest list position first.
    later = profile_warm.warm_profiles(settings, batch=2, reader=reader, now=now + timedelta(days=8))
    assert later == {"candidates": 3, "read": 2, "ok": 2, "errors": 0}
    assert reader.hosts == SEEDS[:2]


def test_warm_profiles_counts_read_errors_and_does_not_retry_them_within_the_window(warm_settings):
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    failing = Reader(robots=("error", None), llms=("error", None))

    first = profile_warm.warm_profiles(settings, batch=3, reader=failing, now=now)
    assert first == {"candidates": 2, "read": 3, "ok": 0, "errors": 3, "causes": {"no_detail": 3}}

    failing.calls.clear()
    healthy = Reader()
    second = profile_warm.warm_profiles(settings, batch=3, reader=healthy, now=now + timedelta(minutes=5))
    assert healthy.hosts == SEEDS[3:]  # failed hosts wait for the next window, not the next cron run
    assert second == {"candidates": 0, "read": 2, "ok": 2, "errors": 0}
    with connect(settings) as conn:
        row = conn.execute("SELECT fetched_at,last_error_at,robots_status FROM host_access_profile WHERE site_host=%s", (SEEDS[0],)).fetchone()
    assert row["fetched_at"] is None and row["last_error_at"] is not None and row["robots_status"] == "error"

    # Transport failures are tried again after WARM_ERROR_RETRY (the lookup path retries sooner, after ERROR_RETRY).
    healthy.calls.clear()
    early = profile_warm.warm_profiles(settings, batch=3, reader=healthy, now=now + timedelta(hours=1, minutes=1))
    assert healthy.calls == [] and early == {"candidates": 0, "read": 0, "ok": 0, "errors": 0}
    third = profile_warm.warm_profiles(settings, batch=3, reader=healthy,
                                       now=now + profile_warm.WARM_ERROR_RETRY + timedelta(minutes=1))
    assert healthy.hosts == SEEDS[:3]
    assert third == {"candidates": 0, "read": 3, "ok": 3, "errors": 0}


def test_warm_profiles_reads_hosts_never_attempted_before_retrying_failed_ones(warm_settings):
    # The production failure: dead names came due again every hour at their place in the file, ahead of every
    # host not yet read, so each run spent its whole batch on them (ok=0 errors=10) and the backlog never shrank.
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    dead = Reader(robots=("error", None, "dns_failed"), llms=("error", None, "dns_failed"))
    first = profile_warm.warm_profiles(settings, batch=2, reader=dead, now=now)
    assert first == {"candidates": 3, "read": 2, "ok": 0, "errors": 2, "causes": {"dns_failed": 2}}

    healthy = Reader()
    # An hour later the dead hosts would have been due again (ERROR_RETRY) and, being first in the file, taken the batch.
    later = profile_warm.warm_profiles(settings, batch=2, reader=healthy, now=now + timedelta(hours=1, minutes=5))
    assert healthy.hosts == SEEDS[2:4]
    assert later == {"candidates": 1, "read": 2, "ok": 2, "errors": 0}

    healthy.calls.clear()
    last = profile_warm.warm_profiles(settings, batch=2, reader=healthy, now=now + timedelta(hours=1, minutes=10))
    assert healthy.hosts == SEEDS[4:]  # the backlog is down to the failed hosts, which keep waiting
    assert last == {"candidates": 0, "read": 1, "ok": 1, "errors": 0}

    healthy.calls.clear()
    retried = profile_warm.warm_profiles(settings, batch=5, reader=healthy,
                                         now=now + profile_warm.WARM_ERROR_RETRY + timedelta(minutes=1))
    assert healthy.hosts == SEEDS[:2]


def test_warm_profiles_puts_expired_answers_before_failed_retries(warm_settings):
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    profile_warm.warm_profiles(settings, batch=5, reader=Reader(), now=now)
    # seed-a, first in the file, later hit a transport failure; the other four answers are simply a week old by now.
    with connect(settings) as conn:
        conn.execute("UPDATE host_access_profile SET last_error_at=%s WHERE site_host=%s", (now + timedelta(hours=1), SEEDS[0]))
    healthy = Reader()
    result = profile_warm.warm_profiles(settings, batch=5, reader=healthy, now=now + timedelta(days=8))
    assert healthy.hosts == SEEDS[1:] + [SEEDS[0]]
    assert result == {"candidates": 0, "read": 5, "ok": 5, "errors": 0}


def test_warm_profiles_names_the_cause_of_each_failure_without_host_names(warm_settings):
    settings, _ = warm_settings

    class Mixed(Reader):
        def __call__(self, site_host, path, *, cap_bytes, **_):
            self.calls.append((site_host, path))
            # The www alias is tried after an apex failure, so it fails the same way.
            label = {"seed-a.com": "dns_failed", "seed-b.com": "connect_timeout", "seed-c.org": "dns_failed"}.get(
                site_host.removeprefix("www."))
            if label:
                return ("error", None, label)
            return self.robots if path == "/robots.txt" else self.llms

    result = profile_warm.warm_profiles(settings, batch=5, reader=Mixed(), now=datetime.now(timezone.utc))
    assert result["errors"] == 3 and result["ok"] == 2
    assert result["causes"] == {"dns_failed": 2, "connect_timeout": 1}
    assert profile_warm.format_causes(result["causes"]) == "dns_failed:2,connect_timeout:1"
    assert "seed" not in profile_warm.format_causes(result["causes"])


def test_warm_profiles_retries_a_transport_failure_that_followed_an_earlier_answer(warm_settings):
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    healthy = Reader()
    profile_warm.warm_profiles(settings, batch=5, reader=healthy, now=now)
    assert stored_hosts(settings) == set(SEEDS)
    # A request-path read later hit a transport failure: the answer stays, only the failure timestamp moves.
    with connect(settings) as conn:
        conn.execute("UPDATE host_access_profile SET last_error_at=%s, robots_detail='dns_timeout' WHERE site_host=%s",
                     (now + timedelta(minutes=30), SEEDS[2]))
    healthy.calls.clear()
    too_soon = profile_warm.warm_profiles(settings, batch=5, reader=healthy, now=now + timedelta(hours=1))
    assert healthy.calls == [] and too_soon == {"candidates": 0, "read": 0, "ok": 0, "errors": 0}
    due = profile_warm.warm_profiles(settings, batch=5, reader=healthy,
                                     now=now + timedelta(minutes=30) + profile_warm.WARM_ERROR_RETRY + timedelta(minutes=1))
    assert healthy.hosts == [SEEDS[2]] and due == {"candidates": 0, "read": 1, "ok": 1, "errors": 0}
    with connect(settings) as conn:
        row = conn.execute("SELECT robots_status,last_error_at,read_host FROM host_access_profile WHERE site_host=%s", (SEEDS[2],)).fetchone()
    assert row["robots_status"] == "ok" and row["last_error_at"] is None and row["read_host"] == SEEDS[2]


def test_warm_profiles_treats_a_refusal_as_an_answer_not_a_retry(warm_settings):
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    refusing = Reader(robots=("refused", None, "http_503"), llms=("refused", None, "http_503"))
    first = profile_warm.warm_profiles(settings, batch=2, reader=refusing, now=now)
    assert first == {"candidates": 3, "read": 2, "ok": 0, "errors": 0}
    refusing.calls.clear()
    again = profile_warm.warm_profiles(settings, batch=5, reader=refusing, now=now + timedelta(hours=2))
    assert refusing.hosts and SEEDS[0] not in refusing.hosts and SEEDS[1] not in refusing.hosts
    assert again["read"] == 3
    with connect(settings) as conn:
        row = conn.execute("SELECT robots_status,robots_detail,fetched_at FROM host_access_profile WHERE site_host=%s", (SEEDS[0],)).fetchone()
    assert row["robots_status"] == "refused" and row["robots_detail"] == "http_503" and row["fetched_at"] is not None


def test_warm_profiles_swallows_an_exception_for_one_host_and_continues(warm_settings):
    settings, _ = warm_settings
    reader = Reader(raise_for={SEEDS[1]})

    result = profile_warm.warm_profiles(settings, batch=3, reader=reader, now=datetime.now(timezone.utc))

    assert result == {"candidates": 2, "read": 2, "ok": 2, "errors": 1, "causes": {"exception:RuntimeError": 1}}
    assert reader.hosts == SEEDS[:3]
    assert stored_hosts(settings) == {SEEDS[0], SEEDS[2]}  # the raising host left no row, so it stays due


def _ticking(monkeypatch, step):
    ticks = iter(range(0, 100_000, step))  # each clock read is ``step`` seconds later
    monkeypatch.setattr(profile_warm, "_clock", lambda: next(ticks))


def test_warm_profiles_stops_starting_new_hosts_after_the_time_budget(warm_settings, monkeypatch):
    settings, _ = warm_settings
    _ticking(monkeypatch, 10)
    reader = Reader()

    result = profile_warm.warm_profiles(settings, batch=5, reader=reader, now=datetime.now(timezone.utc), time_budget=25)

    assert reader.hosts == SEEDS[:3]  # started at 0, checked at 10 and 20 (within 25), the fourth at 30 is over
    assert result == {"candidates": 2, "read": 3, "ok": 3, "errors": 0}


def test_the_default_budget_reads_several_hosts(warm_settings):
    # The worst case of one host (136 s) is above the budget (120 s); budget alone decides how many hosts start.
    assert profile_warm.HOST_WORST_SECONDS > profile_warm.TIME_BUDGET_SECONDS
    settings, _ = warm_settings
    reader = Reader()
    assert profile_warm.warm_profiles(settings, batch=4, reader=reader, now=datetime.now(timezone.utc))["read"] == 4


def test_a_further_host_starts_only_if_its_worst_case_ends_before_the_next_cron_run(warm_settings, monkeypatch):
    # The worst case comes from the reader's own limits: DNS 2 + connect 2*2 + headers 3 + body 10 + last receive 3.
    assert access_profile.REQUEST_WORST_SECONDS == 22.0
    assert profile_warm.HOST_WORST_SECONDS == access_profile.HOST_READ_WORST_SECONDS == 6 * 22.0 + 4.0
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    room = profile_warm.CRON_SAFE_SECONDS - profile_warm.HOST_WORST_SECONDS  # latest cron time a further host may start
    # Plenty of budget, but at the second host's check (clock 100) the cron run is one second past that point.
    _ticking(monkeypatch, 100)
    reader = Reader()
    profile_warm.warm_profiles(settings, batch=5, reader=reader, now=now, time_budget=10_000, cron_started=100 - room - 1)
    assert len(reader.hosts) == 1
    # Exactly on that point the second host starts; at the third check (clock 200) the cron run is past it.
    _ticking(monkeypatch, 100)
    reader = Reader()
    profile_warm.warm_profiles(settings, batch=5, reader=reader, now=now, time_budget=10_000, cron_started=100 - room)
    assert len(reader.hosts) == 2


def test_the_first_host_needs_only_budget(warm_settings, monkeypatch):
    settings, _ = warm_settings
    now = datetime.now(timezone.utc)
    _ticking(monkeypatch, 100)
    # A long queue drain must not starve the seed pre-read: the cron run is long past its line and the budget is
    # nearly gone, yet one host starts ...
    reader = Reader()
    profile_warm.warm_profiles(settings, batch=5, reader=reader, now=now, time_budget=1, cron_started=-10_000)
    assert len(reader.hosts) == 1
    # ... and with no budget at all none does.
    reader = Reader()
    profile_warm.warm_profiles(settings, batch=5, reader=reader, now=now, time_budget=0)
    assert reader.hosts == []


def test_warm_profiles_with_zero_batch_reads_nothing_and_reports_the_backlog(warm_settings):
    settings, pauses = warm_settings
    reader = Reader()
    assert profile_warm.warm_profiles(settings, batch=0, reader=reader) == {"candidates": len(SEEDS), "read": 0, "ok": 0, "errors": 0}
    assert reader.calls == [] and pauses == [] and stored_hosts(settings) == set()


def test_cron_main_prints_the_warm_line_and_honours_the_batch_variable(monkeypatch, capsys):
    seen = []

    def fake_warm(received_settings, *, batch, cron_started, **_options):
        assert isinstance(cron_started, float)  # the cron run's own start reaches the pre-read
        seen.append(batch)
        return {"candidates": 7, "read": 3, "ok": 2, "errors": 1}

    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    # Only the new step is under test: keep the review and status-feed steps (and their network reads) out of it.
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})
    monkeypatch.setattr(status_refresh, "refresh_batch", lambda received_settings: {"checked": 0, "updated": 0, "failed": 0, "remaining": 0})
    monkeypatch.setattr(status_refresh, "sync_curated_sources", lambda received_settings: SYNC_IDLE)
    monkeypatch.setattr(profile_warm, "warm_profiles", fake_warm)
    monkeypatch.setattr(background_reads, "drain", lambda received_settings, *, time_budget: dict(QUEUE_IDLE))

    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    auto_review.main()
    assert "profile_warm read=3 ok=2 errors=1 remaining=7" in capsys.readouterr().out
    monkeypatch.setenv("AHN_WARM_BATCH", "4")
    auto_review.main()
    capsys.readouterr()
    monkeypatch.setenv("AHN_WARM_BATCH", "0")
    auto_review.main()
    assert "profile_warm" not in capsys.readouterr().out
    monkeypatch.setenv("AHN_WARM_BATCH", "not-a-number")
    auto_review.main()
    assert seen == [10, 4, 10]


def test_cron_main_prints_the_failure_causes_after_the_counts(monkeypatch, capsys):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})
    monkeypatch.setattr(status_refresh, "refresh_batch", lambda received_settings: {"checked": 0, "updated": 0, "failed": 0, "remaining": 0})
    monkeypatch.setattr(status_refresh, "sync_curated_sources", lambda received_settings: SYNC_IDLE)
    monkeypatch.setattr(background_reads, "drain", lambda received_settings, *, time_budget: dict(QUEUE_IDLE))
    monkeypatch.setattr(profile_warm, "warm_profiles", lambda received_settings, *, batch, **_options: {
        "candidates": 5, "read": 10, "ok": 0, "errors": 10, "causes": {"connect_timeout": 3, "dns_failed": 7}})
    auto_review.main()
    out = capsys.readouterr().out
    assert "profile_warm read=10 ok=0 errors=10 remaining=5 causes=dns_failed:7,connect_timeout:3" in out


def test_cron_main_survives_a_failing_status_step(monkeypatch, capsys):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})

    def broken(received_settings):
        raise RuntimeError("column last_error_detail does not exist")

    monkeypatch.setattr(status_refresh, "refresh_batch", broken)
    monkeypatch.setattr(status_refresh, "sync_curated_sources", broken)
    monkeypatch.setattr(background_reads, "drain", lambda received_settings, *, time_budget: dict(QUEUE_IDLE))
    monkeypatch.setattr(profile_warm, "warm_profiles", lambda received_settings, *, batch, **_options: {"candidates": 1, "read": 1, "ok": 1, "errors": 0})
    auto_review.main()
    out = capsys.readouterr().out
    assert "status_refresh failed=RuntimeError" in out and "does not exist" not in out
    assert "status_sources failed=RuntimeError" in out  # the sync step fails on its own too
    assert "profile_warm read=1 ok=1 errors=0 remaining=1" in out  # the later step still ran


def test_cron_main_survives_a_failing_warm_step(monkeypatch, capsys):
    def broken(received_settings, *, batch, **_options):
        raise RuntimeError("database unavailable")

    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})
    monkeypatch.setattr(status_refresh, "refresh_batch", lambda received_settings: {"checked": 0, "updated": 0, "failed": 0, "remaining": 0})
    monkeypatch.setattr(status_refresh, "sync_curated_sources", lambda received_settings: SYNC_IDLE)
    monkeypatch.setattr(background_reads, "drain", lambda received_settings, *, time_budget: dict(QUEUE_IDLE))
    monkeypatch.setattr(profile_warm, "warm_profiles", broken)
    auto_review.main()
    out = capsys.readouterr().out
    assert "profile_warm failed=RuntimeError" in out and "database unavailable" not in out


def test_cron_main_drains_the_lookup_queue_before_the_seeds_within_one_budget(monkeypatch, capsys):
    calls = []
    ticks = {"values": iter([-5.0, 0.0, 100.0])}  # cron start, queue start, seed step

    def fake_drain(received_settings, *, time_budget):
        calls.append(("queue", time_budget))
        return {"claimed": 3, "done": 2, "failed": 1, "dropped": 0, "remaining": 4}

    def fake_warm(received_settings, *, batch, time_budget, cron_started):
        calls.append(("seeds", time_budget, cron_started))
        return {"candidates": 0, "read": 1, "ok": 1, "errors": 0}

    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})
    monkeypatch.setattr(status_refresh, "refresh_batch", lambda received_settings: {"checked": 0, "updated": 0, "failed": 0, "remaining": 0})
    monkeypatch.setattr(status_refresh, "sync_curated_sources", lambda received_settings: SYNC_IDLE)
    monkeypatch.setattr(background_reads, "drain", fake_drain)
    monkeypatch.setattr(profile_warm, "warm_profiles", fake_warm)
    monkeypatch.setattr(auto_review.time, "monotonic", lambda: next(ticks["values"]))
    auto_review.main()
    out = capsys.readouterr().out
    assert "background_reads done=2 failed=1 dropped=0 cut=0 remaining=4" in out
    # The queue had the whole seed budget; the seeds get what it left.
    assert calls == [("queue", profile_warm.TIME_BUDGET_SECONDS), ("seeds", profile_warm.TIME_BUDGET_SECONDS - 100.0, -5.0)]
    # A queue that used up the budget leaves the seeds for the next run.
    calls.clear()
    ticks["values"] = iter([-5.0, 0.0, 500.0])
    auto_review.main()
    assert "profile_warm skipped=time_budget" in capsys.readouterr().out and calls == [("queue", profile_warm.TIME_BUDGET_SECONDS)]

    # A broken queue step (for example before migration 020) does not stop the seed step.
    def broken(received_settings, *, time_budget):
        raise RuntimeError("relation background_reads does not exist")

    monkeypatch.setattr(background_reads, "drain", broken)
    ticks["values"] = iter([-5.0, 0.0, 1.0])
    auto_review.main()
    out = capsys.readouterr().out
    assert "background_reads failed=RuntimeError" in out and "does not exist" not in out and "profile_warm read=1" in out
