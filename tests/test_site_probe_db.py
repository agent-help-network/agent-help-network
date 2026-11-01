"""D67: the AHN server check of a home page against the dedicated ahn_test database (migration 033).

Every request goes through a stub: nothing here contacts a real host."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import access_profile, background_reads, platform_metrics, service, site_probe  # noqa: E402
from ahn.access_profile import cached_profile  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.mcp_adapter import ahn_check_line  # noqa: E402
from ahn.models import AccessProfile, AhnCheck, CheckResponse  # noqa: E402
from ahn.service import PUBLIC_REPORTER_ID  # noqa: E402
from ahn.settings import Settings  # noqa: E402

ALLOW = b"User-agent: *\nAllow: /\n"
DENY_HOME = b"User-agent: *\nDisallow: /\n"
HOST = "probe-example.org"


@pytest.fixture
def db(monkeypatch):
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True,
                        collect_lookup_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, "
                     "public_intake_daily, public_intake_host_daily, status_cache, status_sources, queried_hosts, "
                     "host_access_profile, background_reads, mcp_check_metrics, site_probe, site_probe_daily, "
                     "probe_breaker_daily, site_probe_set, probe_state, bandwidth_guard_state, lookup_signals_hourly "
                     "RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
        conn.execute("INSERT INTO bandwidth_guard_state (name,period_start,checked_at,usage_gb,state) VALUES ('web',now(),now(),1,'normal')")
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    monkeypatch.setattr(service, "cached_profile", cached_profile)
    monkeypatch.setattr(site_probe, "PAUSE_SECONDS", 0.0)
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    return settings


class Profiles:
    """Stands in for the robots.txt/llms.txt reader of the background queue; robots per host."""

    def __init__(self, robots=ALLOW, status="ok"):
        self.robots, self.status, self.calls = robots, status, []

    def __call__(self, site_host, path, *, cap_bytes, **_):
        self.calls.append((site_host, path))
        if path == "/robots.txt":
            return (self.status, self.robots if self.status == "ok" else None, "http_200" if self.status == "ok" else "http_403")
        return ("absent", None, "http_404")


class Home:
    """Stands in for site_probe.fetch_home."""

    def __init__(self, status=200, headers=None, delay=0.0, body=b"<html>ok</html>", label=None):
        self.calls, self.delay, self.lock = [], delay, threading.Lock()
        self.result = {"label": label, "status": None if label else status, "headers": headers or {}, "cookies": [],
                       "body": body, "complete": True, "ms": 80, "bytes_read": len(body)}

    def __call__(self, host):
        with self.lock:
            self.calls.append(host)
        if self.delay:
            time.sleep(self.delay)
        return dict(self.result)


def rows(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def read_profile(db, monkeypatch, host=HOST, robots=ALLOW, status="ok", now=None):
    """Read the host's profile through get_profile (so home_gate is the real one) and register the host."""
    stub = Profiles(robots, status)
    return access_profile.get_profile(db, host, now=now or datetime.now(timezone.utc), reader=stub), stub


def measure(db, host=HOST, trigger="lookup", home=None, now=None, **kw):
    return site_probe.measure(db, host, trigger, now=now, reader=home or Home(), paced=False, **kw)


# --- the schema ------------------------------------------------------------------------------------------------------


