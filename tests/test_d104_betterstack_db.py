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
    assert new_row["source_updated_at"].isoformat().startswith("2026-10-30T08:00:00")
    assert new["provider_status"]["affected"] == ["API"]
    assert judgement(new) == judgement(old)


def test_the_one_judgement_change_is_a_minor_page_whose_feed_lists_no_report_at_all(monkeypatch, clients):
    # Before D104 the page's updated_at stood in as the time; now there is none, so the existing "update time unknown" rule applies:
    # the wait stays (action unchanged) with low confidence and the provider_update_time_unknown reason.
    payload = body("degraded", [], [resource("1", "API", "degraded")], page_time=PAGE_TIME)
    (new, new_row), (old, _old_row) = both(monkeypatch, clients, payload)
    assert new_row["source_updated_at"] is None
    new_judgement, old_judgement = judgement(new)[0], judgement(old)[0]
    assert new_judgement["action"] == old_judgement["action"] == "wait_provider_incident"
    assert new_judgement["confidence"] == "low" and "provider_update_time_unknown" in new_judgement["reasons"]
    assert old_judgement["confidence"] != "low" and "provider_update_time_unknown" not in old_judgement["reasons"]


def test_a_healthy_page_with_no_report_has_no_time_and_names_nobody(monkeypatch, clients):
    payload = body("operational", [], [resource("1", "API", "operational")], page_time=PAGE_TIME)
    (new, new_row), (old, _old_row) = both(monkeypatch, clients, payload)
    assert new_row["source_updated_at"] is None and new_row["affected"] is None
    assert judgement(new) == judgement(old)
    assert new["provider_status"]["indicator"] == "none"
