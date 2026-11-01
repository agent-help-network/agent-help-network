"""Scope of a provider's active status (D43): pure tests on synthetic Statuspage-shaped fixtures.

No database and no network: every feed is a MockTransport (or a stand-in pool) answering from
tests/fixtures/status_scope/*.json, small bodies shaped like the live pages read on 2026-10-21.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from ahn import background_reads, status_refresh, status_scope
from ahn.models import CheckResponse, ProviderStatus
from ahn.service import _provider_scope
from ahn.status_refresh import Source
from ahn.triage import ACTIVE_INDICATORS, CHECKS, CHECKS_KO, next_checks, recommendation

FIXTURES = Path(__file__).parent / "fixtures" / "status_scope"
STATUSPAGE = Source("Example Status", "https://status.example.com/", "https://status.example.com/api/v2/status.json")


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    """Record the pauses between reads of one status page instead of sleeping."""
    pauses: list[float] = []
    monkeypatch.setattr(status_refresh, "_sleep", pauses.append)
    return pauses


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def serve(page: dict, *, overrides: dict | None = None):
    """A MockTransport handler serving one fixture page; returns (handler, list of requested paths)."""
    seen: list[str] = []
    routes = {"/api/v2/status.json": page["status"]}
    if "unresolved" in page:
        routes["/api/v2/incidents/unresolved.json"] = page["unresolved"]
    if "maintenances" in page:
        routes["/api/v2/scheduled-maintenances/active.json"] = page["maintenances"]
    routes.update(overrides or {})

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="<html>not found</html>")
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    return handler, seen


def read(page: dict, source: Source = STATUSPAGE, **kw) -> tuple[str, datetime | None, dict, list[str]]:
    handler, seen = serve(page, **kw)
    scope: dict = {}
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        indicator, updated_at = status_refresh._read_source(source, client, scope=scope)
    return indicator, updated_at, scope, seen


def fields(scope: dict) -> dict:
    """The stored scope fields, without the read's outcome bookkeeping."""
    return {key: value for key, value in scope.items() if key not in ("scope_outcome", "scope_error")}


def provider_status(indicator: str, scope: dict, freshness: str = "fresh") -> dict:
    """provider_status as the lookup builds it from a stored row (service._provider_scope)."""
    base = {"source_name": "S", "source_url": "https://status.example.com/", "indicator": indicator,
            "source_updated_at": None, "fetched_at": datetime.now(timezone.utc), "freshness": freshness, "error_detail": None}
    return {**base, **_provider_scope({"indicator": indicator, **scope})}


def partial(indicator: str, scope: dict) -> bool:
    return status_scope.scope_partial(indicator, scope.get("active_incidents"), scope.get("max_incident_impact"),
                                      scope.get("affected_regional"))


def codes(checks) -> list[str]:
    return [item["code"] for item in checks]


# --- request count and platform detection -----------------------------------------------------------------

@pytest.mark.parametrize("indicator", sorted(ACTIVE_INDICATORS))
def test_an_active_statuspage_refresh_makes_exactly_three_requests_to_the_same_page(indicator, no_gap):
    page = fixture("single_incident")
    page["status"]["status"]["indicator"] = indicator
    _indicator, _at, scope, seen = read(page)
    assert seen == ["/api/v2/status.json", "/api/v2/incidents/unresolved.json", "/api/v2/scheduled-maintenances/active.json"]
    assert scope["active_incidents"] == 1
    # At most one request per second to one status host (D44): a pause before each of the two lists.
    assert no_gap == [status_refresh.SCOPE_GAP_SECONDS] * 2 and status_refresh.SCOPE_GAP_SECONDS >= 1.0


def test_every_feed_request_identifies_the_reader():
    agents = []

    def handler(request):
        agents.append(request.headers.get("user-agent"))
        return serve(fixture("cloudflare"))[0](request)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        status_refresh._read_source(STATUSPAGE, client, scope={})
    assert len(agents) == 3 and set(agents) == {status_refresh.READER_VERSION}
    assert status_refresh.READER_VERSION.startswith("AgentHelpNetwork/")