def test_migration_033_creates_the_tables_the_column_and_the_queue_kind(db):
    tables = {r["table_name"] for r in rows(db, "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
    assert {"site_probe", "site_probe_daily", "probe_breaker_daily", "site_probe_set", "probe_state"} <= tables
    columns = {(r["table_name"], r["column_name"]) for r in rows(db, "SELECT table_name,column_name FROM information_schema.columns")}
    assert ("host_access_profile", "home_gate") in columns
    with connect(db) as conn:
        conn.execute("INSERT INTO background_reads (kind,site_host) VALUES ('site_probe','a-example.org')")


# --- robots gating (T-E1, T-E2, T-E5b) -------------------------------------------------------------------------------


def test_e1_a_robots_txt_that_disallows_the_home_page_means_no_request(db, monkeypatch):
    read_profile(db, monkeypatch, robots=DENY_HOME)
    home = Home()
    stored = measure(db, home=home)
    assert home.calls == [] and stored["outcome"] == "not_measured" and stored["not_measured_reason"] == "robots_disallowed"
    again = measure(db, home=home)  # nothing new to say: no second row write
    assert again is None and home.calls == []


def test_e2_a_refused_robots_txt_means_no_request(db, monkeypatch):
    read_profile(db, monkeypatch, status="refused")
    home = Home()
    stored = measure(db, home=home)
    assert home.calls == [] and stored["not_measured_reason"] == "robots_refused"


def test_e2_an_unreadable_robots_txt_leaves_no_verdict_so_no_request(db, monkeypatch):
    read_profile(db, monkeypatch, status="error")        # a failed read stores no home_gate: the check waits for a good read
    home = Home()
    assert measure(db, home=home) is None and home.calls == []


def test_e2_an_absent_robots_txt_allows_the_check(db, monkeypatch):
    read_profile(db, monkeypatch, robots=None, status="absent")
    home = Home()
    assert measure(db, home=home)["outcome"] == "reachable" and home.calls == [HOST]


def test_e5b_no_stored_verdict_or_one_over_24_hours_old_means_no_request(db, monkeypatch):
    home = Home()
    assert measure(db, home=home) is None and home.calls == []           # no profile at all
    read_profile(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("UPDATE host_access_profile SET home_gate=NULL")
    assert measure(db, home=home) is None and home.calls == []           # a profile from before D67
    read_profile(db, monkeypatch, now=datetime.now(timezone.utc) - timedelta(hours=25))
    assert measure(db, home=home) is None and home.calls == []           # a verdict from 25 hours ago


def test_e5b_a_fresh_profile_from_before_d67_is_read_again_so_the_check_does_not_wait_a_day(db, monkeypatch):
    read_profile(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("UPDATE host_access_profile SET home_gate=NULL")
        assert cached_profile(conn, HOST)[1] is True                    # the lookup path queues the read
    _profile, stub = read_profile(db, monkeypatch)                       # within the 24-hour cache window
    assert stub.calls[:1] == [(HOST, "/robots.txt")]
    assert measure(db)["outcome"] == "reachable"
    with connect(db) as conn:
        assert cached_profile(conn, HOST)[1] is False
    _profile, again = read_profile(db, monkeypatch)                      # home_gate stored: a cache hit again
    assert again.calls == []


def test_e5b_a_profile_without_home_gate_keeps_its_cache_when_the_check_is_off_or_the_read_failed(db, monkeypatch):
    read_profile(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("UPDATE host_access_profile SET home_gate=NULL")
    monkeypatch.setenv("AHN_SITE_PROBE", "0")
    _profile, stub = read_profile(db, monkeypatch)
    assert stub.calls == []
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    with connect(db) as conn:
        conn.execute("UPDATE host_access_profile SET last_error_at=now()")  # the last read failed: ERROR_RETRY applies
        assert cached_profile(conn, HOST)[1] is False
    _profile, stub = read_profile(db, monkeypatch)
    assert stub.calls == []


def test_e5b_the_profile_read_makes_no_new_request_and_its_bytes_do_not_change(db, monkeypatch):
    profile, stub = read_profile(db, monkeypatch)
    assert [path for _host, path in stub.calls][:2] == ["/robots.txt", "/llms.txt"] and "/" not in [p for _h, p in stub.calls]
    assert "home_gate" not in profile and "home_gate" not in AccessProfile.model_fields
    AccessProfile(**profile, note="n")
    stored, = rows(db, "SELECT home_gate FROM host_access_profile")
    assert stored["home_gate"]["allowed"] is True and set(stored["home_gate"]) == {"allowed", "reason", "rule", "read_host", "fetched_at"}


def test_the_request_goes_to_the_host_that_answered_robots_txt(db, monkeypatch):
    def www_reads(site_host, path, *, cap_bytes, **_):
        if site_host == HOST:
            return ("error", None, "connect_timeout")
        return ("ok", ALLOW, "http_200") if path == "/robots.txt" else ("absent", None, "http_404")
    access_profile.get_profile(db, HOST, now=datetime.now(timezone.utc), reader=www_reads)
    home = Home()
    measure(db, home=home)
    assert home.calls == ["www." + HOST]


# --- limits, backoff, guard (T-E3, T-E4) -------------------------------------------------------------------------------


def test_e3_a_second_check_of_a_host_within_6_hours_is_not_made(db, monkeypatch):
    read_profile(db, monkeypatch)
    home = Home()
    now = datetime.now(timezone.utc)
    assert measure(db, home=home, now=now)["outcome"] == "reachable"
    assert measure(db, home=home, now=now + timedelta(hours=5, minutes=59)) is None
    assert len(home.calls) == 1


def test_e3_a_failing_host_is_retried_no_sooner_than_6_hours_and_a_refusal_waits_7_days(db, monkeypatch):
    read_profile(db, monkeypatch)
    now = datetime.now(timezone.utc)
    stored = measure(db, home=Home(label="connect_timeout"), now=now)
    assert stored["outcome"] == "unreachable" and stored["next_due_at"] == now + timedelta(hours=6)
    # Past the due time: refused with no marker is a refusal, off the list for 7 days, flat.
    stored = measure(db, home=Home(status=403), now=now + timedelta(hours=7))
    assert stored["outcome"] == "refused" and stored["next_due_at"] - (now + timedelta(hours=7)) == timedelta(days=7)
    assert measure(db, home=Home(status=403), now=now + timedelta(days=7, hours=6)) is None
    later = now + timedelta(days=7, hours=8)
    read_profile(db, monkeypatch, now=later)     # the robots.txt verdict is re-read (it is only good for 24 h)
    stored = measure(db, home=Home(status=403), now=later)
    assert stored["consecutive_refusals"] == 2 and stored["next_due_at"] - later == timedelta(days=7)


def test_e3_daily_cap_boundary_and_the_lookup_cap(db, monkeypatch):
    for index, host in enumerate(("cap-a-example.org", "cap-b-example.org", "cap-c-example.org")):
        read_profile(db, monkeypatch, host=host)
    monkeypatch.setenv("AHN_PROBE_DAILY_CAP", "2")
    home = Home()
    assert measure(db, "cap-a-example.org", home=home) is not None
    assert measure(db, "cap-b-example.org", home=home) is not None
    assert measure(db, "cap-c-example.org", home=home) is None and len(home.calls) == 2
    monkeypatch.delenv("AHN_PROBE_DAILY_CAP")
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe_daily")
        conn.execute("INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes) "
                     "VALUES (CURRENT_DATE,'x-example.org','reachable','','lookup',%s)", (site_probe.LOOKUP_DAILY_CAP - 1,))
    assert measure(db, "cap-c-example.org", home=home) is not None                    # the 300th lookup-started check
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe WHERE site_host='cap-b-example.org'")
    assert measure(db, "cap-b-example.org", "lookup", home=home) is None              # the 301st is refused ...
    assert measure(db, "cap-b-example.org", "set_R", home=home) is not None           # ... the list-driven ones are not


def test_e3_hourly_cap(db, monkeypatch):
    read_profile(db, monkeypatch)
    with connect(db) as conn:
        for index in range(site_probe.HOURLY_CAP):
            conn.execute("INSERT INTO site_probe (site_host,read_host,probed_at,outcome,fingerprint_version,next_due_at,trigger_kind) "
                         "VALUES (%s,%s,now(),'reachable','ahn-fp/1',now()+interval '1 day','set_O')", (f"h{index}-example.org",) * 2)
    home = Home()
    assert measure(db, home=home, trigger="set_O") is None and home.calls == []


def test_the_guard_must_have_a_recent_normal_record(db, monkeypatch):
    read_profile(db, monkeypatch)
    home = Home()
    with connect(db) as conn:
        conn.execute("UPDATE bandwidth_guard_state SET state='warning'")
    assert measure(db, home=home) is None
    with connect(db) as conn:
        conn.execute("UPDATE bandwidth_guard_state SET state='normal', checked_at=now()-interval '7 days'")
    assert measure(db, home=home) is None
    with connect(db) as conn:
        conn.execute("DELETE FROM bandwidth_guard_state")
    assert measure(db, home=home) is None and home.calls == []
    with connect(db) as conn:
        conn.execute("INSERT INTO bandwidth_guard_state (name,period_start,checked_at,usage_gb,state) VALUES ('web',now(),now(),1,'normal')")
    assert measure(db, home=home) is not None


def test_nothing_runs_with_the_switch_off(db, monkeypatch):
    read_profile(db, monkeypatch)
    monkeypatch.delenv("AHN_SITE_PROBE")
    home = Home()
    assert measure(db, home=home) is None and home.calls == []
    assert site_probe.run_cron(db)["breaker"] == "disabled"
    assert rows(db, "SELECT * FROM site_probe") == []


def test_what_is_stored_is_codes_only(db, monkeypatch):
    read_profile(db, monkeypatch)
    home = Home(status=403, headers={"set-cookie": "x=SECRET", "server": "cloudflare", "cf-mitigated": "challenge"},
                body=b"<html>Just a moment SECRET-PAGE-TEXT</html>")
    measure(db, home=home)
    stored, = rows(db, "SELECT * FROM site_probe")
    assert stored["outcome"] == "bot_challenge" and stored["bot_vendor"] == "cloudflare" and stored["http_status"] == 403
    text = json.dumps({k: str(v) for k, v in stored.items()})
    assert "SECRET" not in text and "Just a moment" not in text
    assert stored["vantage"]["kind"] == "datacenter" and stored["user_agent"] == access_profile.USER_AGENT
    daily, = rows(db, "SELECT * FROM site_probe_daily")
    assert (daily["outcome"], daily["bot_vendor"], daily["trigger_kind"], daily["probes"]) == ("bot_challenge", "cloudflare", "lookup", 1)


# --- the queue ----------------------------------------------------------------------------------------------------------


def test_a_queued_check_waits_for_the_profile_without_using_up_attempts_then_runs(db, monkeypatch):
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    with connect(db) as conn:
        background_reads.enqueue(conn, [("site_probe", HOST)])
    first = background_reads.process_due(db)
    assert first["deferred"] == 1 and first["done"] == 0
    queued = {(r["kind"]): r for r in rows(db, "SELECT kind,attempts,not_before FROM background_reads")}
    assert queued["site_probe"]["attempts"] == 0 and queued["access_profile"]["attempts"] == 0
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles())
    with connect(db) as conn:
        conn.execute("UPDATE background_reads SET not_before=now()")
    background_reads.process_due(db, limit=5)
    assert [r["site_host"] for r in rows(db, "SELECT site_host FROM site_probe")] == [HOST]
    assert rows(db, "SELECT * FROM background_reads") == []


def test_a_queued_check_is_dropped_when_its_profile_never_arrives(db, monkeypatch):
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    with connect(db) as conn:
        conn.execute("INSERT INTO background_reads (kind,site_host,requested_at) VALUES ('site_probe',%s,now()-interval '2 hours')", (HOST,))
    result = background_reads.process_due(db)
    assert result["done"] == 1 and "deferred" not in result
    assert rows(db, "SELECT * FROM site_probe") == []


def test_the_lookup_queues_the_check_with_no_delay(db, monkeypatch):
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    result = service.check(db, HOST)
    assert result["access_profile"]["freshness"] == "pending" and result["ahn_check"]["status"] == "pending"
    job, = rows(db, "SELECT not_before,requested_at,claimed_at FROM background_reads WHERE kind='site_probe'")
    assert job["not_before"] <= datetime.now(timezone.utc) and abs((job["not_before"] - job["requested_at"]).total_seconds()) < 1


def test_an_example_host_shows_an_in_memory_not_measured_view_and_nothing_is_requested_or_stored(db, monkeypatch):
    home = Home()
    monkeypatch.setattr(site_probe, "fetch_home", home)
    for _ in range(2):
        shown = service.check(db, "example.com")["ahn_check"]
        assert shown["status"] == "not_measured" and shown["not_measured_reason"] == "not_eligible"
        assert CheckResponse(**service.check(db, "example.org")).ahn_check.not_measured_reason == "not_eligible"
    assert home.calls == []
    assert rows(db, "SELECT * FROM site_probe") == []
    assert rows(db, "SELECT * FROM background_reads WHERE kind='site_probe'") == []


def test_a_first_lookup_with_a_pending_profile_shows_the_profiles_wait_on_the_pending_check(db, monkeypatch):
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    result = service.check(db, HOST)
    profile_wait = result["access_profile"]["retry_after_seconds"]
    assert result["access_profile"]["freshness"] == "pending" and isinstance(profile_wait, int)
    assert profile_wait != 60                                                            # else the test would pass by the default
    assert result["ahn_check"]["status"] == "pending" and result["ahn_check"]["retry_after_seconds"] == profile_wait


def test_the_pending_check_waits_60_seconds_when_the_profile_is_fresh_or_has_no_wait_of_its_own():
    assert site_probe.pending_view()["retry_after_seconds"] == 60
    assert site_probe._profile_retry({"freshness": "fresh", "retry_after_seconds": 5}) == 60
    assert site_probe._profile_retry({"freshness": "pending"}) == 60
    assert site_probe._profile_retry(None) == 60
    assert site_probe._profile_retry({"freshness": "pending", "retry_after_seconds": 5}) == 5
    assert site_probe._profile_retry({"retry_after_seconds": 5}) == 60                   # no freshness key: no KeyError
    assert site_probe._profile_retry({"freshness": "pending", "retry_after_seconds": 0}) == 1
    assert site_probe._profile_retry({"freshness": "pending", "retry_after_seconds": 10 ** 6}) == 3600


def test_a_pending_check_next_to_a_fresh_profile_still_shows_60(db, monkeypatch):
    warm(db, monkeypatch)
    monkeypatch.setattr(site_probe, "fetch_home", Home(delay=0.8))
    monkeypatch.setattr(site_probe, "SYNC_WAIT_SECONDS", 0.2)
    result = service.check(db, HOST)
    assert result["access_profile"]["freshness"] == "fresh"
    assert result["ahn_check"]["status"] == "pending" and result["ahn_check"]["retry_after_seconds"] == 60
    deadline = time.monotonic() + 5                                                      # let the slow check finish here, not in a later test
    while not rows(db, "SELECT * FROM site_probe") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert rows(db, "SELECT * FROM site_probe")


def test_a_thousand_lookups_of_one_host_queue_one_row(db, monkeypatch):
    monkeypatch.setattr(site_probe, "fetch_home", Home())
    for _ in range(30):
        service.check(db, HOST)
    assert len(rows(db, "SELECT * FROM background_reads WHERE kind='site_probe'")) == 1


# --- the lookup path: synchronous attempt (3 seconds), fallback, concurrency ----------------------------------------------------


def warm(db, monkeypatch, host=HOST, robots=ALLOW):
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles(robots))
    service.check(db, host)
    with connect(db) as conn:
        conn.execute("DELETE FROM background_reads WHERE kind='site_probe'")
    background_reads.process_due(db, limit=5)
    assert service.check(db, host)["access_profile"]["freshness"] == "fresh"
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
        conn.execute("DELETE FROM background_reads")


def test_a_lookup_gets_the_measurement_in_the_same_answer_when_the_host_is_fast(db, monkeypatch):
    warm(db, monkeypatch)
    home = Home(status=403, headers={"cf-mitigated": "challenge"})
    monkeypatch.setattr(site_probe, "fetch_home", home)
    started = time.monotonic()
    result = service.check(db, HOST)
    assert time.monotonic() - started < site_probe.SYNC_WAIT_SECONDS + 1
    shown = result["ahn_check"]
    assert shown["status"] == "measured" and shown["outcome"] == "bot_challenge" and shown["bot_defense"]["vendor"] == "cloudflare"
    assert home.calls == [HOST] and rows(db, "SELECT * FROM background_reads") == []
    response = CheckResponse(**result)
    assert response.ahn_check.age_seconds < 5
    assert ahn_check_line(response.ahn_check).startswith("AHN server check just now: 403, bot challenge (Cloudflare).")


def test_a_slow_host_falls_back_to_the_queue_and_the_check_is_not_made_twice(db, monkeypatch):
    warm(db, monkeypatch)
    home = Home(delay=0.8)
    monkeypatch.setattr(site_probe, "fetch_home", home)
    monkeypatch.setattr(site_probe, "SYNC_WAIT_SECONDS", 0.2)
    result = service.check(db, HOST)
    assert result["ahn_check"]["status"] == "pending"
    job, = rows(db, "SELECT not_before,requested_at FROM background_reads WHERE kind='site_probe'")
    assert abs((job["not_before"] - job["requested_at"]).total_seconds()) < 1            # no delay
    background_reads.process_due(db)                                                     # while the first is still running: put back
    assert len(home.calls) == 1
    deadline = time.monotonic() + 5
    while not rows(db, "SELECT * FROM site_probe") and time.monotonic() < deadline:
        time.sleep(0.05)
    assert rows(db, "SELECT outcome FROM site_probe")[0]["outcome"] == "reachable"
    with connect(db) as conn:
        conn.execute("UPDATE background_reads SET not_before=now()")
    background_reads.process_due(db)                                                     # the queued one finds it fresh and does nothing
    assert len(home.calls) == 1 and rows(db, "SELECT * FROM background_reads") == []
    assert service.check(db, HOST)["ahn_check"]["status"] == "measured"


def test_concurrent_lookups_of_one_host_make_one_check(db, monkeypatch):
    warm(db, monkeypatch)
    home = Home(delay=0.3)
    monkeypatch.setattr(site_probe, "fetch_home", home)
    answers = []
    threads = [threading.Thread(target=lambda: answers.append(service.check(db, HOST))) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(home.calls) == 1 and len(answers) == 8
    assert all(answer["ahn_check"]["status"] in ("measured", "pending") for answer in answers)


def test_the_web_process_runs_at_most_four_checks_at_a_time(db, monkeypatch):
    hosts = [f"slot{i}-example.org" for i in range(6)]
    release = threading.Event()

    def blocked(host):
        release.wait(5)
        return Home().result
    monkeypatch.setattr(site_probe, "fetch_home", blocked)
    for host in hosts:
        read_profile(db, monkeypatch, host=host)
    futures = [site_probe.start_sync(db, host) for host in hosts]
    try:
        assert [f is None for f in futures] == [False] * 4 + [True] * 2
        assert site_probe.start_sync(db, hosts[0]) is futures[0]                          # the same host shares its check
    finally:
        release.set()
        for future in futures:
            if future is not None:
                future.result(timeout=5)
    assert site_probe._SLOTS.acquire(blocking=False)
    site_probe._SLOTS.release()


def test_a_robots_txt_that_disallows_the_home_page_is_never_measured_on_the_lookup_path(db, monkeypatch):
    warm(db, monkeypatch, robots=DENY_HOME)
    home = Home()
    monkeypatch.setattr(site_probe, "fetch_home", home)
    shown = service.check(db, HOST)["ahn_check"]
    assert home.calls == [] and shown["status"] == "not_measured" and shown["not_measured_reason"] == "robots_disallowed"
    assert "outcome" not in shown and "http_status" not in shown


def test_h1_a_host_that_answers_with_status_999_does_not_break_the_lookup(db, monkeypatch):
    warm(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
    home = Home(status=999)
    monkeypatch.setattr(site_probe, "fetch_home", home)
    answer = service.check(db, HOST)
    shown = answer["ahn_check"]
    assert shown["status"] == "measured" and shown["outcome"] == "other_status" and "http_status" not in shown
    assert CheckResponse(**answer).model_dump(mode="json")["ahn_check"]["outcome"] == "other_status"
    stored, = rows(db, "SELECT outcome,http_status FROM site_probe")
    assert (stored["outcome"], stored["http_status"]) == ("other_status", None)
    monkeypatch.setattr(site_probe, "fetch_home", lambda host: pytest.fail("fetched twice"))
    assert service.check(db, HOST)["ahn_check"]["outcome"] == "other_status"      # served from the row, not measured again


def test_h2_only_the_lookup_that_started_the_check_waits_for_it(db, monkeypatch):
    import concurrent.futures
    warm(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
    running = concurrent.futures.Future()
    with site_probe._INFLIGHT_LOCK:
        site_probe._INFLIGHT[HOST] = running
    try:
        started = time.monotonic()
        row, why = site_probe.sync_measure(db, HOST, wait=3.0)
        assert (row, why) == (None, "joined") and time.monotonic() - started < 0.5
        with connect(db) as conn:
            started = time.monotonic()
            shown = site_probe.for_lookup(db, conn, HOST, datetime.now(timezone.utc), access_profile={"freshness": "fresh"})
            assert time.monotonic() - started < 0.5 and shown["status"] == "pending" and shown["retry_after_seconds"] == 60
    finally:
        with site_probe._INFLIGHT_LOCK:
            site_probe._INFLIGHT.pop(HOST, None)


def test_h2_the_lookup_holds_no_transaction_while_it_waits_for_the_check(db, monkeypatch):
    import psycopg
    warm(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
    seen = []
    with connect(db) as lookup_conn:
        def home(host):
            seen.append(lookup_conn.info.transaction_status)
            return Home().result
        monkeypatch.setattr(site_probe, "fetch_home", home)
        shown = site_probe.for_lookup(db, lookup_conn, HOST, datetime.now(timezone.utc), access_profile={"freshness": "fresh"})
    assert shown["status"] == "measured"
    assert seen == [psycopg.pq.TransactionStatus.IDLE]


def test_the_switch_off_leaves_the_answer_as_it_was(db, monkeypatch):
    warm(db, monkeypatch)
    monkeypatch.delenv("AHN_SITE_PROBE")
    home = Home()
    monkeypatch.setattr(site_probe, "fetch_home", home)
    before = len(rows(db, "SELECT * FROM background_reads"))
    result = service.check(db, HOST)
    assert "ahn_check" not in result and "ahn_check" not in CheckResponse(**result).model_dump(mode="json")
    assert home.calls == [] and len(rows(db, "SELECT * FROM background_reads")) == before
    assert rows(db, "SELECT * FROM site_probe") == []


def strip(result):
    return {key: value for key, value in result.items() if key not in ("generated_at", "ahn_check", "lookup_activity", "feedback_token")}


@pytest.mark.parametrize("home", [Home(), Home(status=403, headers={"cf-mitigated": "challenge"}), Home(status=429), Home(status=999),
                                  Home(status=302, headers={"location": "https://accounts." + HOST + "/login"}), Home(label="dns_failed")])
def test_b3_whatever_the_measurement_the_recommendation_and_next_steps_do_not_change(db, monkeypatch, home):
    warm(db, monkeypatch)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
    monkeypatch.delenv("AHN_SITE_PROBE")
    off = [(kw, strip(service.check(db, HOST, **kw))) for kw in ({}, {"error_class": "access_denied"}, {"error_class": "rate_limited"})]
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    monkeypatch.setattr(site_probe, "fetch_home", home)
    for kw, expected in off:
        with connect(db) as conn:
            conn.execute("DELETE FROM site_probe")
        on = service.check(db, HOST, **kw)
        assert on["ahn_check"]["status"] == "measured"
        assert strip(on) == expected


# --- following the redirect to the host's own www name (D67) ----------------------------------------------------------------------

WWW = "www." + HOST
TO_WWW = {"location": "https://" + WWW + "/"}


class Routes:
    """site_probe.fetch_home per host: ``Routes({host: Home(...)})``; every host asked is recorded in order."""

    def __init__(self, by_host):
        self.by_host, self.calls = by_host, []

    def __call__(self, host):
        self.calls.append(host)
        return self.by_host[host](host)


def www_ready(db, monkeypatch, robots=ALLOW):
    """The apex and its www name both have a fresh profile (so a fresh home_gate)."""
    read_profile(db, monkeypatch)
    read_profile(db, monkeypatch, host=WWW, robots=robots)


def one_row(db):
    row, = rows(db, "SELECT * FROM site_probe")
    return row


def idle(host=HOST):
    """Wait until the lookup's check thread has finished (so nothing runs on into the next step or test)."""
    deadline = time.monotonic() + 5
    while host in site_probe._INFLIGHT and time.monotonic() < deadline:
        time.sleep(0.02)
    assert host not in site_probe._INFLIGHT


def test_www_follow_a_fresh_allowing_verdict_makes_a_second_request_and_stores_the_second_answer(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home(status=200)})
    row = site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST, WWW]
    stored = one_row(db)
    assert stored["outcome"] == row["outcome"] == "reachable" and stored["http_status"] == 200
    assert stored["read_host"] == WWW and stored["redirect"] == {"followed_www": True, "to_relation": "www_alias", "path_class": "other"}
    shown = AhnCheck(**site_probe.view(stored, datetime.now(timezone.utc)))
    assert shown.outcome == "reachable" and shown.redirect.followed_www is True


def test_www_follow_is_also_made_by_a_paced_check(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "set_O", reader=routes, paced=True)
    assert routes.calls == [HOST, WWW] and one_row(db)["redirect"]["followed_www"] is True


def test_www_follow_without_a_www_profile_keeps_the_redirect_queues_the_profile_and_follows_next_time(db, monkeypatch):
    read_profile(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST]
    stored = one_row(db)
    assert stored["outcome"] == "redirected_same_host" and stored["read_host"] == HOST and stored["redirect"]["followed_www"] is False
    assert [(r["kind"], r["site_host"]) for r in rows(db, "SELECT kind,site_host FROM background_reads")] == [("access_profile", WWW)]
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles())
    background_reads.process_due(db, limit=5)
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST, HOST, WWW] and one_row(db)["redirect"]["followed_www"] is True


def test_www_follow_reserves_the_www_read_30_minutes_before_the_next_check_and_follows_again_a_day_later(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST, WWW]
    stored = one_row(db)
    job, = rows(db, "SELECT kind,site_host,not_before FROM background_reads")
    assert (job["kind"], job["site_host"]) == ("access_profile", WWW)
    assert job["not_before"] == stored["next_due_at"] - timedelta(minutes=site_probe.WWW_READ_LEAD_MINUTES)
    # the queue holds it (it is not dropped as an old job) until it is due
    assert background_reads.process_due(db)["claimed"] == 0 and len(rows(db, "SELECT * FROM background_reads")) == 1
    read_at = stored["next_due_at"] - timedelta(minutes=30)                    # what the worker does at that time
    access_profile.get_profile(db, WWW, now=read_at, reader=Profiles(), max_age_hours=background_reads.ACCESS_PROFILE_JOB_MAX_AGE_HOURS)
    later = stored["next_due_at"] + timedelta(minutes=1)
    read_profile(db, monkeypatch, now=later)                                   # the apex profile is read again by lookups
    row = site_probe.measure(db, HOST, "lookup", reader=routes, paced=False, now=later)
    assert row is not None and routes.calls == [HOST, WWW, HOST, WWW]
    assert one_row(db)["outcome"] == "reachable" and one_row(db)["redirect"]["followed_www"] is True


def test_www_follow_a_failed_reserved_read_gives_the_redirect_for_6_hours_and_queues_the_read_again(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)

    def broken(site_host, path, *, cap_bytes, **_):
        return ("error", None, "connect_timeout")
    monkeypatch.setattr(background_reads, "read_profile_file", broken)
    with connect(db) as conn:
        conn.execute("UPDATE background_reads SET not_before=now()")
    assert background_reads.process_due(db)["done"] == 1                       # the read ran and failed: the old verdict stays
    later = one_row(db)["next_due_at"] + timedelta(minutes=1)
    read_profile(db, monkeypatch, now=later)
    row = site_probe.measure(db, HOST, "lookup", reader=routes, paced=False, now=later)
    assert row is not None and routes.calls == [HOST, WWW, HOST]               # measured as usual, one request
    stored = one_row(db)
    assert stored["outcome"] == "redirected_same_host" and stored["read_host"] == HOST and stored["redirect"]["followed_www"] is False
    assert stored["next_due_at"] - stored["probed_at"] == timedelta(hours=site_probe.MIN_GAP_HOURS)
    assert [r["site_host"] for r in rows(db, "SELECT site_host FROM background_reads WHERE kind='access_profile'")] == [WWW]
    # the same pass again (a cron every 5 minutes) does not measure the host again: its next check is in the future
    assert site_probe.measure(db, HOST, "set_O", reader=routes, paced=True, now=later + timedelta(minutes=5)) is None
    assert routes.calls == [HOST, WWW, HOST]


def test_www_follow_an_apex_whose_robots_txt_already_lives_on_www_reserves_nothing(db, monkeypatch):
    def www_reads(site_host, path, *, cap_bytes, **_):
        if site_host == HOST:
            return ("error", None, "connect_timeout")
        return ("ok", ALLOW, "http_200") if path == "/robots.txt" else ("absent", None, "http_404")
    access_profile.get_profile(db, HOST, now=datetime.now(timezone.utc), reader=www_reads)
    routes = Routes({WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [WWW] and one_row(db)["read_host"] == WWW
    assert rows(db, "SELECT * FROM background_reads") == []


def test_reservations_for_later_do_not_lengthen_the_wait_of_a_new_read(db, monkeypatch):
    monkeypatch.setattr(background_reads, "worker_running", lambda: True)
    later = datetime.now(timezone.utc) + timedelta(hours=20)
    with connect(db) as conn:
        background_reads.enqueue(conn, [("access_profile", f"reserved{i}-example.org") for i in range(400)], not_before=later)
        background_reads.enqueue(conn, [("access_profile", "new-example.org")])
        wait = background_reads.estimate_wait(conn, "access_profile", "new-example.org")
    import math
    alone = int(min(background_reads.RETRY_AFTER_MAX, max(background_reads.RETRY_AFTER_MIN,
                                                           math.ceil(background_reads.JOB_SECONDS / background_reads.WORKER_THREADS))))
    assert wait == alone < background_reads.RETRY_AFTER_MAX


def test_www_read_wanted_now_pulls_a_later_reservation_forward(db, monkeypatch):
    soon_not = datetime.now(timezone.utc) + timedelta(hours=20)
    with connect(db) as conn:
        background_reads.enqueue(conn, [("access_profile", WWW)], not_before=soon_not)
        background_reads.enqueue(conn, [("access_profile", WWW)], not_before=soon_not + timedelta(hours=1))   # keeps the earlier
        job, = conn.execute("SELECT not_before FROM background_reads").fetchall()
        assert job["not_before"] == soon_not
        background_reads.enqueue(conn, [("access_profile", WWW)])                                              # plain: unchanged
        assert conn.execute("SELECT not_before FROM background_reads").fetchone()["not_before"] == soon_not
        background_reads.enqueue(conn, [("access_profile", WWW)], not_before=datetime.now(timezone.utc))
        assert conn.execute("SELECT not_before FROM background_reads").fetchone()["not_before"] < soon_not


def test_www_follow_a_host_without_a_www_verdict_is_due_again_in_6_hours(db, monkeypatch):
    read_profile(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    stored = one_row(db)
    assert stored["next_due_at"] - stored["probed_at"] == timedelta(hours=site_probe.MIN_GAP_HOURS)
    monkeypatch.setattr(background_reads, "read_profile_file", Profiles())
    background_reads.process_due(db, limit=5)                                  # the queued www profile read
    later = datetime.now(timezone.utc) + timedelta(hours=6, minutes=1)
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False, now=later)
    assert routes.calls == [HOST, HOST, WWW] and one_row(db)["redirect"]["followed_www"] is True


def test_www_follow_a_www_name_that_is_not_public_keeps_the_redirect_result(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home(label="not_public")})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST, WWW]
    stored = one_row(db)
    assert stored["outcome"] == "redirected_same_host" and stored["read_host"] == HOST and stored["redirect"]["followed_www"] is False


def test_www_follow_is_not_made_when_the_www_robots_txt_disallows_the_home_page(db, monkeypatch):
    www_ready(db, monkeypatch, robots=DENY_HOME)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST]
    assert one_row(db)["outcome"] == "redirected_same_host" and rows(db, "SELECT * FROM background_reads") == []


def test_www_follow_is_not_made_when_the_www_verdict_is_over_24_hours_old(db, monkeypatch):
    read_profile(db, monkeypatch)
    read_profile(db, monkeypatch, host=WWW, now=datetime.now(timezone.utc) - timedelta(hours=30))
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST] and [r["site_host"] for r in rows(db, "SELECT site_host FROM background_reads")] == [WWW]


@pytest.mark.parametrize("location", [
    "https://" + HOST + "/en/",                 # same_host
    "/en/",                                     # same_host, relative
    "https://shop." + HOST + "/",               # same_site
    "https://other-example.net/",               # other_site
    "https://www.other-example.net/",           # other_site
    "https://" + WWW + ":8443/",                # not the https port
    "http://" + WWW + "/",                      # not https
    "https://user@" + WWW + "/",                # credentials
    "https://login." + HOST + "/",              # not the www name
])
def test_www_follow_only_for_a_redirect_to_exactly_www_dot_the_host(db, monkeypatch, location):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=302, headers={"location": location}), WWW: Home()})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST]
    assert one_row(db)["read_host"] == HOST and one_row(db)["redirect"]["followed_www"] is False


