"""The lookup path does no external I/O: it answers from the cache and queues reads. Pure: no database, no network.

The database is replaced by a recording stand-in, and every function that could reach a third-party
host is replaced by one that sleeps for three seconds and records the call, so a lookup that waited
on any of them would both be slow and leave a trace.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
import time
from datetime import datetime, timedelta, timezone

import pytest

from ahn import access_profile, background_reads, service, status_refresh
from ahn.models import CheckResponse
from ahn.settings import Settings
from ahn.triage import CHECKS, CHECKS_KO, next_checks, recommendation

SLOW_READ_SECONDS = 3
# A lookup that waited on a read would take SLOW_READ_SECONDS or more. The recorded calls (slow_network == [])
# are the exact check; the clock is only a backstop, set against the read's delay instead of an absolute
# 100 ms because a loaded machine (parallel test runs) stretches a millisecond lookup by orders of magnitude.
LOOKUP_BUDGET = SLOW_READ_SECONDS / 2
SETTINGS = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, collect_lookup_metrics=True)
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.org/", "developer_portal": None, "note": None}


class FakeDatabase:
    """Answers the lookup's SQL from canned rows and records every statement."""

    def __init__(self, profile_row=None, fail_on=()):
        self.profile_row = profile_row
        self.fail_on = fail_on
        self.statements: list[str] = []
        self.params: list = []

    def connect(self, _settings):
        return FakeConnection(self)


class FakeResult:
    def __init__(self, one=None, many=()):
        self.one, self.many = one, list(many)

    def fetchone(self):
        return self.one

    def fetchall(self):
        return self.many


class FakeConnection:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @contextmanager
    def transaction(self):
        yield

    def execute(self, sql, params=None):
        self.db.statements.append(" ".join(sql.split()))
        self.db.params.append(params)
        if any(marker in sql for marker in self.db.fail_on):
            raise RuntimeError("simulated database failure")
        if "FROM host_access_profile" in sql:
            return FakeResult(self.db.profile_row)
        if "count(*) AS n FROM queried_hosts" in sql:
            return FakeResult({"n": 0})
        if sql.lstrip().startswith("INSERT INTO background_reads"):
            return FakeResult({"kind": params[0]})
        if sql.lstrip().startswith("WITH me AS"):
            return FakeResult({"not_before": datetime.now(timezone.utc), "ahead": 0})
        return FakeResult()

    def commit(self):
        self.db.statements.append("COMMIT")


@pytest.fixture
def slow_network(monkeypatch):
    """Every external read sleeps 3 s and is recorded."""
    calls = []

    def slow(name):
        def run(*args, **kwargs):
            calls.append(name)
            time.sleep(SLOW_READ_SECONDS)
            return ("ok", b"User-agent: *\nAllow: /\n", "http_200")
        return run

    monkeypatch.setattr(access_profile, "fetch_public_file", slow("fetch_public_file"))
    monkeypatch.setattr(access_profile, "get_profile", slow("get_profile"))
    monkeypatch.setattr(status_refresh, "refresh_one", slow("refresh_one"))
    monkeypatch.setattr(status_refresh, "discover_source", slow("discover_source"))
    monkeypatch.setattr(status_refresh, "_read_source", slow("_read_source"))
    for name in ("read_profile_file", "refresh_feed", "discover"):
        monkeypatch.setattr(background_reads, name, slow(name))
    monkeypatch.setattr(service, "cached_profile", access_profile.cached_profile)
    monkeypatch.setattr(service, "official_paths", lambda host: dict(OFFICIAL) if host == "fresh-example.org" else None)
    return calls


def lookup(monkeypatch, db, host="fresh-example.org", **kwargs):
    monkeypatch.setattr(service, "connect", db.connect)
    started = time.perf_counter()
    result = service.check(SETTINGS, host, **kwargs)
    return result, time.perf_counter() - started


def queued(db):
    """The (kind, host) of every background read the lookup queued."""
    return [tuple(params[:2]) for sql, params in zip(db.statements, db.params) if sql.startswith("INSERT INTO background_reads")]


