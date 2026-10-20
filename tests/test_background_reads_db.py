"""Background reads, provenance columns and export against the dedicated ahn_test database (migration 020).

Every read uses a stub reader; nothing here contacts a real host.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402  (after the skip: ahn.main builds an app from the environment)

from ahn import background_reads, export, service
from ahn.access_profile import cached_profile
from ahn.db import connect, migrate
from ahn.main import create_app
from ahn.service import PUBLIC_REPORTER_ID
from ahn.settings import Settings
from ahn.tool_metrics import mcp_metric_token


ROBOTS = b"User-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
LLMS = b"# Example\n"


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
                     "host_access_profile, background_reads, mcp_check_metrics RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
    monkeypatch.setattr(service, "cached_profile", cached_profile)
    return settings


class Reader:
    def __init__(self, delay=0.0, fail=False):
        self.delay, self.fail, self.calls = delay, fail, []
        self.lock = threading.Lock()

    def __call__(self, site_host, path, *, cap_bytes, **_):
        with self.lock:
            self.calls.append((site_host, path))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("simulated processing failure")
        return ("ok", ROBOTS, "http_200") if path == "/robots.txt" else ("ok", LLMS, "http_200")


def rows(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


def test_migration_020_adds_the_queue_and_provenance_columns(db):
    columns = {(row["table_name"], row["column_name"]) for row in rows(db, """
        SELECT table_name,column_name FROM information_schema.columns
        WHERE table_name IN ('background_reads','host_access_profile','status_cache','status_sources','observations')""")}
    for table, column in [("background_reads", "not_before"), ("background_reads", "claimed_at"),
                          ("host_access_profile", "robots_sha256"), ("host_access_profile", "llms_sha256"),
                          ("host_access_profile", "vantage"), ("status_cache", "body_sha256"), ("status_cache", "reader_version"),
                          ("status_sources", "curated_by"), ("status_sources", "evidence_url"),
                          ("observations", "origin_observation_id"), ("observations", "received_channel")]:
        assert (table, column) in columns


def test_lookup_returns_within_100ms_while_the_reader_takes_3s(db, monkeypatch):
    reader = Reader(delay=3.0)
    monkeypatch.setattr(background_reads, "read_profile_file", reader)
    service.check(db, "warmup-example.org")  # first connection setup is not what is measured
    started = time.perf_counter()
    result = service.check(db, "slow-example.org")
    elapsed = time.perf_counter() - started
    assert elapsed < 0.1, f"lookup took {elapsed:.3f}s"
    assert reader.calls == [] and result["access_profile"]["robots_status"] == "pending"


def test_processing_fills_the_cache_with_provenance_and_empties_the_queue(db, monkeypatch):
    reader = Reader()
    monkeypatch.setattr(background_reads, "read_profile_file", reader)
    monkeypatch.setenv("AHN_OPERATOR_ID", "ops.example.net")
    first = service.check(db, "fill-example.org")
    assert first["access_profile"]["freshness"] == "pending"
    assert background_reads.process_due(db) == {"claimed": 2, "done": 2, "failed": 0, "dropped": 0}
    assert rows(db, "SELECT * FROM background_reads") == []
    answer = service.check(db, "fill-example.org")
    profile = answer["access_profile"]
    assert profile["freshness"] == "fresh" and profile["robots"]["agents_blocked"] == ["ChatGPT-User"]
    assert profile["llms_txt_status"] == "present" and "retry_after_seconds" not in profile
    stored, = rows(db, "SELECT origin_operator,vantage,reader_version,robots_sha256,llms_sha256 FROM host_access_profile")
    assert stored["origin_operator"] == "ops.example.net"
    assert stored["vantage"] == {"kind": "datacenter", "region": "US", "asn": None, "declared": True}
    assert stored["reader_version"].startswith("AgentHelpNetwork/")
    assert stored["robots_sha256"] == hashlib.sha256(ROBOTS).hexdigest() and stored["llms_sha256"] == hashlib.sha256(LLMS).hexdigest()
    assert reader.calls == [("fill-example.org", "/robots.txt"), ("fill-example.org", "/llms.txt")]
    # Nothing new is queued while the answer is fresh, and a second processing run reads nothing.
    assert background_reads.process_due(db)["claimed"] == 0 and len(reader.calls) == 2


def test_a_transport_failure_is_a_finished_job_and_keeps_the_hourly_retry(db, monkeypatch):
    monkeypatch.setattr(background_reads, "read_profile_file", lambda h, p, *, cap_bytes, **_: ("error", None, "dns_timeout"))
    service.check(db, "down-example.org")
    assert background_reads.process_due(db)["done"] == 2
    profile = service.check(db, "down-example.org")["access_profile"]
    # The failure is the cached answer for an hour: no read is queued and nothing says one is under way.
    assert profile["robots_status"] == "error" and profile["freshness"] == "pending" and "retry_after_seconds" not in profile
    assert rows(db, "SELECT kind FROM background_reads") == []


def test_a_job_that_raises_backs_off_and_is_dropped_after_five_attempts(db, monkeypatch):
    monkeypatch.setattr(background_reads, "read_profile_file", Reader(fail=True))
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
    with connect(db) as conn:
        background_reads.enqueue(conn, [("access_profile", "broken-example.org")])
    for attempt in range(1, 5):
        result = background_reads.process_due(db)
        assert result == {"claimed": 1, "done": 0, "failed": 1, "dropped": 0}
        job, = rows(db, "SELECT attempts,claimed_at,not_before - now() AS wait FROM background_reads")
        assert job["attempts"] == attempt and job["claimed_at"] is None
        expected = timedelta(minutes=min(2 ** attempt, 60))
        assert expected - timedelta(seconds=5) < job["wait"] <= expected
        assert background_reads.process_due(db)["claimed"] == 0  # not due again before the backoff ends
        with connect(db) as conn:
            conn.execute("UPDATE background_reads SET not_before=now()")
    assert background_reads.process_due(db) == {"claimed": 1, "done": 0, "failed": 1, "dropped": 1}
    assert rows(db, "SELECT * FROM background_reads") == []


def test_claims_skip_locked_rows_and_honour_the_lease(db):
    with connect(db) as conn:
        background_reads.enqueue(conn, [("access_profile", "a-example.org"), ("access_profile", "b-example.org"),
                                        ("access_profile", "c-example.org")])
        conn.execute("UPDATE background_reads SET claimed_at=now() WHERE site_host='c-example.org'")
    with connect(db) as holder:
        # Another processor is in the middle of claiming a-example.org.
        holder.execute("SELECT * FROM background_reads WHERE site_host='a-example.org' FOR UPDATE")
        claimed = background_reads._claim(db, 5)
        assert [job["site_host"] for job in claimed] == ["b-example.org"]  # a is locked, c is leased
    with connect(db) as conn:
        conn.execute("UPDATE background_reads SET claimed_at=now() - interval '6 minutes' WHERE site_host='c-example.org'")
    assert sorted(job["site_host"] for job in background_reads._claim(db, 5)) == ["a-example.org", "c-example.org"]


def test_two_processors_never_read_the_same_host_twice(db, monkeypatch):
    reader = Reader(delay=0.05)
    monkeypatch.setattr(background_reads, "read_profile_file", reader)
    hosts = [f"host{index}-example.org" for index in range(12)]
    with connect(db) as conn:
        background_reads.enqueue(conn, [("access_profile", host) for host in hosts])
    results = []

    def work():
        while True:
            step = background_reads.process_due(db, limit=2)
            results.append(step)
            if not step["claimed"]:
                break

    threads = [threading.Thread(target=work) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    robots_reads = [host for host, path in reader.calls if path == "/robots.txt"]
    assert sorted(robots_reads) == sorted(hosts)  # each exactly once
    assert sum(step["done"] for step in results) == len(hosts)
    assert rows(db, "SELECT * FROM background_reads") == []


def test_drain_stops_at_the_time_budget_and_reports_the_rest(db, monkeypatch):
    now = [0.0]
    ran = []

    def slow_job(settings, job):  # each job takes 3 seconds of the fake clock
        ran.append(job["site_host"])
        now[0] += 3.0

    monkeypatch.setattr(background_reads, "_clock", lambda: now[0])
    monkeypatch.setattr(background_reads, "_run", slow_job)
    with connect(db) as conn:
        background_reads.enqueue(conn, [("access_profile", f"budget{index}-example.org") for index in range(3)])
    result = background_reads.drain(db, time_budget=5.0, batch=5)
    # Started at 0 and 3 (within 5 s); at 6 no new job starts and the last claim is given back.
    assert result["done"] == 2 and result["remaining"] == 1 and len(ran) == 2
    assert [row["claimed_at"] for row in rows(db, "SELECT claimed_at FROM background_reads")] == [None]


def test_reports_record_operator_their_own_id_and_channel(db):
    client = TestClient(create_app(db))
    sent = {"site_host": "channel-example.org", "error_class": "site_access", "outcome": "blocked"}
    rest = client.post("/v1/observations", json=sent, headers={"CF-Connecting-IP": "198.51.100.201"}).json()
    web = client.post("/v1/observations", json={**sent, "site_host": "channel2-example.org"},
                      headers={"CF-Connecting-IP": "198.51.100.202", "X-AHN-Channel": "web"}).json()
    mcp = client.post("/v1/observations", json={**sent, "site_host": "channel3-example.org"},
                      headers={"CF-Connecting-IP": "198.51.100.203", "X-AHN-MCP-Call": mcp_metric_token(db.key_pepper)}).json()
    stored = {str(row["id"]): row for row in rows(db, "SELECT id,origin_operator,origin_observation_id,received_channel FROM observations")}
    assert {stored[r["observation_id"]]["received_channel"] for r in (rest, web, mcp)} == {"rest", "web", "mcp"}
    assert stored[rest["observation_id"]]["received_channel"] == "rest"
    assert stored[web["observation_id"]]["received_channel"] == "web"
    assert stored[mcp["observation_id"]]["received_channel"] == "mcp"
    for receipt in (rest, web, mcp):
        row = stored[receipt["observation_id"]]
        assert row["origin_operator"] == "agenthelpnetwork.com" and row["origin_observation_id"] == UUID(receipt["observation_id"])


def test_export_keeps_only_published_real_unwithdrawn_reports(db, monkeypatch):
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        incident = conn.execute("INSERT INTO incidents (site_host,task_step,error_class,visibility) VALUES "
                                "('export-example.org','site_open','site_access','published') RETURNING id").fetchone()["id"]

        def add(review_state="published", synthetic=False, withdrawn=False):
            own = uuid4()
            conn.execute(
                """INSERT INTO observations (id,incident_id,reporter_id,observed_at,agent_family,attempt_actor,attempt_env,outcome,
                       challenge_vendor,challenge_kind,synthetic,review_state,withdrawn_at,origin_operator,origin_observation_id,received_channel)
                   VALUES (%s,%s,%s,%s,'other','agent','unknown','blocked','none','none',%s,%s,%s,'agenthelpnetwork.com',%s,'rest')""",
                (own, incident, PUBLIC_REPORTER_ID, now, synthetic, review_state, now if withdrawn else None, own),
            )
            return own

        kept = add()
        hidden, pending, synthetic, withdrawn = add("hidden"), add("pending"), add(synthetic=True), add(withdrawn=True)
    out = io.StringIO()
    assert export.export(db, "observation", out) == 1
    record = json.loads(out.getvalue())
    assert record["kind"] == "observation" and record["payload"]["site_host"] == "export-example.org"
    assert record["observed_at"].endswith(":00:00Z") and record["signature"] is None
    text = out.getvalue()
    for own in (kept, hidden, pending, synthetic, withdrawn):
        assert str(own) not in text
    assert "reporter" not in text


def test_export_of_profiles_and_status_rows(db, monkeypatch):
    monkeypatch.setattr(background_reads, "read_profile_file", Reader())
    service.check(db, "export-profile-example.org")
    background_reads.process_due(db)
    out = io.StringIO()
    assert export.export(db, "access_profile", out) == 1
    record = json.loads(out.getvalue())
    assert record["content_sha256"] == hashlib.sha256(ROBOTS).hexdigest()
    assert record["payload"]["llms_sha256"] == hashlib.sha256(LLMS).hexdigest()
    assert record["vantage"]["kind"] == "datacenter" and record["reader_version"].startswith("AgentHelpNetwork/")
    assert export.export(db, "status", io.StringIO()) == 0  # no status feed was read
