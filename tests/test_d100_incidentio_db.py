"""D100 (migration 038) against the dedicated ahn_test database: an incident.io-shaped status page whose overall indicator reads "none"
while an unresolved incident is listed is stored with that incident and answered without "no active issue".

Stub feeds only (MockTransport); no real status page and no real robots.txt is read."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, feed_robots, status_refresh  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.response_guide import FEED_HANDOFF_TAIL, STATUS_ROWS  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import refresh_batch, refresh_one  # noqa: E402

ULID = "01JMDK9XYNY6RXSED6SDWW50WY"
ULID_2 = "01JMDK9XYNY6RXSED6SDWW50WZ"
ATLASSIAN = "yh6f0r4529hb"
OPENAI = "status.openai.com"
CLOUDFLARE = "www.cloudflarestatus.com"
T6 = STATUS_ROWS["T6"]


def status_body(page_id, indicator="none", **page_extra):
    return {"page": {"id": page_id, "name": "Example", "url": "https://status.example.com/", "updated_at": "2026-11-05T17:05:56Z", **page_extra},
            "status": {"description": "All Systems Operational", "indicator": indicator}}


def summary_body(page_id, incidents):
    return {"page": {"id": page_id, "name": "Example"}, "components": [], "incidents": incidents}


OPEN = {"id": "i1", "name": "Elevated errors", "status": "monitoring", "impact": "minor", "created_at": "2026-11-05T15:25:03Z",
        "updated_at": "2026-11-05T17:05:56Z", "components": []}


class Feeds:
    """Stub status pages: host -> {path: body or httpx.Response}; records every path asked for."""

    def __init__(self, **pages):
        self.pages = pages
        self.seen: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(f"{request.url.host}{request.url.path}")
        body = self.pages.get(request.url.host, {}).get(request.url.path)
        if body is None:
            return httpx.Response(404, text="<html></html>")
        return body if isinstance(body, httpx.Response) else httpx.Response(200, json=body)


class SummaryDenied:
    """A gate that allows every path but summary.json (the robots.txt of such a host forbids it)."""

    def __init__(self, *_a, **_k):
        pass

    def decide(self, host, paths):
        return feed_robots.Decision(host, "ok", "http_200", datetime.now(timezone.utc),
                                    {p: {"allowed": not p.endswith("/summary.json"), "rule": None, "group": None} for p in paths})


@pytest.fixture
def lookup():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, collect_lookup_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE lookup_metrics, request_metrics, mcp_check_metrics, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads, provider_status_events RESTART IDENTITY")
    with TestClient(create_app(settings)) as client:
        yield settings, client


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    monkeypatch.setattr(status_refresh, "_sleep", lambda _seconds: None)


def use(monkeypatch, feeds: Feeds) -> httpx.Client:
    remote = httpx.Client(transport=httpx.MockTransport(feeds))
    monkeypatch.setattr(background_reads, "refresh_feed", lambda s, host: refresh_one(s, host, client=remote, wait=True))
    return remote


def ask(client, host, **params):
    response = client.get("/v1/incidents/check", params={"site_host": host, **params})
    assert response.status_code == 200
    return response.json()


def status_check(client, host, **params):
    return ask(client, host, task_step="status_check", **params)


def read_feed(settings, client, host):
    ask(client, host)
    background_reads.process_due(settings, limit=10)


def row(settings, host):
    with connect(settings) as conn:
        return conn.execute("SELECT indicator,active_incidents,max_incident_impact,active_maintenances,affected,feed_platform,"
                            "scope_failures,scope_error,failures FROM status_cache WHERE site_host=%s", (host,)).fetchone()


def openai_feed(summary=None, **kw):
    pages = {OPENAI: {"/api/v2/status.json": status_body(ULID), "/api/v2/summary.json": summary_body(ULID, [OPEN])}}
    if summary is not None:
        pages[OPENAI]["/api/v2/summary.json"] = summary
    return Feeds(**pages, **kw)


def test_an_open_incident_on_a_none_indicator_is_stored_and_answered_as_a_listed_incident(lookup, monkeypatch):
    settings, client = lookup
    feeds = openai_feed()
    with use(monkeypatch, feeds):
        read_feed(settings, client, "openai.com")
        plain = ask(client, "openai.com")
        answer = status_check(client, "openai.com")
    assert feeds.seen == [f"{OPENAI}/api/v2/status.json", f"{OPENAI}/api/v2/summary.json"]
    stored = row(settings, "openai.com")
    assert stored["indicator"] == "none" and stored["active_incidents"] == 1 and stored["max_incident_impact"] == "minor"
    assert stored["feed_platform"] == "incident_io" and stored["active_maintenances"] is None and stored["affected"] == ["Elevated errors"]
    assert stored["scope_failures"] is None and stored["failures"] is None
    status = answer["provider_status"]
    assert status["indicator"] == "none" and status["freshness"] == "fresh" and status["active_incidents"] == 1
    assert "basis" not in status and "active_maintenances" not in status and status["affected"] == ["Elevated errors"]
    assert answer["summary_line"] == T6
    action = answer["next_action"]
    assert action["do"] == "use_official_path" and action["url"] == action["status_url"] == "https://status.openai.com/"
    # the verdict is the one a quiet page gets: no wait, nothing new in reasons
    assert answer["recommendation"]["action"] != "wait_provider_incident"
    codes = [item["code"] for item in plain["next_checks"]]
    assert "provider_scope" in codes and "provider_clear" not in codes
    assert plain["summary_line"] != T6                                  # not a status_check lookup: its sentence is the row's own


def test_the_recommendation_is_the_same_with_and_without_the_listed_incident(lookup, monkeypatch):
    settings, client = lookup
    with use(monkeypatch, openai_feed(summary_body(ULID, []))):
        read_feed(settings, client, "openai.com")
        quiet = status_check(client, "openai.com")
    assert row(settings, "openai.com")["active_incidents"] == 0
    with connect(settings) as conn:
        conn.execute("TRUNCATE status_cache")
    with use(monkeypatch, openai_feed()):
        read_feed(settings, client, "openai.com")
        listed = status_check(client, "openai.com")
    assert quiet["recommendation"] == listed["recommendation"]
    assert quiet["summary_line"] == STATUS_ROWS["T1"] and listed["summary_line"] == T6
    assert quiet["next_action"] == listed["next_action"]


def test_an_empty_list_reads_as_before_no_active_issue(lookup, monkeypatch):
    settings, client = lookup
    with use(monkeypatch, openai_feed(summary_body(ULID, [{**OPEN, "status": "resolved"}]))):
        read_feed(settings, client, "openai.com")
        answer = status_check(client, "openai.com")
    assert row(settings, "openai.com")["active_incidents"] == 0
    assert answer["summary_line"] == STATUS_ROWS["T1"]
    assert answer["provider_status"]["active_incidents"] == 0
    assert "provider_clear" in [item["code"] for item in answer["next_checks"]]


@pytest.mark.parametrize("answer", [httpx.Response(403), httpx.Response(503), httpx.Response(200, text="<html>no</html>"),
                                    httpx.Response(200, content=b"x" * 40_000)])
def test_a_list_that_could_not_be_read_says_so_and_does_not_fail_the_feed(lookup, monkeypatch, answer):
    settings, client = lookup
    with use(monkeypatch, openai_feed(answer)):
        read_feed(settings, client, "openai.com")
        result = status_check(client, "openai.com")
    stored = row(settings, "openai.com")
    assert stored["indicator"] == "none" and stored["active_incidents"] is None and stored["feed_platform"] == "incident_io"
    assert stored["failures"] is None and stored["scope_failures"] == 1 and stored["scope_error"]       # the feed read itself is a success
    assert result["provider_status"]["freshness"] == "fresh" and "active_incidents" not in result["provider_status"]
    assert result["summary_line"] == STATUS_ROWS["T1-U"]
    assert result["next_action"]["do"] == "use_official_path" and result["next_action"]["url"] == "https://status.openai.com/"


def test_a_summary_the_robots_file_forbids_is_not_requested_and_is_not_a_failure(lookup, monkeypatch):
    settings, client = lookup
    feeds = openai_feed()
    monkeypatch.setattr(status_refresh, "make_gate", lambda _settings=None: SummaryDenied())
    with use(monkeypatch, feeds):
        read_feed(settings, client, "openai.com")
        result = status_check(client, "openai.com")
    assert feeds.seen == [f"{OPENAI}/api/v2/status.json"]
    stored = row(settings, "openai.com")
    assert stored["active_incidents"] is None and stored["feed_platform"] == "incident_io"
    assert stored["scope_failures"] is None and stored["scope_error"] is None and stored["failures"] is None
    assert result["summary_line"] == STATUS_ROWS["T1-U"]


def test_the_status_page_that_did_not_open_with_a_listed_incident_goes_to_the_user(lookup, monkeypatch):
    settings, client = lookup
    with use(monkeypatch, openai_feed()):
        read_feed(settings, client, "openai.com")
        result = status_check(client, OPENAI, error_class="service_error", failure_stage="before_target_response")
        unread = None
    assert result["summary_line"] == STATUS_ROWS["T6-F"] + FEED_HANDOFF_TAIL  # D115: a feed status_url on a hand-off
    assert result["next_action"]["do"] == "hand_off_to_human" and result["next_action"]["url"] is None
    with connect(settings) as conn:
        conn.execute("TRUNCATE status_cache")
    with use(monkeypatch, openai_feed(httpx.Response(403))):
        read_feed(settings, client, "openai.com")
        unread = status_check(client, OPENAI, error_class="service_error", failure_stage="before_target_response")
    assert unread["summary_line"] == STATUS_ROWS["T2-F"] + FEED_HANDOFF_TAIL and unread["next_action"]["do"] == "hand_off_to_human"


def test_an_atlassian_page_with_the_indicator_none_is_read_and_answered_as_before(lookup, monkeypatch):
    settings, client = lookup
    pages = {CLOUDFLARE: {"/api/v2/status.json": status_body(ATLASSIAN, time_zone="Etc/UTC"),
                          "/api/v2/summary.json": summary_body(ATLASSIAN, [OPEN])}}
    feeds = Feeds(**pages)
    with use(monkeypatch, feeds):
        read_feed(settings, client, "cloudflare.com")
        answer = status_check(client, "cloudflare.com")
    assert feeds.seen == [f"{CLOUDFLARE}/api/v2/status.json"]               # one request, as before
    stored = row(settings, "cloudflare.com")
    assert stored["active_incidents"] is None and stored["feed_platform"] is None
    assert answer["summary_line"] == STATUS_ROWS["T1"] and "active_incidents" not in answer["provider_status"]


def test_the_switch_off_reads_an_incident_io_page_as_before(lookup, monkeypatch):
    settings, client = lookup
    off = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, collect_lookup_metrics=True,
                   d100_incidentio_incidents=False)
    feeds = openai_feed()
    with TestClient(create_app(off)) as off_client:
        with use(monkeypatch, feeds):
            monkeypatch.setattr(background_reads, "refresh_feed", lambda s, host: refresh_one(off, host, client=httpx.Client(
                transport=httpx.MockTransport(feeds)), wait=True))
            ask(off_client, "openai.com")
            background_reads.process_due(off, limit=10)
            answer = status_check(off_client, "openai.com")
    assert feeds.seen == [f"{OPENAI}/api/v2/status.json"]
    stored = row(settings, "openai.com")
    assert stored["active_incidents"] is None and stored["feed_platform"] is None
    assert answer["summary_line"] == STATUS_ROWS["T1"]


def test_a_cron_run_asks_for_at_most_the_limit_of_summaries_and_the_rest_wait(lookup, monkeypatch):
    settings, client = lookup
    monkeypatch.setattr(status_refresh, "FEED_INCIDENTIO_LIST_MAX", 1)
    feeds = Feeds(**{OPENAI: {"/api/v2/status.json": status_body(ULID), "/api/v2/summary.json": summary_body(ULID, [OPEN])},
                     CLOUDFLARE: {"/api/v2/status.json": status_body(ULID_2), "/api/v2/summary.json": summary_body(ULID_2, [OPEN])}})
    with use(monkeypatch, feeds) as remote:
        ask(client, "openai.com")
        ask(client, "cloudflare.com")
        assert refresh_batch(settings, client=remote) == {"checked": 2, "updated": 2, "failed": 0, "remaining": 0}
    assert sum(path.endswith("/summary.json") for path in feeds.seen) == 1
    counts = sorted((row(settings, host)["active_incidents"] is None) for host in ("openai.com", "cloudflare.com"))
    assert counts == [False, True]                                          # one was read; the other is marked and shows T1-U
    assert row(settings, "openai.com")["feed_platform"] == row(settings, "cloudflare.com")["feed_platform"] == "incident_io"
    assert row(settings, "openai.com")["scope_failures"] is None and row(settings, "cloudflare.com")["scope_failures"] is None


def test_a_later_read_replaces_the_marker_and_the_incident(lookup, monkeypatch):
    settings, client = lookup
    feeds = openai_feed()
    with use(monkeypatch, feeds) as remote:
        read_feed(settings, client, "openai.com")
        assert row(settings, "openai.com")["active_incidents"] == 1
        feeds.pages[OPENAI]["/api/v2/summary.json"] = summary_body(ULID, [])
        assert refresh_one(settings, "openai.com", client=remote, min_interval=0, wait=True)
        assert row(settings, "openai.com")["active_incidents"] == 0
        # the page changes platform: the shape and the marker follow the body
        feeds.pages[OPENAI]["/api/v2/status.json"] = status_body(ATLASSIAN, time_zone="Etc/UTC")
        assert refresh_one(settings, "openai.com", client=remote, min_interval=0, wait=True)
        assert row(settings, "openai.com")["feed_platform"] is None and row(settings, "openai.com")["active_incidents"] is None


def test_the_migration_adds_one_nullable_column_with_a_closed_value_set(lookup):
    settings, _client = lookup
    with connect(settings) as conn:
        column = conn.execute("""SELECT is_nullable,column_default,data_type FROM information_schema.columns
                                 WHERE table_name='status_cache' AND column_name='feed_platform'""").fetchone()
        assert column == {"is_nullable": "YES", "column_default": None, "data_type": "character varying"}
        with pytest.raises(Exception):
            conn.execute("UPDATE status_cache SET feed_platform='other' WHERE false OR site_host='none'")
            conn.execute("INSERT INTO status_cache (site_host,indicator,feed_platform) VALUES ('x.example','none','other')")


# --- an active indicator is answered and stored as before D100 (the switch on and off give one answer) ----------------------

def _stale_incident():
    when = (datetime.now(timezone.utc) - timedelta(days=9)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {**OPEN, "created_at": when, "updated_at": when}


def _answers_with_switch(monkeypatch, on: bool, indicator: str, maintenance_key: bool):
    """One lookup round (a plain one, a status check, and a failure report) against an incident.io page with an active indicator."""
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, collect_lookup_metrics=True,
                        d100_incidentio_incidents=on)
    with connect(settings) as conn:
        conn.execute("TRUNCATE status_cache, status_sources, queried_hosts, host_access_profile, background_reads, provider_status_events")
    summary = summary_body(ULID, [_stale_incident()])
    if maintenance_key:
        summary["scheduled_maintenances"] = []
    feeds = Feeds(**{OPENAI: {"/api/v2/status.json": status_body(ULID, indicator), "/api/v2/summary.json": summary}})
    remote = httpx.Client(transport=httpx.MockTransport(feeds))
    monkeypatch.setattr(background_reads, "refresh_feed", lambda s, host: refresh_one(s, host, client=remote, wait=True))
    with TestClient(create_app(settings)) as client:
        ask(client, "openai.com")
        background_reads.process_due(settings, limit=10)
        answers = [ask(client, "openai.com", error_class="service_error"),
                   status_check(client, "openai.com"),
                   ask(client, "openai.com", error_class="service_error", task_step="status_check")]
    stored = row(settings, "openai.com")
    return feeds, stored, answers


@pytest.mark.parametrize("indicator", ["minor", "major"])
@pytest.mark.parametrize("maintenance_key", [False, True])
def test_an_active_indicator_gives_the_same_answer_with_the_switch_on_and_off(lookup, monkeypatch, indicator, maintenance_key):
    on_feeds, on_row, on_answers = _answers_with_switch(monkeypatch, True, indicator, maintenance_key)
    off_feeds, off_row, off_answers = _answers_with_switch(monkeypatch, False, indicator, maintenance_key)
    for feeds in (on_feeds, off_feeds):
        assert feeds.seen == [f"{OPENAI}/api/v2/status.json"]                # no summary.json for an active indicator
    for stored in (on_row, off_row):
        assert stored["indicator"] == indicator and stored["active_incidents"] is None and stored["active_maintenances"] is None
        assert stored["feed_platform"] is None and stored["affected"] is None
    assert on_row == off_row
    for on, off in zip(on_answers, off_answers):
        for key in ("recommendation", "next_action", "summary_line", "next_checks"):
            assert on.get(key) == off.get(key), key
        assert {k: v for k, v in on["provider_status"].items() if k != "fetched_at"} ==             {k: v for k, v in off["provider_status"].items() if k != "fetched_at"}
        assert "basis" not in on["provider_status"] or on["provider_status"]["basis"] == off["provider_status"]["basis"]


def test_a_none_indicator_page_that_lists_an_incident_is_read_again_after_fifteen_minutes(lookup, monkeypatch):
    settings, client = lookup
    feeds = openai_feed()
    with use(monkeypatch, feeds) as remote:
        read_feed(settings, client, "openai.com")
        assert row(settings, "openai.com")["active_incidents"] == 1
        now = datetime.now(timezone.utc)
        later = now + timedelta(minutes=20)
        with connect(settings) as conn:
            ready, _ = status_refresh._due_groups(conn, later)
            assert [group["hosts"] for group in ready] == [["openai.com"]]       # listed incident: due after 15 minutes, not an hour
            ready, _ = status_refresh._due_groups(conn, now + timedelta(minutes=10))
            assert ready == []
        # the incident is gone: back to the idle interval (one hour in the cron)
        feeds.pages[OPENAI]["/api/v2/summary.json"] = summary_body(ULID, [])
        assert refresh_one(settings, "openai.com", client=remote, min_interval=0, wait=True)
        with connect(settings) as conn:
            ready, _ = status_refresh._due_groups(conn, datetime.now(timezone.utc) + timedelta(minutes=20))
            assert ready == []


def test_a_refresh_one_with_a_spent_slot_does_not_ask_for_the_summary(lookup, monkeypatch):
    settings, client = lookup
    feeds = openai_feed()
    with use(monkeypatch, feeds) as remote:
        ask(client, "openai.com")
        assert refresh_one(settings, "openai.com", client=remote, min_interval=0, wait=True, incidentio_slot=lambda: False)
    assert feeds.seen == [f"{OPENAI}/api/v2/status.json"]
    stored = row(settings, "openai.com")
    assert stored["active_incidents"] is None and stored["feed_platform"] == "incident_io"
