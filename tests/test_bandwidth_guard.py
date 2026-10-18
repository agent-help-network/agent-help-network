"""Pure bandwidth-guard tests: no database, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from ahn.bandwidth_guard import _threshold_env, parse_bandwidth, run_guard


RESOURCE = "srv-db0hnkfavr4c73fp6et0"
NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def series(value, unit="GB", stamp=NOW, resource=RESOURCE):
    return [{"labels": [{"field": "service", "value": resource}],
             "values": [{"timestamp": stamp.isoformat(), "value": value}], "unit": unit}]


def parse(data):
    return parse_bandwidth(data, RESOURCE, NOW - timedelta(hours=1), NOW)


@pytest.mark.parametrize("unit,value,expected", [
    ("GB", 1.5, Decimal("1.5")),
    ("gb", "2", Decimal("2")),
    (" Gb ", 3, Decimal("3")),
    ("MB", 1500, Decimal("1.5")),
    ("mb", 1500, Decimal("1.5")),
    ("kb", 2_000_000, Decimal("2")),
    ("bytes", 5_000_000_000, Decimal("5")),
    ("B", 1_000_000_000, Decimal("1")),
    ("TB", "0.001", Decimal("1")),
])
def test_parse_bandwidth_scales_byte_units_to_gb(unit, value, expected):
    assert parse(series(value, unit)) == [(NOW, expected)]


@pytest.mark.parametrize("unit", ["GiB", "", None, 7, "mbps", "GB/s"])
def test_parse_bandwidth_rejects_unknown_units_without_default(unit):
    with pytest.raises(ValueError, match="bandwidth unit invalid"):
        parse(series(1, unit))


def test_parse_bandwidth_unit_diagnostic_is_truncated_label_only():
    with pytest.raises(ValueError) as error:
        parse(series(1, "x" * 80))
    message = str(error.value)
    assert "'" + "x" * 24 + "'" in message and "x" * 25 not in message


@pytest.mark.parametrize("value", ["abc", None, True, "NaN", "sNaN", "Infinity", "-Infinity", "-1", object(), 1e308])
def test_parse_bandwidth_rejects_non_numeric_or_unbounded_values(value):
    with pytest.raises(ValueError, match="bandwidth (point|amount) invalid"):
        parse(series(value))


def test_parse_bandwidth_rejects_amount_above_cap_after_scaling():
    assert parse(series("1e9")) == [(NOW, Decimal("1e9"))]
    assert parse(series("1e6", "TB")) == [(NOW, Decimal("1e9"))]
    with pytest.raises(ValueError, match="bandwidth amount invalid"):
        parse(series("1e13", "MB"))
    with pytest.raises(ValueError, match="bandwidth amount invalid"):
        parse(series("1000000001", "GB"))


def test_parse_bandwidth_rejects_missing_or_malformed_point_fields():
    good = series(1)
    for point in ({"value": 1}, {"timestamp": NOW.isoformat()}, {"timestamp": 5, "value": 1},
                  {"timestamp": None, "value": 1}, {"timestamp": "2026-10-02T12:00:00", "value": 1}):
        good[0]["values"] = [point]
        with pytest.raises(ValueError):
            parse(good)


def test_parse_bandwidth_keeps_only_the_configured_window_and_service():
    other = series(9, resource="srv-other")[0]
    inside = {"timestamp": (NOW - timedelta(minutes=30)).isoformat(), "value": 2}
    edge = {"timestamp": (NOW - timedelta(hours=1)).isoformat(), "value": 7}
    data = [other, {"labels": [{"field": "resource", "value": RESOURCE}], "unit": "GB",
                    "values": [inside, edge, {"timestamp": NOW.isoformat(), "value": 1}]}]
    assert parse(data) == [(NOW - timedelta(minutes=30), Decimal("2")), (NOW, Decimal("1"))]
    with pytest.raises(ValueError, match="one matching"):
        parse([other])
    with pytest.raises(ValueError, match="duplicate"):
        parse(series(1) + series(1))


def test_threshold_env_defaults_when_unset_or_blank(monkeypatch):
    monkeypatch.delenv("AHN_GUARD_WARNING_GB", raising=False)
    assert _threshold_env("AHN_GUARD_WARNING_GB", Decimal("10")) == Decimal("10")
    monkeypatch.setenv("AHN_GUARD_WARNING_GB", "  \t ")
    assert _threshold_env("AHN_GUARD_WARNING_GB", Decimal("10")) == Decimal("10")
    monkeypatch.setenv("AHN_GUARD_WARNING_GB", " 12.5 ")
    assert _threshold_env("AHN_GUARD_WARNING_GB", Decimal("10")) == Decimal("12.5")


@pytest.mark.parametrize("raw", ["ten", "10GB", "1,5", "Infinity", "-Infinity", "NaN", "sNaN", "0", "-3"])
def test_threshold_env_rejects_invalid_or_non_finite_values(monkeypatch, raw):
    monkeypatch.setenv("AHN_GUARD_STOP_GB", raw)
    with pytest.raises(RuntimeError, match="AHN_GUARD_STOP_GB"):
        _threshold_env("AHN_GUARD_STOP_GB", Decimal("20"))


@pytest.mark.parametrize("warning,stop", [
    (Decimal("10"), Decimal("10")), (Decimal("0"), Decimal("20")), (Decimal("Infinity"), Decimal("20")),
    (Decimal("10"), Decimal("Infinity")), (Decimal("NaN"), Decimal("20")), (Decimal("10"), Decimal("NaN")),
])
def test_run_guard_rejects_non_finite_or_inverted_thresholds(warning, stop):
    with pytest.raises(ValueError, match="invalid bandwidth thresholds"):
        run_guard("postgresql://unused", "fake-test-key", RESOURCE, now=NOW, warning_gb=warning, stop_gb=stop)
