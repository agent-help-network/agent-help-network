"""D111: a lookup marked as a test (D106: X-AHN-Test: 1, ahn_test=1, the MCP test argument) gets the same answer, and the home-page check it
runs or queues is recorded with trigger_kind 'test'; the counts of what lookups started leave it out.

Against the dedicated ahn_test database (migration 041). Every request goes through a stub: nothing here contacts a real host."""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from test_site_probe_db import HOST, Home, db, rows  # noqa: E402,F401
from test_site_probe_db import warm as _warm  # noqa: E402

from ahn import background_reads, service, site_probe  # noqa: E402
from ahn.db import connect  # noqa: E402


def warm(db, monkeypatch):
    """The host's profile read, with no check and no daily count left over from reading it."""
    _warm(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe_daily")


def busy(monkeypatch):
    """The lookup cannot start the check on its own thread, so it queues it (the path a slow or crowded process takes)."""
    monkeypatch.setattr(site_probe, "sync_measure", lambda *a, **k: (None, "busy"))


def test_migration_041_adds_one_nullable_column_with_no_default(db):
    column, = rows(db, "SELECT data_type,is_nullable,column_default,character_maximum_length FROM information_schema.columns "
                       "WHERE table_name='background_reads' AND column_name='trigger_kind'")
    assert (column["data_type"], column["is_nullable"], column["column_default"]) == ("character varying", "YES", None)


def test_a_marked_lookup_that_measures_on_the_spot_records_a_test_check_and_no_lookup_check(db, monkeypatch):
    warm(db, monkeypatch)
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    result = service.check(db, HOST, test_marked=True)
    assert result["ahn_check"]["status"] == "measured"
    assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe")] == ["test"]
    assert [(r["trigger_kind"], r["probes"]) for r in rows(db, "SELECT trigger_kind,probes FROM site_probe_daily")] == [("test", 1)]


def test_an_unmarked_lookup_still_records_a_lookup_check(db, monkeypatch):
    warm(db, monkeypatch)
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    service.check(db, HOST)
    assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe")] == ["lookup"]
    assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe_daily")] == ["lookup"]


def test_the_answer_is_the_same_with_and_without_the_mark(db, monkeypatch):
    seen = []
    busy(monkeypatch)
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    for marked in (False, True):
        warm(db, monkeypatch)
        result = service.check(db, HOST, test_marked=marked)
        seen.append({k: result[k] for k in ("recommendation", "next_checks", "summary_line", "next_action", "ahn_check") if k in result})
    assert seen[0]["ahn_check"]["status"] == "pending"
    assert seen[0] == seen[1]


def test_a_queued_check_of_a_marked_lookup_carries_the_mark_to_the_worker(db, monkeypatch):
    warm(db, monkeypatch)
    busy(monkeypatch)
    home = Home()
    monkeypatch.setattr(site_probe, "fetch_home", home)
    assert service.check(db, HOST, test_marked=True)["ahn_check"]["status"] == "pending"
    job, = rows(db, "SELECT trigger_kind FROM background_reads WHERE kind='site_probe'")
    assert job["trigger_kind"] == "test"
    background_reads.process_due(db)
    assert home.calls == [HOST]
    assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe")] == ["test"]
    assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe_daily")] == ["test"]
    assert rows(db, "SELECT * FROM background_reads") == []


def test_a_queued_check_of_an_unmarked_lookup_is_a_lookup_check_and_a_real_lookup_takes_over_a_marked_row(db, monkeypatch):
    warm(db, monkeypatch)
    busy(monkeypatch)
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    service.check(db, HOST, test_marked=True)
    service.check(db, HOST)                                      # a real lookup: the row is no longer a test's
    job, = rows(db, "SELECT trigger_kind FROM background_reads WHERE kind='site_probe'")
    assert job["trigger_kind"] is None
    background_reads.process_due(db)
    assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe")] == ["lookup"]
    service.check(db, HOST, test_marked=True)                    # a marked lookup does not mark a row that is waiting for a real one
    service.check(db, HOST)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
        conn.execute("DELETE FROM background_reads")
    service.check(db, HOST)
    service.check(db, HOST, test_marked=True)
    job, = rows(db, "SELECT trigger_kind FROM background_reads WHERE kind='site_probe'")
    assert job["trigger_kind"] is None


def test_the_lookup_caps_do_not_count_test_checks_and_a_test_is_held_to_the_same_cap_by_its_own_count(db):
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        conn.execute("INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes) "
                     "VALUES (CURRENT_DATE,'x-example.org','reachable','','test',%s)", (site_probe.LOOKUP_DAILY_CAP,))
        assert site_probe.caps_ok(conn, "lookup", now) is None            # 300 test checks leave the lookup cap untouched
        assert site_probe.caps_ok(conn, "test", now) == "budget"          # a test has its own count against the same cap
        conn.execute("DELETE FROM site_probe_daily")
        conn.execute("INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes) "
                     "VALUES (CURRENT_DATE,'x-example.org','reachable','','lookup',%s)", (site_probe.LOOKUP_DAILY_CAP,))
        assert site_probe.caps_ok(conn, "lookup", now) == "budget"
        assert site_probe.caps_ok(conn, "test", now) is None


def test_a_missing_column_leaves_a_queued_check_a_lookups(db, monkeypatch):
    """The code runs before the migration is applied on a deploy: the worker must still run the check, as a lookup's."""
    warm(db, monkeypatch)
    home = Home()
    monkeypatch.setattr(site_probe, "fetch_home", home)
    with connect(db) as conn:
        background_reads.enqueue(conn, [("site_probe", HOST)])
        conn.execute("ALTER TABLE background_reads DROP COLUMN trigger_kind")
    try:
        background_reads.process_due(db)
        assert home.calls == [HOST]
        assert [r["trigger_kind"] for r in rows(db, "SELECT trigger_kind FROM site_probe")] == ["lookup"]
    finally:
        with connect(db) as conn:
            conn.execute("ALTER TABLE background_reads ADD COLUMN IF NOT EXISTS trigger_kind varchar(8)")
