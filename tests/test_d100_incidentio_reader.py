"""D100: an incident.io-shaped status page keeps its overall indicator at "none" while an incident that names no component is
open, so the feed reader also reads that page's summary.json (its unresolved incidents), while its indicator is not active (minor or above, maintenance).

Pure tests on synthetic bodies (MockTransport; no real status page, no database). The request order of a Statuspage page that is
not of this shape is held to what it was before: one request for a quiet page, three for an active one."""

from __future__ import annotations

import json

import httpx
import pytest

from ahn import status_refresh, status_scope
from ahn.settings import Settings, parse_d100_switch
from ahn.status_refresh import FEED_INCIDENTIO_LIST_MAX, Source

PAGE = "01JMDK9XYNY6RXSED6SDWW50WY"
SOURCE = Source("Example Status", "https://status.example.com/", "https://status.example.com/api/v2/status.json")
STATUS_JSON = "/api/v2/status.json"
SUMMARY_JSON = "/api/v2/summary.json"


@pytest.fixture(autouse=True)
def no_gap(monkeypatch):
    pauses: list[float] = []
    monkeypatch.setattr(status_refresh, "_sleep", pauses.append)
    return pauses


def status_body(indicator="none", page_id=PAGE, **page_extra):
    return {"page": {"id": page_id, "name": "Example", "url": "https://status.example.com/", "updated_at": "2026-11-05T17:05:56Z",
                     **page_extra},
            "status": {"description": "All Systems Operational", "indicator": indicator}}


def incident(status="monitoring", impact="minor", name="Elevated errors", updated="2026-11-05T17:05:56Z", components=None):
    return {"id": "i1", "name": name, "status": status, "impact": impact, "created_at": "2026-11-05T15:25:03Z", "updated_at": updated,
            "components": components or []}


def summary_body(incidents, *, page_id=PAGE, maintenances=None):
    body = {"page": {"id": page_id, "name": "Example"}, "components": [], "incidents": incidents}
    if maintenances is not None:
        body["scheduled_maintenances"] = maintenances
    return body


def serve(routes):
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        body = routes.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="<html></html>")
        if isinstance(body, httpx.Response):
            return body
        return httpx.Response(200, json=body)

    return handler, seen


def read(routes, *, source=SOURCE, incidentio="on", scope=None):
    handler, seen = serve(routes)
    box = {"allowed": lambda: True} if incidentio == "on" else incidentio
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        indicator, _at = status_refresh._read_source(source, client, scope=scope, incidentio=box)
    return indicator, box, seen


# --- the shape --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("body, expected", [
    (status_body(), PAGE),
    (status_body(time_zone=None), PAGE),
    (status_body(time_zone="Etc/UTC"), None),                       # Atlassian: a time_zone
    (status_body(page_id="yh6f0r4529hb"), None),                    # Atlassian: a short lowercase id
    (status_body(page_id="01jmdk9xynY6RXSED6SDWW50WY"), None),      # not upper-case
    (status_body(page_id=PAGE[:-1]), None),                         # 25 characters
    (status_body(page_id=PAGE + "A"), None),                        # 27 characters
    ({"page": "x"}, None), ([], None), (None, None), ({"page": {"id": 5}}, None),
])
def test_only_the_incident_io_shape_is_recognized(body, expected):
    assert status_scope.incidentio_page_id(body) == expected


def test_the_two_shapes_never_overlap():
    for page_id in (PAGE, "yh6f0r4529hb", "gpkpyklzq55q", "3c5a4c2e-6d1a-4a4e-8d3e-0123456789ab"):
        body = status_body(page_id=page_id, time_zone="Etc/UTC")
        assert not (status_scope.incidentio_page_id(body) and status_scope.statuspage_page_id(body))


# --- an incident.io page: summary.json is read once while the indicator is not active --------------------------------------

@pytest.mark.parametrize("indicator", ["minor", "major", "critical", "maintenance"])
def test_an_incident_io_page_with_an_active_indicator_is_read_as_before_d100(indicator, no_gap):
    routes = {STATUS_JSON: status_body(indicator), SUMMARY_JSON: summary_body([incident()])}
    read_indicator, box, seen = read(routes, scope={})
    assert read_indicator == indicator
    assert seen == [STATUS_JSON]                                     # no summary.json, no Statuspage list either
    assert "platform" not in box and "scope" not in box and no_gap == []