@pytest.mark.parametrize("indicator", ["none", "unknown"])
def test_a_statuspage_refresh_that_reads_none_makes_one_request(indicator):
    # The curated feeds are read on a schedule (D44): a quiet page costs its status.json only.
    page = fixture("single_incident")
    page["status"]["status"]["indicator"] = indicator
    read_indicator, _at, scope, seen = read(page)
    assert read_indicator == indicator and seen == ["/api/v2/status.json"] and scope == {}
    assert "basis" not in provider_status(indicator, scope)


def test_incident_io_pages_get_one_request_and_no_scope():
    # incident.io answers status.json (ULID page id, no time_zone) but its lists are 404 HTML pages.
    indicator, _at, scope, seen = read(fixture("incident_io"))
    assert indicator == "minor" and seen == ["/api/v2/status.json"] and scope == {}
    assert set(provider_status("minor", scope)) == set(provider_status("minor", {}))  # no scope field at all
    assert recommendation(None, provider_status("minor", scope), None, None)["action"] == "wait_provider_incident"


def test_a_uuid_page_id_statuspage_gets_its_scope_read():
    # A page whose old page-wide updated_at would hide a current incident: the scope carries the incident's own time.
    page = fixture("single_incident")
    uuid = "c0e43449-112f-4c8d-8a00-3dac671ec84c"
    for key in ("status", "unresolved", "maintenances"):
        page[key]["page"]["id"] = uuid
        page[key]["page"]["updated_at"] = "2026-07-15T23:59:30.000Z"
    for incident in page["unresolved"]["incidents"]:
        incident["page_id"] = uuid
    indicator, _at, scope, seen = read(page)
    assert indicator == "major" and len(seen) == 3
    assert scope["active_incidents"] == 1 and scope["max_incident_impact"] == "major"
    assert scope["incident_updated_at"] == datetime(2026, 10, 21, 2, 40, tzinfo=timezone.utc)
    assert provider_status("major", scope)["basis"] == "incident"


def test_other_feed_kinds_and_calls_without_scope_make_one_request():
    page = fixture("single_incident")
    assert read(page, Source("I", "https://status.example.com/", "https://status.example.com/api/v2/status.json", "instatus"),
                overrides={"/api/v2/status.json": {"page": {"status": "HASISSUES"}}})[3] == ["/api/v2/status.json"]
    handler, seen = serve(page)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert status_refresh._read_source(STATUSPAGE, client)[0] == "major"  # no scope dict: the old single read
    assert seen == ["/api/v2/status.json"]


@pytest.mark.parametrize("page,expected", [
    ({"page": {"id": "yh6f0r4529hb", "time_zone": "Etc/UTC"}}, "yh6f0r4529hb"),
    ({"page": {"id": "c0e43449-112f-4c8d-8a00-3dac671ec84c", "time_zone": "America/Los_Angeles"}},
     "c0e43449-112f-4c8d-8a00-3dac671ec84c"),                                       # lowercase UUID
    ({"page": {"id": "C0E43449-112F-4C8D-8A00-3DAC671EC84C", "time_zone": "Etc/UTC"}}, None),   # upper-case UUID
    ({"page": {"id": "c0e43449-112f-4c8d-8a00-3dac671ec84c"}}, None),                # UUID, no time_zone
    ({"page": {"id": "c0e43449-112f-4c8d-8a00-3dac671ec84c/../x", "time_zone": "Etc/UTC"}}, None),
    ({"page": {"id": "01JMDK9XYNY6RXSED6SDWW50WY", "time_zone": "Etc/UTC"}}, None),   # ULID
    ({"page": {"id": "yh6f0r4529hb"}}, None),                                        # no time_zone
    ({"page": {"id": "../../x", "time_zone": "Etc/UTC"}}, None),
    ({"status": {"indicator": "none"}}, None),
    ([], None),
])
def test_only_atlassian_statuspage_bodies_are_recognized(page, expected):
    assert status_scope.statuspage_page_id(page) == expected


