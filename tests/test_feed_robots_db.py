"""Robots.txt decisions for status-feed hosts (D50) against the dedicated ahn_test database.

Stub robots.txt files and stub feeds only: no real host is read. The gate runs for real (feed_robots.Gate with a stub
robots.txt reader and a clock the test moves), the reads go through the real cron batch, the lookup's background
refresh and the first discovery.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, feed_robots, service, signal_history, status_refresh  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import SOURCES as REAL_SOURCES  # noqa: E402
from ahn.status_refresh import Source, due_feeds, refresh_batch, refresh_one, sync_curated_sources  # noqa: E402

REAL_DISCOVER = background_reads.discover  # tests/conftest.py replaces it for every test; this is the real one
FIXTURES = Path(__file__).parent / "fixtures" / "status_scope"
ATLASSIAN_DEFAULT = b"User-agent: *\nDisallow: /api/\nDisallow: /embed/\nDisallow: /history.atom\n"  # Atom closed too: D52 reads it otherwise (tests/test_status_atom_db.py)
QUIET = {"page": {"updated_at": "2026-10-21T11:00:00Z"}, "status": {"indicator": "none"}}

OPEN = Source("Open Status", "https://open.example/", "https://open.example/api/v2/status.json")
SHUT = Source("Shut Status", "https://shut.example/", "https://shut.example/api/v2/status.json")
FAIL = Source("Fail Status", "https://fail.example/", "https://fail.example/api/v2/status.json")
TABLE = {"open.test": OPEN, "shut.test": SHUT, "shut2.test": SHUT, "fail.test": FAIL}  # two hosts share the shut feed


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
                     "robots_read_daily, feed_robots RESTART IDENTITY")
    return settings


class Robots:
    """Stub robots.txt reader: host -> the result fetch_public_file would give; records every read."""

    def __init__(self, **answers):
        self.answers = {host.replace("_", "."): answer for host, answer in answers.items()}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, host, path, *, cap_bytes, **_options):
        self.calls.append((host, path))
        return self.answers.get(host, ("absent", None, "http_404"))

    def hosts(self):
        return sorted(host for host, _path in self.calls)


class World:
    def __init__(self):
        self.now = datetime.now(timezone.utc)  # status_cache stamps use the database clock, so the test clock starts at the real one
        self.sleeps: list[float] = []
        self.robots = Robots(open_example=("absent", None, "http_404"), shut_example=("ok", ATLASSIAN_DEFAULT, "http_200"),
                             fail_example=("refused", None, "http_503"))
        self.feed_requests: list[str] = []

    def clock(self):
        return self.now

    def gate(self, settings):
        return feed_robots.Gate(settings, fetch=self.robots, clock=self.clock)

    def feeds(self, request: httpx.Request) -> httpx.Response:
        self.feed_requests.append(f"{request.url.host}{request.url.path}")
        return httpx.Response(200, json=QUIET)


@pytest.fixture
def world(db, real_feed_robots, monkeypatch):
    w = World()
    monkeypatch.setattr(status_refresh, "SOURCES", dict(TABLE))
    monkeypatch.setattr(service, "SOURCES", dict(TABLE))
    monkeypatch.setattr(status_refresh, "make_gate", w.gate)
    monkeypatch.setattr(status_refresh, "_sleep", w.sleeps.append)
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    return w


def q(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def advance(settings, w: World, delta: timedelta):
    """Move the clock and age every stored read by the same time (status_cache stamps use the database's own clock)."""
    w.now += delta
    with connect(settings) as conn:
        conn.execute("UPDATE status_cache SET fetched_at=fetched_at - %s, last_error_at=last_error_at - %s", (delta, delta))


def batch(settings, w: World):
    with httpx.Client(transport=httpx.MockTransport(w.feeds)) as client:
        return refresh_batch(settings, client=client, now=w.now)


def cache(settings, host):
    return q(settings, "SELECT indicator,fetched_at,last_error_at,last_error_detail,failures,active_incidents,scope_failures "
                       "FROM status_cache WHERE site_host=%s", (host,))[0]


def seed_statement(settings, host="shut.test", source=SHUT):
    with connect(settings) as conn:
        conn.execute("""INSERT INTO status_sources (site_host,source_name,status_page_url,api_url,evidence)
                        VALUES (%s,%s,%s,%s,'curated_mapping') ON CONFLICT (site_host) DO NOTHING""",
                     (host, source.name, source.page_url, source.api_url))
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at,active_incidents) "
                     "VALUES (%s,'major',now() - interval '2 hours',2)", (host,))  # due: the next batch reads it


