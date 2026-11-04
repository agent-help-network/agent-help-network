"""D86: migration 036 moves stored first home-page refusals that still wait the old 7 days onto the D83 schedule.

Runs the migration file's SQL against rows seeded in a dedicated test database; nothing here contacts a host."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import site_probe  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402

MIGRATION = Path(__file__).resolve().parent.parent / "migrations" / "036_site_probe_first_refusal_backfill.sql"
SQL = MIGRATION.read_text(encoding="utf-8")
DAY = timedelta(days=1)


# The shared fixture of the D67 tests: resets the database to the state the check expects (migration 036 included).
from test_site_probe_db import Home, db, put_set, read_profile  # noqa: E402,F401


def seed(settings, host, outcome, *, refusals, probed_ago, due_in=None, due_after=None, status=None):
    """One site_probe row. next_due_at is now + due_in, or probed_at + due_after."""
    now = datetime.now(timezone.utc)
    probed = now - probed_ago
    due = now + due_in if due_in is not None else probed + due_after
    with connect(settings) as conn:
        conn.execute(
            """INSERT INTO site_probe (site_host,read_host,probed_at,outcome,http_status,fingerprint_version,next_due_at,
               consecutive_refusals,trigger_kind) VALUES (%s,%s,%s,%s,%s,'t',%s,%s,'set_a')""",
            (host, host, probed, outcome, status, due, refusals))
    return probed


def table(settings):
    with connect(settings) as conn:
        return {r["site_host"]: r for r in conn.execute("SELECT site_host,probed_at,next_due_at FROM site_probe")}


def run_migration(settings):
    with connect(settings) as conn:
        conn.execute(SQL)


def test_old_first_refusals_move_to_24_hours_after_the_check_or_to_now(db):
    fresh = seed(db, "fresh-bot.example", "bot_challenge", refusals=1, probed_ago=timedelta(hours=3), due_after=7 * DAY, status=403)
    old = seed(db, "old-403.example", "refused", refusals=1, probed_ago=timedelta(hours=30), due_after=7 * DAY, status=403)
    long_ago = seed(db, "long-ago.example", "bot_challenge", refusals=1, probed_ago=timedelta(days=6), due_after=7 * DAY)
    before = datetime.now(timezone.utc)
    run_migration(db)
    rows = table(db)
    assert rows["fresh-bot.example"]["next_due_at"] == fresh + timedelta(hours=24)       # probed_at + 24 h
    for host in ("old-403.example", "long-ago.example"):                                   # 24 h already over: due now
        assert before <= rows[host]["next_due_at"] <= datetime.now(timezone.utc) + timedelta(seconds=5)
    assert old < before and long_ago < before


def test_rows_outside_the_d83_rule_are_not_touched(db):
    seed(db, "second-refusal.example", "refused", refusals=2, probed_ago=timedelta(hours=3), due_after=7 * DAY)
    seed(db, "login-wall.example", "login_wall", refusals=1, probed_ago=timedelta(hours=3), due_after=7 * DAY)
    seed(db, "rate-limited.example", "rate_limited", refusals=1, probed_ago=timedelta(hours=3), due_after=7 * DAY, status=429)
    seed(db, "unavailable.example", "server_error", refusals=1, probed_ago=timedelta(hours=3), due_after=7 * DAY, status=503)
    seed(db, "reachable.example", "reachable", refusals=0, probed_ago=timedelta(hours=3), due_after=timedelta(hours=24))
    seed(db, "already-due.example", "refused", refusals=1, probed_ago=timedelta(days=8), due_after=7 * DAY)
    seed(db, "d83-row.example", "bot_challenge", refusals=1, probed_ago=timedelta(hours=3), due_after=timedelta(hours=24))
    seed(db, "d83-retry-after.example", "refused", refusals=1, probed_ago=timedelta(hours=3), due_after=timedelta(hours=60))
    snapshot = table(db)
    run_migration(db)
    assert table(db) == snapshot


def test_the_backfill_never_makes_a_due_time_later_and_running_it_twice_changes_nothing(db):
    seed(db, "a.example", "bot_challenge", refusals=1, probed_ago=timedelta(hours=2), due_after=7 * DAY)
    seed(db, "b.example", "refused", refusals=1, probed_ago=timedelta(hours=40), due_after=7 * DAY)
    before = table(db)
    run_migration(db)
    once = table(db)
    assert all(once[h]["next_due_at"] <= before[h]["next_due_at"] for h in before)
    run_migration(db)
    assert table(db) == once


def test_a_backfilled_row_gets_the_time_the_d83_rule_gives_a_new_first_refusal(db):
    # No Retry-After is stored, so the comparison is with a first refusal without one.
    probed = seed(db, "same.example", "bot_challenge", refusals=1, probed_ago=timedelta(hours=1), due_after=7 * DAY)
    run_migration(db)
    wait, refusals, _failures = site_probe.schedule("bot_challenge", None, None, 403)
    assert refusals == 1 and table(db)["same.example"]["next_due_at"] == probed + wait


def test_the_migration_is_numbered_and_applies_once(db):
    with connect(db) as conn:
        assert conn.execute("SELECT 1 FROM schema_migrations WHERE version=36").fetchone() is not None


def run_with_notices(settings):
    notices = []
    with connect(settings) as conn:
        conn.add_notice_handler(lambda diag: notices.append(diag.message_primary))
        conn.execute(SQL)
    return notices


def seed_many(settings, count):
    for n in range(count):
        seed(settings, f"bulk-{n}.example", "refused", refusals=1, probed_ago=timedelta(hours=2), due_after=7 * DAY)


def test_gate_50_rows_are_applied(db):
    seed_many(db, 50)
    notices = run_with_notices(db)
    assert any("50 target rows" in m and "applied" in m and "not applied" not in m for m in notices)
    assert all(r["next_due_at"] - r["probed_at"] == timedelta(hours=24) for r in table(db).values())
    assert run_with_notices(db) and all(r["next_due_at"] - r["probed_at"] == timedelta(hours=24) for r in table(db).values())  # idempotent


def test_gate_51_rows_change_nothing_and_say_so(db):
    seed_many(db, 51)
    snapshot = table(db)
    notices = run_with_notices(db)
    assert any("51 target rows" in m and "not applied" in m for m in notices)
    assert table(db) == snapshot
    assert run_with_notices(db) and table(db) == snapshot                     # still nothing on a second run


def test_old_and_d83_rows_are_told_apart_by_the_real_schedule_values_with_the_switch_off_and_on(db, monkeypatch):
    monkeypatch.setenv("AHN_REFUSAL_CONFIRM", "0")
    old_wait, _r, _f = site_probe.schedule("bot_challenge", None, None, 403)       # what the old rule wrote
    monkeypatch.setenv("AHN_REFUSAL_CONFIRM", "1")
    new_wait, _r, _f = site_probe.schedule("bot_challenge", None, None, 403)       # what D83 writes
    monkeypatch.setenv("AHN_REFUSAL_CONFIRM", "1")
    capped_wait, _r, _f = site_probe.schedule("refused", None, 2 * 86400, 403)     # D83 with a Retry-After of two days
    probed = seed(db, "old.example", "bot_challenge", refusals=1, probed_ago=timedelta(hours=2), due_after=old_wait)
    seed(db, "d83.example", "bot_challenge", refusals=1, probed_ago=timedelta(hours=2), due_after=new_wait)
    seed(db, "d83-ra.example", "refused", refusals=1, probed_ago=timedelta(hours=2), due_after=capped_wait)
    assert old_wait == timedelta(days=7) and new_wait == timedelta(hours=24) and capped_wait == timedelta(days=2)
    run_migration(db)
    rows = table(db)
    assert rows["old.example"]["next_due_at"] == probed + timedelta(hours=24)
    assert rows["d83.example"]["next_due_at"] - rows["d83.example"]["probed_at"] == new_wait
    assert rows["d83-ra.example"]["next_due_at"] - rows["d83-ra.example"]["probed_at"] == capped_wait


def test_the_migrate_path_applies_036_to_existing_rows_in_a_non_utc_session(db):
    seed(db, "old.example", "refused", refusals=1, probed_ago=timedelta(hours=2), due_after=7 * DAY)
    with connect(db) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version=36")
    assert 36 in migrate(db)
    row = table(db)["old.example"]
    assert row["next_due_at"] - row["probed_at"] == timedelta(hours=24)
    with connect(db) as conn:
        conn.execute("DELETE FROM schema_migrations WHERE version=36")
        conn.execute("SET TIME ZONE 'America/New_York'")
        conn.execute("UPDATE site_probe SET next_due_at = probed_at + interval '168 hours'")
        conn.execute(SQL)                                     # the hour-based comparison is the same in any session zone
    row = table(db)["old.example"]
    assert row["next_due_at"] - row["probed_at"] == timedelta(hours=24)


def test_the_cron_checks_a_backfilled_host_that_is_on_the_list(db, monkeypatch):
    host = "backfilled-example.org"
    read_profile(db, monkeypatch, host=host)
    seed(db, host, "refused", refusals=1, probed_ago=timedelta(hours=30), due_after=7 * DAY, status=403)
    put_set(db, [host])
    home = Home()
    assert site_probe.run_cron(db, now=datetime.now(timezone.utc), reader=home)["done"] == 0 and home.calls == []   # before: not due
    run_migration(db)
    result = site_probe.run_cron(db, now=datetime.now(timezone.utc) + timedelta(minutes=1), reader=home)
    assert result["done"] == 1 and home.calls == [host]