def test_the_discovered_reader_reads_the_lists_through_the_same_pinned_pool(monkeypatch, allow_all_gate):
    page = fixture("single_incident")
    page["status"]["page"]["id"] = page["unresolved"]["page"]["id"] = page["maintenances"]["page"]["id"] = "abcdefghijkl"
    bodies = {"/api/v2/status.json": page["status"], "/api/v2/incidents/unresolved.json": page["unresolved"],
              "/api/v2/scheduled-maintenances/active.json": page["maintenances"]}
    paths: list[str] = []
    pools: list[str] = []

    class Response:
        def __init__(self, body):
            self.status, self.body = 200, json.dumps(body).encode()

        def read(self, limit):
            return self.body[:limit]

        def release_conn(self):
            pass

    class Pool:
        def __init__(self, address, **_kw):
            pools.append(address)

        def request(self, _method, path, **_kw):
            paths.append(path)
            return Response(bodies[path])

        def close(self):
            pass

    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh, "_resolve", lambda host, timeout: {"93.184.216.34"})
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    scope: dict = {}
    source = Source("Example Status", "https://status.example.org/", "https://status.example.org/api/v2/status.json", "discovered_statuspage")
    assert status_refresh._read_source(source, httpx.Client(), scope=scope)[0] == "major"
    assert paths == list(bodies) and pools == ["93.184.216.34"] and scope["active_incidents"] == 1
    # The first discovery itself stays one request: the scope comes with the next refresh.
    paths.clear()
    assert status_refresh.discover_source("example.org", gate=allow_all_gate) is not None and paths == ["/api/v2/status.json"]
    # A discovered page that reads none is one request too.
    page["status"]["status"]["indicator"] = "none"
    bodies["/api/v2/status.json"] = page["status"]
    paths.clear()
    scope = {}
    assert status_refresh._read_source(source, httpx.Client(), scope=scope)[0] == "none"
    assert paths == ["/api/v2/status.json"] and scope == {}


# --- the four page types ----------------------------------------------------------------------------------

def test_cloudflare_type_keeps_waiting_but_says_the_scope_is_partial():
    indicator, updated_at, scope, _seen = read(fixture("cloudflare"))
    assert indicator == "major" and updated_at == datetime(2026, 8, 27, tzinfo=timezone.utc)
    assert fields(scope) == {"active_incidents": 1, "max_incident_impact": "minor",
                     "incident_updated_at": datetime(2026, 9, 1, 8, 29, 56, tzinfo=timezone.utc),
                     "active_maintenances": 2,  # the "scheduled" one is not in progress
                     "affected": ["WARP", "Paris, France - (CDG)", "Nashville, United States - (BNA)"],
                     "affected_regional": False}  # WARP is a product, not a data-center group
    status = provider_status(indicator, scope)
    assert status["basis"] == "incident"
    assert recommendation(None, status, None, None)["action"] == "wait_provider_incident"  # an incident is announced
    assert partial(indicator, scope)  # its worst impact (minor) is below the page indicator (major)
    checks = codes(next_checks("site_access", status, "no_reports", provider_partial=partial(indicator, scope)))
    assert checks == ["locate_failure_stage", "provider_scope", "provider_scope_partial", "reports_unknown"]