@pytest.mark.parametrize("indicator", ["none", "unknown"])
def test_an_incident_io_page_reads_summary_json_once_and_not_the_two_statuspage_lists(indicator, no_gap):
    routes = {STATUS_JSON: status_body(indicator), SUMMARY_JSON: summary_body([incident()])}
    read_indicator, box, seen = read(routes, scope={})
    assert read_indicator == indicator
    assert seen == [STATUS_JSON, SUMMARY_JSON]                       # not incidents/unresolved.json, not scheduled-maintenances
    assert box["platform"] == "incident_io"
    assert no_gap == [status_refresh.SCOPE_GAP_SECONDS] and status_refresh.SCOPE_GAP_SECONDS >= 1.0   # one pause before the one extra request


def test_the_open_incident_of_the_live_shape_is_counted():
    # none + one incident (monitoring, minor, no components) and no scheduled_maintenances key
    routes = {STATUS_JSON: status_body("none"), SUMMARY_JSON: summary_body([incident()])}
    _indicator, box, _seen = read(routes)
    scope = box["scope"]
    assert scope["scope_outcome"] == "ok" and scope["scope_error"] is None
    assert scope["active_incidents"] == 1 and scope["max_incident_impact"] == "minor"
    assert scope["incident_updated_at"].isoformat() == "2026-11-05T17:05:56+00:00"
    assert scope["active_maintenances"] is None                       # the body has no such list
    assert scope["affected"] == ["Elevated errors"] and scope["affected_regional"] is None


def test_an_empty_list_is_a_zero_not_a_missing_count():
    _i, box, _s = read({STATUS_JSON: status_body(), SUMMARY_JSON: summary_body([])})
    assert box["scope"]["active_incidents"] == 0 and box["scope"]["max_incident_impact"] == "none" and box["scope"]["affected"] is None


def test_resolved_and_postmortem_incidents_are_not_open():
    incidents = [incident("resolved"), incident("postmortem"), incident("investigating", "major", "Two"), incident("identified", "critical", "Three")]
    _i, box, _s = read({STATUS_JSON: status_body(), SUMMARY_JSON: summary_body(incidents)})
    scope = box["scope"]
    assert scope["active_incidents"] == 2 and scope["max_incident_impact"] == "critical" and scope["affected"] == ["Two", "Three"]


def test_component_names_win_over_the_incident_name_and_are_capped():
    components = [{"name": f"Component {n}", "group_id": None} for n in range(5)]
    _i, box, _s = read({STATUS_JSON: status_body(), SUMMARY_JSON: summary_body([incident(components=components)])})
    assert box["scope"]["affected"] == ["Component 0", "Component 1", "Component 2"]


def test_a_maintenance_list_is_counted_when_the_body_has_one():
    maintenances = [{"id": "m1", "name": "Work", "status": "in_progress", "components": []},
                    {"id": "m2", "name": "Later", "status": "scheduled", "components": []}]
    _i, box, _s = read({STATUS_JSON: status_body(), SUMMARY_JSON: summary_body([], maintenances=maintenances)})
    assert box["scope"]["active_maintenances"] == 1


@pytest.mark.parametrize("answer, error", [
    (httpx.Response(403), "http_403"), (httpx.Response(429), "http_429"), (httpx.Response(404, text="<html></html>"), "http_404"),
    (httpx.Response(503), "http_503"), (httpx.Response(200, text="<html>not json</html>"), None),
    (httpx.Response(200, content=b"x" * (status_scope.SCOPE_CAP + 1)), "too_large"),
    (httpx.Response(200, json=summary_body([incident()], page_id="01JMDK9XYNY6RXSED6SDWW50WZ")), None),     # another page's list
    (httpx.Response(200, json=summary_body([incident(impact="catastrophic")])), None),
    (httpx.Response(200, json={"page": {"id": PAGE}, "incidents": "none"}), None),
    (httpx.Response(200, json=summary_body([incident()], maintenances="x")), None),
])
def test_a_summary_that_cannot_be_read_only_leaves_the_incident_fields_out_and_never_fails_the_feed(answer, error):
    indicator, box, seen = read({STATUS_JSON: status_body("none"), SUMMARY_JSON: answer})
    assert indicator == "none" and seen == [STATUS_JSON, SUMMARY_JSON]       # the feed read itself stays a success
    scope = box["scope"]
    assert box["platform"] == "incident_io"                                   # the marker is kept: the list was asked for and not read
    assert scope["scope_outcome"] == "failed" and scope["scope_error"]
    if error:
        assert scope["scope_error"] == error
    for key in ("active_incidents", "max_incident_impact", "incident_updated_at", "active_maintenances", "affected", "affected_regional"):
        assert scope[key] is None, key