def test_www_follow_a_www_name_does_not_follow_to_its_own_www(db, monkeypatch):
    read_profile(db, monkeypatch, host=WWW)
    routes = Routes({WWW: Home(status=301, headers={"location": "https://www." + WWW + "/"}), "www." + WWW: Home()})
    site_probe.measure(db, WWW, "lookup", reader=routes, paced=False)
    assert routes.calls == [WWW]


def test_www_follow_a_challenge_from_the_www_name_is_recorded_with_its_vendor(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home(status=403, headers={"cf-mitigated": "challenge"})})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    stored = one_row(db)
    assert routes.calls == [HOST, WWW]
    assert stored["outcome"] == "bot_challenge" and stored["http_status"] == 403 and stored["bot_vendor"] == "cloudflare"
    assert stored["redirect"]["followed_www"] is True and stored["next_due_at"] > stored["probed_at"] + timedelta(hours=24)


def test_www_follow_a_second_redirect_is_recorded_and_not_followed(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home(status=302, headers={"location": "https://" + WWW + "/en/"})})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert routes.calls == [HOST, WWW]
    stored = one_row(db)
    assert stored["outcome"] == "redirected_same_host" and stored["read_host"] == WWW
    assert stored["redirect"] == {"followed_www": True, "to_relation": "same_host", "path_class": "locale"}


