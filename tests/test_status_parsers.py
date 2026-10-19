"""Pure tests for the status feed parsers: no database, no network."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from ahn import status_refresh
from ahn.status_refresh import FEED_KINDS, Source, failure_label, parse_betterstack, parse_instatus, parse_status
from ahn.tool_metrics import provider_signal_from_result


def test_statuspage_maintenance_is_a_known_indicator():
    body = {"page": {"updated_at": "2026-10-19T20:44:18.855Z"}, "status": {"indicator": "maintenance", "description": "Service Under Maintenance"}}
    indicator, updated_at = parse_status(body)
    assert indicator == "maintenance"
    assert updated_at == datetime(2026, 10, 19, 20, 44, 18, 855000, tzinfo=timezone.utc)


@pytest.mark.parametrize("value", ["none", "minor", "major", "critical", "unknown"])
def test_existing_statuspage_values_are_unchanged(value):
    assert parse_status({"status": {"indicator": value}}) == (value, None)


@pytest.mark.parametrize("value", ["Maintenance", "under_maintenance", "purple", "", None, 3])
def test_other_statuspage_values_are_still_rejected(value):
    with pytest.raises(ValueError):
        parse_status({"status": {"indicator": value}})


def test_maintenance_counts_as_a_provider_signal_for_tool_metrics():
    assert provider_signal_from_result({"provider_status": {"freshness": "fresh", "indicator": "maintenance"}}) == "active"
    assert provider_signal_from_result({"provider_status": {"freshness": "stale", "indicator": "maintenance"}}) == "unavailable"


def test_a_maintenance_feed_is_read_through_the_real_reader():
    feed = Source("Example Status", "https://status.example.com/", "https://status.example.com/api/v2/status.json")
    body = {"page": {"updated_at": "2026-10-19T20:44:18Z"}, "status": {"indicator": "maintenance"}}
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body))) as client:
        assert status_refresh._read_source(feed, client)[0] == "maintenance"


# --- Instatus (<page>/summary.json) ------------------------------------------------------------------------

INSTATUS = Source("Example Status", "https://status.example.com/", "https://status.example.com/summary.json", "instatus")
BETTERSTACK = Source("Example Status", "https://status.example.com/", "https://status.example.com/index.json", "betterstack")


def _read(source, handler):
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        return status_refresh._read_source(source, client)


def _read_label(source, handler) -> str:
    with pytest.raises(Exception) as caught:
        _read(source, handler)
    return failure_label(caught.value)


@pytest.mark.parametrize("state,indicator", [("UP", "none"), ("HASISSUES", "minor"), ("UNDERMAINTENANCE", "maintenance")])
def test_instatus_states_map_to_indicators(state, indicator):
    body = {"page": {"name": "Example", "url": "https://status.example.com", "status": state}}
    assert parse_instatus(body) == (indicator, None)
    assert _read(INSTATUS, lambda _r: httpx.Response(200, json=body)) == (indicator, None)


def test_instatus_up_with_a_scheduled_maintenance_still_reads_none():
    # Seen on status.mollie.com: page UP while a maintenance is NOTSTARTEDYET. The page-level state is what is reported.
    body = {"page": {"status": "UP"}, "activeMaintenances": [{"status": "NOTSTARTEDYET", "updatedAt": "2026-10-19T13:22:13.723Z"}]}
    assert parse_instatus(body) == ("none", None)


def test_instatus_uses_the_latest_update_of_the_active_items_when_the_page_is_not_up():
    body = {"page": {"status": "HASISSUES"},
            "activeIncidents": [{"updatedAt": "2026-10-19T10:00:00.000Z"}, {"updatedAt": "2026-10-19T11:30:00Z"}, {"name": "no time"}],
            "activeMaintenances": []}
    assert parse_instatus(body) == ("minor", datetime(2026, 10, 19, 11, 30, tzinfo=timezone.utc))
    assert parse_instatus({"page": {"status": "UNDERMAINTENANCE"}, "activeMaintenances": [{"updatedAt": "2026-10-19T09:00:00Z"}]})[1] == \
        datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("state", ["up", "HASISSUE", "MAJOROUTAGE", "OPERATIONAL", "", None, 1])
def test_instatus_unknown_state_is_invalid_body(state):
    body = {"page": {"status": state}}
    with pytest.raises(ValueError):
        parse_instatus(body)
    assert _read_label(INSTATUS, lambda _r: httpx.Response(200, json=body)) == "invalid_body"


@pytest.mark.parametrize("body", [[], "UP", None, {}, {"page": None}, {"page": "UP"}, {"status": "UP"},
                                  {"page": {"status": "HASISSUES"}, "activeIncidents": "none"},
                                  {"page": {"status": "HASISSUES"}, "activeIncidents": [{"updatedAt": "yesterday"}]},
                                  {"page": {"status": "HASISSUES"}, "activeIncidents": [{"updatedAt": "2026-10-19T10:00:00"}]}])
def test_instatus_malformed_body_is_invalid_body(body):
    with pytest.raises(ValueError):
        parse_instatus(body)
    assert _read_label(INSTATUS, lambda _r: httpx.Response(200, json=body)) == "invalid_body"
    assert _read_label(INSTATUS, lambda _r: httpx.Response(200, content=b"<html>not json")) == "invalid_body"


def test_instatus_keeps_the_small_cap():
    assert FEED_KINDS["instatus"][0] == 32_768
    assert _read_label(INSTATUS, lambda _r: httpx.Response(200, content=b"x" * 32_769)) == "too_large"
    assert _read_label(INSTATUS, lambda _r: httpx.Response(503)) == "http_503"
    assert _read_label(INSTATUS, lambda _r: httpx.Response(301, headers={"Location": "https://elsewhere.example/summary.json"})) == "http_301"


# --- Better Stack (<page>/index.json) ----------------------------------------------------------------------

def _betterstack(state, updated_at="2026-10-19T21:18:50.858Z", **extra):
    attributes = {"company_name": "Example", "aggregate_state": state}
    if updated_at is not None:
        attributes["updated_at"] = updated_at
    attributes.update(extra)
    return {"data": {"id": "1", "type": "status_page", "attributes": attributes}, "included": []}


@pytest.mark.parametrize("state,indicator", [("operational", "none"), ("degraded", "minor"), ("downtime", "major"), ("maintenance", "maintenance")])
def test_betterstack_states_map_to_indicators(state, indicator):
    expected = (indicator, datetime(2026, 10, 19, 21, 18, 50, 858000, tzinfo=timezone.utc))
    assert parse_betterstack(_betterstack(state)) == expected
    assert _read(BETTERSTACK, lambda _r: httpx.Response(200, json=_betterstack(state))) == expected


def test_betterstack_update_time_is_optional():
    assert parse_betterstack(_betterstack("operational", updated_at=None)) == ("none", None)


@pytest.mark.parametrize("state", ["Operational", "outage", "UP", "partial_outage", "", None, 0])
def test_betterstack_unknown_state_is_invalid_body(state):
    with pytest.raises(ValueError):
        parse_betterstack(_betterstack(state))
    assert _read_label(BETTERSTACK, lambda _r: httpx.Response(200, json=_betterstack(state))) == "invalid_body"


@pytest.mark.parametrize("body", [[], "operational", None, {}, {"data": None}, {"data": []}, {"data": {}}, {"data": {"attributes": None}},
                                  {"data": {"attributes": {}}}, {"page": {"status": "operational"}},
                                  _betterstack("operational", updated_at="soon"), _betterstack("operational", updated_at=5),
                                  _betterstack("operational", updated_at="2026-10-19T21:18:50")])
def test_betterstack_malformed_body_is_invalid_body(body):
    with pytest.raises(ValueError):
        parse_betterstack(body)
    assert _read_label(BETTERSTACK, lambda _r: httpx.Response(200, json=body)) == "invalid_body"
    assert _read_label(BETTERSTACK, lambda _r: httpx.Response(200, content=b"{not json")) == "invalid_body"


def _padded(size):
    """A valid Better Stack body padded with unused data to exactly this many bytes."""
    body = _betterstack("operational")
    body["included"] = [{"pad": ""}]
    base = len(json.dumps(body, separators=(",", ":")))
    body["included"][0]["pad"] = "x" * (size - base)
    raw = json.dumps(body, separators=(",", ":")).encode()
    assert len(raw) == size
    return raw


def test_betterstack_cap_is_raised_to_hold_the_measured_pages_and_still_fails_closed():
    cap = FEED_KINDS["betterstack"][0]
    assert cap == 524_288 and cap > 266_919  # the largest mapped page (brave.com) measured 2026-10-19
    assert _read(BETTERSTACK, lambda _r: httpx.Response(200, content=_padded(266_919)))[0] == "none"
    assert _read(BETTERSTACK, lambda _r: httpx.Response(200, content=_padded(cap)))[0] == "none"
    assert _read_label(BETTERSTACK, lambda _r: httpx.Response(200, content=_padded(cap + 1))) == "too_large"
    # Only Better Stack's cap was raised: the same 266,919 bytes are refused for a Statuspage or Instatus feed.
    assert _read_label(INSTATUS, lambda _r: httpx.Response(200, content=_padded(266_919))) == "too_large"
    assert FEED_KINDS["statuspage"][0] == FEED_KINDS["instatus"][0] == 32_768


def test_an_unlisted_feed_kind_fails_instead_of_being_parsed_as_statuspage():
    odd = Source("Example Status", "https://status.example.com/", "https://status.example.com/api/v2/status.json", "rss")
    assert _read_label(odd, lambda _r: httpx.Response(200, json={"status": {"indicator": "none"}})) == "error"
