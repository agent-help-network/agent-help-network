"""D104: a Better Stack feed's update time is that of its reports, not the page's own ``updated_at`` (pure tests; no database, no network).

The page-level ``data.attributes.updated_at`` moves independently of the reports (observed to be refreshed periodically), so a page
sorted on it sat at the top of the public board for no reason (the live shape of 2026-11-05: ``aggregate_state`` downtime with one open report
whose latest update was a day older than the page's ``updated_at``, and 42 reports of which 41 read ``resolved`` with ``ends_at`` null).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from ahn import signal_history, status_refresh
from ahn.settings import Settings, parse_d104_switch
from ahn.status_refresh import (SOURCES, Source, betterstack_affected, betterstack_event_time, parse_betterstack)

PAGE_TIME = "2026-11-06T02:20:00.147Z"  # the page's settings changed; nothing was reported
BETTERSTACK = Source("Example", "https://status.example.com/", "https://status.example.com/index.json", "betterstack")


def at(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def report(rid, state, updates, starts="2026-11-01T00:00:00.000Z", ends=None):
    return {"id": rid, "type": "status_report",
            "attributes": {"title": "t", "report_type": "manual", "starts_at": starts, "ends_at": ends, "aggregate_state": state},
            "relationships": {"status_updates": {"data": [{"id": uid, "type": "status_update"} for uid, _t in updates]}}}


def update_items(updates):
    return [{"id": uid, "type": "status_update", "attributes": {"message": "m", "published_at": published}} for uid, published in updates]


def resource(rid, name, status, kind="Monitor"):
    return {"id": rid, "type": "status_page_resource",
            "attributes": {"public_name": name, "status": status, "resource_type": kind, "status_history": []}}


def body(state="operational", reports=(), resources=(), page_time=PAGE_TIME):
    """reports: (id, state, [(update id, published_at)], extra); the updates are listed in ``included`` as Better Stack does."""
    included = list(resources)
    for entry in reports:
        rid, rstate, updates = entry[:3]
        extra = entry[3] if len(entry) > 3 else {}
        included.append(report(rid, rstate, updates, **extra))
        included += update_items(updates)
    return {"data": {"id": "1", "type": "status_page", "attributes": {"aggregate_state": state, "updated_at": page_time}},
            "included": included}


def test_the_page_updated_at_is_not_the_update_time():
    quiet = body("operational")
    assert parse_betterstack(quiet) == ("none", None)
    assert parse_betterstack(quiet, events=False) == ("none", at(PAGE_TIME))


def test_an_open_report_gives_the_latest_update_of_that_report():
    # An older open report, a newer open one with two updates, and a resolved one posted later than both: only open reports count.
    reports = [("1", "downtime", [("u1", "2026-11-04T13:34:21.674Z")]),
               ("2", "degraded", [("u2", "2026-11-03T10:00:00.000Z"), ("u3", "2026-11-04T15:00:00.000Z")]),
               ("3", "resolved", [("u4", "2026-11-05T20:00:00.000Z")])]
    assert parse_betterstack(body("downtime", reports)) == ("major", at("2026-11-04T15:00:00Z"))


def test_the_eclipse_shape_open_report_is_older_than_the_page_time():
    # The page changed at 2026-11-06T02:20Z; the open report was last posted on 2026-11-04T13:34Z; a resolved automatic report
    # still has ends_at null (it is not "open" for that).
    reports = [("1", "downtime", [("u1", "2026-11-04T13:34:21.674Z")]),
               ("2", "resolved", [("u2", "2026-11-04T00:30:00.000Z"), ("u3", "2026-11-04T01:00:00.000Z")])]
    indicator, updated = parse_betterstack(body("downtime", reports))
    assert indicator == "major" and updated == at("2026-11-04T13:34:21.674Z") and updated < at(PAGE_TIME)


def test_without_an_open_report_the_last_posted_update_of_any_report_is_used():
    reports = [("1", "resolved", [("u1", "2026-10-01T00:00:00.000Z")]),
               ("2", "resolved", [("u2", "2026-10-20T08:00:00.000Z"), ("u3", "2026-10-20T09:30:00.000Z")])]
    assert parse_betterstack(body("operational", reports)) == ("none", at("2026-10-20T09:30:00Z"))


def test_an_open_report_with_no_readable_update_uses_its_start():
    open_no_updates = [("1", "downtime", [], {"starts": "2026-11-02T03:00:00.000Z"})]
    assert parse_betterstack(body("downtime", open_no_updates)) == ("major", at("2026-11-02T03:00:00Z"))


@pytest.mark.parametrize("state,indicator", [("downtime", "major"), ("degraded", "minor"), ("maintenance", "maintenance")])
def test_a_page_that_is_not_operational_keeps_its_page_time_when_no_open_report_gives_a_usable_time(state, indicator):
    page = at(PAGE_TIME)
    future = "2999-01-01T00:00:00.000Z"
    cases = {
        "no report": [],
        "only a resolved report": [("1", "resolved", [("u1", "2026-10-01T00:00:00.000Z")])],
        "open report, unreadable update and start": [("1", state, [("u1", "soon")], {"starts": "nonsense"})],
        "open report, future update and start": [("1", state, [("u1", future)], {"starts": future})],
        "open report without updates or start": [("1", state, [], {"starts": None})],
    }
    for name, reports in cases.items():
        assert parse_betterstack(body(state, reports)) == (indicator, page), name
    # An operational page does not borrow it.
    assert parse_betterstack(body("operational", cases["open report, future update and start"])) == ("none", None)
    # No page time either: still no time (as before D104).
    assert parse_betterstack(body(state, [], page_time=None)) == (indicator, None)


def test_a_future_update_is_not_used_for_a_quiet_page():
    reports = [("1", "resolved", [("u1", "2999-01-01T00:00:00.000Z"), ("u2", "2026-10-01T00:00:00.000Z")])]
    assert parse_betterstack(body("operational", reports)) == ("none", at("2026-10-01T00:00:00Z"))


def test_an_item_without_an_id_matches_nothing():
    payload = body("downtime", [("1", "downtime", [("u1", "2026-11-04T13:00:00.000Z")])])
    for item in payload["included"]:
        if item["type"] == "status_update":
            del item["id"]
        if item["type"] == "status_report":
            item["relationships"]["status_updates"]["data"] = [{"type": "status_update"}, {"id": None}, {"id": True}, {"id": ""}]
    # No update matched, so the open report's start (2026-11-01 in the helper) is the time, never a mismatched "None" key.
    assert betterstack_event_time(payload) == at("2026-11-01T00:00:00Z")
    for item in payload["included"]:
        if item["type"] == "status_report":
            item["attributes"]["starts_at"] = None
    assert betterstack_event_time(payload) is None
    assert parse_betterstack(payload) == ("major", at(PAGE_TIME))


def _mutations():
    def base():
        return body("downtime", [("1", "downtime", [("u1", "2026-11-04T13:00:00.000Z")])], [resource("1", "eclip.se", "downtime")])

    junk = ["text", ["x"], 5, True, 1.5, None, [], {}]
    out = []

    def make(label, change):
        payload = base()
        change(payload)
        out.append(pytest.param(payload, id=label))

    def each(payload, kind, key, value):
        for item in payload["included"]:
            if isinstance(item, dict) and item.get("type") == kind:
                item["attributes"][key] = value

    reports = lambda p: [i for i in p["included"] if isinstance(i, dict) and i.get("type") == "status_report"]  # noqa: E731
    for n, value in enumerate(junk):
        make(f"relationships-{n}", lambda p, v=value: [r.__setitem__("relationships", v) for r in reports(p)])
        make(f"status_updates-{n}", lambda p, v=value: [r["relationships"].__setitem__("status_updates", v) for r in reports(p)])
        make(f"status_updates.data-{n}", lambda p, v=value: [r["relationships"]["status_updates"].__setitem__("data", v) for r in reports(p)])
        make(f"ref-{n}", lambda p, v=value: [r["relationships"]["status_updates"].__setitem__("data", [v]) for r in reports(p)])
        make(f"item-{n}", lambda p, v=value: p["included"].append(v))
        make(f"attributes-{n}", lambda p, v=value: [i.__setitem__("attributes", v) for i in p["included"]])
        make(f"report-state-{n}", lambda p, v=value: each(p, "status_report", "aggregate_state", v))
        make(f"report-starts-{n}", lambda p, v=value: each(p, "status_report", "starts_at", v))
        make(f"resource-status-{n}", lambda p, v=value: each(p, "status_page_resource", "status", v))
        make(f"resource-name-{n}", lambda p, v=value: each(p, "status_page_resource", "public_name", v))
        make(f"resource-kind-{n}", lambda p, v=value: each(p, "status_page_resource", "resource_type", v))
        make(f"published-{n}", lambda p, v=value: each(p, "status_update", "published_at", v))
        make(f"id-{n}", lambda p, v=value: [i.__setitem__("id", v) for i in p["included"]])
        make(f"type-{n}", lambda p, v=value: [i.__setitem__("type", v) for i in p["included"]])
        make(f"included-{n}", lambda p, v=value: p.__setitem__("included", v))
    return out


@pytest.mark.parametrize("payload", _mutations())
def test_a_malformed_part_of_the_body_never_raises(payload):
    # Anything but the page's own aggregate_state is optional detail: it may leave a value out, never fail the read.
    parse_betterstack(payload)
    betterstack_event_time(payload)
    betterstack_affected(payload)
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    status_refresh._read_source(BETTERSTACK, client, None, {})


@pytest.mark.parametrize("state,indicator", [("downtime", "major"), ("degraded", "minor"), ("maintenance", "maintenance")])
def test_the_board_keeps_a_page_that_is_down_now_even_when_it_has_no_report_time(state, indicator):
    now = at(PAGE_TIME) + timedelta(hours=1)
    for reports in ([], [("1", "resolved", [("u1", "2020-01-01T00:00:00.000Z")])], [("1", state, [("u1", "soon")], {"starts": "x"})]):
        _indicator, updated = parse_betterstack(body(state, reports))
        status = {"indicator": indicator, "source_updated_at": updated, "fetched_at": now, "freshness": "fresh"}
        assert signal_history.board_provider(status, now) is None, reports
    # The one board change: a page whose open report itself was last updated more than a week ago leaves the board as stale,
    # as every provider's incident time does; before D104 the page's own time kept it on.
    _indicator, updated = parse_betterstack(body(state, [("1", state, [("u1", "2026-10-01T00:00:00.000Z")])]))
    status = {"indicator": indicator, "source_updated_at": updated, "fetched_at": now, "freshness": "fresh"}
    assert signal_history.board_provider(status, now) == "stale_update"


def test_affected_is_not_stored_for_a_page_that_reads_operational():
    payload = body("operational", resources=[resource("1", "API", "downtime")])  # a leftover resource status under an operational page
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    scope: dict = {}
    assert status_refresh._read_source(BETTERSTACK, client, None, scope)[0] == "none"
    assert scope == {}


def test_the_future_slack_matches_the_one_the_board_uses():
    from ahn import triage

    assert status_refresh.BETTERSTACK_FUTURE_SLACK == triage.FUTURE_SLACK


@pytest.mark.parametrize("included", [None, "x", [], [None, 3, {}], [{"type": "status_report"}], [{"type": "status_update", "attributes": 1}]])
def test_a_feed_without_readable_reports_has_no_time_and_is_still_read(included):
    payload = body("operational")
    payload["included"] = included
    assert parse_betterstack(payload) == ("none", None)
    assert betterstack_event_time(payload) is None and betterstack_affected(payload) is None


def test_an_unreadable_update_is_skipped_not_fatal():
    reports = [("1", "downtime", [("u1", "2026-11-04T13:00:00.000Z"), ("u2", "not a time"), ("u3", "2026-11-04T12:00:00")])]
    assert parse_betterstack(body("downtime", reports)) == ("major", at("2026-11-04T13:00:00Z"))


def test_the_state_rules_are_unchanged():
    for state in ("Operational", "outage", "", None):
        with pytest.raises(ValueError):
            parse_betterstack(body(state))
    for state, indicator in (("operational", "none"), ("degraded", "minor"), ("downtime", "major"), ("maintenance", "maintenance")):
        assert parse_betterstack(body(state))[0] == indicator


def test_affected_names_the_resources_that_are_down_or_degraded_now():
    resources = [resource("1", "Core Services", "downtime", "ResourceGroup"), resource("2", "Docs", "operational"),
                 resource("3", "Slow API", "degraded"), resource("4", "eclip.se", "downtime"), resource("5", "Maint", "maintenance")]
    # Down before degraded, a monitor before the group that restates it, maintenance is not an outage.
    assert betterstack_affected(body("downtime", resources=resources)) == ["eclip.se", "Core Services", "Slow API"]
    assert betterstack_affected(body("operational", resources=[resource("1", "Docs", "operational")])) is None


def test_affected_is_capped_cleaned_and_deduplicated():
    resources = [resource(str(i), f"Service {i}\n\x07", "downtime") for i in range(6)] + [resource("9", "Service 0", "downtime")]
    names = betterstack_affected(body("downtime", resources=resources))
    assert names == ["Service 0", "Service 1", "Service 2"]
    assert betterstack_affected(body("downtime", resources=[resource("1", "x" * 200, "downtime")])) == ["x" * 80]
    assert betterstack_affected(body("downtime", resources=[resource("1", None, "downtime"), resource("2", "  ", "downtime")])) is None


def test_the_reader_stores_the_report_time_and_names_in_scope_and_the_switch_restores_the_page_time():
    reports = [("1", "downtime", [("u1", "2026-11-04T13:34:21.674Z")])]
    payload = body("downtime", reports, [resource("1", "eclip.se", "downtime")])
    client = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=payload)))
    scope: dict = {}
    assert status_refresh._read_source(BETTERSTACK, client, None, scope) == ("major", at("2026-11-04T13:34:21.674Z"))
    assert scope == {"affected": ["eclip.se"]}
    scope = {}
    assert status_refresh._read_source(BETTERSTACK, client, None, scope, betterstack_events=False) == ("major", at(PAGE_TIME))
    assert scope == {}


def test_the_reader_makes_one_request_and_other_kinds_are_untouched():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=body("operational"))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    status_refresh._read_source(BETTERSTACK, client, None, {})
    assert seen == ["https://status.example.com/index.json"]
    statuspage = {"page": {"id": "yh6f0r4529hb", "updated_at": "2026-11-05T17:05:56Z"}, "status": {"indicator": "none"}}
    other = httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=statuspage)))
    scope: dict = {}
    source = Source("S", "https://status.example.com/", "https://status.example.com/api/v2/status.json")
    assert status_refresh._read_source(source, other, None, scope) == ("none", at("2026-11-05T17:05:56Z"))
    assert scope == {}


def test_the_switch_defaults_on_and_an_unknown_value_is_off():
    assert parse_d104_switch("") is True and parse_d104_switch("1") is True and parse_d104_switch(" 1 ") is True
    assert parse_d104_switch("0") is False and parse_d104_switch("true") is False and parse_d104_switch("off") is False
    assert Settings(None, "x" * 40).d104_betterstack_updated_at is True


def test_the_switch_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AHN_D104_BETTERSTACK_UPDATED_AT", "0")
    assert Settings.from_env().d104_betterstack_updated_at is False
    monkeypatch.delenv("AHN_D104_BETTERSTACK_UPDATED_AT")
    assert Settings.from_env().d104_betterstack_updated_at is True


def _entry(host, updated_at):
    return {"site_host": host, "provider": {"updated_at": updated_at}}


def test_the_public_board_no_longer_pins_a_page_whose_settings_changed_at_the_top():
    # eclipse.org-like page: the page's updated_at is newer than anything reported. Two other pages report on 2026-11-05.
    eclipse_payload = body("operational", [("1", "resolved", [("u1", "2026-10-01T00:00:00.000Z")])])
    other_payload = body("downtime", [("2", "downtime", [("u2", "2026-11-05T08:00:00.000Z")])], page_time="2026-10-02T00:00:00.000Z")

    def entries(events):
        out = []
        for host, payload in (("eclipse.org", eclipse_payload), ("other-a.example", other_payload)):
            _indicator, updated = parse_betterstack(payload, events=events)
            out.append(_entry(host, updated.isoformat().replace("+00:00", "Z") if updated else None))
        out.append(_entry("other-b.example", "2026-11-03T00:00:00Z"))
        return [e["site_host"] for e in signal_history.board_order(out)]

    assert entries(events=False)[0] == "eclipse.org"       # before D104: the page time put it first
    assert entries(events=True) == ["other-a.example", "other-b.example", "eclipse.org"]
    assert entries(events=True)[0] != "eclipse.org"


def test_a_quiet_page_with_no_report_at_all_sorts_last_not_first():
    quiet = body("operational")
    _indicator, updated = parse_betterstack(quiet)
    ordered = signal_history.board_order([_entry("quiet.example", updated and updated.isoformat()), _entry("old.example", "2020-01-01T00:00:00Z")])
    assert [e["site_host"] for e in ordered] == ["old.example", "quiet.example"]


def test_every_mapped_betterstack_feed_is_read_by_the_event_parser():
    assert status_refresh.FEED_KINDS["betterstack"][1] is parse_betterstack
    assert any(source.kind == "betterstack" for source in SOURCES.values())


def test_documents_name_the_rule():
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    decisions = (root / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "| D104 " in decisions and "AHN_D104_BETTERSTACK_UPDATED_AT" in decisions
    assert decisions.index("\n| D103 ") < decisions.index("\n| D104 ") < decisions.index("\n| D105 ")
    for name in ("docs/api-contract.md", "docs/api-contract.en.md"):
        text = (root / name).read_text(encoding="utf-8")
        assert "D104" in text and "AHN_D104_BETTERSTACK_UPDATED_AT" in text and "provider_update_time_unknown" in text, name
    llms = (root / "ahn" / "llms.txt").read_text(encoding="utf-8")
    assert "D104" in llms and "Better Stack" in llms
    # D100 follow-ups: the contract no longer says only the scheduled reader reads summary.json.
    assert "scheduled reader, and the background refresh a lookup starts" in (root / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    assert "조회가 띄우는 뒤편 갱신" in (root / "docs" / "api-contract.md").read_text(encoding="utf-8")
