"""Curated status feeds registered and read without a lookup (D44, migration 026), against ahn_test.

Stub feeds only (MockTransport); no real status page is read.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import psycopg
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, service, signal_history, status_refresh  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import CURATED_EVIDENCE, SOURCES, Source, refresh_batch, sync_curated_sources  # noqa: E402

QUIET = {"page": {"updated_at": "2026-10-21T11:00:00Z"}, "status": {"indicator": "none"}}
SMALL = {
    "github.com": SOURCES["github.com"], "github.io": SOURCES["github.io"],  # two hosts, one feed
    "cloudflare.com": SOURCES["cloudflare.com"], "render.com": SOURCES["render.com"],
}


@pytest.fixture
def db():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, collect_lookup_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE lookup_metrics, request_metrics, mcp_check_metrics, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads, provider_status_events, robots_read_events, outage_episodes, "
                     "robots_read_daily RESTART IDENTITY")
    return settings


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    monkeypatch.setattr(status_refresh, "_sleep", lambda _seconds: None)


@pytest.fixture
def small(monkeypatch):
    """A four-host mapping table, so a batch reads a handful of stub feeds instead of 580."""
    monkeypatch.setattr(status_refresh, "SOURCES", dict(SMALL))
    monkeypatch.setattr(service, "SOURCES", dict(SMALL))
    return SMALL


class Feeds:
    def __init__(self, answer=None):
        self.answer = answer or (lambda request: httpx.Response(200, json=QUIET))
        self.seen: list[str] = []

    def __call__(self, request):
        self.seen.append(str(request.url))
        return self.answer(request)


def q(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def age(settings, minutes, host=None):
    with connect(settings) as conn:
        conn.execute("""UPDATE status_cache SET fetched_at=fetched_at - make_interval(mins => %s),
                        last_error_at=last_error_at - make_interval(mins => %s) WHERE %s::text IS NULL OR site_host=%s""",
                     (minutes, minutes, host, host))


def batch(settings, feeds, **options):
    with httpx.Client(transport=httpx.MockTransport(feeds)) as client:
        return refresh_batch(settings, client=client, **options)


# --- registration --------------------------------------------------------------------------------------

def test_sync_registers_every_curated_feed_and_is_idempotent(db):
    assert q(db, "SELECT count(*) AS n FROM status_sources")[0]["n"] == 0
    first = sync_curated_sources(db)
    assert first == {"curated": len(SOURCES), "added": len(SOURCES), "changed": 0, "removed": 0}
    rows = q(db, "SELECT site_host,api_url,evidence,enabled FROM status_sources")
    assert len(rows) == len(SOURCES) and all(r["evidence"] == CURATED_EVIDENCE and r["enabled"] for r in rows)
    assert {r["site_host"]: r["api_url"] for r in rows} == {host: source.api_url for host, source in SOURCES.items()}
    assert sync_curated_sources(db) == {"curated": len(SOURCES), "added": 0, "changed": 0, "removed": 0}
    # Nothing was read and no lookup register row was made.
    assert q(db, "SELECT count(*) AS n FROM status_cache")[0]["n"] == 0
    assert q(db, "SELECT count(*) AS n FROM queried_hosts")[0]["n"] == 0


def test_the_owner_board_counts_the_preregistered_feeds(db):
    with connect(db) as conn:
        before = signal_history.board_summary(conn)["coverage"]
    assert before["registered_feeds"] == 0
    sync_curated_sources(db)
    with connect(db) as conn:
        after = signal_history.board_summary(conn)["coverage"]
        feeds = {feed["host"]: feed for feed in signal_history.board_summary(conn)["infra_feeds"]}
    assert after["registered_feeds"] == len(SOURCES)
    assert after["registered_feeds_curated"] == len(set(SOURCES) & signal_history.board_hosts()) > before["registered_feeds_curated"]
    # Shared infrastructure feeds (Akamai, Cloudflare) are registered without anyone looking them up.
    assert any(feed["feed"] for feed in feeds.values())
    assert all(feed["registered"] for feed in feeds.values() if feed["feed"])


def test_sync_unregisters_only_its_own_rows_and_keeps_an_operator_switch(db, monkeypatch, small):
    sync_curated_sources(db)
    with connect(db) as conn:
        # A page discovered for a looked-up host, a legacy row and an operator-disabled curated feed.
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence)
                        VALUES ('example.org','Example Status','https://status.example.org/',
                                'https://status.example.org/api/v2/status.json','discovered_cname'),
                               ('legacy.example','Legacy','https://status.legacy.example/','https://status.legacy.example/api/v2/status.json',NULL)""")
        conn.execute("UPDATE status_sources SET enabled=false WHERE site_host='render.com'")
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('cloudflare.com','major',now())")
    # cloudflare.com leaves the table; github.io's feed address changes.
    changed = {**SMALL, "github.io": Source("GitHub Status", "https://www.githubstatus.com/", "https://www.githubstatus.com/api/v2/status.json?x")}
    del changed["cloudflare.com"]
    with connect(db) as conn:
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('github.io','none',now())")
    monkeypatch.setattr(status_refresh, "SOURCES", changed)
    assert sync_curated_sources(db) == {"curated": 3, "added": 0, "changed": 1, "removed": 1}
    hosts = {r["site_host"]: r for r in q(db, "SELECT site_host,evidence,enabled FROM status_sources")}
    assert set(hosts) == {"github.com", "github.io", "render.com", "example.org", "legacy.example"}
    assert hosts["example.org"]["evidence"] == "discovered_cname" and hosts["legacy.example"]["evidence"] is None
    assert hosts["render.com"]["enabled"] is False  # the operator's switch stays
    assert {r["site_host"] for r in q(db, "SELECT site_host FROM status_cache")} == set()  # removed and changed: no stale read