def test_rubygems_type_is_components_only_and_not_a_reason_to_wait():
    indicator, _at, scope, _seen = read(fixture("rubygems"))
    assert indicator == "maintenance"
    assert fields(scope) == {"active_incidents": 0, "max_incident_impact": "none", "incident_updated_at": None,
                     "active_maintenances": 0, "affected": None, "affected_regional": None}
    status = provider_status(indicator, scope)
    assert status["basis"] == "components_only" and "affected" not in status and "incident_updated_at" not in status
    # Falls through to the next rule: here the robots-unknown default, with an official path the official path.
    assert recommendation(None, status, None, None) == {"action": "unknown", "confidence": "low", "reasons": []}
    official = {"api_docs": "https://guides.rubygems.org/rubygems-org-api/"}
    assert recommendation(None, status, None, official)["action"] == "use_official_path"
    assert recommendation("human_verification", status, None, None)["action"] == "hand_off_to_human"
    checks = codes(next_checks("site_access", status, "no_reports", provider_partial=partial(indicator, scope)))
    assert checks == ["locate_failure_stage", "provider_components_only", "reports_unknown"]


def test_twilio_type_carrier_groups_make_the_scope_partial():
    indicator, _at, scope, _seen = read(fixture("twilio"))
    assert scope["active_incidents"] == 7 and scope["max_incident_impact"] == "minor" and scope["active_maintenances"] == 1
    assert scope["affected"] == ["SMS, Europe", "Voice, Latin America", "Voice, APAC"]
    assert scope["affected_regional"] is True  # every incident and the maintenance sit in CARRIER NETWORK
    assert scope["incident_updated_at"] == datetime(2026, 10, 21, 2, 16, 14, 84000, tzinfo=timezone.utc)
    status = provider_status(indicator, scope)
    assert status["basis"] == "incident"
    assert recommendation(None, status, None, None)["action"] == "wait_provider_incident"
    assert codes(next_checks("site_access", status, "no_reports", provider_partial=partial(indicator, scope)))[1:3] == [
        "provider_scope", "provider_scope_partial"]


