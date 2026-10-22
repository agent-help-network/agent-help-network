"""The Atom history of a Statuspage page as an open-announcement signal (D52) against the dedicated ahn_test database.

Stub robots.txt files and stub feeds only: no real host is read. The gate is the real one (feed_robots.Gate with a stub
robots.txt reader), the reads go through the real cron batch and the lookup's background refresh, and the answers come
from the real service.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from html import escape

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
from ahn.status_refresh import Source, refresh_batch, refresh_one, sync_curated_sources  # noqa: E402

DEFAULT = b"User-agent: *\nDisallow: /api/\nDisallow: /embed/\n"          # what most hosted pages say: the API is closed, the Atom is open
ALL_CLOSED = b"User-agent: *\nDisallow: /\n"
ATOM_CLOSED = b"User-agent: *\nDisallow: /history.atom\n"                  # the API is open, the Atom is not
API_AND_ATOM_CLOSED = b"User-agent: *\nDisallow: /api/\nDisallow: /history.atom\n"
PAGE = Source("Page Status", "https://page.example/", "https://page.example/api/v2/status.json")
TABLE = {"page.test": PAGE}
NOW = datetime.now(timezone.utc)
MAJOR = {"page": {"id": "p", "updated_at": "2026-10-22T11:00:00Z"}, "status": {"indicator": "major"}}


def atom(*entries) -> bytes:
    def one(words, published, updated):
        body = "".join(f"<p><small>Oct <var data-var='date'>22</var>, <var data-var='time'>08:07</var> UTC</small><br> <strong>{w}</strong> - text</p>" for w in words)
        return (f"<entry><id>tag:page.example,2005:Incident/1</id><published>{published:%Y-%m-%dT%H:%M:%SZ}</published>"
                f"<updated>{updated:%Y-%m-%dT%H:%M:%SZ}</updated><title>Title</title><content type=\"html\">{escape(body)}</content></entry>")
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><feed xmlns=\"http://www.w3.org/2005/Atom\"><id>x</id><title>h</title>"
            + "".join(one(*entry) for entry in entries) + "</feed>").encode()


def fresh_open():
    """One incident, open, updated 20 minutes ago, and an old resolved one."""
    return atom((["Monitoring", "Investigating"], NOW - timedelta(hours=2), NOW - timedelta(minutes=20)),
                (["Resolved", "Investigating"], NOW - timedelta(days=10), NOW - timedelta(days=10)))


def quiet():
    return atom((["Resolved", "Investigating"], NOW - timedelta(days=10), NOW - timedelta(days=10)))


class Robots:
    def __init__(self, body=DEFAULT):
        self.body = body
        self.calls: list[str] = []

    def __call__(self, host, path, *, cap_bytes, **_options):
        self.calls.append(f"{host}{path}")
        return ("ok", self.body, "http_200")


class World:
    def __init__(self):
        self.now = datetime.now(timezone.utc)
        self.robots = Robots()
        self.requests: list[str] = []
        self.atom = fresh_open()
        self.atom_status = 200
        self.api = MAJOR

    def gate(self, settings):
        return feed_robots.Gate(settings, fetch=self.robots, clock=lambda: self.now)

    def feeds(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url.path)
        if request.url.path == "/history.atom":
            if self.atom_status in (301, 302):
                return httpx.Response(self.atom_status, headers={"Location": "/inactive.atom"})
            return httpx.Response(self.atom_status, content=self.atom, headers={"content-type": "application/atom+xml"})
        if request.url.path == "/api/v2/status.json":
            return httpx.Response(200, json=self.api)
        return httpx.Response(404)


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


@pytest.fixture
def world(db, real_feed_robots, monkeypatch):
    w = World()
    monkeypatch.setattr(status_refresh, "SOURCES", dict(TABLE))
    monkeypatch.setattr(service, "SOURCES", dict(TABLE))
    monkeypatch.setattr(status_refresh, "make_gate", w.gate)
    monkeypatch.setattr(status_refresh, "_sleep", lambda _s: None)
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    sync_curated_sources(db)
    return w


def q(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def batch(settings, w):
    with httpx.Client(transport=httpx.MockTransport(w.feeds)) as client:
        return refresh_batch(settings, client=client, now=w.now)


def row(settings):
    return q(settings, "SELECT indicator,fetched_at,last_error_at,last_error_detail,failures,active_incidents,max_incident_impact,"
                       "incident_updated_at,active_maintenances,affected,feed_source,window_blind,body_sha256 FROM status_cache WHERE site_host='page.test'")[0]


def age(settings, delta):
    with connect(settings) as conn:
        conn.execute("UPDATE status_cache SET fetched_at=fetched_at - %s, last_error_at=last_error_at - %s", (delta, delta))


def look(settings, error_class="site_access"):
    with TestClient(create_app(settings)) as client:
        return client.get("/v1/incidents/check", params={"site_host": "page.test", "error_class": error_class}).json()


# --- the read ------------------------------------------------------------------------------------------------------------

def test_where_only_the_api_is_disallowed_the_atom_is_read_and_the_api_never(db, world):
    batch(db, world)
    assert world.requests == ["/history.atom"]  # not status.json, not either scope list
    stored = row(db)
    assert (stored["indicator"], stored["feed_source"], stored["active_incidents"], stored["window_blind"]) == ("unknown", "history_atom", 1, False)
    assert stored["fetched_at"] is not None and stored["last_error_detail"] is None and stored["body_sha256"]
    assert stored["max_incident_impact"] is None and stored["affected"] is None  # nothing the Atom does not say is filled in
    assert abs((stored["incident_updated_at"] - (NOW - timedelta(minutes=20))).total_seconds()) < 2
    assert world.robots.calls == ["page.example/robots.txt"]  # one robots.txt read, which decided every path


def test_a_host_that_closes_both_paths_is_not_read_at_all(db, world):
    world.robots.body = API_AND_ATOM_CLOSED
    batch(db, world)
    assert world.requests == []
    assert row(db)["last_error_detail"] == "robots_disallowed" and row(db)["feed_source"] is None
    world.robots.body = ALL_CLOSED
    age(db, timedelta(0))
    world.now += timedelta(hours=27)
    batch(db, world)
    assert world.requests == []


def test_a_host_that_allows_the_api_is_read_as_before_even_when_it_closes_the_atom(db, world):
    world.robots.body = ATOM_CLOSED
    batch(db, world)
    assert "/history.atom" not in world.requests and "/api/v2/status.json" in world.requests
    stored = row(db)
    assert stored["indicator"] == "major" and stored["feed_source"] is None and stored["window_blind"] is None


def test_the_atom_is_read_at_most_once_per_refresh_and_a_lookup_refresh_uses_the_same_gate(db, world):
    with httpx.Client(transport=httpx.MockTransport(world.feeds)) as client:
        assert refresh_one(db, "page.test", client=client, min_interval=0, wait=True) is True
    assert world.requests == ["/history.atom"] and world.robots.calls == ["page.example/robots.txt"]
    assert row(db)["feed_source"] == "history_atom"


def test_a_switched_off_feed_is_disabled_not_failing_and_is_asked_again_a_day_later(db, world):
    world.atom_status = 302
    assert batch(db, world)["failed"] == 1
    stored = row(db)
    assert stored["last_error_detail"] == "feed_disabled" and stored["feed_source"] is None and stored["fetched_at"] is None
    assert world.requests == ["/history.atom"]  # the redirect was not followed
    with connect(db) as conn:
        due = status_refresh.due_feeds(conn, datetime.now(timezone.utc) + timedelta(hours=23))
        assert due == []
        assert len(status_refresh.due_feeds(conn, datetime.now(timezone.utc) + timedelta(hours=24, minutes=1))) == 1
    answer = look(db)
    assert not answer.get("provider_status") and answer["official_status_page"]["source_url"] == "https://page.example/"  # a link, never a reading


@pytest.mark.parametrize("body,label", [
    (b'<?xml version="1.0"?><!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]><feed xmlns="http://www.w3.org/2005/Atom">&x;</feed>', "invalid_body"),
    (b"<html>maintenance</html>", "invalid_body"),
    (b"x" * (131_072 + 10), "too_large"),
], ids=["doctype", "html", "oversize"])
def test_a_body_that_is_not_a_safe_atom_feed_is_a_failed_read_that_stores_nothing(db, world, body, label):
    world.atom = body
    assert batch(db, world)["failed"] == 1
    stored = row(db)
    assert stored["last_error_detail"] == label and stored["failures"] == 1
    assert stored["feed_source"] is None and stored["active_incidents"] is None and stored["fetched_at"] is None


def test_a_later_failure_keeps_the_earlier_reading_on_show_and_the_atom_source_label(db, world):
    batch(db, world)
    world.atom_status = 500
    age(db, timedelta(minutes=61))
    world.now += timedelta(minutes=61)
    batch(db, world)
    stored = row(db)
    assert stored["last_error_detail"] == "http_500" and stored["feed_source"] == "history_atom" and stored["active_incidents"] == 1
    assert look(db)["provider_status"]["source_kind"] == "history_atom"


# --- what a lookup says ---------------------------------------------------------------------------------------------------

def test_an_open_incident_is_shown_as_information_and_does_not_change_the_recommendation(db, world):
    batch(db, world)
    answer = look(db)
    provider = answer["provider_status"]
    assert provider["source_kind"] == "history_atom" and provider["indicator"] is None and provider["freshness"] == "fresh"
    assert provider["active_incidents"] == 1 and "incident_updated_at" in provider
    assert not {"basis", "max_incident_impact", "affected", "window_blind"} & set(provider)
    assert provider["active_maintenances"] == 0  # the window is trusted: nothing is crowding maintenance out of the feed
    assert answer["recommendation"]["action"] != "wait_provider_incident"  # information only: the verdict is the one without the feed
    assert "provider_scope" not in answer["recommendation"]["reasons"]
    assert "provider_unknown" in [c["code"] for c in answer["next_checks"]]
    assert answer["provider_source_state"] == "feed_connected"
    assert {"summary_line", "next_action"} <= set(answer)  # the response guide still finds its row


def test_no_open_incident_reads_as_unknown_never_as_healthy(db, world):
    world.atom = quiet()
    batch(db, world)
    provider = look(db)["provider_status"]
    assert provider["source_kind"] == "history_atom" and provider["indicator"] is None
    assert "active_incidents" not in provider  # a count of zero would read as "none": left out
    assert "provider_clear" not in [c["code"] for c in look(db)["next_checks"]]


def test_a_blind_window_is_marked_and_the_maintenance_count_is_left_out(db, world):
    world.atom = atom(*[(["Scheduled"], NOW + timedelta(days=1 + i), NOW) for i in range(25)])
    batch(db, world)
    stored = row(db)
    assert stored["window_blind"] is True and stored["active_maintenances"] is None
    provider = look(db)["provider_status"]
    assert provider["window_blind"] is True and "active_maintenances" not in provider and "active_incidents" not in provider


def test_the_same_answer_with_and_without_the_feed_has_the_same_recommendation(db, world):
    without = look(db)
    batch(db, world)
    with_atom = look(db)
    assert without["recommendation"] == with_atom["recommendation"]


def test_before_the_first_atom_read_the_answer_links_the_page_and_a_read_is_queued(db, world):
    assert not look(db).get("provider_status")  # nothing decided yet: the interim list does not know this host
    with connect(db) as conn:
        feed_robots.store(conn, feed_robots.build("page.example", ("ok", DEFAULT, "http_200"), status_refresh.feed_paths(PAGE), datetime.now(timezone.utc)))
    answer = look(db)
    assert not answer.get("provider_status") and answer["official_status_page"]["source_url"] == "https://page.example/"  # a link, never a reading
    assert q(db, "SELECT kind FROM background_reads WHERE kind='status_feed'")  # the read that will show it is queued


# --- the API comes and goes -----------------------------------------------------------------------------------------------

def episodes(db):
    return q(db, "SELECT ended_at,ended_reason FROM outage_episodes WHERE signal='P' AND site_host='page.test'")


def test_the_switch_between_the_api_and_the_atom_drops_what_the_other_said(db, world, monkeypatch):
    github = REAL_SOURCES["github.com"]
    monkeypatch.setattr(status_refresh, "SOURCES", {"github.com": github})
    monkeypatch.setattr(service, "SOURCES", {"github.com": github})
    sync_curated_sources(db)
    feed_host = "www.githubstatus.com"
    world.robots = Robots(ATOM_CLOSED)  # the API is open
    batch(db, world)
    assert q(db, "SELECT indicator,feed_source FROM status_cache WHERE site_host='github.com'") == [{"indicator": "major", "feed_source": None}]
    with connect(db) as conn:
        signal_history._apply_provider_events(conn, datetime.now(timezone.utc), 1000)
    assert [e["ended_at"] for e in q(db, "SELECT ended_at FROM outage_episodes WHERE signal='P' AND site_host='github.com'")] == [None]
    world.robots.body = DEFAULT   # now the API is closed and the Atom is open
    age(db, timedelta(hours=27))
    world.now += timedelta(hours=27)
    world.requests.clear()
    batch(db, world)
    assert world.requests == ["/history.atom"]
    now = q(db, "SELECT indicator,feed_source,active_incidents,max_incident_impact FROM status_cache WHERE site_host='github.com'")[0]
    assert now == {"indicator": "unknown", "feed_source": "history_atom", "active_incidents": 1, "max_incident_impact": None}
    ended = q(db, "SELECT ended_reason FROM outage_episodes WHERE signal='P' AND site_host='github.com'")
    assert [e["ended_reason"] for e in ended] == ["robots_disallowed"]  # the problem that rested on the API ends
    world.robots.body = ATOM_CLOSED   # and back
    age(db, timedelta(hours=27))
    world.now += timedelta(hours=27)
    batch(db, world)
    back = q(db, "SELECT indicator,feed_source,window_blind FROM status_cache WHERE site_host='github.com'")[0]
    assert back == {"indicator": "major", "feed_source": None, "window_blind": None}
    assert feed_host


# --- a discovered page ------------------------------------------------------------------------------------------------------

class Pool:
    paths: list = []

    def __init__(self, *_a, **_kw):
        pass

    def request(self, _method, path, **_kw):
        Pool.paths.append(path)
        payload = fresh_open()

        class Response:
            status = 200

            def read(self, _limit):
                return payload

            def release_conn(self):
                pass
        return Response()

    def close(self):
        pass


def test_a_discovered_page_reads_its_atom_through_the_pinned_pool(monkeypatch):
    Pool.paths = []
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    source = Source("Example Status", "https://status.example.org/", "https://status.example.org/api/v2/status.json", "discovered_statuspage")
    digest: dict = {}
    reading, updated = status_refresh._read_source(source, None, digest, history=True)
    assert Pool.paths == ["/history.atom"] and updated is None and reading.open_incidents == 1 and digest["body_sha256"]


# --- review fixes: failed first Atom read, odd times, export, discovered 302 ----------------------------------------------

@pytest.mark.parametrize("failure", ["redirect", "http_500", "invalid_body"])
def test_a_failed_first_atom_read_still_drops_the_api_statement_and_closes_its_problem(db, world, monkeypatch, failure):
    github = REAL_SOURCES["github.com"]
    monkeypatch.setattr(status_refresh, "SOURCES", {"github.com": github})
    monkeypatch.setattr(service, "SOURCES", {"github.com": github})
    sync_curated_sources(db)
    world.robots = Robots(ATOM_CLOSED)  # the API is open: read as before
    batch(db, world)
    with connect(db) as conn:
        signal_history._apply_provider_events(conn, datetime.now(timezone.utc), 1000)
    assert q(db, "SELECT indicator FROM status_cache WHERE site_host='github.com'") == [{"indicator": "major"}]
    world.robots.body = DEFAULT  # now only the Atom is open, and it cannot be read
    if failure == "redirect":
        world.atom_status = 302
    elif failure == "http_500":
        world.atom_status = 500
    elif failure == "invalid_body":
        world.atom = b"<html>no</html>"
    age(db, timedelta(hours=27))
    world.now += timedelta(hours=27)
    batch(db, world)
    age(db, timedelta(hours=26))  # past any backoff: a second failed attempt
    world.now += timedelta(hours=26)
    batch(db, world)
    assert q(db, "SELECT failures FROM status_cache WHERE site_host='github.com'") == [{"failures": 2}]  # the backoff count is kept
    stored = q(db, "SELECT indicator,fetched_at,feed_source,active_incidents,last_error_detail FROM status_cache WHERE site_host='github.com'")[0]
    assert stored["indicator"] == "unknown" and stored["fetched_at"] is None and stored["feed_source"] is None and stored["active_incidents"] is None
    assert stored["last_error_detail"] is not None and stored["last_error_detail"] != "robots_disallowed"  # the failed Atom read is recorded
    ended = q(db, "SELECT ended_reason FROM outage_episodes WHERE signal='P' AND site_host='github.com'")
    assert [e["ended_reason"] for e in ended] == ["robots_disallowed"]


OTHER = Source("Other Status", "https://other.example/", "https://other.example/api/v2/status.json")


def two_pages(db, world, monkeypatch, body):
    table = {"page.test": PAGE, "other.test": OTHER}
    monkeypatch.setattr(status_refresh, "SOURCES", dict(table))
    monkeypatch.setattr(service, "SOURCES", dict(table))
    sync_curated_sources(db)
    world.robots = Robots(body)
    plain = world.feeds

    def by_host(request):
        if request.url.host == "page.example":
            return plain(request)
        return httpx.Response(200, content=quiet(), headers={"content-type": "application/atom+xml"}) if request.url.path == "/history.atom" \
            else httpx.Response(200, json={"page": {"updated_at": "2026-10-22T11:00:00Z"}, "status": {"indicator": "none"}})
    world.feeds = by_host


@pytest.mark.parametrize("edge", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_an_odd_time_in_an_atom_feed_is_no_time_and_the_batch_reads_on(db, world, monkeypatch, edge):
    two_pages(db, world, monkeypatch, DEFAULT)
    world.atom = atom((["Investigating"], NOW - timedelta(hours=1), NOW)).replace(f"{NOW:%Y-%m-%dT%H:%M:%SZ}</updated>".encode(), f"{edge}</updated>".encode())
    result = batch(db, world)
    assert result["failed"] == 0 and result["updated"] == 2
    assert row(db)["active_incidents"] == 1 and row(db)["incident_updated_at"] is None  # the incident is read; its time is unknown
    assert q(db, "SELECT feed_source FROM status_cache WHERE site_host='other.test'") == [{"feed_source": "history_atom"}]


@pytest.mark.parametrize("edge", ["0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00"])
def test_an_odd_time_in_a_status_json_fails_that_feed_and_the_batch_reads_the_next(db, world, monkeypatch, edge):
    two_pages(db, world, monkeypatch, ATOM_CLOSED)
    world.api = {"page": {"id": "p", "updated_at": edge}, "status": {"indicator": "none"}}
    result = batch(db, world)
    assert result["failed"] == 1 and result["updated"] == 1
    assert row(db)["last_error_detail"] == "invalid_body" and row(db)["failures"] == 1


def test_a_reading_from_the_atom_is_not_exported_as_a_status_record(db, world, tmp_path):
    from ahn import export
    batch(db, world)
    assert row(db)["feed_source"] == "history_atom"
    with connect(db) as conn:
        assert list(export._rows(conn, "status", None)) == []
        conn.execute("UPDATE status_cache SET feed_source=NULL")
        assert len(list(export._rows(conn, "status", None))) == 1  # the same row as an API reading is exported


def test_a_discovered_page_whose_atom_redirects_is_disabled_not_failing(monkeypatch):
    class Redirecting(Pool):
        def request(self, _method, path, **_kw):
            class Response:
                status = 302

                def release_conn(self):
                    pass
            return Response()

    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Redirecting)
    source = Source("Example Status", "https://status.example.org/", "https://status.example.org/api/v2/status.json", "discovered_statuspage")
    with pytest.raises(status_refresh.FeedFailure) as caught:
        status_refresh._read_source(source, None, {}, history=True)
    assert caught.value.label == "feed_disabled"
    assert background_reads.feed_backoff(1, "feed_disabled") == background_reads.FEED_BACKOFF_MAX


def test_a_problem_still_open_after_the_cache_row_was_deleted_is_closed_by_the_first_atom_read(db, world, monkeypatch):
    github = REAL_SOURCES["github.com"]
    monkeypatch.setattr(status_refresh, "SOURCES", {"github.com": github})
    monkeypatch.setattr(service, "SOURCES", {"github.com": github})
    sync_curated_sources(db)
    world.robots = Robots(ATOM_CLOSED)  # the API is open
    batch(db, world)
    with connect(db) as conn:
        signal_history._apply_provider_events(conn, datetime.now(timezone.utc), 1000)
        conn.execute("DELETE FROM status_cache WHERE site_host='github.com'")  # e.g. a changed feed address drops the cached read
    assert [e["ended_at"] for e in q(db, "SELECT ended_at FROM outage_episodes WHERE signal='P' AND site_host='github.com'")] == [None]
    world.robots.body = DEFAULT  # only the Atom is open now
    world.now += timedelta(hours=27)
    batch(db, world)
    assert q(db, "SELECT feed_source FROM status_cache WHERE site_host='github.com'") == [{"feed_source": "history_atom"}]
    ended = q(db, "SELECT ended_reason FROM outage_episodes WHERE signal='P' AND site_host='github.com'")
    assert [e["ended_reason"] for e in ended] == ["robots_disallowed"]