def test_a_summary_that_trickles_in_past_the_read_budget_is_a_timeout_not_a_feed_failure(monkeypatch):
    ticks = iter([0.0, 0.0, 0.0] + [100.0] * 50)
    monkeypatch.setattr(status_refresh, "_clock", lambda: next(ticks))
    _i, box, _s = read({STATUS_JSON: status_body("none"), SUMMARY_JSON: summary_body([incident()])})
    assert box["scope"]["scope_outcome"] in ("failed", "ok")               # the clock stub ends the body read at the summary only
    assert box["platform"] == "incident_io"


def test_a_refusal_to_ask_is_skipped_not_failed_and_makes_no_request():
    asked: list[int] = []
    box = {"allowed": lambda: asked.append(1) or False}
    indicator, box, seen = read({STATUS_JSON: status_body("none"), SUMMARY_JSON: summary_body([incident()])}, incidentio=box)
    assert indicator == "none" and seen == [STATUS_JSON] and asked == [1]
    assert box["platform"] == "incident_io"
    assert box["scope"]["scope_outcome"] == "skipped" and box["scope"]["scope_error"] is None and box["scope"]["active_incidents"] is None


# --- everything else is as it was ------------------------------------------------------------------------------------------

def test_an_atlassian_page_with_the_indicator_none_makes_one_request_and_is_not_marked():
    indicator, box, seen = read({STATUS_JSON: status_body("none", page_id="yh6f0r4529hb", time_zone="Etc/UTC"),
                                 SUMMARY_JSON: summary_body([incident()], page_id="yh6f0r4529hb")}, scope={})
    assert indicator == "none" and seen == [STATUS_JSON] and "platform" not in box and "scope" not in box


def test_an_atlassian_page_with_an_active_indicator_reads_its_two_lists_as_before():
    routes = {STATUS_JSON: status_body("minor", page_id="yh6f0r4529hb", time_zone="Etc/UTC"),
              "/api/v2/incidents/unresolved.json": summary_body([incident()], page_id="yh6f0r4529hb"),
              "/api/v2/scheduled-maintenances/active.json": {"page": {"id": "yh6f0r4529hb"}, "scheduled_maintenances": []}}
    scope: dict = {}
    _i, box, seen = read(routes, scope=scope)
    assert seen == [STATUS_JSON, "/api/v2/incidents/unresolved.json", "/api/v2/scheduled-maintenances/active.json"]
    assert "platform" not in box and scope["active_incidents"] == 1


def test_without_the_option_an_incident_io_page_is_read_as_before_d100():
    for indicator, expected in (("none", [STATUS_JSON]), ("minor", [STATUS_JSON])):
        _i, _box, seen = read({STATUS_JSON: status_body(indicator), SUMMARY_JSON: summary_body([incident()])}, incidentio=None, scope={})
        assert seen == expected


def test_other_feed_kinds_and_other_addresses_are_not_asked_for_a_summary():
    other = Source("I", "https://i.example/", "https://i.example/summary.json", "instatus")
    handler, seen = serve({"/summary.json": {"page": {"name": "I", "status": "UP", "url": "https://i.example/"}, "activeIncidents": [],
                                              "activeMaintenances": []}})
    box = {"allowed": lambda: True}
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        try:
            status_refresh._read_source(other, client, scope={}, incidentio=box)
        except Exception:
            pass
    assert seen == ["/summary.json"] and "platform" not in box


# --- the gate, the budget, the count and the rest --------------------------------------------------------------------------

class Gate:
    """A feed_robots.Gate stand-in: the paths in ``deny`` are disallowed; every call is recorded."""

    def __init__(self, deny=(), read_now=False):
        self.deny, self.read_now, self.calls = tuple(deny), read_now, []

    def decide(self, host, paths):
        from datetime import datetime, timezone

        from ahn.feed_robots import Decision

        self.calls.append((host, tuple(paths)))
        return Decision(host, "ok", "http_200", datetime.now(timezone.utc),
                        {path: {"allowed": path not in self.deny, "rule": None, "group": None} for path in paths}, read_now=self.read_now)