def test_first_lookup_answers_within_100ms_while_every_reader_would_take_3s(monkeypatch, slow_network):
    db = FakeDatabase()
    result, elapsed = lookup(monkeypatch, db)
    assert elapsed < LOOKUP_BUDGET, f"lookup took {elapsed:.3f}s: it waited on something"
    assert slow_network == []  # nothing external was even started
    profile = result["access_profile"]
    assert profile["robots_status"] == "pending" and profile["llms_txt_status"] == "pending"
    assert profile["freshness"] == "pending" and profile["fetched_at"] is None and profile["robots"] is None
    assert profile["retry_after_seconds"] == background_reads.RETRY_AFTER_MAX  # no worker runs in tests: the cron's 5 minutes
    assert profile["read_host"] == "fresh-example.org" and profile["note"] == service.ACCESS_PROFILE_NOTE
    assert profile["source_urls"]["robots_txt"] == "https://fresh-example.org/robots.txt"
    assert result["recommendation"] == {"action": "use_official_path", "confidence": "medium",
                                        "reasons": ["profile_pending", "official_path_available"]}
    codes = [item["code"] for item in result["next_checks"]]
    assert "profile_pending" in codes and "robots_unknown" not in codes
    assert queued(db) == [("access_profile", "fresh-example.org")]
    CheckResponse(**result)  # the public response model accepts the pending shape


def test_the_pending_profile_waits_five_seconds_while_the_worker_runs(monkeypatch, slow_network):
    monkeypatch.setattr(background_reads, "worker_running", lambda: True)
    result, _ = lookup(monkeypatch, FakeDatabase())
    assert result["access_profile"]["retry_after_seconds"] == 5


def test_an_expired_profile_is_returned_as_stale_at_once_and_refreshed_behind_the_lookup(monkeypatch, slow_network):
    old = datetime.now(timezone.utc) - timedelta(hours=30)
    row = {"fetched_at": old, "robots_status": "ok", "robots": access_profile.parse_robots(b"User-agent: *\nAllow: /\n"),
           "llms_txt_status": "absent", "last_error_at": None, "robots_detail": "http_200", "llms_detail": "http_404",
           "read_host": "fresh-example.org"}
    db = FakeDatabase(profile_row=row)
    result, elapsed = lookup(monkeypatch, db, error_class="site_access")
    assert elapsed < LOOKUP_BUDGET and slow_network == []
    profile = result["access_profile"]
    assert profile["freshness"] == "stale" and profile["robots_status"] == "ok" and "retry_after_seconds" not in profile
    assert queued(db) == [("access_profile", "fresh-example.org")]
    assert "profile_pending" not in [item["code"] for item in result["next_checks"]]


def test_a_fresh_profile_queues_nothing(monkeypatch, slow_network):
    row = {"fetched_at": datetime.now(timezone.utc) - timedelta(hours=1), "robots_status": "absent", "robots": None,
           "llms_txt_status": "absent", "last_error_at": None, "robots_detail": "http_404", "llms_detail": "http_404",
           "read_host": None}
    db = FakeDatabase(profile_row=row)
    result, _ = lookup(monkeypatch, db)
    assert result["access_profile"]["freshness"] == "fresh" and queued(db) == []


def test_queue_and_metrics_failures_never_fail_the_lookup(monkeypatch, slow_network):
    db = FakeDatabase(fail_on=("INSERT INTO background_reads", "lookup_metrics (day", "UPDATE queried_hosts"))
    result, elapsed = lookup(monkeypatch, db)
    assert elapsed < LOOKUP_BUDGET and result["access_profile"]["robots_status"] == "pending"
    assert result["evidence_level"] == "no_reports"
    # Nothing was queued, so nothing tells the caller to come back: no retry time, no profile_pending.
    assert "retry_after_seconds" not in result["access_profile"]
    assert "profile_pending" not in [item["code"] for item in result["next_checks"]]


def test_registration_is_committed_before_the_reads(monkeypatch, slow_network):
    db = FakeDatabase()
    lookup(monkeypatch, db)
    commit = db.statements.index("COMMIT")
    assert any(s.startswith("INSERT INTO lookup_metrics") for s in db.statements[:commit])
    assert any("FROM observations" in s for s in db.statements[commit:])