def test_www_follow_a_failing_www_name_is_what_the_check_reports(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home(label="connect_timeout")})
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False)
    assert one_row(db)["outcome"] == "unreachable" and one_row(db)["read_host"] == WWW


def test_www_follow_is_skipped_when_too_little_of_the_lookups_time_is_left(db, monkeypatch):
    www_ready(db, monkeypatch)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home()})
    soon = site_probe._clock() + site_probe.SECOND_REQUEST_MIN_SECONDS / 2
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False, deadline=soon)
    assert routes.calls == [HOST] and one_row(db)["outcome"] == "redirected_same_host"
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe")
    site_probe.measure(db, HOST, "lookup", reader=routes, paced=False, deadline=site_probe._clock() + 3.0)
    assert routes.calls == [HOST, HOST, WWW]


def test_www_follow_a_lookup_shows_the_www_answer_and_leaves_the_recommendation_alone(db, monkeypatch):
    warm(db, monkeypatch)
    monkeypatch.delenv("AHN_SITE_PROBE")
    off = [(kw, strip(service.check(db, HOST, **kw))) for kw in ({}, {"error_class": "access_denied"})]
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    read_profile(db, monkeypatch, host=WWW)
    routes = Routes({HOST: Home(status=301, headers=TO_WWW), WWW: Home(status=403, headers={"cf-mitigated": "challenge"})})
    monkeypatch.setattr(site_probe, "fetch_home", routes)
    for kw, expected in off:
        with connect(db) as conn:
            conn.execute("DELETE FROM site_probe")
        on = service.check(db, HOST, **kw)
        idle()
        shown = on["ahn_check"]
        assert shown["status"] == "measured" and shown["outcome"] == "bot_challenge" and shown["redirect"]["followed_www"] is True
        assert strip(on) == expected
    assert routes.calls == [HOST, WWW] * 2


