"""Curated status feeds registered and read without a lookup (D44, migration 026), against ahn_test.

Stub feeds only (MockTransport); no real status page is read.
"""

from __future__ import annotations

import os
import threading

import httpx
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
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
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
    # A fake clock that moves 40 s with each paced read: reads start at 0 s and 40 s; at 80 s the 60 s budget is spent.
    clock = [0.0]
    monkeypatch.setattr(status_refresh, "_clock", lambda: clock[0])
    monkeypatch.setattr(background_reads, "_pace", lambda: clock.__setitem__(0, clock[0] + 40.0))
    result = batch(db, feeds, time_budget=60.0)
    assert result["checked"] == 2 and result["updated"] == 2 and result["remaining"] == 1
    assert batch(db, feeds, time_budget=-1.0)["checked"] == 0  # no read starts once the budget is spent


def test_a_feed_a_lookup_is_reading_is_skipped_by_the_run(db, small):
    sync_curated_sources(db)
    lock = status_refresh._SOURCE_LOCKS.setdefault("cloudflare.com", threading.Lock())
    with lock:
        result = batch(db, Feeds())
    assert result["checked"] == 2 and result["remaining"] == 1


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