# --- reading -------------------------------------------------------------------------------------------

def test_curated_feeds_are_read_without_a_lookup_and_aliases_share_one_read(db, small):
    sync_curated_sources(db)
    feeds = Feeds()
    assert batch(db, feeds) == {"checked": 3, "updated": 3, "failed": 0, "remaining": 0}
    # github.com and github.io share one feed: three requests for four hosts, one each, all with status.json only.
    assert sorted(feeds.seen) == sorted({source.api_url for source in SMALL.values()})
    cache = {r["site_host"]: r for r in q(db, "SELECT site_host,indicator,fetched_at,failures FROM status_cache")}
    assert set(cache) == set(SMALL) and all(r["indicator"] == "none" and r["failures"] is None for r in cache.values())
    # Nothing is due again within the hour.
    feeds.seen.clear()
    assert batch(db, feeds) == {"checked": 0, "updated": 0, "failed": 0, "remaining": 0} and feeds.seen == []
    age(db, 59)
    assert batch(db, feeds)["checked"] == 0
    age(db, 2)
    assert batch(db, feeds)["checked"] == 3


def test_a_first_lookup_of_a_preregistered_host_answers_fresh_at_once(db, small):
    sync_curated_sources(db)
    batch(db, Feeds())
    settings = db
    with TestClient(create_app(settings)) as client:
        answer = client.get("/v1/incidents/check", params={"site_host": "render.com"}).json()
    assert answer["provider_status"]["freshness"] == "fresh" and answer["provider_status"]["indicator"] == "none"
    assert answer["provider_source_state"] == "feed_connected"
    assert q(db, "SELECT kind FROM background_reads WHERE kind='status_feed'") == []  # nothing to read behind it