# --- today's list, the cron pass, the automatic stop ---------------------------------------------------------------------------


def test_compute_set_orders_r_then_c_then_o_within_500_and_skips_example_hosts(db):
    with connect(db) as conn:
        for index in range(320):
            conn.execute("INSERT INTO lookup_signals_hourly (hour,site_host,error_class,failure_stage,http_status_class,challenge,"
                         "retry_after,via,client_kind,action,after_action,lookups,sources) VALUES (date_trunc('hour',now()),%s,"
                         "'access_denied','unspecified','unspecified','unspecified','unspecified','mcp','mcp','x','none',%s,1)",
                         (f"blocked{index}-example.org", 1000 - index))
        conn.execute("INSERT INTO lookup_signals_hourly (hour,site_host,error_class,failure_stage,http_status_class,challenge,"
                     "retry_after,via,client_kind,action,after_action,lookups,sources) VALUES (date_trunc('hour',now()),'example.com',"
                     "'access_denied','unspecified','unspecified','unspecified','unspecified','mcp','mcp','x','none',9999,1)")
        counts = site_probe.compute_set(conn, datetime.now(timezone.utc))
        conn.commit()
    assert counts["R"] == site_probe.SET_REFUSED_MAX and counts["C"] > 0
    assert sum(counts.values()) <= site_probe.PROBE_SET_MAX
    listed = rows(db, "SELECT site_host,set_code,rank FROM site_probe_set ORDER BY rank")
    assert listed[0]["site_host"] == "blocked0-example.org" and listed[0]["set_code"] == "R"
    assert "example.com" not in {r["site_host"] for r in listed}
    assert [r["set_code"] for r in listed] == sorted((r["set_code"] for r in listed), key={"R": 0, "C": 1, "O": 2}.get)