def test_one_incident_type_is_unchanged_and_not_partial():
    indicator, _at, scope, _seen = read(fixture("single_incident"))
    assert scope["affected"] == ["API"] and scope["affected_regional"] is False and scope["max_incident_impact"] == "major"
    status = provider_status(indicator, scope)
    assert status["basis"] == "incident" and not partial(indicator, scope)
    now = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)  # the fixture's incident was updated 20 minutes before (D45)
    assert recommendation(None, status, None, None, now=now) == {
        "action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
    assert codes(next_checks("site_access", status, "no_reports", now=now)) == ["locate_failure_stage", "provider_scope", "reports_unknown"]


def test_a_maintenance_only_page_still_waits_with_the_maintenance_text():
    page = fixture("cloudflare")
    page["unresolved"]["incidents"] = []
    page["status"]["status"]["indicator"] = "maintenance"
    indicator, _at, scope, _seen = read(page)
    status = provider_status(indicator, scope)
    assert status["basis"] == "maintenance" and scope["affected_regional"] is True  # only data centers
    assert recommendation(None, status, None, None)["action"] == "wait_provider_incident"
    checks = next_checks("site_access", status, "no_reports", provider_partial=partial(indicator, scope))
    assert codes(checks)[1:3] == ["provider_scope", "provider_scope_partial"] and "scheduled maintenance" in checks[1]["text"]


# --- failures only empty the scope ------------------------------------------------------------------------

def test_an_oversized_list_leaves_only_its_own_fields_out():
    page = fixture("twilio")
    big = dict(page["unresolved"], padding="x" * status_scope.SCOPE_CAP)  # like the live 112 KB list
    indicator, _at, scope, seen = read(page, overrides={"/api/v2/incidents/unresolved.json": big})
    assert indicator == "minor" and len(seen) == 3
    assert fields(scope) == {"active_incidents": None, "max_incident_impact": None, "incident_updated_at": None,
                     "active_maintenances": 1, "affected": None, "affected_regional": None}
    status = provider_status(indicator, scope)
    assert "basis" not in status and "active_incidents" not in status and status["active_maintenances"] == 1
    # Without the incident list nothing is known about incidents: the recommendation stays as before.
    assert recommendation(None, status, None, None)["action"] == "wait_provider_incident"
    assert not partial(indicator, scope)


def test_a_body_just_at_the_cap_is_read():
    page = fixture("rubygems")
    raw = json.dumps(page["unresolved"]).encode()
    exact = raw[:-1] + b" " * (status_scope.SCOPE_CAP - len(raw)) + b"}"
    assert len(exact) == status_scope.SCOPE_CAP
    _i, _a, scope, _s = read(page, overrides={"/api/v2/incidents/unresolved.json": httpx.Response(200, content=exact)})
    assert scope["active_incidents"] == 0
    _i, _a, scope, _s = read(page, overrides={"/api/v2/incidents/unresolved.json": httpx.Response(200, content=exact + b" ")})
    assert scope["active_incidents"] is None  # one byte over: too_large


def test_an_oversized_maintenance_list_on_a_page_without_incidents_is_not_components_only():
    page = fixture("rubygems")
    big = dict(page["maintenances"], padding="x" * status_scope.SCOPE_CAP)
    indicator, _at, scope, _seen = read(page, overrides={"/api/v2/scheduled-maintenances/active.json": big})
    status = provider_status(indicator, scope)
    assert scope["active_incidents"] == 0 and scope["active_maintenances"] is None and "basis" not in status
    assert recommendation(None, status, None, None)["action"] == "wait_provider_incident"


@pytest.mark.parametrize("answer", [
    httpx.Response(404, text="<html>not found</html>"),
    httpx.Response(200, text="<html>not json</html>"),
    httpx.Response(200, json={"incidents": "none"}),
    httpx.Response(200, json={"page": {"id": "anotherpage1"}, "incidents": []}),          # another page's list
    httpx.Response(200, json={"page": {"id": "pclby00q90vc"}, "incidents": [{"impact": "purple"}]}),
    httpx.Response(200, json={"page": {"id": "pclby00q90vc"}, "incidents": [{"impact": "minor", "updated_at": "2026-10-21T01:00:00"}]}),
    httpx.Response(301, headers={"Location": "https://elsewhere.example/"}),
])
def test_a_failed_or_odd_incident_list_never_fails_the_feed_read(answer):
    indicator, _at, scope, _seen = read(fixture("rubygems"), overrides={"/api/v2/incidents/unresolved.json": answer})
    assert indicator == "maintenance"
    assert scope["active_incidents"] is None and scope["active_maintenances"] == 0
    assert "basis" not in provider_status(indicator, scope)


def test_resolved_items_and_upcoming_maintenances_are_not_counted():
    page = fixture("single_incident")
    page["unresolved"]["incidents"].append({"name": "Old", "status": "resolved", "impact": "critical", "components": []})
    page["maintenances"]["scheduled_maintenances"].append({"name": "Later", "status": "scheduled", "components": []})
    _i, _a, scope, _s = read(page)
    assert scope["active_incidents"] == 1 and scope["max_incident_impact"] == "major" and scope["active_maintenances"] == 0


# --- names --------------------------------------------------------------------------------------------

def test_affected_names_are_cleaned_cut_deduplicated_and_at_most_three():
    page = fixture("single_incident")
    long_name = "Z" * 120
    page["unresolved"]["incidents"] = [
        {"name": "No components here", "status": "investigating", "impact": "minor", "components": []},
        {"name": "x", "status": "investigating", "impact": "minor",
         "components": [{"name": "API"}, {"name": "API"}, {"name": "Line\nbreak\x00name"}, {"name": long_name}, {"name": "Fifth"}]},
    ]
    _i, _a, scope, _s = read(page)
    assert scope["affected"] == ["No components here", "API", "Line break name"]
    assert all(len(name) <= status_scope.AFFECTED_CHARS for name in scope["affected"])
    assert status_scope._clean(long_name) == "Z" * 80
    # An incident that names no component is never "regional".
    assert scope["affected_regional"] is False


# --- recommendation and next_checks invariants ------------------------------------------------------------

@pytest.mark.parametrize("indicator", sorted(ACTIVE_INDICATORS))
@pytest.mark.parametrize("basis", [None, "incident", "maintenance"])
def test_wait_is_unchanged_whenever_something_is_announced_or_unknown(indicator, basis):
    # A current page update time: D54 (a minor page with no list, its time old or missing) is tested in test_page_stale.py.
    status = {"indicator": indicator, "freshness": "fresh", "source_updated_at": datetime.now(timezone.utc),
              **({"basis": basis} if basis else {})}
    assert recommendation(None, status, None, None) == {"action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_scope"]}
    assert recommendation("login_auth", status, None, None)["action"] == "wait_provider_incident"


