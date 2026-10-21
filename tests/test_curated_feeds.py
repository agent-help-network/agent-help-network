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


@pytest.mark.parametrize("failures,minutes", [(None, 15), (1, 15), (2, 30), (3, 60), (4, 60), (8, 60), (50, 60)])
@pytest.mark.parametrize("label", ["http_502", "http_503", "timeout", "too_large", "connect_error", "invalid_body"])
def test_other_failures_back_off_but_never_past_an_hour(failures, minutes, label):
    # A status page failing during the outage it should report must not drop out: an hour is below the two hours
    # after which the statement reads as stale (and stops advising a wait).
    assert feed_backoff(failures, label) == timedelta(minutes=minutes)
    assert background_reads.FEED_FAILURE_BACKOFF_MAX < timedelta(hours=background_reads.FEED_STALE_HOURS)
    assert feed_next_due(row(last_error_at=T, failures=failures, last_error_detail=label), HOUR) == T + timedelta(minutes=minutes)


@pytest.mark.parametrize("indicator", ["minor", "major", "critical", "maintenance"])
def test_a_feed_whose_last_statement_was_active_keeps_fifteen_minutes_while_failing(indicator):
    failing = row(indicator=indicator, fetched_at=T, last_error_at=T + timedelta(minutes=15), failures=6, last_error_detail="timeout")
    assert feed_next_due(failing, HOUR) == T + timedelta(minutes=30)
    # A refusal still backs off, active or not.
    assert feed_next_due({**failing, "last_error_detail": "http_429"}, HOUR) == T + timedelta(minutes=15) + timedelta(hours=24)


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


# --- freshness of the last statement (D46) -------------------------------------------------------------

@pytest.mark.parametrize("label", ["timeout", "http_502", "too_large", "http_403", "http_429", "http_401"])
def test_a_failure_after_a_recent_success_keeps_the_statement_fresh(label):
    # A status page that times out once during the outage it reports keeps its last statement: freshness is the
    # age of that statement, and the failure shows as error_detail (failed_later). A refusal is the same: it says
    # nothing about the provider's state, and its own backoff starts at an hour, inside FEED_STALE_HOURS.
    now = T + timedelta(minutes=70)
    assert background_reads.feed_freshness(T, T + timedelta(minutes=65), now) == ("fresh", True)
    assert background_reads.feed_backoff(1, label) <= timedelta(hours=background_reads.FEED_STALE_HOURS)


def test_freshness_is_the_age_of_the_last_success():
    stale_after = timedelta(hours=background_reads.FEED_STALE_HOURS)
    fresh = background_reads.feed_freshness
    assert fresh(T, None, T + stale_after) == ("fresh", False)  # exactly two hours is still fresh
    assert fresh(T, None, T + stale_after + timedelta(seconds=1)) == ("stale", False)
    assert fresh(T, T + timedelta(minutes=90), T + stale_after + timedelta(seconds=1)) == ("stale", True)
    assert fresh(T, T - timedelta(minutes=5), T + timedelta(minutes=1)) == ("fresh", False)  # failure before success
    assert fresh(None, None, T) == ("pending", False)
    assert fresh(None, T, T + timedelta(minutes=1)) == ("stale", True)  # only failures: no statement to keep


def test_the_freshness_rule_is_written_in_the_contract_and_the_decisions():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    ko = (root / "docs" / "api-contract.md").read_text(encoding="utf-8")
    en = (root / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    decisions = (root / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "the age of the last successful read" in en and "marks the previous value `stale`" not in en
    assert "마지막 성공 읽기의 나이" in ko and "이전 값을 `stale`로 표시" not in ko
    assert "read together or not at all" in en and "함께 읽거나 함께 건너뛴다" in ko
    assert "| D46 (" in decisions and "마지막 성공 읽기의 나이" in decisions


@pytest.mark.parametrize("label", ["no_cname", "id_mismatch", "not_public"])
def test_a_verification_failure_ends_the_statement_at_once(label):
    now = T + timedelta(minutes=20)
    assert background_reads.feed_freshness(T, T + timedelta(minutes=15), now, label) == ("stale", True)
    # Only as the latest attempt: a verification failure before the last success does not count.
    assert background_reads.feed_freshness(T, T - timedelta(minutes=5), now, label) == ("fresh", False)
    assert background_reads.feed_freshness(T, T + timedelta(minutes=15), now, "timeout") == ("fresh", True)


def test_the_plugin_skill_says_fresh_can_come_with_a_failure():
    from pathlib import Path
    skill = Path(__file__).resolve().parents[1] / "plugins" / "claude-code" / "ahn" / "skills" / "ahn-lookup" / "SKILL.md"
    assert "`freshness: fresh` plus `error_detail`: latest read failed" in skill.read_text(encoding="utf-8")
