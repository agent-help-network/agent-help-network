"""Pure tests for the status feed parsers: no database, no network."""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from ahn import status_refresh
from ahn.status_refresh import Source, parse_status
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