@pytest.mark.parametrize("indicator", ["minor", "maintenance"])
@pytest.mark.parametrize("error_class,official", [(None, None), ("site_access", {"developer_portal": "https://dev.example.com/"}),
                                                  ("human_verification", None), ("login_auth", None)])
def test_minor_or_maintenance_components_only_answers_as_if_no_provider_were_active(indicator, error_class, official):
    status = {"indicator": indicator, "freshness": "fresh", "basis": "components_only"}
    assert recommendation(error_class, status, None, official) == recommendation(error_class, None, None, official)
    checks = codes(next_checks(error_class, status, "no_reports", official=official))
    assert "provider_components_only" in checks and "provider_scope" not in checks


@pytest.mark.parametrize("indicator", ["major", "critical"])
@pytest.mark.parametrize("error_class,official,profile", [
    ("service_error", None, {"robots_status": "ok"}),  # the review's reproduction: this used to answer proceed
    (None, {"developer_portal": "https://dev.example.com/"}, None),
    ("site_access", None, None), ("human_verification", None, None), ("login_auth", None, None)])
def test_major_or_critical_components_only_still_waits(indicator, error_class, official, profile):
    # A monitoring integration can flip components to major_outage before an incident is posted, and the incident
    # list can come from a cache that lags status.json: nothing announced is not a reason to stop waiting here.
    status = {"indicator": indicator, "freshness": "fresh", "basis": "components_only"}
    assert recommendation(error_class, status, profile, official) == {
        "action": "wait_provider_incident", "confidence": "medium", "reasons": ["provider_components_only"]}
    checks = codes(next_checks(error_class, status, "no_reports", official=official))
    assert "provider_components_only" in checks and "provider_scope" not in checks
    assert "major or critical one still is" in CHECKS["provider_components_only"]


def test_a_stale_major_components_only_does_not_wait():
    status = {"indicator": "major", "freshness": "stale", "basis": "components_only"}
    assert recommendation(None, status, None, None)["action"] != "wait_provider_incident"


# --- the two lists back off ---------------------------------------------------------------------------

def test_a_refused_list_stops_the_other_and_is_recorded():
    page = fixture("cloudflare")
    _i, _a, scope, seen = read(page, overrides={"/api/v2/incidents/unresolved.json": httpx.Response(429)})
    assert seen == ["/api/v2/status.json", "/api/v2/incidents/unresolved.json"]  # maintenances not asked for
    assert scope["scope_outcome"] == "failed" and scope["scope_error"] == "http_429"
    assert scope["active_incidents"] is None and scope["active_maintenances"] is None
    # A refusal of the second list is recorded the same way.
    _i, _a, scope, seen = read(page, overrides={"/api/v2/scheduled-maintenances/active.json": httpx.Response(403)})
    assert len(seen) == 3 and scope["scope_error"] == "http_403" and scope["active_incidents"] == 1


@pytest.mark.parametrize("answer,label", [
    (httpx.Response(502), "http_502"),
    (httpx.Response(200, content=b"x" * (status_scope.SCOPE_CAP + 1)), "too_large"),
    (httpx.Response(200, text="<html></html>"), "invalid_body"),
])
def test_a_failed_list_is_a_scope_failure_but_the_other_list_is_still_read(answer, label):
    _i, _a, scope, seen = read(fixture("cloudflare"), overrides={"/api/v2/incidents/unresolved.json": answer})
    assert len(seen) == 3 and scope["scope_outcome"] == "failed" and scope["scope_error"] == label
    assert scope["active_maintenances"] == 2