# --- the cron batch --------------------------------------------------------------------------------------------------

def test_the_batch_reads_each_robots_txt_once_and_requests_only_what_it_allows(db, world):
    sync_curated_sources(db)
    assert batch(db, world) == {"checked": 1, "updated": 1, "failed": 0, "remaining": 0}
    assert world.robots.hosts() == ["fail.example", "open.example", "shut.example"]  # one read per host, not per alias
    assert {path for _h, path in world.robots.calls} == {"/robots.txt"}
    assert world.feed_requests == ["open.example/api/v2/status.json"]  # nothing at all was asked of shut or fail
    assert world.sleeps == [status_refresh.SCOPE_GAP_SECONDS]  # a second between the robots.txt read and the feed it allowed
    rows = {r["feed_host"]: r for r in q(db, "SELECT * FROM feed_robots")}
    assert {h: r["robots_status"] for h, r in rows.items()} == {"open.example": "absent", "shut.example": "ok", "fail.example": "error"}
    shut = rows["shut.example"]
    assert shut["decisions"]["/api/v2/status.json"] == {"allowed": False, "rule": "disallow: /api/", "group": "*"}
    assert shut["decisions"]["/history.atom"]["allowed"] is False  # decided and stored too (this file closes it; D52 reads it when it is open)
    assert shut["robots_sha256"] == hashlib.sha256(ATLASSIAN_DEFAULT).hexdigest() and shut["parser_version"] == feed_robots.PARSER_VERSION
    assert "Disallow" not in json.dumps({k: str(v) for k, v in shut.items()})  # the file itself is not stored
    assert all(not rows["fail.example"]["decisions"][p]["allowed"] for p in rows["fail.example"]["decisions"])
    assert cache(db, "open.test")["indicator"] == "none" and cache(db, "open.test")["fetched_at"] is not None


def test_a_blocked_feed_is_not_a_failure_and_starts_no_backoff(db, world):
    sync_curated_sources(db)
    batch(db, world)
    for host in ("shut.test", "shut2.test", "fail.test"):  # the alias shares the feed's decision; a 503 file blocks too
        row = cache(db, host)
        assert row["last_error_detail"] == "robots_disallowed"
        assert row["last_error_at"] is None and row["failures"] is None and row["fetched_at"] is None and row["indicator"] == "unknown"
    with connect(db) as conn:
        assert due_feeds(conn, world.now + timedelta(minutes=30)) == []  # nothing due: not the blocked feeds, not the fresh one
        due = {tuple(hosts) for _t, hosts, _s in due_feeds(conn, world.now + timedelta(hours=1, minutes=1))}
    assert due == {("open.test",), ("fail.test",)}  # the read file expires after an hour; a 24-hour decision is not due yet
    assert refresh_one(db, "shut.test", min_interval=0, wait=True) is False
    assert world.robots.hosts() == ["fail.example", "open.example", "shut.example"]  # still one read each


def test_the_decision_lasts_a_day_and_a_failed_read_is_tried_again_after_an_hour(db, world):
    sync_curated_sources(db)
    batch(db, world)
    advance(db, world, timedelta(minutes=61))
    assert batch(db, world)["checked"] == 1  # the open feed is due hourly
    assert world.robots.hosts() == ["fail.example", "fail.example", "open.example", "shut.example"]
    assert world.feed_requests == ["open.example/api/v2/status.json"] * 2
    advance(db, world, timedelta(hours=23))  # 24 hours 1 minute after the first read: the slack (up to 2 hours) is not over
    batch(db, world)
    assert world.robots.hosts().count("open.example") + world.robots.hosts().count("shut.example") == 2  # not read again within a day
    advance(db, world, timedelta(hours=3))  # past a day and the slack
    batch(db, world)
    assert world.robots.hosts().count("open.example") == 2 and world.robots.hosts().count("shut.example") == 2


def test_a_changed_robots_txt_is_followed_in_both_directions(db, world):
    sync_curated_sources(db)
    batch(db, world)
    world.robots.answers["shut.example"] = ("absent", None, "http_404")
    world.robots.answers["open.example"] = ("ok", ATLASSIAN_DEFAULT, "http_200")
    world.robots.answers["fail.example"] = ("ok", b"User-agent: AgentHelpNetwork\nAllow: /\n\nUser-agent: *\nDisallow: /\n", "http_200")
    advance(db, world, timedelta(hours=27))
    world.feed_requests.clear()
    batch(db, world)
    assert sorted(world.feed_requests) == ["fail.example/api/v2/status.json", "shut.example/api/v2/status.json"]
    for host in ("shut.test", "shut2.test", "fail.test"):
        row = cache(db, host)
        assert row["indicator"] == "none" and row["fetched_at"] is not None and row["last_error_detail"] is None
    opened = cache(db, "open.test")  # what was read before is gone the moment its file closed the path
    assert opened["indicator"] == "unknown" and opened["fetched_at"] is None and opened["last_error_detail"] == "robots_disallowed"