def test_a_full_queue_queues_nothing_and_promises_no_retry(monkeypatch, slow_network):
    class FullQueue(FakeConnection):
        def execute(self, sql, params=None):
            if sql.lstrip().startswith("INSERT INTO background_reads"):
                self.db.statements.append(" ".join(sql.split()))
                self.db.params.append(params)
                return FakeResult(None)  # WHERE count(*) < QUEUE_CAP was false
            return super().execute(sql, params)

    db = FakeDatabase()
    monkeypatch.setattr(db, "connect", lambda _settings: FullQueue(db))
    result, _ = lookup(monkeypatch, db)
    assert result["access_profile"]["robots_status"] == "pending" and "retry_after_seconds" not in result["access_profile"]


def test_subdomains_still_say_profile_not_read_and_queue_nothing(monkeypatch, slow_network):
    db = FakeDatabase()
    result, _ = lookup(monkeypatch, db, host="user123.fresh-example.org")
    assert result["access_profile"] is None and queued(db) == []
    assert result["recommendation"]["reasons"] == ["profile_not_read"]


def test_failed_reads_only_so_far_are_pending_with_a_retry_once_the_hour_has_passed(monkeypatch, slow_network):
    failed = datetime.now(timezone.utc) - timedelta(hours=2)
    row = {"fetched_at": None, "robots_status": "error", "robots": None, "llms_txt_status": "error",
           "last_error_at": failed, "robots_detail": "dns_timeout", "llms_detail": "dns_timeout", "read_host": None}
    db = FakeDatabase(profile_row=row)
    result, _ = lookup(monkeypatch, db)
    assert result["access_profile"]["freshness"] == "pending" and result["access_profile"]["retry_after_seconds"]
    assert "profile_pending" in [item["code"] for item in result["next_checks"]]
    # Within the hour the failure is the answer: nothing is queued and nothing claims a read is under way.
    row["last_error_at"] = datetime.now(timezone.utc) - timedelta(minutes=10)
    db = FakeDatabase(profile_row=row)
    result, _ = lookup(monkeypatch, db)
    assert "retry_after_seconds" not in result["access_profile"] and queued(db) == []
    assert "robots_unknown" in [item["code"] for item in result["next_checks"]]


# --- triage ------------------------------------------------------------------------------------------

PENDING = access_profile.pending_profile("example.org", 5)


def test_pending_profile_recommendation_follows_the_unreadable_policy_path():
    assert recommendation(None, None, PENDING, OFFICIAL) == {
        "action": "use_official_path", "confidence": "medium", "reasons": ["profile_pending", "official_path_available"]}
    assert recommendation("site_access", None, PENDING, None) == {"action": "unknown", "confidence": "low", "reasons": ["profile_pending"]}
    # Handoffs and a fresh provider incident still come first.
    assert recommendation("human_verification", None, PENDING, OFFICIAL)["action"] == "hand_off_to_human"
    assert recommendation(None, {"freshness": "fresh", "indicator": "major"}, PENDING, None)["action"] == "wait_provider_incident"


def test_pending_next_check_text_in_both_languages():
    codes = [item["code"] for item in next_checks(None, None, "no_reports", None, PENDING, None)]
    # Changed on purpose by D63: no failure signal was sent, so locate_failure_stage is not asked.
    assert codes == ["profile_pending", "provider_unknown", "reports_unknown"]
    assert "retry_after_seconds" in CHECKS["profile_pending"] and "Do not poll repeatedly" in CHECKS["profile_pending"]
    assert "retry_after_seconds" in CHECKS_KO["profile_pending"] and "반복 폴링은 하지 마세요" in CHECKS_KO["profile_pending"]


# --- the worker ----------------------------------------------------------------------------------------

def test_reads_are_paced_to_two_per_second(monkeypatch):
    now = [100.0]
    sleeps = []
    monkeypatch.setattr(background_reads, "_clock", lambda: now[0])
    monkeypatch.setattr(background_reads, "_sleep", lambda seconds: (sleeps.append(seconds), now.__setitem__(0, now[0] + seconds)))
    monkeypatch.setattr(background_reads, "_last_read", [0.0])
    for _ in range(3):
        background_reads._pace()
    assert sleeps == [0.5, 0.5]
    now[0] += 2
    background_reads._pace()
    assert sleeps == [0.5, 0.5]


