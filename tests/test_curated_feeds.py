"""Schedule and backoff of the status-feed reads (D44): pure, no database, no network."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ahn import background_reads, status_refresh
from ahn.background_reads import FEED_BACKOFF_MAX, feed_backoff, feed_next_due

T = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


def row(**values):
    return {"indicator": None, "fetched_at": None, "last_error_at": None, "failures": None, "last_error_detail": None, **values}


def test_a_feed_never_attempted_is_due_now():
    assert feed_next_due(row(), HOUR) is None


@pytest.mark.parametrize("indicator,after", [("none", HOUR), ("unknown", HOUR), ("minor", timedelta(minutes=15)),
                                             ("maintenance", timedelta(minutes=15)), ("critical", timedelta(minutes=15))])
def test_a_successful_read_is_due_after_an_hour_or_fifteen_minutes_while_active(indicator, after):
    assert feed_next_due(row(indicator=indicator, fetched_at=T), HOUR) == T + after
    # The lookup path uses the same rule with its own idle interval (two hours).
    lookup_after = after if indicator not in ("none", "unknown") else timedelta(hours=2)
    assert feed_next_due(row(indicator=indicator, fetched_at=T), timedelta(hours=background_reads.FEED_STALE_HOURS)) == T + lookup_after


@pytest.mark.parametrize("failures,minutes", [(None, 15), (1, 15), (2, 30), (3, 60), (4, 120), (7, 960), (8, 1440), (50, 1440)])
def test_failures_back_off_exponentially_up_to_a_day(failures, minutes):
    assert feed_backoff(failures, "http_502") == timedelta(minutes=minutes)
    assert feed_next_due(row(last_error_at=T, failures=failures, last_error_detail="timeout"), HOUR) == T + timedelta(minutes=minutes)


@pytest.mark.parametrize("label", ["http_401", "http_403", "http_429"])
def test_a_refusal_starts_at_an_hour_and_also_stops_at_a_day(label):
    assert feed_backoff(1, label) == HOUR and feed_backoff(2, label) == 2 * HOUR
    assert feed_backoff(30, label) == FEED_BACKOFF_MAX == timedelta(days=1)


def test_a_failure_after_a_success_waits_for_both_rules():
    # A value read at T, a failure at T+50 min: the hour since the success and the backoff since the failure must pass.
    failing = row(indicator="none", fetched_at=T, last_error_at=T + timedelta(minutes=50), failures=1, last_error_detail="timeout")
    assert feed_next_due(failing, HOUR) == T + timedelta(minutes=65)
    assert feed_next_due({**failing, "failures": 3}, HOUR) == T + timedelta(minutes=110)


def test_batch_limits_fit_the_cron_and_the_per_host_rate():
    # 580 distinct feeds hourly need about 49 reads per 5-minute run; one run may read up to FEED_BATCH_MAX.
    distinct = len({source.api_url for source in status_refresh.SOURCES.values()})
    assert distinct <= 12 * status_refresh.FEED_BATCH_MAX * 0.7
    assert status_refresh.FEED_BATCH_SECONDS <= 60 and status_refresh.CRON_IDLE == HOUR
    assert background_reads.MIN_READ_INTERVAL >= 0.5 and status_refresh.SCOPE_GAP_SECONDS >= 1.0
    with pytest.raises(ValueError):
        status_refresh.refresh_batch(None, limit=status_refresh.FEED_BATCH_MAX + 1)