def test_an_earlier_statement_is_dropped_at_once_and_the_answer_links_the_page(db, world):
    seed_statement(db)
    seed_statement(db, "shut2.test")
    batch(db, world)
    for host in ("shut.test", "shut2.test"):
        row = cache(db, host)
        assert row["indicator"] == "unknown" and row["fetched_at"] is None and row["active_incidents"] is None
    with TestClient(create_app(db)) as client:
        answer = client.get("/v1/incidents/check", params={"site_host": "shut.test"}).json()
    assert not answer.get("provider_status") and answer["provider_source_state"] == "official_page_only"
    assert answer["official_status_page"] == {"source_name": "Shut Status", "source_url": "https://shut.example/"}
    assert q(db, "SELECT kind FROM background_reads WHERE kind IN ('status_feed','status_discovery')") == []  # nothing queued to read it


# --- the other read paths --------------------------------------------------------------------------------------------

def test_a_lookups_background_refresh_asks_the_same_gate(db, world):
    sync_curated_sources(db)
    with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
        assert refresh_one(db, "shut.test", client=client, min_interval=0, wait=True) is False
        assert refresh_one(db, "open.test", client=client, min_interval=0, wait=True) is True
        assert refresh_one(db, "fail.test", client=client, min_interval=0, wait=True) is False
    assert world.feed_requests == ["open.example/api/v2/status.json"]
    assert world.robots.hosts() == ["fail.example", "open.example", "shut.example"]


def scope_world(db, world, monkeypatch, robots):
    monkeypatch.setattr(status_refresh, "SOURCES", {"rubygems.org": REAL_SOURCES["rubygems.org"]})
    monkeypatch.setattr(service, "SOURCES", {"rubygems.org": REAL_SOURCES["rubygems.org"]})
    world.robots.answers["status.rubygems.org"] = robots
    page = json.loads((FIXTURES / "rubygems.json").read_text(encoding="utf-8"))
    keys = {"/api/v2/status.json": "status", "/api/v2/incidents/unresolved.json": "unresolved",
            "/api/v2/scheduled-maintenances/active.json": "maintenances"}
    world.feeds = lambda request: (world.feed_requests.append(request.url.path), httpx.Response(200, json=page[keys[request.url.path]]))[1]
    sync_curated_sources(db)


def test_a_scope_list_the_file_disallows_is_skipped_without_a_failure(db, world, monkeypatch):
    scope_world(db, world, monkeypatch, ("ok", b"User-agent: *\nDisallow: /api/v2/incidents/\n", "http_200"))
    with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
        assert refresh_one(db, "rubygems.org", client=client, min_interval=0, wait=True) is True
    assert world.feed_requests == ["/api/v2/status.json"]  # the feed is allowed, so it is read; no list is asked for
    row = q(db, "SELECT indicator,failures,scope_failures,scope_error,active_incidents FROM status_cache WHERE site_host='rubygems.org'")[0]
    assert row["indicator"] == "maintenance" and row["failures"] is None
    assert row["scope_failures"] is None and row["scope_error"] is None and row["active_incidents"] is None


def test_the_lists_are_read_when_the_file_allows_them(db, world, monkeypatch):
    scope_world(db, world, monkeypatch, ("absent", None, "http_404"))
    with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
        assert refresh_one(db, "rubygems.org", client=client, min_interval=0, wait=True) is True
    assert world.feed_requests == ["/api/v2/status.json", "/api/v2/incidents/unresolved.json", "/api/v2/scheduled-maintenances/active.json"]
    assert q(db, "SELECT active_incidents FROM status_cache WHERE site_host='rubygems.org'")[0]["active_incidents"] == 0


class Pool:
    created: list = []

    def __init__(self, *_a, **_kw):
        Pool.created.append(1)

    def request(self, _method, path, **_kw):
        class Response:
            status = 200

            def read(self, _limit):
                return json.dumps({"page": {"id": "abcdefghijkl", "name": "Example"}, "status": {"indicator": "none"}}).encode()

            def release_conn(self):
                pass
        return Response()

    def close(self):
        pass