def test_lookup_registered_discovered_feeds_keep_being_read(db, small, monkeypatch):
    with connect(db) as conn:
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence)
                        VALUES ('example.org','Example Status','https://status.example.org/',
                                'https://status.example.org/api/v2/status.json','discovered_cname'),
                               ('notlooked.org','Other Status','https://status.notlooked.org/',
                                'https://status.notlooked.org/api/v2/status.json','discovered_cname')""")
        conn.execute("INSERT INTO queried_hosts (site_host) VALUES ('example.org')")
    read = []
    monkeypatch.setattr(status_refresh, "_read_discovered_statuspage",
                        lambda host, *_args: (read.append(host), (None, "none", None))[1])
    assert batch(db, Feeds()) == {"checked": 1, "updated": 1, "failed": 0, "remaining": 0}
    assert read == ["example.org"]  # a discovered page nobody looked up is not read


def test_failures_back_off_and_refusals_are_not_retried_around(db, small):
    sync_curated_sources(db)

    def answer(request):
        return httpx.Response(403) if "cloudflarestatus" in str(request.url) else httpx.Response(502)

    feeds = Feeds(answer)
    assert batch(db, feeds) == {"checked": 3, "updated": 0, "failed": 3, "remaining": 0}
    rows = {r["site_host"]: r for r in q(db, "SELECT site_host,failures,last_error_detail FROM status_cache")}
    assert rows["cloudflare.com"]["last_error_detail"] == "http_403" and rows["render.com"]["last_error_detail"] == "http_502"
    assert {r["failures"] for r in rows.values()} == {1}
    # 502: due again after 15 minutes; 403: after an hour. Each request went once, unchanged (no other agent, no retry).
    assert len(feeds.seen) == 3 and len(set(feeds.seen)) == 3
    age(db, 16)
    feeds.seen.clear()
    assert batch(db, feeds)["checked"] == 2 and not any("cloudflarestatus" in url for url in feeds.seen)
    rows = {r["site_host"]: r["failures"] for r in q(db, "SELECT site_host,failures FROM status_cache")}
    assert rows["render.com"] == 2 and rows["cloudflare.com"] == 1
    age(db, 16)
    assert batch(db, feeds)["checked"] == 0  # the second failure waits 30 minutes
    age(db, 30)
    assert batch(db, feeds)["checked"] == 3  # 30 more minutes: the 502s again, and the 403's hour has passed
    # A failure that is not a refusal never waits past an hour (the third and fourth 502), while the second 403 waits two.
    age(db, 59)
    assert batch(db, feeds)["checked"] == 0
    age(db, 2)
    assert batch(db, feeds)["checked"] == 2 and not any("cloudflarestatus" in url for url in feeds.seen[-2:])
    assert q(db, "SELECT failures FROM status_cache WHERE site_host='render.com'")[0]["failures"] == 4
    age(db, 61)
    assert batch(db, feeds)["checked"] == 3  # the 502s after an hour again; the 403 after its two hours
    # A success clears the count.
    age(db, 24 * 60)
    feeds.answer = lambda request: httpx.Response(200, json=QUIET)
    assert batch(db, feeds)["updated"] == 3
    assert {r["failures"] for r in q(db, "SELECT failures FROM status_cache")} == {None}


def test_one_run_is_bounded_by_its_limit_and_its_time_budget(db, small, monkeypatch):
    sync_curated_sources(db)
    feeds = Feeds()
    assert batch(db, feeds, limit=1) == {"checked": 1, "updated": 1, "failed": 0, "remaining": 2}
    age(db, 61)
    # Every feed has a stored statement now (a feed without one is a read that only decides, and stops at half the time, D50).
    with connect(db) as conn:
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) SELECT site_host,'none',now() - interval '2 hours' "
                     "FROM status_sources WHERE site_host NOT IN (SELECT site_host FROM status_cache)")
    # A fake clock that moves 40 s with each paced read: reads start at 0 s and 40 s; at 80 s the 60 s budget is spent.
    clock = [0.0]
    monkeypatch.setattr(status_refresh, "_clock", lambda: clock[0])
    monkeypatch.setattr(background_reads, "_pace", lambda: clock.__setitem__(0, clock[0] + 40.0))
    result = batch(db, feeds, time_budget=60.0)
    assert result["checked"] == 2 and result["updated"] == 2 and result["remaining"] == 1
    assert batch(db, feeds, time_budget=-1.0)["checked"] == 0  # no read starts once the budget is spent


def test_a_feed_another_process_is_reading_is_skipped(db, small):
    # The lock is the database's, keyed by the feed address, so the cron and the web process exclude each other.
    sync_curated_sources(db)
    feeds = Feeds()
    with connect(db) as holder:
        holder.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (SOURCES["cloudflare.com"].api_url,))
        result = batch(db, feeds)
        assert result["checked"] == 2 and result["remaining"] == 1
        assert not any("cloudflarestatus" in url for url in feeds.seen)
        # The lookup path's reader skips it too and answers from the cache (nothing cached yet: not ok).
        with httpx.Client(transport=httpx.MockTransport(feeds)) as client:
            assert status_refresh.refresh_one(db, "cloudflare.com", client=client, min_interval=0, wait=True) is False
        assert not any("cloudflarestatus" in url for url in feeds.seen)
    # Released: the next run reads it.
    assert batch(db, feeds)["checked"] == 1


@pytest.mark.parametrize("answer,expected", [
    (lambda request: httpx.Response(200, json=QUIET), True),
    (lambda request: httpx.Response(503, text="down"), False),
], ids=["read_ok", "read_fails"])
def test_the_feed_lock_connection_holds_no_table_lock_during_the_read(db, small, answer, expected):
    # The connection holding the feed's advisory lock stays open for the whole HTTP read (up to about 70 s). If it
    # had read status_cache, its ACCESS SHARE lock would make a deploy's ALTER wait and, behind it, every lookup.
    sync_curated_sources(db)
    with connect(db) as conn:  # an earlier attempt exists, so the 15 s rule has a row to read
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES "
                     "('render.com','none',now() - interval '1 hour')")
    seen = []

    def during_read(request):
        with connect(db) as probe:
            table_locks = probe.execute(
                """SELECT c.relname, l.mode FROM pg_locks l JOIN pg_class c ON c.oid=l.relation
                   WHERE l.locktype='relation' AND l.granted AND c.relname LIKE 'status_cache%'
                     AND l.pid <> pg_backend_pid()""").fetchall()
            advisory = probe.execute(
                "SELECT count(*) AS n FROM pg_locks WHERE locktype='advisory' AND granted AND pid <> pg_backend_pid()"
            ).fetchone()["n"]
            try:  # what a deploy's ALTER needs: it must not have to wait for this read
                probe.execute("LOCK TABLE status_cache IN ACCESS EXCLUSIVE MODE NOWAIT")
                lockable = True
            except psycopg.errors.LockNotAvailable:
                lockable = False
        seen.append((table_locks, advisory, lockable))
        return answer(request)

    with httpx.Client(transport=httpx.MockTransport(during_read)) as client:
        outcome = status_refresh.refresh_one(db, "render.com", client=client, min_interval=15, wait=True)
    assert outcome is expected
    assert seen, "the feed was not requested"
    for table_locks, advisory, lockable in seen:
        assert table_locks == [] and lockable
        assert advisory == 1  # the feed's own lock is held while it is read
    # The lock is gone with the read: a second reader is not excluded.
    with connect(db) as other:
        assert other.execute("SELECT pg_try_advisory_xact_lock(hashtext(%s)) AS ok",
                             (SOURCES["render.com"].api_url,)).fetchone()["ok"]


def test_a_feed_read_moments_ago_elsewhere_is_not_read_again(db, small):
    sync_curated_sources(db)
    feeds = Feeds()
    with httpx.Client(transport=httpx.MockTransport(feeds)) as client:
        assert status_refresh.refresh_one(db, "render.com", client=client, min_interval=0, wait=True)
    with connect(db) as conn:  # make it due again by age, but leave the attempt itself seconds old
        conn.execute("UPDATE status_cache SET indicator='major' WHERE site_host='render.com'")
    feeds.seen.clear()
    batch(db, feeds, now=datetime.now(timezone.utc) + timedelta(hours=1))
    assert not any("render" in url for url in feeds.seen)  # attempted within 15 s: skipped, at most 1 request/s per host


@pytest.mark.parametrize("indicator,queued_at_16_minutes", [("major", True), ("maintenance", True), ("none", False)])
def test_the_lookup_path_keeps_a_failing_active_feed_at_fifteen_minutes(db, small, indicator, queued_at_16_minutes):
    # During a large outage the status page itself may time out or answer 5xx: a feed whose last statement was
    # active is retried every 15 minutes however often it failed; a quiet one waits at most an hour.
    sync_curated_sources(db)
    with connect(db) as conn:
        conn.execute("""INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail,failures)
                        VALUES ('render.com',%s,now() - interval '40 minutes',now() - interval '16 minutes','timeout',6)""",
                     (indicator,))
    with TestClient(create_app(db)) as client:
        client.get("/v1/incidents/check", params={"site_host": "render.com"})
        assert bool(q(db, "SELECT kind FROM background_reads")) is queued_at_16_minutes
    if not queued_at_16_minutes:
        # A quiet statement stays fresh on the lookup path for two hours; the cron retries the failing feed after
        # the one-hour ceiling, however many failures came before.
        def due_hosts():
            with connect(db) as conn:
                return {host for _due, hosts, _source in status_refresh.due_feeds(conn, datetime.now(timezone.utc)) for host in hosts}
        assert "render.com" not in due_hosts()
        age(db, 45)  # 61 minutes after the sixth failure
        assert "render.com" in due_hosts()


ACTIVE_PAGE = json.loads((Path(__file__).parent / "fixtures" / "status_scope" / "cloudflare.json").read_text(encoding="utf-8"))


def active_answer(refuse: str | None = None):
    routes = {"/api/v2/status.json": ACTIVE_PAGE["status"], "/api/v2/incidents/unresolved.json": ACTIVE_PAGE["unresolved"],
              "/api/v2/scheduled-maintenances/active.json": ACTIVE_PAGE["maintenances"]}

    def answer(request):
        if "cloudflarestatus" not in str(request.url):
            return httpx.Response(200, json=QUIET)
        if request.url.path == refuse:
            return httpx.Response(429)
        return httpx.Response(200, json=routes[request.url.path])
    return answer


def cloudflare_paths(feeds):
    return [url.split("cloudflarestatus.com")[1] for url in feeds.seen if "cloudflarestatus" in url]


def test_no_scope_list_starts_once_the_run_budget_is_spent(db, small, monkeypatch):
    sync_curated_sources(db)
    with connect(db) as conn:  # only Cloudflare is due
        conn.execute("UPDATE status_sources SET enabled=false WHERE site_host<>'cloudflare.com'")
    clock = [0.0]
    monkeypatch.setattr(status_refresh, "_clock", lambda: clock[0])
    # The pause before the feed moves the clock past the 60 s budget: status.json is still read (it was started),
    # its lists are not, and that is not counted as a scope failure.
    monkeypatch.setattr(background_reads, "_pace", lambda: clock.__setitem__(0, clock[0] + 70.0))
    feeds = Feeds(active_answer())
    assert batch(db, feeds, time_budget=60.0)["updated"] == 1
    assert cloudflare_paths(feeds) == ["/api/v2/status.json"]
    row = q(db, "SELECT indicator,active_incidents,scope_failures FROM status_cache WHERE site_host='cloudflare.com'")[0]
    assert row == {"indicator": "major", "active_incidents": None, "scope_failures": None}


def test_a_refused_scope_list_rests_in_the_cron_too(db, small):
    sync_curated_sources(db)
    feeds = Feeds(active_answer(refuse="/api/v2/incidents/unresolved.json"))
    batch(db, feeds)
    assert cloudflare_paths(feeds) == ["/api/v2/status.json", "/api/v2/incidents/unresolved.json"]
    assert q(db, "SELECT scope_failures,scope_error FROM status_cache WHERE site_host='cloudflare.com'")[0] == {
        "scope_failures": 1, "scope_error": "http_429"}
    # Active, so due again after 15 minutes; the lists rest an hour after the refusal: status.json only.
    age(db, 16)
    feeds.seen.clear()
    assert batch(db, feeds)["checked"] >= 1
    assert cloudflare_paths(feeds) == ["/api/v2/status.json"]


def test_a_lookup_before_the_sync_drops_the_cache_of_a_changed_feed_address(db, monkeypatch, small):
    with connect(db) as conn:
        assert status_refresh.register_source(conn, "render.com")
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('render.com','major',now())")
    moved = {**SMALL, "render.com": Source("Render Status", "https://status.render.com/", "https://status.render.com/api/v2/status.json?moved")}
    monkeypatch.setattr(status_refresh, "SOURCES", moved)
    with connect(db) as conn:
        assert status_refresh.register_source(conn, "render.com")
    assert q(db, "SELECT count(*) AS n FROM status_cache WHERE site_host='render.com'")[0]["n"] == 0
    # An unchanged address keeps its cache.
    with connect(db) as conn:
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('render.com','none',now())")
        assert status_refresh.register_source(conn, "render.com")
    assert q(db, "SELECT count(*) AS n FROM status_cache WHERE site_host='render.com'")[0]["n"] == 1


def test_a_successful_discovery_clears_the_failure_count(db):
    page = "https://status.example.org/"
    source = Source("Example Status", page, page + "api/v2/status.json", "discovered_statuspage")
    with connect(db) as conn:
        assert status_refresh.register_source(conn, "example.org", source)
        conn.execute("""INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail,failures)
                        VALUES ('example.org','unknown',NULL,now(),'timeout',5)""")
    background_reads._store_discovered(db, "example.org", (source, "none", None), {"body_sha256": "a" * 64})
    row = q(db, "SELECT indicator,failures,last_error_at FROM status_cache WHERE site_host='example.org'")[0]
    assert row == {"indicator": "none", "failures": None, "last_error_at": None}


def test_the_lookup_path_follows_the_same_backoff(db, small):
    sync_curated_sources(db)
    batch(db, Feeds(lambda request: httpx.Response(429)))
    with TestClient(create_app(db)) as client:
        for minutes in (20, 30):  # 20 and 50 minutes after a 429: inside its first hour
            age(db, minutes)
            client.get("/v1/incidents/check", params={"site_host": "render.com"})
            assert q(db, "SELECT kind FROM background_reads") == []
        age(db, 15)
        client.get("/v1/incidents/check", params={"site_host": "render.com"})
    assert [r["kind"] for r in q(db, "SELECT kind FROM background_reads")] == ["status_feed"]


# --- D46: the last statement outlives one failed read; the two lists are read together ---------------------

def datto_type_row(db, *, fetched_minutes: int, error: str | None):
    """A critical page with four incidents updated yesterday, read fetched_minutes ago, then one failed read."""
    with connect(db) as conn:
        conn.execute("""INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail,failures,
                                                  active_incidents,max_incident_impact,incident_updated_at,active_maintenances)
                        VALUES ('render.com','critical',now() - make_interval(mins => %s),
                                CASE WHEN %s::text IS NULL THEN NULL ELSE now() - interval '1 minute' END,%s,
                                CASE WHEN %s::text IS NULL THEN NULL ELSE 1 END,4,'critical',now() - interval '1 day',0)""",
                     (fetched_minutes, error, error, error))


@pytest.mark.parametrize("error", ["timeout", "http_502", "http_429", "http_403"])
def test_one_failed_read_after_a_recent_statement_still_waits(db, small, error):
    # The live case: a status page that answers in 13 s against a 10 s body limit, read successfully 70 minutes
    # earlier. The statement is 70 minutes old, so it is fresh and still a reason to wait; the failure is shown.
    sync_curated_sources(db)
    datto_type_row(db, fetched_minutes=70, error=error)
    answer = service.check(db, "render.com")
    assert answer["provider_status"]["freshness"] == "fresh"
    assert answer["provider_status"]["error_detail"] == error
    assert answer["recommendation"]["action"] == "wait_provider_incident"


def test_a_statement_older_than_two_hours_is_stale_with_or_without_a_failure(db, small):
    sync_curated_sources(db)
    datto_type_row(db, fetched_minutes=121, error="timeout")
    answer = service.check(db, "render.com")
    assert answer["provider_status"]["freshness"] == "stale" and answer["provider_status"]["error_detail"] == "timeout"
    assert answer["recommendation"]["action"] != "wait_provider_incident"


def test_a_recent_statement_without_a_failure_has_no_error_detail(db, small):
    sync_curated_sources(db)
    datto_type_row(db, fetched_minutes=70, error=None)
    status = service.check(db, "render.com")["provider_status"]
    assert status["freshness"] == "fresh" and status.get("error_detail") is None


def test_only_failures_are_still_stale(db, small):
    sync_curated_sources(db)
    with connect(db) as conn:
        conn.execute("""INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail,failures)
                        VALUES ('render.com','unknown',NULL,now(),'timeout',1)""")
    answer = service.check(db, "render.com")
    assert answer["provider_status"]["freshness"] == "stale" and answer["provider_status"]["error_detail"] == "timeout"
    assert answer["recommendation"]["action"] != "wait_provider_incident"


def test_the_owner_board_follows_the_same_freshness(db, small):
    sync_curated_sources(db)
    datto_type_row(db, fetched_minutes=70, error="timeout")
    with connect(db) as conn:
        assert "render.com" in signal_history._provider_now(conn, datetime.now(timezone.utc))


def test_the_budget_ending_after_the_first_list_still_reads_the_second(db, small, monkeypatch):
    sync_curated_sources(db)
    with connect(db) as conn:  # only Cloudflare is due
        conn.execute("UPDATE status_sources SET enabled=false WHERE site_host<>'cloudflare.com'")
    clock = [0.0]
    monkeypatch.setattr(status_refresh, "_clock", lambda: clock[0])
    inner = active_answer()

    def answer(request):
        if request.url.path == "/api/v2/incidents/unresolved.json":
            clock[0] += 70.0  # the incident list takes the run past its 60 s budget
        return inner(request)
    feeds = Feeds(answer)
    assert batch(db, feeds, time_budget=60.0)["updated"] == 1
    assert cloudflare_paths(feeds) == ["/api/v2/status.json", "/api/v2/incidents/unresolved.json",
                                       "/api/v2/scheduled-maintenances/active.json"]
    row = q(db, "SELECT active_incidents,active_maintenances,scope_failures FROM status_cache WHERE site_host='cloudflare.com'")[0]
    assert row["active_incidents"] is not None and row["active_maintenances"] == 2 and row["scope_failures"] is None


def test_the_search_page_shows_a_failure_under_a_fresh_statement(db, small):
    sync_curated_sources(db)
    datto_type_row(db, fetched_minutes=70, error="timeout")
    with TestClient(create_app(db)) as client:
        page = client.get("/en/search", params={"site_host": "render.com"}).text
    assert "Critical provider-reported issue" in page and "Latest feed read failed:" in page and "timeout" in page
    assert "Live source unavailable" not in page


def test_the_owner_board_infra_feed_stays_fresh_after_one_failed_read(db, small):
    sync_curated_sources(db)
    with connect(db) as conn:
        conn.execute("""INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail,failures)
                        VALUES ('cloudflare.com','major',now() - interval '70 minutes',now() - interval '1 minute','timeout',1)""")
    with connect(db) as conn:
        feeds = {feed["host"]: feed for feed in signal_history._infra_feeds(conn, set(), datetime.now(timezone.utc))}
    assert feeds["cloudflare.com"]["fresh"] is True


@pytest.mark.parametrize("label", sorted(background_reads.FEED_UNVERIFIED))
def test_a_source_that_no_longer_verifies_is_stale_at_once(db, small, label):
    # no_cname, id_mismatch, not_public: the discovered source is no longer verified, so its last statement may
    # not be the provider's; unlike a timeout this ends the statement at once (D46).
    sync_curated_sources(db)
    datto_type_row(db, fetched_minutes=10, error=label)
    with connect(db) as conn:
        conn.execute("""INSERT INTO status_cache (site_host,indicator,fetched_at,last_error_at,last_error_detail,failures)
                        VALUES ('cloudflare.com','major',now() - interval '10 minutes',now(),%s,1)""", (label,))
    answer = service.check(db, "render.com")
    assert answer["provider_status"]["freshness"] == "stale" and answer["provider_status"]["error_detail"] == label
    assert answer["recommendation"]["action"] != "wait_provider_incident"
    with connect(db) as conn:
        now = datetime.now(timezone.utc)
        assert "render.com" not in signal_history._provider_now(conn, now)
        feeds = {feed["host"]: feed for feed in signal_history._infra_feeds(conn, set(), now)}
    assert feeds["cloudflare.com"]["fresh"] is False
