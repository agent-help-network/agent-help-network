"""D103 against the ahn_test database: a host whose profile read keeps failing leaves a not_measured robots_unreadable row
instead of deferring for a robots.txt verdict that no read is going to produce. Every request goes through a stub."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("AHN_TEST_DATABASE_URL", ""):
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import service, site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402
from test_site_probe_db import HOST, Home, db, measure, read_profile, rows  # noqa: E402,F401


def now_utc():
    return datetime.now(timezone.utc)


def succeeded_then_failing(db, monkeypatch):
    """One good read 25 hours ago (its home_gate is stale now), then a read that fails."""
    read_profile(db, monkeypatch, now=now_utc() - timedelta(hours=25))
    read_profile(db, monkeypatch, status="error")


def test_a_read_that_succeeded_once_and_fails_since_records_robots_unreadable(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    home = Home()
    stored = measure(db, home=home)
    assert home.calls == []
    assert stored["outcome"] == "not_measured" and stored["not_measured_reason"] == "robots_unreadable"
    assert [r["not_measured_reason"] for r in rows(db, "SELECT not_measured_reason FROM site_probe")] == ["robots_unreadable"]


def test_a_host_never_read_successfully_records_robots_unreadable(db, monkeypatch):
    read_profile(db, monkeypatch, status="error")
    profile, = rows(db, "SELECT fetched_at,home_gate,last_error_at FROM host_access_profile")
    assert profile["fetched_at"] is None and profile["home_gate"] is None and profile["last_error_at"] is not None
    home = Home()
    stored = measure(db, home=home)
    assert home.calls == [] and stored["not_measured_reason"] == "robots_unreadable"


def test_a_profile_that_is_only_old_still_defers(db, monkeypatch):
    read_profile(db, monkeypatch, now=now_utc() - timedelta(hours=25))   # no failure: the next read will refresh it
    home = Home()
    assert measure(db, home=home) is None and home.calls == []
    assert rows(db, "SELECT * FROM site_probe") == []


def test_a_host_without_a_profile_row_still_defers(db, monkeypatch):
    assert measure(db, home=Home()) is None
    assert rows(db, "SELECT * FROM site_probe") == []


def test_the_same_reason_under_its_backoff_is_skipped(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    first = measure(db)
    assert first is not None
    with connect(db) as conn:
        verdict, why, _ctx = site_probe.decide(conn, HOST, "lookup", now_utc())
    assert (verdict, why) == ("skip", "backoff")
    assert measure(db) is None
    again, = rows(db, "SELECT probed_at FROM site_probe")
    assert again["probed_at"] == first["probed_at"]


def test_switch_off_restores_the_deferral(db, monkeypatch):
    monkeypatch.setenv("AHN_D103_FAILED_PROFILE_NOT_PENDING", "0")
    succeeded_then_failing(db, monkeypatch)
    home = Home()
    assert measure(db, home=home) is None and home.calls == []
    assert rows(db, "SELECT * FROM site_probe") == []
    with connect(db) as conn:
        assert site_probe.decide(conn, HOST, "lookup", now_utc())[:2] == ("defer", "robots_stale")


def test_a_later_good_read_measures_again(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    assert measure(db)["not_measured_reason"] == "robots_unreadable"
    later = now_utc() + timedelta(hours=2)                                # past the retry window after a failed read
    read_profile(db, monkeypatch, now=later)
    home = Home()
    stored = measure(db, home=home, now=later)
    assert home.calls == [HOST] and stored["outcome"] == "reachable"


def test_the_lookup_answer_shows_not_measured_not_pending(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    assert measure(db)["not_measured_reason"] == "robots_unreadable"
    shown = service.check(db, HOST)["ahn_check"]
    assert shown["status"] == "not_measured" and shown["not_measured_reason"] == "robots_unreadable"
    assert "retry_after_seconds" not in shown