def test_worker_switch_and_modes(monkeypatch):
    started = []
    monkeypatch.setattr(threading.Thread, "start", lambda self: started.append(self.name))
    monkeypatch.setattr(background_reads, "_workers", [])
    monkeypatch.setenv("AHN_BACKGROUND_READS", "0")
    assert background_reads.start_worker(SETTINGS) is False
    monkeypatch.delenv("AHN_BACKGROUND_READS")
    assert background_reads.start_worker(Settings("postgresql://invalid/unused", "x" * 32)) is False  # local build
    assert background_reads.start_worker(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, test_mode=True)) is False
    assert started == []
    assert background_reads.start_worker(SETTINGS) is True
    assert started == ["ahn-background-reads-0", "ahn-background-reads-1"]
    background_reads._workers.clear()


def test_enqueue_refuses_unknown_kinds():
    with pytest.raises(ValueError):
        background_reads.enqueue(None, [("everything", "example.org")])


def test_the_guard_keeps_tests_off_the_network():
    with pytest.raises(RuntimeError, match="must not reach real hosts"):
        background_reads.read_profile_file("example.org", "/robots.txt", cap_bytes=10)
    with pytest.raises(RuntimeError):
        background_reads.refresh_feed(SETTINGS, "example.org")
    with pytest.raises(RuntimeError):
        background_reads.discover("example.org")


# --- whole-body time limits ------------------------------------------------------------------------

class Trickle:
    """A streamed response that sends one byte per receive, forever."""

    status = 200
    headers = {}

    def __init__(self, clock):
        self.clock = clock

    def read1(self, _amount):
        self.clock[0] += 1.0  # each receive takes a second
        return b"a"

    def release_conn(self):
        pass


def test_a_trickling_robots_txt_ends_as_read_timeout(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(access_profile, "_clock", lambda: clock[0])
    monkeypatch.setattr(access_profile, "_resolve", lambda host, timeout: {"93.184.216.34"})

    class Pool:
        def __init__(self, *_a, **_kw):
            pass

        def request(self, *_a, **_kw):
            return Trickle(clock)

        def close(self):
            pass

    monkeypatch.setattr(access_profile.urllib3, "HTTPSConnectionPool", Pool)
    assert access_profile.fetch_public_file("example.com", "/robots.txt", cap_bytes=262_144) == ("error", None, "read_timeout")
    assert clock[0] <= access_profile.BODY_READ_BUDGET + 2


def test_read_capped_returns_the_body_or_too_large():
    class Chunks:
        def __init__(self, parts):
            self.parts = list(parts)

        def read1(self, amount):
            return self.parts.pop(0)[:amount] if self.parts else b""

    assert access_profile.read_capped(Chunks([b"ab", b"cd"]), 10) == (b"abcd", None)
    assert access_profile.read_capped(Chunks([b"abcdef", b"gh"]), 6) == (None, "too_large")


def test_a_trickling_status_feed_and_discovery_end_as_timeout(monkeypatch):
    import httpx

    clock = [0.0]
    monkeypatch.setattr(status_refresh, "_clock", lambda: clock[0])

    def trickle():
        while True:
            clock[0] += 1.0
            yield b" "

    feed = status_refresh.Source("Example Status", "https://status.example.org/", "https://status.example.org/api/v2/status.json")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=trickle()))) as client:
        with pytest.raises(status_refresh.FeedFailure) as failure:
            status_refresh._read_source(feed, client)
    assert failure.value.label == "timeout" and clock[0] <= status_refresh.FEED_READ_BUDGET + 2

    clock[0] = 0.0
    monkeypatch.setattr(access_profile, "_clock", lambda: clock[0])
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh, "_resolve", lambda host, timeout: {"93.184.216.34"})

    class Pool:
        def __init__(self, *_a, **_kw):
            pass

        def request(self, *_a, **_kw):
            return Trickle(clock)

        def close(self):
            pass

    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    with pytest.raises(status_refresh.FeedFailure) as failure:
        status_refresh._read_discovered_statuspage("example.org")
    assert failure.value.label == "timeout"