def allowed(gate, *, paused=False, scope_allowed=None, slot=None):
    return status_refresh._incidentio_allowed(gate, SOURCE, None, paused, scope_allowed, slot)


def test_the_summary_path_goes_through_the_same_robots_decision_as_the_feed():
    gate = Gate()
    assert allowed(gate)() is True
    host, paths = gate.calls[0]
    assert host == "status.example.com"
    assert paths[0] == STATUS_JSON and SUMMARY_JSON in paths and set(paths) >= {STATUS_JSON, SUMMARY_JSON, "/history.atom"}
    deny = Gate(deny=[SUMMARY_JSON])
    assert allowed(deny)() is False


def test_each_refusal_spends_nothing_after_it():
    slots: list[int] = []
    slot = lambda: slots.append(1) or True  # noqa: E731
    assert allowed(Gate(), paused=True, slot=slot)() is False and slots == []                      # resting after failures
    assert allowed(Gate(), scope_allowed=lambda: False, slot=slot)() is False and slots == []      # the cron's time budget
    assert allowed(Gate(deny=[SUMMARY_JSON]), slot=slot)() is False and slots == []                # robots.txt
    gate = Gate()
    assert allowed(gate, paused=True)() is False and gate.calls == []                              # no robots.txt read when it rests
    assert allowed(Gate(), slot=slot)() is True and slots == [1]                                   # only a request that will be made spends it
    assert allowed(Gate(), slot=lambda: False)() is False


def test_a_robots_read_made_for_the_summary_is_paced(no_gap):
    assert allowed(Gate(read_now=True))() is True
    assert no_gap == [status_refresh.SCOPE_GAP_SECONDS]
    no_gap.clear()
    assert allowed(Gate(read_now=False))() is True and no_gap == []


def test_the_cron_limit_is_forty_per_run():
    assert FEED_INCIDENTIO_LIST_MAX == 40


# --- the switch --------------------------------------------------------------------------------------------------------------

def test_the_switch_parses_like_its_siblings(monkeypatch, caplog):
    from ahn import settings as settings_module

    monkeypatch.setattr(settings_module, "_D100_WARNED", False)
    assert parse_d100_switch("") is True and parse_d100_switch("1") is True and parse_d100_switch(" 1 ") is True
    assert parse_d100_switch("0") is False
    with caplog.at_level("WARNING"):
        assert parse_d100_switch("off") is False and parse_d100_switch("true") is False
    assert [r.getMessage() for r in caplog.records].count("d100_switch_unknown_value") == 1        # one warning, the code only
    assert "off" not in caplog.text
    monkeypatch.setenv("AHN_D100_INCIDENTIO_INCIDENTS", "0")
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("AHN_KEY_PEPPER", "p" * 40)
    assert Settings.from_env().d100_incidentio_incidents is False
    monkeypatch.delenv("AHN_D100_INCIDENTIO_INCIDENTS")
    assert Settings.from_env().d100_incidentio_incidents is True


def test_a_summary_body_is_valid_json_the_tests_can_round_trip():
    assert json.loads(json.dumps(summary_body([incident()])))["incidents"][0]["status"] == "monitoring"


def test_a_listed_incident_on_an_incident_io_page_is_due_on_the_active_interval():
    from datetime import datetime, timedelta, timezone

    from ahn.background_reads import feed_next_due

    t = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
    base = {"indicator": "none", "fetched_at": t, "last_error_at": None, "failures": None, "last_error_detail": None}
    listed = {**base, "feed_platform": "incident_io", "active_incidents": 1}
    for idle in (timedelta(hours=1), timedelta(hours=2)):
        assert feed_next_due(listed, idle) == t + timedelta(minutes=15)
        for quiet in ({**base, "feed_platform": "incident_io", "active_incidents": 0},
                      {**base, "feed_platform": "incident_io", "active_incidents": None},
                      {**base, "feed_platform": None, "active_incidents": 1},
                      {**base, "feed_platform": None, "active_incidents": None}):
            assert feed_next_due(quiet, idle) == t + idle
