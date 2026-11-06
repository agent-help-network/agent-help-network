"""D104 against the dedicated ahn_test database: a Better Stack feed is stored with its reports' time and the names of the
resources that are down, answered with the same recommendation as before, and the switch restores the earlier reading.

Stub feeds only (MockTransport); no real status page and no real robots.txt is read."""

from __future__ import annotations

import os

import httpx
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, status_refresh  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import refresh_one  # noqa: E402

from test_d104_betterstack_time import body, resource  # noqa: E402

PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
ON_HOST, ON_PAGE = "huggingface.co", "status.huggingface.co"
OFF_HOST, OFF_PAGE = "brave.com", "status.brave.app"
PAGE_TIME = "2026-11-06T02:20:00.147Z"
REPORT_TIME = "2026-11-04T13:34:21.674Z"


@pytest.fixture
def clients(monkeypatch):
    on = Settings(TEST_URL, PEPPER, public_mode=True, collect_lookup_metrics=True)
    off = Settings(TEST_URL, PEPPER, public_mode=True, collect_lookup_metrics=True, d104_betterstack_updated_at=False)
    with connect(on) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(on)
    with connect(on) as conn:
        conn.execute("TRUNCATE lookup_metrics, request_metrics, mcp_check_metrics, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads, provider_status_events RESTART IDENTITY")
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    monkeypatch.setattr(status_refresh, "_sleep", lambda _seconds: None)
    with TestClient(create_app(on)) as on_client, TestClient(create_app(off)) as off_client:
        yield (on, on_client), (off, off_client)


def run(monkeypatch, pair, host, page, payload):
    """Look the host up, let the background read store it, and look it up again."""
    settings, client = pair
    remote = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload) if request.url.host == page and request.url.path == "/index.json"
        else httpx.Response(404, text="")))
    monkeypatch.setattr(background_reads, "refresh_feed", lambda s, h: refresh_one(settings, h, client=remote, wait=True))
    client.get("/v1/incidents/check", params={"site_host": host})
    background_reads.process_due(settings, limit=10)
    answer = client.get("/v1/incidents/check", params={"site_host": host}).json()
    with connect(settings) as conn:
        stored = conn.execute("SELECT source_updated_at,affected,active_incidents FROM status_cache WHERE site_host=%s", (host,)).fetchone()
    return answer, stored


def judgement(answer):
    recommendation = answer["recommendation"]
    next_action = answer.get("next_action") or {}
    return ({key: recommendation.get(key) for key in ("action", "confidence", "reasons")},
            {key: next_action.get(key) for key in ("action", "kind", "type") if key in next_action})


def both(monkeypatch, clients, payload):
    on, off = clients
    return run(monkeypatch, on, ON_HOST, ON_PAGE, payload), run(monkeypatch, off, OFF_HOST, OFF_PAGE, payload)


def test_an_open_report_is_stored_with_its_time_and_the_down_resource_and_the_judgement_is_unchanged(monkeypatch, clients):
    payload = body("downtime", [("1", "downtime", [("u1", REPORT_TIME)])], [resource("1", "eclip.se", "downtime")], page_time=PAGE_TIME)
    (new, new_row), (old, old_row) = both(monkeypatch, clients, payload)
    assert new_row["source_updated_at"].isoformat().startswith("2026-11-04T13:34:21")
    assert old_row["source_updated_at"].isoformat().startswith("2026-11-06T02:20:00")
    assert new["provider_status"]["affected"] == ["eclip.se"] and new_row["active_incidents"] is None
    assert "affected" not in old["provider_status"]
    assert judgement(new) == judgement(old) and new["recommendation"]["action"] == "wait_provider_incident"
    assert new["provider_status"]["source_updated_at"].startswith("2026-11-04T13:34:21") and new["provider_status"]["source_time"] == "page"


def test_a_degraded_page_with_a_resolved_report_keeps_its_judgement(monkeypatch, clients):
    payload = body("degraded", [("1", "resolved", [("u1", "2026-10-30T08:00:00.000Z")])], [resource("1", "API", "degraded")], page_time=PAGE_TIME)
    (new, new_row), (old, _old_row) = both(monkeypatch, clients, payload)
    assert new_row["source_updated_at"].isoformat().startswith("2026-11-06T02:20:00")  # no open report: the page time, as before D104
    assert new["provider_status"]["affected"] == ["API"]
    assert judgement(new) == judgement(old)


@pytest.mark.parametrize("state", ["degraded", "downtime", "maintenance"])
def test_a_page_that_is_not_operational_with_no_report_time_is_stored_and_judged_exactly_as_before(monkeypatch, clients, state):
    # No usable report time: the page's updated_at stands in as before D104, so the stored time and the answer do not change.
    payload = body(state, [], [resource("1", "API", "degraded" if state == "degraded" else "downtime")], page_time=PAGE_TIME)
    (new, new_row), (old, old_row) = both(monkeypatch, clients, payload)
    assert new_row["source_updated_at"] == old_row["source_updated_at"]
    assert new_row["source_updated_at"].isoformat().startswith("2026-11-06T02:20:00")
    assert judgement(new) == judgement(old)
    assert "provider_update_time_unknown" not in new["recommendation"].get("reasons", [])


def test_a_malformed_report_part_leaves_the_read_and_the_cron_run_going(monkeypatch, clients):
    from ahn.status_refresh import refresh_batch, sync_curated_sources

    (on, _client), _off = clients
    broken = body("downtime", [("1", "downtime", [("u1", REPORT_TIME)])], [resource("1", "eclip.se", "downtime")])
    for item in broken["included"]:
        if item["type"] == "status_report":
            item["relationships"] = "oops"       # a truthy value that is not an object
    healthy = body("downtime", [("2", "downtime", [("u2", REPORT_TIME)])], [resource("2", "Other", "downtime")])
    pages = {ON_PAGE: broken, OFF_PAGE: healthy}
    remote = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=pages[request.url.host]) if request.url.host in pages and request.url.path == "/index.json"
        else httpx.Response(404, text="")))
    monkeypatch.setattr(status_refresh, "SOURCES", {host: status_refresh.SOURCES[host] for host in (ON_HOST, OFF_HOST)})
    monkeypatch.setattr(background_reads, "_pace", lambda: None)
    sync_curated_sources(on)
    result = refresh_batch(on, client=remote)
    assert result["checked"] == 2 and result["updated"] == 2 and result["failed"] == 0
    with connect(on) as conn:
        rows = {r["site_host"]: r for r in conn.execute("SELECT site_host,fetched_at,failures,affected FROM status_cache").fetchall()}
    assert rows[ON_HOST]["fetched_at"] is not None and rows[OFF_HOST]["fetched_at"] is not None and rows[ON_HOST]["failures"] is None
    assert rows[OFF_HOST]["affected"] == ["Other"]


def test_a_healthy_page_with_no_report_has_no_time_and_names_nobody(monkeypatch, clients):
    payload = body("operational", [], [resource("1", "API", "operational")], page_time=PAGE_TIME)
    (new, new_row), (old, _old_row) = both(monkeypatch, clients, payload)
    assert new_row["source_updated_at"] is None and new_row["affected"] is None
    assert judgement(new) == judgement(old)
    assert new["provider_status"]["indicator"] == "none"
