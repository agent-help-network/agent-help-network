"""Seed pre-read of declared-policy profiles. DB tests use only the dedicated ahn_test database and stub readers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone

import pytest

from ahn import auto_review, profile_warm, status_refresh
from ahn.db import connect, migrate
from ahn.official_paths import OFFICIAL_PATHS
from ahn.service import aggregate_eligible_host
from ahn.settings import Settings

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
SEEDS = ["seed-a.com", "seed-b.com", "seed-c.org", "seed-d.net", "seed-e.io"]
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
    assert first == {"candidates": 2, "read": 3, "ok": 0, "errors": 3}

    failing.calls.clear()
    healthy = Reader()
    second = profile_warm.warm_profiles(settings, batch=3, reader=healthy, now=now + timedelta(minutes=5))
    assert healthy.hosts == SEEDS[3:]  # failed hosts wait for the next window, not the next cron run
    assert second == {"candidates": 0, "read": 2, "ok": 2, "errors": 0}
    with connect(settings) as conn:
        row = conn.execute("SELECT fetched_at,last_error_at,robots_status FROM host_access_profile WHERE site_host=%s", (SEEDS[0],)).fetchone()
    assert row["fetched_at"] is None and row["last_error_at"] is not None and row["robots_status"] == "error"

    # Transport failures are due again after ERROR_RETRY, ahead of the weekly refresh of healthy hosts.
    healthy.calls.clear()
    third = profile_warm.warm_profiles(settings, batch=3, reader=healthy, now=now + timedelta(hours=1, minutes=1))
    assert healthy.hosts == SEEDS[:3]
    assert third == {"candidates": 0, "read": 3, "ok": 3, "errors": 0}


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
    due = profile_warm.warm_profiles(settings, batch=5, reader=healthy, now=now + timedelta(hours=1, minutes=31))
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

    assert result == {"candidates": 2, "read": 2, "ok": 2, "errors": 1}
    assert reader.hosts == SEEDS[:3]
    assert stored_hosts(settings) == {SEEDS[0], SEEDS[2]}  # the raising host left no row, so it stays due


def test_warm_profiles_stops_starting_new_hosts_after_the_time_budget(warm_settings, monkeypatch):
    settings, _ = warm_settings
    ticks = iter(range(0, 1000, 100))  # each clock read is 100 seconds later
    monkeypatch.setattr(profile_warm, "_clock", lambda: next(ticks))
    reader = Reader()

    result = profile_warm.warm_profiles(settings, batch=5, reader=reader, now=datetime.now(timezone.utc), time_budget=150)

    assert reader.hosts == SEEDS[:2]  # started at 0, second host checked at 100 (ok), third at 200 (over budget)
    assert result == {"candidates": 3, "read": 2, "ok": 2, "errors": 0}


def test_warm_profiles_with_zero_batch_reads_nothing_and_reports_the_backlog(warm_settings):
    settings, pauses = warm_settings
    reader = Reader()
    assert profile_warm.warm_profiles(settings, batch=0, reader=reader) == {"candidates": len(SEEDS), "read": 0, "ok": 0, "errors": 0}
    assert reader.calls == [] and pauses == [] and stored_hosts(settings) == set()


def test_cron_main_prints_the_warm_line_and_honours_the_batch_variable(monkeypatch, capsys):
    seen = []

    def fake_warm(received_settings, *, batch):
        seen.append(batch)
        return {"candidates": 7, "read": 3, "ok": 2, "errors": 1}

    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    # Only the new step is under test: keep the review and status-feed steps (and their network reads) out of it.
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})
    monkeypatch.setattr(status_refresh, "refresh_batch", lambda received_settings: {"checked": 0, "updated": 0, "failed": 0})
    monkeypatch.setattr(profile_warm, "warm_profiles", fake_warm)

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


def test_cron_main_survives_a_failing_status_step(monkeypatch, capsys):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})

    def broken(received_settings):
        raise RuntimeError("column last_error_detail does not exist")

    monkeypatch.setattr(status_refresh, "refresh_batch", broken)
    monkeypatch.setattr(profile_warm, "warm_profiles", lambda received_settings, *, batch: {"candidates": 1, "read": 1, "ok": 1, "errors": 0})
    auto_review.main()
    out = capsys.readouterr().out
    assert "status_refresh failed=RuntimeError" in out and "does not exist" not in out
    assert "profile_warm read=1 ok=1 errors=0 remaining=1" in out  # the later step still ran


def test_cron_main_survives_a_failing_warm_step(monkeypatch, capsys):
    def broken(received_settings, *, batch):
        raise RuntimeError("database unavailable")

    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://never-connected.invalid/none")
    monkeypatch.delenv("AHN_WARM_BATCH", raising=False)
    monkeypatch.setattr(auto_review, "review_batch", lambda database_url: {"published": 0, "held": 0})
    monkeypatch.setattr(status_refresh, "refresh_batch", lambda received_settings: {"checked": 0, "updated": 0, "failed": 0})
    monkeypatch.setattr(profile_warm, "warm_profiles", broken)
    auto_review.main()
    out = capsys.readouterr().out
    assert "profile_warm failed=RuntimeError" in out and "database unavailable" not in out