def test_the_cron_drain_returns_at_its_budget_even_when_a_read_hangs(monkeypatch):
    started_job = threading.Event()
    release = threading.Event()

    def hanging(settings, *, limit, time_budget, stop):
        started_job.set()
        release.wait(10)  # the hung read; released at the end so the daemon thread does not linger
        return {"claimed": 1, "done": 1, "failed": 0, "dropped": 0, "skipped": 0}

    class Count:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def execute(self, sql, params=None):
            return FakeResult({"n": 7})

    monkeypatch.setattr(background_reads, "process_due", hanging)
    monkeypatch.setattr(background_reads, "connect", lambda _settings: Count())
    began = time.perf_counter()
    try:
        result = background_reads.drain(SETTINGS, time_budget=0.3)
        # Budget 0.3 s, hang 10 s: the bound sits well above the budget (load) and well below the hang.
        assert time.perf_counter() - began < 5.0 and started_job.is_set()
    finally:
        release.set()
    assert result["cut"] == 1 and result["remaining"] == 7


def test_the_drain_reports_a_failure_of_its_own_thread(monkeypatch):
    def broken(settings, *, limit, time_budget, stop):
        raise RuntimeError("relation background_reads does not exist")

    monkeypatch.setattr(background_reads, "process_due", broken)
    with pytest.raises(RuntimeError):
        background_reads.drain(SETTINGS, time_budget=2.0)


# --- retry_after_seconds is absent from the JSON unless a read is queued ---------------------------

def _answer(profile):
    return {
        "site_host": "example.org", "generated_at": "2026-10-20T00:00:00Z", "synthetic": False, "window_hours": 24,
        "data_horizon_hours": 168, "current_site_status": "unknown", "evidence_level": "no_reports",
        "summary_code": "NO_MATCHING_REPORTS_STATUS_UNKNOWN", "summary": "x", "failures": 0, "successes": 0,
        "reporting_sources": 0, "observations": [], "provider_status": None,
        "provider_source_state": "not_verified_by_current_methods", "access_profile": profile,
        "recommendation": {"action": "unknown", "confidence": "low", "reasons": []}, "next_checks": [],
    }


FRESH = {"robots_status": "absent", "robots": None, "llms_txt_status": "absent", "read_host": "example.org",
         "source_urls": {"robots_txt": "https://example.org/robots.txt", "llms_txt": "https://example.org/llms.txt"},
         "fetched_at": None, "freshness": "fresh", "note": "n"}


def test_rest_json_leaves_retry_after_out_unless_a_read_is_queued(monkeypatch):
    from fastapi.testclient import TestClient

    # ahn.main builds a module-level app from the environment when first imported.
    monkeypatch.setenv("AHN_DATABASE_URL", os.environ.get("AHN_DATABASE_URL") or "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", os.environ.get("AHN_KEY_PEPPER") or "x" * 32)
    from ahn.main import create_app

    answers = iter([_answer(dict(FRESH)), _answer(dict(FRESH, retry_after_seconds=None)),
                    _answer({**access_profile.pending_profile("example.org", 7), "note": "n"})])
    monkeypatch.setattr("ahn.main.check", lambda *args, **kwargs: next(answers))
    client = TestClient(create_app(SETTINGS))
    for expected in (False, False, True):
        response = client.get("/v1/incidents/check", params={"site_host": "example.org"})
        assert response.status_code == 200
        assert ('"retry_after_seconds"' in response.text) is expected
        assert ("retry_after_seconds" in response.json()["access_profile"]) is expected
    assert response.json()["access_profile"]["retry_after_seconds"] == 7


def test_mcp_json_leaves_retry_after_out_unless_a_read_is_queued(monkeypatch):
    import asyncio

    from ahn import mcp_adapter

    async def fake_api(method, path, *, params=None, payload=None, write=False, extra_headers=None):
        return fake_api.answer

    monkeypatch.setattr(mcp_adapter, "call_api", fake_api)
    for profile, expected in ((dict(FRESH), False), ({**access_profile.pending_profile("example.org", 7), "note": "n"}, True)):
        fake_api.answer = _answer(profile)
        result = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "example.org"}))
        assert ("retry_after_seconds" in result.structured_content["access_profile"]) is expected
        assert ('"retry_after_seconds"' in result.content[0].text) is expected
