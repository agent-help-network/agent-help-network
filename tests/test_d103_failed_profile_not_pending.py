"""D103 against the ahn_test database: a lookup of a host with no stored check whose profile read keeps failing (no robots.txt
verdict newer than 24 hours) shows ahn_check not_measured / robots_unreadable instead of an open-ended pending. The view is
built in memory: nothing is stored, the profile re-read stays queued, a stored check is never replaced. Every request goes
through a stub."""

from __future__ import annotations

import os
from dataclasses import replace
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


def lookup(db, **kw):
    # D99 off, so only D103 can turn the pending view into not_measured
    return service.check(replace(db, d99_unreadable_not_pending=False), HOST, **kw)


def test_a_read_that_succeeded_once_and_fails_since_is_not_pending(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    shown = lookup(db)["ahn_check"]
    assert shown["status"] == "not_measured" and shown["not_measured_reason"] == "robots_unreadable"
    assert "retry_after_seconds" not in shown


def test_a_host_never_read_successfully_is_not_pending(db, monkeypatch):
    read_profile(db, monkeypatch, status="error")
    shown = lookup(db)["ahn_check"]
    assert shown["status"] == "not_measured" and shown["not_measured_reason"] == "robots_unreadable"


def test_a_profile_that_is_only_old_stays_pending(db, monkeypatch):
    read_profile(db, monkeypatch, now=now_utc() - timedelta(hours=25))   # no failure: the next read refreshes it
    assert lookup(db)["ahn_check"]["status"] == "pending"


def test_a_host_without_a_profile_row_stays_pending(db, monkeypatch):
    assert lookup(db)["ahn_check"]["status"] == "pending"


def test_a_failed_read_behind_a_fresh_gate_stays_pending(db, monkeypatch):
    read_profile(db, monkeypatch)                                         # fresh gate
    with connect(db) as conn:
        conn.execute("UPDATE host_access_profile SET last_error_at=now()")
    assert lookup(db)["ahn_check"]["status"] == "pending"


def test_nothing_is_stored_and_the_profile_reread_stays_queued(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    lookup(db)
    assert rows(db, "SELECT * FROM site_probe") == []
    assert ("site_probe", HOST) in {(r["kind"], r["site_host"]) for r in rows(db, "SELECT kind,site_host FROM background_reads")}


def test_switch_off_restores_pending(db, monkeypatch):
    monkeypatch.setenv("AHN_D103_FAILED_PROFILE_NOT_PENDING", "0")
    succeeded_then_failing(db, monkeypatch)
    shown = lookup(db)["ahn_check"]
    assert shown["status"] == "pending" and shown["retry_after_seconds"] == 60


def seed_confirmed_refusal(db, probed_at):
    """A stored bot_challenge (DataDome, two refusals) whose backoff has run out."""
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO site_probe (site_host,read_host,probed_at,outcome,http_status,bot_vendor,bot_signal,bot_confidence,
                   edge_vendors,redirect,login_signal,response_ms,bytes_read,not_measured_reason,fingerprint_version,next_due_at,
                   consecutive_refusals,consecutive_failures,trigger_kind,origin_operator,vantage,user_agent,refusal_counted_at)
               VALUES (%s,%s,%s,'bot_challenge',403,'datadome','cookie','high','{}',NULL,NULL,90,100,NULL,%s,%s,2,0,'set_C',
                       'op','{"kind":"datacenter","region":"x"}','ua',%s)""",
            (HOST, HOST, probed_at, site_probe.FINGERPRINT_VERSION, now_utc() - timedelta(minutes=5), probed_at))


def test_a_stored_check_is_never_replaced_and_the_judgement_is_unchanged(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    seed_confirmed_refusal(db, now_utc() - timedelta(days=7, hours=2))
    before = rows(db, "SELECT * FROM site_probe")
    on = lookup(db, error_class="site_access")
    assert rows(db, "SELECT * FROM site_probe") == before                 # row kept: vendor and refusal count intact
    assert on["ahn_check"]["status"] == "measured" and on["ahn_check"]["outcome"] == "bot_challenge"
    monkeypatch.setenv("AHN_D103_FAILED_PROFILE_NOT_PENDING", "0")
    off = lookup(db, error_class="site_access")
    drop = ("generated_at", "lookup_activity", "feedback_token")
    assert {k: v for k, v in on.items() if k not in drop} == {k: v for k, v in off.items() if k not in drop}


def test_the_cron_check_keeps_the_profile_reread_queued(db, monkeypatch):
    read_profile(db, monkeypatch, now=now_utc() - timedelta(hours=27))
    read_profile(db, monkeypatch, status="error", now=now_utc() - timedelta(hours=2))
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads")
    assert measure(db, trigger="set_C") is None                           # defers, as before D103
    assert "access_profile" in {r["kind"] for r in rows(db, "SELECT kind FROM background_reads")}
    assert rows(db, "SELECT * FROM site_probe") == []


def test_a_recovered_profile_is_measured_at_once(db, monkeypatch):
    succeeded_then_failing(db, monkeypatch)
    assert lookup(db)["ahn_check"]["status"] == "not_measured"
    later = now_utc() + timedelta(hours=2)                                # past the retry window after a failed read
    read_profile(db, monkeypatch, now=later)
    home = Home()
    stored = measure(db, home=home, now=later)
    assert home.calls == [HOST] and stored["outcome"] == "reachable"
    assert lookup(db)["ahn_check"]["status"] == "measured"