def test_both_lists_read_is_ok_and_a_quiet_page_asks_for_none():
    assert read(fixture("cloudflare"))[2]["scope_outcome"] == "ok"
    page = fixture("cloudflare")
    page["status"]["status"]["indicator"] = "none"
    assert "scope_outcome" not in read(page)[2]  # nothing asked for: the stored counters stay as they are


def test_a_read_that_is_not_allowed_is_skipped_not_failed():
    calls = []
    out = status_scope.read_scope(lambda path: calls.append(path) or b"{}", "yh6f0r4529hb", allowed=lambda: False)
    assert calls == [] and out["scope_outcome"] == "skipped" and out["scope_error"] is None


@pytest.mark.parametrize("failures,error,rest", [(1, "timeout", 15), (2, "too_large", 30), (3, "http_502", 60),
                                                 (1, "http_429", 60), (2, "http_403", 120), (20, "too_large", 1440),
                                                 (20, "http_401", 1440)])
def test_the_lists_rest_after_failures(failures, error, rest):
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    row = {"scope_failures": failures, "scope_failed_at": now, "scope_error": error}
    assert status_scope.scope_backoff(failures, error) == timedelta(minutes=rest)
    assert status_scope.scope_paused(row, now + timedelta(minutes=rest) - timedelta(seconds=1))
    assert not status_scope.scope_paused(row, now + timedelta(minutes=rest))
    assert not status_scope.scope_paused(None, now) and not status_scope.scope_paused({"scope_failures": None}, now)


def test_stale_components_only_is_still_provider_unknown():
    status = {"indicator": "major", "freshness": "stale", "basis": "components_only"}
    assert codes(next_checks("site_access", status, "no_reports", provider_partial=True)) == ["locate_failure_stage", "provider_unknown", "reports_unknown"]


@pytest.mark.parametrize("indicator,incidents,impact,regional,expected", [
    ("major", 1, "minor", False, True),
    ("critical", 2, "major", None, True),
    ("minor", 1, "minor", False, False),
    ("major", 1, "critical", False, False),
    ("major", 0, "none", None, False),          # components_only is told by its own code
    ("maintenance", 1, "minor", False, False),  # no rank to compare with
    ("maintenance", 0, "none", True, True),
    ("none", 0, "none", True, False),
    ("major", None, None, None, False),
])
def test_scope_partial_rule(indicator, incidents, impact, regional, expected):
    assert status_scope.scope_partial(indicator, incidents, impact, regional) is expected


def test_regional_groups_table_holds_only_well_formed_ids_and_public_names():
    for page_id, groups in status_scope.REGIONAL_GROUPS.items():
        assert status_scope._PAGE_ID.fullmatch(page_id)
        for group_id, name in groups.items():
            assert status_scope._PAGE_ID.fullmatch(group_id) and 1 <= len(name) <= 80


def test_new_checks_have_english_and_korean_text():
    for code in ("provider_components_only", "provider_incident_stale", "provider_scope_partial"):
        assert code in CHECKS and code in CHECKS_KO
    assert "provider_status.affected" in CHECKS["provider_scope_partial"]


# --- the response omits what was not read ------------------------------------------------------------------

def test_scope_fields_are_omitted_not_null_in_the_response_model():
    plain = ProviderStatus(source_name="S", source_url="https://s/", indicator="minor", fetched_at=None,
                           source_updated_at=None, freshness="fresh")
    assert set(plain.model_dump(mode="json")) == {"source_name", "source_url", "indicator", "fetched_at",
                                                  "source_updated_at", "freshness", "error_detail"}
    full = ProviderStatus(**provider_status("major", read(fixture("cloudflare"))[2]))
    dumped = full.model_dump(mode="json")
    assert dumped["basis"] == "incident" and dumped["affected"][0] == "WARP" and dumped["active_maintenances"] == 2
    assert "affected_regional" not in dumped
    with pytest.raises(ValueError):
        ProviderStatus(**{**dumped, "affected": ["a", "b", "c", "d"]})
    assert "provider_status" in CheckResponse.model_fields


