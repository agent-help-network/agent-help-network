"""Interim stop (D49 stage 0) against ahn_test: blocked feeds are never requested, not counted as failures, and an
earlier stored statement is not answered from. Stub feeds only."""

from __future__ import annotations

import os

import httpx
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, service, status_refresh  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import FEED_ALLOWLIST_ENV, Source, refresh_batch, sync_curated_sources  # noqa: E402

QUIET = {"page": {"updated_at": "2026-10-21T11:00:00Z"}, "status": {"indicator": "none"}}
OPEN = Source("Open Status", "https://open.example/", "https://open.example/api/v2/status.json")
SHUT = Source("Shut Status", "https://shut.example/", "https://shut.example/api/v2/status.json")
TABLE = {"open.test": OPEN, "shut.test": SHUT, "shut2.test": SHUT}  # two hosts share the blocked feed


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
def table(monkeypatch):
    monkeypatch.setattr(status_refresh, "SOURCES", dict(TABLE))
    monkeypatch.setattr(service, "SOURCES", dict(TABLE))
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
    monkeypatch.setattr(status_refresh, "_sleep", lambda _seconds: None)
    monkeypatch.setenv(FEED_ALLOWLIST_ENV, "1")
    monkeypatch.setattr(status_refresh, "_allowed_feed_hosts", lambda: frozenset({"open.example"}))


def q(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


class Feeds:
    def __init__(self):
        self.seen: list[str] = []

    def __call__(self, request):
        self.seen.append(str(request.url))
        return httpx.Response(200, json=QUIET)


def batch(settings, feeds):
    with httpx.Client(transport=httpx.MockTransport(feeds)) as client:
        return refresh_batch(settings, client=client)


def seed_old_statement(settings, host="shut.test"):
    """What an earlier build stored for a feed that is now blocked: an enabled row and a fresh 'major' statement."""
    with connect(settings) as conn:
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence)
                        VALUES (%s,'Shut Status','https://shut.example/','https://shut.example/api/v2/status.json','curated_mapping')""", (host,))
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at,active_incidents) VALUES (%s,'major',now(),2)", (host,))


def test_sync_registers_only_listed_feeds_and_drops_old_rows_of_blocked_ones(db):
    seed_old_statement(db)
    with connect(db) as conn:  # a discovered feed row is dropped too (its host is not listed)
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence)
                        VALUES ('example.org','E','https://status.example.org/','https://status.example.org/api/v2/status.json','discovered_cname')""")
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('example.org','major',now())")
    result = sync_curated_sources(db)
    assert result["curated"] == 1 and result["added"] == 1 and result["removed"] == 2
    assert [r["site_host"] for r in q(db, "SELECT site_host FROM status_sources")] == ["open.test"]
    assert q(db, "SELECT site_host FROM status_cache") == []


def test_batch_requests_only_listed_feeds_and_a_blocked_one_is_neither_failure_nor_backoff(db):
    sync_curated_sources(db)
    feeds = Feeds()
    assert batch(db, feeds) == {"checked": 1, "updated": 1, "failed": 0, "remaining": 0}
    assert feeds.seen == ["https://open.example/api/v2/status.json"]  # zero requests to the blocked host
    assert [r["site_host"] for r in q(db, "SELECT site_host FROM status_cache")] == ["open.test"]


def test_a_blocked_row_that_is_still_stored_is_not_due_and_not_read(db):
    seed_old_statement(db)  # no sync yet: the row is still there
    feeds = Feeds()
    assert batch(db, feeds) == {"checked": 0, "updated": 0, "failed": 0, "remaining": 0}
    assert feeds.seen == []
    assert status_refresh.refresh_one(db, "shut.test") is False
    assert q(db, "SELECT failures FROM status_cache WHERE site_host='shut.test'")[0]["failures"] is None


def test_an_old_statement_is_not_answered_after_a_lookup(db):
    seed_old_statement(db)
    with TestClient(create_app(db)) as client:
        answer = client.get("/v1/incidents/check", params={"site_host": "shut.test"}).json()
    assert not answer.get("provider_status")
    assert answer.get("provider_source_state") != "feed_connected"
    assert q(db, "SELECT site_host FROM status_sources WHERE site_host='shut.test'") == []
    assert q(db, "SELECT site_host FROM status_cache WHERE site_host='shut.test'") == []
    assert q(db, "SELECT kind FROM background_reads WHERE kind IN ('status_feed','status_discovery')") == []  # nothing queued to read it


def test_a_lookup_of_a_listed_host_still_registers_and_answers(db):
    sync_curated_sources(db)
    batch(db, Feeds())
    with TestClient(create_app(db)) as client:
        answer = client.get("/v1/incidents/check", params={"site_host": "open.test"}).json()
    assert answer["provider_status"]["freshness"] == "fresh" and answer["provider_source_state"] == "feed_connected"


def test_a_discovered_feed_is_not_registered_or_stored(db):
    source = Source("E", "https://status.example.org/", "https://status.example.org/api/v2/status.json", "discovered_statuspage")
    with connect(db) as conn:
        assert status_refresh.register_source(conn, "example.org", source) is False
        assert conn.execute("SELECT count(*) AS n FROM status_sources").fetchone()["n"] == 0
    background_reads._store_discovered(db, "example.org", (source, "none", None), {})
    assert q(db, "SELECT count(*) AS n FROM status_cache")[0]["n"] == 0


def test_switch_off_reads_everything_again(db, monkeypatch):
    monkeypatch.setenv(FEED_ALLOWLIST_ENV, "0")
    sync_curated_sources(db)
    feeds = Feeds()
    assert batch(db, feeds)["checked"] == 2  # the open feed and the shared shut feed, once each
    assert sorted(feeds.seen) == ["https://open.example/api/v2/status.json", "https://shut.example/api/v2/status.json"]
    assert {r["site_host"] for r in q(db, "SELECT site_host FROM status_cache")} == set(TABLE)