def put_set(db, hosts, code="O"):
    with connect(db) as conn:
        conn.execute("DELETE FROM site_probe_set")
        for rank, host in enumerate(hosts, start=1):
            conn.execute("INSERT INTO site_probe_set (site_host,set_code,rank,set_day) VALUES (%s,%s,%s,CURRENT_DATE)", (host, code, rank))
    with connect(db) as conn:
        conn.execute("INSERT INTO probe_state (name,value) VALUES ('baseline','{\"n\":0,\"refused\":0,\"rate\":0.0}') ON CONFLICT DO NOTHING")


def test_the_cron_pass_checks_a_couple_of_hosts_and_logs_counts_without_names(db, monkeypatch):
    hosts = [f"cron{i}-example.org" for i in range(4)]
    for host in hosts:
        read_profile(db, monkeypatch, host=host)
    put_set(db, hosts)
    home = Home()
    now = datetime.now(timezone.utc).replace(hour=9)
    result = site_probe.run_cron(db, now=now, reader=home)
    assert result["done"] == 2 and result["reachable"] == 2 and len(home.calls) == 2 and result["set"] == 4
    line = site_probe.format_result(result)
    assert "example.org" not in line and "breaker=off" in line


def test_e6_a_second_check_starts_only_if_it_still_ends_before_the_cron_deadline(db, monkeypatch):
    hosts = [f"late{i}-example.org" for i in range(3)]
    for host in hosts:
        read_profile(db, monkeypatch, host=host)
    put_set(db, hosts)
    home = Home()
    clock = {"now": 1000.0}
    monkeypatch.setattr(site_probe, "_clock", lambda: clock["now"])
    monkeypatch.setattr(site_probe, "_last_request_at", 0.0)

    def slow_home(host):                                  # the first check takes so long that no second one fits
        clock["now"] += site_probe.CRON_SAFE_SECONDS - site_probe.PROBE_WORST_SECONDS + 1
        return home(host)
    result = site_probe.run_cron(db, cron_started=clock["now"], now=datetime.now(timezone.utc).replace(hour=9), reader=slow_home, time_budget=10_000)
    assert result["done"] == 1 and len(home.calls) == 1


def test_a_host_without_a_profile_gets_its_profile_read_queued_by_the_cron(db, monkeypatch):
    put_set(db, ["noprofile-example.org"])
    home = Home()
    result = site_probe.run_cron(db, now=datetime.now(timezone.utc).replace(hour=9), reader=home)
    assert result["done"] == 0 and home.calls == []
    assert [(r["kind"], r["site_host"]) for r in rows(db, "SELECT kind,site_host FROM background_reads")] == [("access_profile", "noprofile-example.org")]


def test_e7_the_breaker_stops_the_list_after_two_days_of_more_refusals_among_control_hosts(db, monkeypatch):
    read_profile(db, monkeypatch)
    put_set(db, [HOST])
    with connect(db) as conn:
        conn.execute("UPDATE probe_state SET value='{\"n\":100,\"refused\":2,\"rate\":0.02}' WHERE name='baseline'")
        for back in (1, 2):
            conn.execute("INSERT INTO probe_breaker_daily (day,grp,robots_reads,robots_refused) VALUES (CURRENT_DATE-%s,'control',100,6)", (back,))
    home = Home()
    result = site_probe.run_cron(db, now=datetime.now(timezone.utc).replace(hour=9), reader=home)
    assert result["breaker"] == "on:control_refusals" and home.calls == [] and result["done"] == 0
    assert rows(db, "SELECT value FROM probe_state WHERE name='breaker'")[0]["value"]["stopped"] is True
    assert measure(db, trigger="set_O", home=home) is None                                 # list-driven checks stay stopped
    assert measure(db, trigger="lookup", home=home) is None                                # so does a lookup's own: the stop is for all
    assert home.calls == []