# --- the re-read interval -------------------------------------------------------------------------------

@pytest.mark.parametrize("indicator,expected", [("minor", timedelta(minutes=15)), ("major", timedelta(minutes=15)),
                                                ("critical", timedelta(minutes=15)), ("maintenance", timedelta(minutes=15)),
                                                ("none", timedelta(hours=2)), ("unknown", timedelta(hours=2)), (None, timedelta(hours=2))])
def test_an_active_cached_indicator_is_due_after_fifteen_minutes(indicator, expected):
    assert background_reads.FEED_RETRY_MINUTES == 15
    assert background_reads.feed_refresh_after(indicator, timedelta(hours=background_reads.FEED_STALE_HOURS)) == expected
    assert background_reads.FEED_ACTIVE_INDICATORS == ACTIVE_INDICATORS


def test_the_budget_is_asked_once_so_both_lists_are_read_together():
    # The cron's budget can run out while the first list is read; the second list is still read, because one list
    # without the other leaves the basis unknown and keeps a stale-incident wait (D45) the budget decided (D46).
    page = fixture("cloudflare")
    handler, seen = serve(page)
    answers = iter([True])  # a second question would raise StopIteration
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        scope: dict = {}
        status_refresh._read_source(STATUSPAGE, client, scope=scope, scope_allowed=lambda: next(answers, False))
    assert seen == ["/api/v2/status.json", "/api/v2/incidents/unresolved.json", "/api/v2/scheduled-maintenances/active.json"]
    assert scope["scope_outcome"] == "ok" and scope["active_incidents"] is not None and scope["active_maintenances"] == 2


def test_a_tenable_type_page_lifts_the_wait_once_both_lists_are_read():
    # One incident last updated 120 days ago, no maintenance (an empty list): D45 does not wait on it. Before D46
    # the budget could end after the incident list, leave the maintenance count unknown, and keep the wait.
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    page = {"status": {"page": {"id": "yh6f0r4529hb", "time_zone": "Etc/UTC", "updated_at": old},
                       "status": {"indicator": "minor"}},
            "unresolved": {"page": {"id": "yh6f0r4529hb"}, "incidents": [
                {"name": "Degraded scans in one region", "status": "monitoring", "impact": "minor", "updated_at": old,
                 "components": [{"name": "Scanning", "group_id": None}]}]},
            "maintenances": {"page": {"id": "yh6f0r4529hb"}, "scheduled_maintenances": []}}
    handler, _seen = serve(page)
    answers = iter([True])
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        scope: dict = {}
        indicator, _ = status_refresh._read_source(STATUSPAGE, client, scope=scope, scope_allowed=lambda: next(answers, False))
    assert scope["active_incidents"] == 1 and scope["active_maintenances"] == 0
    status = provider_status(indicator, fields(scope))
    assert status["basis"] == "incident"
    assert recommendation(None, status, None, None)["action"] != "wait_provider_incident"
    assert "provider_incident_stale" in codes(next_checks(None, status, "no_reports"))
    # The same read with the maintenance count unknown (the old partial read) keeps waiting: unknown is not stale.
    unknown = provider_status(indicator, {**fields(scope), "active_maintenances": None})
    assert recommendation(None, unknown, None, None)["action"] == "wait_provider_incident"


def test_a_budget_that_refuses_the_lists_skips_both():
    calls = []
    answers = iter([False, True])  # even if a later question would say yes, nothing is read
    out = status_scope.read_scope(lambda path: calls.append(path) or b"{}", "yh6f0r4529hb", allowed=lambda: next(answers))
    assert calls == [] and out["scope_outcome"] == "skipped" and out["active_incidents"] is None