def discovery(monkeypatch):
    Pool.created = []
    steps: list[str] = []
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda host: (steps.append("cname"), ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))[1])
    monkeypatch.setattr(status_refresh, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    return steps


def test_discovery_asks_after_the_dns_check_and_before_any_request(db, world, monkeypatch):
    steps = discovery(monkeypatch)
    world.robots.answers["status.example.org"] = ("ok", ATLASSIAN_DEFAULT, "http_200")
    assert REAL_DISCOVER("example.org", settings=db) is None  # the real background_reads.discover, with settings
    assert steps == ["cname"] and Pool.created == []  # DNS only so far, and no request to the page
    assert world.robots.calls == [("status.example.org", "/robots.txt")]
    assert REAL_DISCOVER("example.org", settings=db) is None
    assert len(world.robots.calls) == 1  # the day's decision is reused, so a retried discovery costs no request
    assert q(db, "SELECT feed_host FROM feed_robots") == [{"feed_host": "status.example.org"}]


def test_discovery_proceeds_when_the_file_allows_the_feed_and_never_without_a_gate(db, world, monkeypatch):
    discovery(monkeypatch)
    world.robots.answers["status.example.org"] = ("absent", None, "http_404")
    source, indicator, _updated = REAL_DISCOVER("example.org", settings=db)
    assert source.api_url == "https://status.example.org/api/v2/status.json" and indicator == "none" and Pool.created == [1]
    assert world.sleeps == [status_refresh.SCOPE_GAP_SECONDS]
    steps = discovery(monkeypatch)
    assert status_refresh.discover_source("example.org") is None and steps == [] and Pool.created == []  # no gate: not even DNS
    assert REAL_DISCOVER("example.org") is None  # no settings, no gate


# --- what a lookup shows ----------------------------------------------------------------------------------------------

def lookup_world(db, monkeypatch):
    listed_host = sorted(feed_robots.interim_hosts())[0]
    site, source = next((h, s) for h, s in REAL_SOURCES.items() if feed_robots.host_and_path(s.api_url)[0] == listed_host)
    unlisted_site, unlisted = next((h, s) for h, s in REAL_SOURCES.items()
                                   if feed_robots.host_and_path(s.api_url)[0] not in feed_robots.interim_hosts())
    table = {site: source, unlisted_site: unlisted}
    monkeypatch.setattr(status_refresh, "SOURCES", table)
    monkeypatch.setattr(service, "SOURCES", table)
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    sync_curated_sources(db)
    return site, source, unlisted_site, unlisted


def read_before(db, host):
    """A statement an earlier read stored."""
    with connect(db) as conn:
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES (%s,'minor',now())", (host,))


def look(db, host):
    with TestClient(create_app(db)) as client:
        return client.get("/v1/incidents/check", params={"site_host": host}).json()


def decide(db, source, *, fetched):
    host, _path = feed_robots.host_and_path(source.api_url)
    with connect(db) as conn:
        feed_robots.store(conn, feed_robots.build(host, fetched, status_refresh.feed_paths(source), datetime.now(timezone.utc)))


def test_the_interim_list_decides_what_shows_only_until_a_decision_is_stored(db, real_feed_robots, monkeypatch):
    site, source, unlisted_site, unlisted = lookup_world(db, monkeypatch)
    read_before(db, site)
    # No decision yet: a host on the interim list (read by hand on 2026-10-22) keeps its statement on show ...
    assert look(db, site)["provider_status"]["indicator"] == "minor"
    # ... any other host shows none, and the read that decides is queued behind the lookup.
    answer = look(db, unlisted_site)
    assert not answer.get("provider_status")
    assert ("status_feed", unlisted_site) in {(r["kind"], r["site_host"]) for r in q(db, "SELECT kind,site_host FROM background_reads")}
    # A stored decision replaces the list in both directions.
    read_before(db, unlisted_site)
    decide(db, unlisted, fetched=("absent", None, "http_404"))
    assert look(db, unlisted_site)["provider_status"]["indicator"] == "minor"
    decide(db, source, fetched=("ok", b"User-agent: *\nDisallow: /\n", "http_200"))
    answer = look(db, site)
    assert not answer.get("provider_status") and answer["official_status_page"]["source_url"] == source.page_url
    decide(db, source, fetched=("error", None, "http_503"))  # a file that could not be read shows nothing either
    assert not look(db, site).get("provider_status")


def test_the_owner_board_counts_allowed_disallowed_and_unknown_feeds(db, world):
    sync_curated_sources(db)
    with connect(db) as conn:
        assert feed_robots.counts(conn) == {"allowed": 0, "disallowed": 0, "unknown": 3}  # nothing decided yet
    batch(db, world)
    with connect(db) as conn:
        # open allowed; shut disallowed; fail's file could not be read (503): not "disallowed", unknown
        assert feed_robots.counts(conn) == {"allowed": 1, "disallowed": 1, "unknown": 1}
        assert signal_history.board_summary(conn, now=datetime.now(timezone.utc))["coverage"]["feed_robots"] == {"allowed": 1, "disallowed": 1, "unknown": 1}


def test_a_second_feed_path_on_a_decided_host_is_added_without_a_second_day_of_reads(db, world):
    gate = world.gate(db)
    gate.decide("status.a.example", ["/api/v2/status.json"])
    decision = gate.decide("status.a.example", ["/other/feed.json"])  # asked for the new path only
    assert decision.read_now and set(decision.decisions) == {"/api/v2/status.json", "/other/feed.json"}  # the earlier one is kept
    again = gate.decide("status.a.example", ["/api/v2/status.json"])
    assert again.read_now is False and len(world.robots.calls) == 2  # still decided: no read a third time
    bad = gate.decide("", ["/x"])
    assert bad.robots_status == "error" and not bad.allows("/x") and len(world.robots.calls) == 2


# --- a failed read of a file that was read before (RFC 9309 section 2.4) ------------------------------------------------

OK_FILE = ("ok", b"User-agent: *\nDisallow: /embed/\n", "http_200")  # allows the feed


def test_an_ok_file_keeps_deciding_for_48_hours_while_the_file_cannot_be_read(db, world):
    gate = world.gate(db)
    paths = ["/api/v2/status.json"]
    world.robots.answers["status.b.example"] = OK_FILE
    assert gate.decide("status.b.example", paths).allows(paths[0])
    world.robots.answers["status.b.example"] = ("error", None, "read_timeout")
    world.now += timedelta(hours=27)  # the day and the slack are over: the file is read again, and fails
    kept = gate.decide("status.b.example", paths)
    assert kept.allows(paths[0]) and kept.robots_status == "ok" and kept.read_now
    row = q(db, "SELECT robots_status,error_detail,error_at FROM feed_robots")[0]
    assert (row["robots_status"], row["error_detail"]) == ("ok", "read_timeout") and row["error_at"] is not None
    world.now += timedelta(minutes=30)
    assert gate.decide("status.b.example", paths).allows(paths[0]) and len(world.robots.calls) == 2  # an hour holds the next try back
    world.now += timedelta(minutes=40)
    assert gate.decide("status.b.example", paths).allows(paths[0]) and len(world.robots.calls) == 3  # tried again, still failing
    world.now += timedelta(hours=20)  # 48 hours and more since the last ok read
    gone = gate.decide("status.b.example", paths)
    assert not gone.allows(paths[0]) and gone.robots_status == "error" and len(world.robots.calls) == 4
    world.robots.answers["status.b.example"] = OK_FILE  # the file is back: a fresh decision, no failure left on it
    world.now += timedelta(hours=1, minutes=1)
    back = gate.decide("status.b.example", paths)
    assert back.allows(paths[0]) and back.error_at is None
    assert q(db, "SELECT error_at FROM feed_robots")[0]["error_at"] is None


def test_no_grace_for_a_host_never_read_or_last_read_as_missing(db, world):
    gate = world.gate(db)
    paths = ["/api/v2/status.json"]
    world.robots.answers["status.c.example"] = ("error", None, "http_503")
    assert not gate.decide("status.c.example", paths).allows(paths[0])  # never read
    world.robots.answers["status.d.example"] = ("absent", None, "http_404")
    assert gate.decide("status.d.example", paths).allows(paths[0])
    world.robots.answers["status.d.example"] = ("error", None, "http_503")
    world.now += timedelta(hours=27)
    assert not gate.decide("status.d.example", paths).allows(paths[0])  # an absent file declared nothing to fall back on
    # An ok file that does not answer a wanted path cannot stand in for it either.
    world.robots.answers["status.e.example"] = OK_FILE
    gate.decide("status.e.example", paths)
    world.robots.answers["status.e.example"] = ("error", None, "http_503")
    world.now += timedelta(hours=27)
    assert not gate.decide("status.e.example", ["/another/feed.json"]).allows("/another/feed.json")


def test_a_blocked_stale_ok_file_still_blocks_and_the_cron_waits_for_the_next_try(db, world):
    sync_curated_sources(db)
    batch(db, world)  # shut: an ok file that disallows the feed
    world.robots.answers["shut.example"] = ("error", None, "http_500")
    advance(db, world, timedelta(hours=27))
    world.feed_requests.clear()
    batch(db, world)
    assert "shut.example/api/v2/status.json" not in world.feed_requests  # the declared rule still holds
    assert cache(db, "shut.test")["last_error_detail"] == "robots_disallowed"
    with connect(db) as conn:
        assert all(hosts != ["shut.test", "shut2.test"] for _t, hosts, _s in due_feeds(conn, world.now + timedelta(minutes=30)))


# --- the first runs after the change ----------------------------------------------------------------------------------

def many_feeds(monkeypatch, world, uncached=45, cached=5):
    table = {}
    for i in range(uncached):
        table[f"u{i:02d}.test"] = Source(f"U{i}", f"https://u{i:02d}.example/", f"https://u{i:02d}.example/api/v2/status.json")
        world.robots.answers[f"u{i:02d}.example"] = ("ok", ATLASSIAN_DEFAULT, "http_200")  # every one of them is blocked
    for i in range(cached):
        table[f"c{i}.test"] = Source(f"C{i}", f"https://c{i}.example/", f"https://c{i}.example/api/v2/status.json")
    monkeypatch.setattr(status_refresh, "SOURCES", table)
    monkeypatch.setattr(service, "SOURCES", table)
    return table


def test_a_run_makes_only_so_many_reads_that_only_decide_and_feeds_with_a_statement_go_first(db, world, monkeypatch):
    many_feeds(monkeypatch, world)
    sync_curated_sources(db)
    with connect(db) as conn:
        for i in range(5):  # statements stored by the build before this one, due for a read
            conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES (%s,'none',now() - interval '3 hours')", (f"c{i}.test",))
    result = batch(db, world)
    decided = [h for h in world.robots.hosts() if h.startswith("u")]
    assert len(decided) == status_refresh.FEED_DECISION_ONLY_MAX == 30
    assert sorted(r for r in world.feed_requests) == [f"c{i}.example/api/v2/status.json" for i in range(5)]  # none was starved
    assert result["checked"] == 5 and result["remaining"] == 15  # the rest stay due for the next run
    batch(db, world)
    assert len({h for h in world.robots.hosts() if h.startswith("u")}) == 45  # and are decided by it
    assert len([h for h in world.robots.hosts() if h.startswith("u")]) == 45  # each read once
    assert batch(db, world)["remaining"] == 0


def test_feeds_due_at_the_same_time_go_cached_first(db, world, monkeypatch):
    monkeypatch.setattr(feed_robots, "slack", lambda _host: timedelta(0))
    table = {"a.test": Source("A", "https://a.example/", "https://a.example/api/v2/status.json"),
             "z.test": Source("Z", "https://z.example/", "https://z.example/api/v2/status.json")}
    monkeypatch.setattr(status_refresh, "SOURCES", table)
    sync_curated_sources(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES ('z.test','none',now() - interval '3 hours')")
        for host in ("a.example", "z.example"):
            feed_robots.store(conn, feed_robots.build(host, ("ok", ATLASSIAN_DEFAULT, "http_200"), ["/api/v2/status.json"], world.now - timedelta(hours=30)))
        order = [hosts for _t, hosts, _s in due_feeds(conn, world.now)]
    assert order == [["z.test"], ["a.test"]]  # alphabetical order would put a.test first


def test_decisions_expire_spread_over_two_hours_and_never_early(db, world):
    slacks = {feed_robots.slack(f"h{i}.example") for i in range(200)}
    assert min(slacks) >= timedelta(0) and max(slacks) <= timedelta(hours=2) and len(slacks) > 150
    assert feed_robots.slack("h1.example") == feed_robots.slack("h1.example")  # fixed per host
    paths = ["/api/v2/status.json"]
    gate = world.gate(db)
    first = gate.decide("status.f.example", paths)
    assert first.expires_at() - first.checked_at == timedelta(hours=24) + feed_robots.slack("status.f.example")


# --- a provider problem ends with the statement; a slow reader does not undo a newer answer ----------------------------

def test_a_block_ends_the_open_provider_problem_of_the_hosts(db, world):
    sync_curated_sources(db)
    with connect(db) as conn:
        for host in ("shut.test", "shut2.test", "open.test"):
            conn.execute("""INSERT INTO outage_episodes (site_host,signal,kind,first_fail_at,confirmed_at,last_event_at,feed_connected)
                            VALUES (%s,'P','major',now(),now(),now(),true)""", (host,))
    batch(db, world)
    ended = {r["site_host"]: (r["ended_at"] is not None, r["ended_reason"]) for r in q(db, "SELECT site_host,ended_at,ended_reason FROM outage_episodes")}
    assert ended["shut.test"] == (True, "robots_disallowed") and ended["shut2.test"] == (True, "robots_disallowed")
    assert ended["open.test"] == (False, None)  # a feed that is read keeps its open problem


def test_an_older_answer_never_overwrites_a_newer_one(db):
    now = datetime.now(timezone.utc)
    newer = feed_robots.build("race.example", ("absent", None, "http_404"), ["/a"], now)
    older = feed_robots.build("race.example", ("error", None, "http_503"), ["/a"], now - timedelta(minutes=5))
    with connect(db) as conn:
        feed_robots.store(conn, newer)
        feed_robots.store(conn, older)  # a slower reader finishing late
        row = feed_robots.load(conn, "race.example")
    assert row.robots_status == "absent" and row.allows("/a")


# --- a provider problem and a block that comes and goes ------------------------------------------------------------------

MAJOR = {"page": {"id": "pgithub", "updated_at": "2026-10-22T11:00:00Z"}, "status": {"indicator": "major"}}


def github_world(db, world, monkeypatch):
    """github.com is a curated host (its provider problems are recorded): a stub feed that reports `major`."""
    table = {"github.com": REAL_SOURCES["github.com"]}
    monkeypatch.setattr(status_refresh, "SOURCES", table)
    monkeypatch.setattr(service, "SOURCES", table)
    world.feeds = lambda request: (world.feed_requests.append(request.url.path),
                                   httpx.Response(200, json=MAJOR) if request.url.path.endswith("status.json") else httpx.Response(404))[1]
    sync_curated_sources(db)
    return "www.githubstatus.com"


def apply_events(db):
    with connect(db) as conn:
        signal_history._apply_provider_events(conn, datetime.now(timezone.utc), 1000)


def episodes(db):
    return q(db, "SELECT id,ended_at,ended_reason FROM outage_episodes WHERE signal='P' AND site_host='github.com' ORDER BY id")


def test_a_problem_closed_because_the_file_could_not_be_read_opens_again_when_the_feed_is_read(db, world, monkeypatch):
    feed_host = github_world(db, world, monkeypatch)
    batch(db, world)
    apply_events(db)
    assert [e["ended_at"] for e in episodes(db)] == [None]  # the provider's major problem is open
    world.robots.answers[feed_host] = ("error", None, "http_503")
    advance(db, world, timedelta(hours=27))
    batch(db, world)
    apply_events(db)
    assert [(e["ended_at"] is not None, e["ended_reason"]) for e in episodes(db)] == [(True, "robots_unreadable")]
    assert [r["to_indicator"] for r in q(db, "SELECT to_indicator FROM provider_status_events WHERE site_host='github.com' ORDER BY id")] == ["major", "unknown"]
    world.robots.answers[feed_host] = ("absent", None, "http_404")
    advance(db, world, timedelta(hours=2))
    batch(db, world)
    apply_events(db)
    rows = episodes(db)
    assert len(rows) == 2 and rows[1]["ended_at"] is None  # still major: a new period, not silence
    assert cache(db, "github.com")["indicator"] == "major"


def test_a_problem_closed_by_a_rule_opens_again_when_the_file_allows_the_feed(db, world, monkeypatch):
    feed_host = github_world(db, world, monkeypatch)
    batch(db, world)
    apply_events(db)
    world.robots.answers[feed_host] = ("ok", ATLASSIAN_DEFAULT, "http_200")
    advance(db, world, timedelta(hours=27))
    batch(db, world)
    apply_events(db)
    assert [(e["ended_at"] is not None, e["ended_reason"]) for e in episodes(db)] == [(True, "robots_disallowed")]
    world.robots.answers[feed_host] = ("absent", None, "http_404")
    advance(db, world, timedelta(hours=27))
    batch(db, world)
    apply_events(db)
    rows = episodes(db)
    assert len(rows) == 2 and rows[1]["ended_at"] is None


def test_an_event_not_yet_applied_when_the_block_comes_does_not_open_a_period_afterwards(db, world, monkeypatch):
    feed_host = github_world(db, world, monkeypatch)
    batch(db, world)  # the major event is recorded but the rollup has not applied it yet
    assert q(db, "SELECT count(*) AS n FROM provider_status_events WHERE applied_at IS NULL")[0]["n"] == 1
    world.robots.answers[feed_host] = ("ok", ATLASSIAN_DEFAULT, "http_200")
    advance(db, world, timedelta(hours=27))
    batch(db, world)
    assert q(db, "SELECT count(*) AS n FROM provider_status_events WHERE applied_at IS NULL")[0]["n"] == 0
    apply_events(db)
    assert episodes(db) == []  # the statement was dropped: the old event must not open a period now


# --- the first run after the change, in time -----------------------------------------------------------------------------

class Timed(Robots):
    def __init__(self, clock, **answers):
        super().__init__(**answers)
        self.time = clock

    def __call__(self, host, path, *, cap_bytes, **options):
        self.time.t += 0.3  # one robots.txt request
        return super().__call__(host, path, cap_bytes=cap_bytes, **options)


class FakeTime:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_thirty_new_allowed_feeds_do_not_starve_the_feeds_that_are_already_read(db, world, monkeypatch):
    clock = FakeTime()
    world.robots = Timed(clock)  # every host: no robots.txt, so every feed is allowed
    monkeypatch.setattr(status_refresh, "_clock", clock)
    monkeypatch.setattr(status_refresh, "_sleep", lambda seconds: setattr(clock, "t", clock.t + seconds))  # the 1 s gap is real time here
    monkeypatch.setattr(background_reads, "_pace", lambda: setattr(clock, "t", clock.t + 0.5))  # two reads a second
    table = {}
    for i in range(30):
        table[f"n{i:02d}.test"] = Source(f"N{i}", f"https://n{i:02d}.example/", f"https://n{i:02d}.example/api/v2/status.json")
    for i in range(10):
        table[f"c{i}.test"] = Source(f"C{i}", f"https://c{i}.example/", f"https://c{i}.example/api/v2/status.json")
    monkeypatch.setattr(status_refresh, "SOURCES", table)
    monkeypatch.setattr(service, "SOURCES", table)
    sync_curated_sources(db)
    with connect(db) as conn:
        for i in range(10):  # active statuses due their 15-minute refresh
            conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES (%s,'major',now() - interval '20 minutes')", (f"c{i}.test",))
    started = clock.t
    with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
        result = refresh_batch(db, client=client, now=world.now)
    assert clock.t - started <= status_refresh.FEED_BATCH_SECONDS + 5  # inside the run's time
    fresh = {r["site_host"] for r in q(db, "SELECT site_host FROM status_cache WHERE indicator='none'")}
    assert {f"c{i}.test" for i in range(10)} <= fresh  # every feed that was already read got its refresh
    new = len([h for h in fresh if h.startswith("n")])
    assert 0 < new < 30 and clock.t - started <= status_refresh.FEED_BATCH_SECONDS  # new ones only until half the time was gone
    assert result["checked"] == 10 + new and result["remaining"] == 30 - new
    for _ in range(5):  # the rest follow in the next runs
        with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
            refresh_batch(db, client=client, now=world.now)
    assert len(q(db, "SELECT site_host FROM status_cache WHERE indicator='none'")) == 40


def test_when_time_is_short_the_feeds_already_read_go_before_the_new_ones(db, world, monkeypatch):
    clock = FakeTime()
    world.robots = Timed(clock)
    monkeypatch.setattr(status_refresh, "_clock", clock)
    monkeypatch.setattr(status_refresh, "_sleep", lambda seconds: setattr(clock, "t", clock.t + seconds))
    monkeypatch.setattr(background_reads, "_pace", lambda: setattr(clock, "t", clock.t + 0.5))
    table = {f"n{i:02d}.test": Source(f"N{i}", f"https://n{i:02d}.example/", f"https://n{i:02d}.example/api/v2/status.json") for i in range(30)}
    table.update({f"c{i}.test": Source(f"C{i}", f"https://c{i}.example/", f"https://c{i}.example/api/v2/status.json") for i in range(10)})
    monkeypatch.setattr(status_refresh, "SOURCES", table)
    monkeypatch.setattr(service, "SOURCES", table)
    sync_curated_sources(db)
    with connect(db) as conn:
        for i in range(10):
            conn.execute("INSERT INTO status_cache (site_host,indicator,fetched_at) VALUES (%s,'major',now() - interval '20 minutes')", (f"c{i}.test",))
    with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
        refresh_batch(db, client=client, now=world.now, time_budget=24.0)  # ten feeds cost about 18 s: room for them, not for thirty more
    fresh = {r["site_host"] for r in q(db, "SELECT site_host FROM status_cache WHERE indicator='none'")}
    assert {f"c{i}.test" for i in range(10)} <= fresh