def test_e7_one_bad_day_or_a_small_sample_does_not_stop_it(db, monkeypatch):
    with connect(db) as conn:
        conn.execute("INSERT INTO probe_state (name,value) VALUES ('baseline','{\"n\":100,\"refused\":2,\"rate\":0.02}') ON CONFLICT DO NOTHING")
        conn.execute("INSERT INTO probe_breaker_daily (day,grp,robots_reads,robots_refused) VALUES (CURRENT_DATE-1,'control',100,6)")
        conn.execute("INSERT INTO probe_breaker_daily (day,grp,robots_reads,robots_refused) VALUES (CURRENT_DATE-2,'control',10,9)")
        assert site_probe.evaluate_breaker(conn, datetime.now(timezone.utc)) is None


def feed_refusals(conn):
    for back in range(2, 9):
        conn.execute("INSERT INTO probe_breaker_daily (day,grp,feed_403) VALUES (CURRENT_DATE-%s,'feed',1)", (back,))
    for back in (1, 2):
        conn.execute("INSERT INTO probe_breaker_daily (day,grp,feed_403,feed_429) VALUES (CURRENT_DATE-%s,'feed',5,3) ON CONFLICT (day,grp) DO UPDATE SET feed_403=5,feed_429=3", (back,))


def baseline_taken(conn, days_ago):
    taken = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    conn.execute("INSERT INTO probe_state (name,value) VALUES ('baseline',%s) ON CONFLICT (name) DO UPDATE SET value=EXCLUDED.value",
                 (json.dumps({"n": 0, "refused": 0, "rate": 0.0, "taken_at": taken}),))


def test_m3_the_feed_comparison_waits_until_seven_days_before_the_last_two_have_been_counted(db):
    with connect(db) as conn:
        baseline_taken(conn, 5)
        feed_refusals(conn)
        assert site_probe.evaluate_breaker(conn, datetime.now(timezone.utc)) is None
        assert conn.execute("SELECT 1 FROM probe_state WHERE name='breaker'").fetchone() is None
        baseline_taken(conn, 10)
        assert site_probe.evaluate_breaker(conn, datetime.now(timezone.utc)) == "feed_refusals"


def test_m3_a_stop_is_lifted_by_a_later_evaluation_when_its_cause_is_gone(db):
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        baseline_taken(conn, 20)
        conn.execute("INSERT INTO probe_state (name,value) VALUES ('breaker',%s)",
                     (json.dumps({"stopped": True, "reason": "feed_refusals", "since": (now - timedelta(hours=2)).isoformat()}),))
        assert site_probe.evaluate_breaker(conn, now) is None and site_probe._breaker_stopped(conn)       # too early to lift
        conn.execute("UPDATE probe_state SET value=%s WHERE name='breaker'",
                     (json.dumps({"stopped": True, "reason": "feed_refusals", "since": (now - timedelta(days=2)).isoformat()}),))
        feed_refusals(conn)
        assert site_probe.evaluate_breaker(conn, now) is None and site_probe._breaker_stopped(conn)       # the cause is still there
        conn.execute("DELETE FROM probe_breaker_daily")
        assert site_probe.evaluate_breaker(conn, now) is None and not site_probe._breaker_stopped(conn)   # lifted


def test_m2_when_the_stop_state_cannot_be_read_the_check_stops():
    class Broken:
        def transaction(self):
            raise RuntimeError("no table")
    assert site_probe._breaker_stopped(Broken()) is True


def test_m4_the_list_is_made_before_the_baseline(db, monkeypatch):
    order = []
    monkeypatch.setattr(site_probe, "compute_set", lambda conn, now: order.append("set") or {})
    monkeypatch.setattr(site_probe, "ensure_baseline", lambda conn, now: order.append("baseline"))
    site_probe.run_cron(db, now=datetime.now(timezone.utc).replace(hour=9), reader=Home())
    assert order == ["set", "baseline"]


def test_l3_even_the_first_check_of_a_pass_needs_time_before_the_safety_line(db, monkeypatch):
    read_profile(db, monkeypatch)
    put_set(db, [HOST])
    clock = {"now": 1000.0}
    monkeypatch.setattr(site_probe, "_clock", lambda: clock["now"])
    started = clock["now"] - site_probe.CRON_SAFE_SECONDS            # no room left for any check
    home = Home()
    result = site_probe.run_cron(db, cron_started=started, now=datetime.now(timezone.utc).replace(hour=9), reader=home)
    assert result["done"] == 0 and home.calls == []


def test_e7_feed_refusals_that_double_stop_it(db, monkeypatch):
    with connect(db) as conn:
        baseline_taken(conn, 10)
        for back in range(2, 9):
            conn.execute("INSERT INTO probe_breaker_daily (day,grp,feed_403) VALUES (CURRENT_DATE-%s,'feed',1)", (back,))
        for back in (1, 2):
            conn.execute("INSERT INTO probe_breaker_daily (day,grp,feed_403,feed_429) VALUES (CURRENT_DATE-%s,'feed',5,3) ON CONFLICT (day,grp) DO UPDATE SET feed_403=5,feed_429=3", (back,))
        assert site_probe.evaluate_breaker(conn, datetime.now(timezone.utc)) == "feed_refusals"


def test_the_baseline_is_taken_once_from_control_hosts_and_robots_reads_are_counted_by_group(db, monkeypatch):
    control = sorted(site_probe.tranco_hosts())[0]
    with connect(db) as conn:
        conn.execute("INSERT INTO host_access_profile (site_host,fetched_at,robots_status,llms_txt_status) VALUES (%s,now(),'refused','absent')", (control,))
        site_probe.ensure_baseline(conn, datetime.now(timezone.utc))
        site_probe.ensure_baseline(conn, datetime.now(timezone.utc))
        baseline, = conn.execute("SELECT value FROM probe_state WHERE name='baseline'").fetchall()
        assert baseline["value"]["n"] == 1 and baseline["value"]["rate"] == 1.0
        now = datetime.now(timezone.utc)
        site_probe.record_robots_read(conn, control, "refused", now)
        site_probe.record_robots_read(conn, control, "ok", now)
        conn.execute("INSERT INTO site_probe_set (site_host,set_code,rank,set_day) VALUES ('listed-example.org','O',1,CURRENT_DATE)")
        site_probe.record_robots_read(conn, "listed-example.org", "ok", now)
        site_probe.record_robots_read(conn, "unrelated-example.org", "refused", now)
        groups = {r["grp"]: (r["robots_reads"], r["robots_refused"]) for r in conn.execute("SELECT * FROM probe_breaker_daily").fetchall()}
    assert groups == {"control": (2, 1), "probe_set": (1, 0)}


def test_feed_403_and_429_failures_are_counted_without_a_host(db):
    from ahn import status_refresh
    with connect(db) as conn:
        conn.execute("INSERT INTO status_sources (site_host,source_name,status_page_url,api_url) VALUES "
                     "('feed-example.org','Feed','https://status.feed-example.org','https://status.feed-example.org/api')")
        status_refresh._store_failure(conn, "feed-example.org", "http_403")
        status_refresh._store_failure(conn, "feed-example.org", "http_429")
        status_refresh._store_failure(conn, "feed-example.org", "connect_timeout")
        counts, = conn.execute("SELECT feed_403,feed_429 FROM probe_breaker_daily WHERE grp='feed'").fetchall()
    assert (counts["feed_403"], counts["feed_429"]) == (1, 1)


# --- retention --------------------------------------------------------------------------------------------------------------------


