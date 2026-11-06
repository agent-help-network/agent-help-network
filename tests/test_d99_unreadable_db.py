"""D99 against the ahn_test database: the unreadable-robots shape through service.check. Only ahn_check differs between the switch on
and off; the rest of the answer is the same (D67: the AHN check is information only)."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

if not os.environ.get("AHN_TEST_DATABASE_URL", ""):
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import background_reads, service  # noqa: E402
from ahn.db import connect  # noqa: E402
from test_site_probe_db import HOST, Profiles, db  # noqa: E402,F401


def failed_read_host(db, monkeypatch):
    """First lookup queues the read; the read fails (error); returns the settings."""
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles(status="error"))
    first = service.check(db, HOST)
    assert first["ahn_check"]["status"] == "pending"                 # nothing read yet: still the first-lookup wait
    assert background_reads.process_due(db, limit=5)["done"] >= 1
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads")                # no read queued now (the wait before a retry is not due)
        conn.execute("DELETE FROM site_probe")


def lookup(db, switch):
    from dataclasses import replace
    return service.check(replace(db, d99_unreadable_not_pending=switch), HOST, error_class="service_error",
                         failure_stage="before_target_response")


def test_unreadable_host_not_measured_and_rest_of_answer_unchanged(db, monkeypatch):
    failed_read_host(db, monkeypatch)
    on = lookup(db, True)
    assert on["access_profile"]["robots_status"] == "error" and "retry_after_seconds" not in on["access_profile"]
    assert on["ahn_check"]["status"] == "not_measured" and on["ahn_check"]["not_measured_reason"] == "robots_unreadable"
    assert "retry_after_seconds" not in on["ahn_check"]
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads")
        conn.execute("DELETE FROM site_probe")
    monkeypatch.setenv("AHN_D103_FAILED_PROFILE_NOT_PENDING", "0")   # D103 also covers this host; off for the earlier answer
    off = lookup(db, False)
    assert off["ahn_check"]["status"] == "pending" and off["ahn_check"]["retry_after_seconds"] == 60
    drop = ("generated_at", "ahn_check", "lookup_activity", "feedback_token")
    assert {k: v for k, v in on.items() if k not in drop} == {k: v for k, v in off.items() if k not in drop}
    assert on["recommendation"] == off["recommendation"] and on["summary_line"] == off["summary_line"]
    assert on["next_action"] == off["next_action"] and on.get("cause") == off.get("cause")


def test_no_row_is_written_for_the_not_measured_view(db, monkeypatch):
    failed_read_host(db, monkeypatch)
    lookup(db, True)
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM site_probe").fetchone()["n"] == 0
