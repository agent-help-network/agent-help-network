"""The lookup path does no external I/O: it answers from the cache and queues reads. Pure: no database, no network.

The database is replaced by a recording stand-in, and every function that could reach a third-party
host is replaced by one that sleeps for three seconds and records the call, so a lookup that waited
on any of them would both be slow and leave a trace.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
import time
from datetime import datetime, timedelta, timezone

import pytest

from ahn import access_profile, background_reads, service, status_refresh
from ahn.models import CheckResponse
from ahn.settings import Settings
from ahn.triage import CHECKS, CHECKS_KO, next_checks, recommendation

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
        if sql.lstrip().startswith("INSERT INTO queried_hosts"):
            return FakeResult()
        return FakeResult()


@pytest.fixture
def slow_network(monkeypatch):
    """Every external read sleeps 3 s and is recorded."""
    calls = []

    def slow(name):
        def run(*args, **kwargs):
            calls.append(name)
            time.sleep(3)
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
    return [params for sql, params in zip(db.statements, db.params) if sql.startswith("INSERT INTO background_reads")]


def test_first_lookup_answers_within_100ms_while_every_reader_would_take_3s(monkeypatch, slow_network):
    db = FakeDatabase()
    result, elapsed = lookup(monkeypatch, db)
    assert elapsed < 0.1, f"lookup took {elapsed:.3f}s: it waited on something"
    assert slow_network == []  # nothing external was even started
    profile = result["access_profile"]
    assert profile["robots_status"] == "pending" and profile["llms_txt_status"] == "pending"
    assert profile["freshness"] == "pending" and profile["fetched_at"] is None and profile["robots"] is None
    assert profile["retry_after_seconds"] == background_reads.RETRY_AFTER_CRON_SECONDS  # no worker runs in tests
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
    assert elapsed < 0.1 and slow_network == []
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
    assert elapsed < 0.1 and result["access_profile"]["robots_status"] == "pending"
    assert result["evidence_level"] == "no_reports"


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
    assert codes == ["locate_failure_stage", "profile_pending", "provider_unknown", "reports_unknown"]
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