def test_retention_30_days_for_the_row_and_35_for_the_counts(db):
    with connect(db) as conn:
        for host, days in (("old-example.org", 31), ("new-example.org", 29)):
            conn.execute("INSERT INTO site_probe (site_host,read_host,probed_at,outcome,fingerprint_version,next_due_at,trigger_kind) "
                         "VALUES (%s,%s,now()-%s*interval '1 day','reachable','ahn-fp/1',now(),'set_O')", (host, host, days))
        for days in (34, 35):
            conn.execute("INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes) VALUES (CURRENT_DATE-%s,'x-example.org','reachable','','set_O',1)", (days,))
            conn.execute("INSERT INTO probe_breaker_daily (day,grp) VALUES (CURRENT_DATE-%s,'control')", (days,))
    platform_metrics.prune_analytics(TEST_URL)
    assert [r["site_host"] for r in rows(db, "SELECT site_host FROM site_probe")] == ["new-example.org"]
    assert len(rows(db, "SELECT * FROM site_probe_daily")) == 1 and len(rows(db, "SELECT * FROM probe_breaker_daily")) == 1


# --- D68: list-driven checks feed the "Site problems right now" history -----------------------------------------------------------

from ahn import signal_history  # noqa: E402


class Sequence:
    """Answers in turn (the last one repeats): stands in for fetch_home across a retry."""

    def __init__(self, *homes):
        self.homes, self.calls = list(homes), []

    def __call__(self, host):
        self.calls.append(host)
        home = self.homes[min(len(self.calls), len(self.homes)) - 1]
        return home(host)


@pytest.fixture
def history(db, monkeypatch):
    """HOST counts as listed; the history tables start empty."""
    monkeypatch.setattr(signal_history, "listed_hosts", lambda: frozenset({HOST}))
    with connect(db) as conn:
        conn.execute("TRUNCATE robots_read_events, robots_read_daily, outage_episodes RESTART IDENTITY")
    return db


def events(settings):
    return rows(settings, "SELECT site_host,read_host,robots_status,detail,a_outcome,source,rt_band FROM robots_read_events "
                         "WHERE source='home' ORDER BY id")   # the profile read of a listed host is recorded too, as before


def test_d68_a_list_driven_check_of_a_listed_host_is_recorded_as_a_home_event_with_codes_only(history, monkeypatch):
    read_profile(history, monkeypatch)
    site_probe.measure(history, HOST, "set_O", reader=Home(status=200), paced=True)
    assert events(history) == [{"site_host": HOST, "read_host": HOST, "robots_status": "home", "detail": "http_200",
                                "a_outcome": "responding", "source": "home", "rt_band": "lt1s"}]
    assert rows(history, "SELECT home_reads FROM robots_read_daily") == [{"home_reads": 1}]


def test_d68_a_lookup_check_and_an_unlisted_host_are_not_recorded(history, monkeypatch):
    read_profile(history, monkeypatch)
    measure(history, home=Home(label="connect_error"))                     # a lookup's own check
    read_profile(history, monkeypatch, host="other-example.org")
    site_probe.measure(history, "other-example.org", "set_R", reader=Home(label="connect_error"), paced=True)
    assert events(history) == [] and rows(history, "SELECT site_host FROM robots_read_daily WHERE home_reads IS NOT NULL") == []


def test_d68_a_transient_failure_is_asked_once_more_and_only_the_second_answer_is_recorded(history, monkeypatch):
    read_profile(history, monkeypatch)
    reader = Sequence(Home(label="connect_timeout"), Home(status=200))
    row = site_probe.measure(history, HOST, "set_O", reader=reader, paced=True)
    assert reader.calls == [HOST, HOST] and row["outcome"] == "reachable"
    assert [(e["a_outcome"], e["detail"]) for e in events(history)] == [("responding", "http_200")]


def test_d68_two_failures_still_count_as_one_check_and_a_retry_never_follows_www(history, monkeypatch):
    read_profile(history, monkeypatch)
    access_profile.get_profile(history, WWW, now=datetime.now(timezone.utc), reader=Profiles())
    reader = Sequence(Home(status=500), Home(status=301, headers=TO_WWW))
    site_probe.measure(history, HOST, "set_O", reader=reader, paced=True)
    assert reader.calls == [HOST, HOST]                                     # two requests at most
    with connect(history) as conn:
        conn.execute("DELETE FROM site_probe")
    failing = Sequence(Home(status=502), Home(label="read_timeout"))
    site_probe.measure(history, HOST, "set_O", reader=failing, paced=True)
    assert failing.calls == [HOST, HOST]
    assert [e["a_outcome"] for e in events(history)][-1] == "no_response:timeout"
    assert rows(history, "SELECT home_reads FROM robots_read_daily") == [{"home_reads": 2}]


@pytest.mark.parametrize("home", [Home(status=503), Home(status=403), Home(status=429), Home(status=404),
                                  Home(status=403, headers={"cf-mitigated": "challenge"})])
def test_d68_a_refusal_or_an_ordinary_answer_is_not_asked_again(history, monkeypatch, home):
    read_profile(history, monkeypatch)
    reader = Sequence(home, Home(status=200))
    site_probe.measure(history, HOST, "set_O", reader=reader, paced=True)
    assert reader.calls == [HOST]
    [event] = events(history)
    assert event["a_outcome"] in ("refused", "responding")


def test_d68_a_lookup_and_an_unpaced_check_are_never_retried(history, monkeypatch):
    read_profile(history, monkeypatch)
    reader = Sequence(Home(label="connect_error"), Home(status=200))
    measure(history, home=reader)                                           # lookup, unpaced
    assert reader.calls == [HOST]
    with connect(history) as conn:
        conn.execute("DELETE FROM site_probe")
    reader = Sequence(Home(label="connect_error"), Home(status=200))
    site_probe.measure(history, HOST, "set_O", reader=reader, paced=False)
    assert reader.calls == [HOST]


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "192.168.1.5"])
def test_d68_ssrf_a_name_that_resolves_to_a_private_or_metadata_address_is_never_requested_or_recorded(history, monkeypatch, address):
    read_profile(history, monkeypatch)
    monkeypatch.setattr(access_profile, "_resolve", lambda *_a, **_k: [address])
    sent = []
    monkeypatch.setattr(site_probe.ap, "tracked_pool", lambda *a, **k: sent.append(1) or pytest.fail("no request may be made"))
    row = site_probe.measure(history, HOST, "set_O", paced=True)                # the real reader, up to its address check
    assert sent == [] and row["outcome"] == "not_measured" and row["not_measured_reason"] == "not_eligible"
    assert events(history) == []


def test_d68_a_confirmed_home_check_period_leaves_the_lookup_answer_unchanged(history, monkeypatch):
    warm(history, monkeypatch)
    before = strip(service.check(history, HOST))
    for _ in range(2):
        with connect(history) as conn:
            conn.execute("DELETE FROM site_probe")
        assert site_probe.measure(history, HOST, "set_O", reader=Home(label="connect_error"), paced=True) is not None
    with connect(history) as conn:
        # the two failures 60 minutes apart (the checks themselves ran just now)
        conn.execute("UPDATE robots_read_events SET read_at=read_at-interval '60 minutes' "
                     "WHERE id=(SELECT min(id) FROM robots_read_events WHERE source='home')")
        conn.execute("UPDATE robots_read_events SET read_at=read_at-interval '120 minutes' WHERE source IS NULL")  # the profile read
        signal_history.rollup(conn, now=datetime.now(timezone.utc) + timedelta(hours=1), thresholds=signal_history.STAGES["1"])
        conn.commit()
    [period] = rows(history, "SELECT confirmed_at FROM outage_episodes WHERE signal='A'")
    assert period["confirmed_at"] is not None
    assert strip(service.check(history, HOST)) == before
