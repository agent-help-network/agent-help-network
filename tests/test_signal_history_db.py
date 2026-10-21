"""Signal history against the dedicated ahn_test database (migration 024). Stub readers only; no real host is read."""

from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone

import httpx
import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)  # ahn.main builds its module-level app from the environment
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")

from fastapi.testclient import TestClient  # noqa: E402

from ahn import platform_metrics, signal_history
from ahn.access_profile import get_profile
from ahn.db import connect, migrate
from ahn.main import create_app
from ahn.settings import Settings
from ahn.status_refresh import SOURCES, refresh_one, register_source

T0 = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
CURATED = sorted(host for host in signal_history.board_hosts()
                 if host not in signal_history.INFRA_HOSTS and len(host.split(".")) == 2 and host.isascii())[:12]
TABLES = "robots_read_events, provider_status_events, outage_episodes, robots_read_daily"


@pytest.fixture
def db():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute(f"TRUNCATE {TABLES}, status_cache, status_sources, queried_hosts, host_access_profile RESTART IDENTITY")
    return settings


def q(settings, sql, params=()):
    with connect(settings) as conn:
        return conn.execute(sql, params).fetchall()


class Reader:
    def __init__(self, robots=("ok", b"User-agent: *\nAllow: /\n", "http_200")):
        self.robots, self.calls = robots, []

    def __call__(self, host, path, *, cap_bytes, **_):
        self.calls.append((host, path))
        return self.robots if path == "/robots.txt" else ("absent", None, "http_404")


def add_reads(settings, rows):
    """rows: (host, read_at, a_outcome)."""
    with connect(settings) as conn:
        for host, at, outcome in rows:
            conn.execute(
                """INSERT INTO robots_read_events (site_host,read_at,read_host,robots_status,detail,a_outcome,trigger,origin_operator,vantage)
                   VALUES (%s,%s,%s,%s,NULL,%s,'seed','agenthelpnetwork.com','{}'::jsonb)""",
                (host, at, host, "ok" if outcome == "responding" else "refused" if outcome == "refused" else "error", outcome))


def rollup(settings, now):
    with connect(settings) as conn:
        return signal_history.rollup(conn, now=now, thresholds=signal_history.STAGES["1"])


def episodes(settings, signal="A"):
    return q(settings, "SELECT * FROM outage_episodes WHERE signal=%s ORDER BY id", (signal,))


def test_a_read_of_a_curated_host_is_recorded_without_any_extra_request(db):
    host = "github.com"
    assert host in signal_history.board_hosts()
    curated, plain = Reader(), Reader()
    get_profile(db, host, now=T0, reader=curated, trigger="seed")
    get_profile(db, "not-curated-example.org", now=T0, reader=plain)
    # Same requests as an unrecorded host: robots.txt and llms.txt, nothing else.
    assert [path for _, path in curated.calls] == [path for _, path in plain.calls] == ["/robots.txt", "/llms.txt"]
    rows = q(db, "SELECT site_host,read_at,read_host,robots_status,detail,a_outcome,trigger,origin_operator FROM robots_read_events")
    assert rows == [{"site_host": host, "read_at": T0, "read_host": host, "robots_status": "ok", "detail": "http_200",
                     "a_outcome": "responding", "trigger": "seed", "origin_operator": "agenthelpnetwork.com"}]
    # A cached answer is not a read and records nothing.
    get_profile(db, host, now=T0 + timedelta(hours=1), reader=curated)
    assert len(q(db, "SELECT id FROM robots_read_events")) == 1 and len(curated.calls) == 2


def test_tranco_only_and_not_public_reads_are_not_recorded(db):
    from ahn import bundle
    from ahn.profile_warm import load_seed_hosts
    tranco_only = sorted(set(load_seed_hosts()) - signal_history.board_hosts() - set(bundle.bundle_seed_hosts()))[0]
    get_profile(db, tranco_only, now=T0, reader=Reader(("error", None, "connect_timeout")))
    get_profile(db, "github.com", now=T0, reader=Reader(("not_public", None, "not_public")))
    assert q(db, "SELECT id FROM robots_read_events") == []


def test_a_failed_history_write_leaves_the_read_and_its_cache_row(db):
    with connect(db) as conn:
        conn.execute("ALTER TABLE robots_read_events RENAME TO robots_read_events_away")
    try:
        profile = get_profile(db, "github.com", now=T0, reader=Reader(("error", None, "connect_timeout")))
        assert profile["robots_status"] == "error"
        assert q(db, "SELECT robots_detail FROM host_access_profile WHERE site_host='github.com'") == [{"robots_detail": "connect_timeout"}]
    finally:
        with connect(db) as conn:
            conn.execute("ALTER TABLE robots_read_events_away RENAME TO robots_read_events")


def test_state_machine_two_failures_over_45_minutes_then_one_answer(db):
    host = CURATED[0]
    add_reads(db, [(host, T0, "no_response:connect"), (host, T0 + timedelta(minutes=30), "no_response:timeout")])
    rollup(db, T0 + timedelta(hours=1))
    [suspect] = episodes(db)
    assert suspect["confirmed_at"] is None and suspect["reads"] == 2 and suspect["kind"] == "no_response"  # under 45 minutes
    add_reads(db, [(host, T0 + timedelta(minutes=50), "server_error")])
    rollup(db, T0 + timedelta(hours=2))
    [down] = episodes(db)
    assert down["confirmed_at"] == T0 + timedelta(minutes=50) and down["ended_at"] is None and down["reads"] == 3
    add_reads(db, [(host, T0 + timedelta(minutes=110), "refused")])  # a refusal is neither failure nor recovery
    rollup(db, T0 + timedelta(hours=3))
    assert episodes(db)[0]["ended_at"] is None
    add_reads(db, [(host, T0 + timedelta(minutes=170), "responding")])
    rollup(db, T0 + timedelta(hours=4))
    [ended] = episodes(db)
    assert ended["ended_at"] == T0 + timedelta(minutes=170)
    daily = q(db, "SELECT reads,responding,failed,refused,held FROM robots_read_daily WHERE site_host=%s", (host,))
    assert daily == [{"reads": 5, "responding": 1, "failed": 3, "refused": 1, "held": 0}]
    # Every event is applied exactly once.
    assert q(db, "SELECT count(*) AS n FROM robots_read_events WHERE applied_at IS NULL") == [{"n": 0}]
    rollup(db, T0 + timedelta(hours=5))
    assert q(db, "SELECT reads FROM robots_read_daily WHERE site_host=%s", (host,)) == [{"reads": 5}]


def test_a_suspect_clears_on_one_answer_and_refusals_never_open_a_period(db):
    a, b = CURATED[0], CURATED[1]
    add_reads(db, [(a, T0, "tls_error"), (a, T0 + timedelta(minutes=60), "responding")])
    add_reads(db, [(b, T0 + timedelta(minutes=m), "refused") for m in (0, 60, 120, 180)])
    rollup(db, T0 + timedelta(hours=5))
    rows = episodes(db)
    assert [(row["site_host"], row["confirmed_at"], row["ended_at"]) for row in rows] == [(a, None, T0 + timedelta(minutes=60))]


def test_an_open_window_waits(db):
    add_reads(db, [(CURATED[0], T0 + timedelta(minutes=14), "no_response:dns")])
    assert rollup(db, T0 + timedelta(minutes=16))["a_events"] == 0  # closed, but not yet for CLOSE_GRACE
    assert rollup(db, T0 + timedelta(minutes=20))["a_events"] == 1


def test_simultaneous_failures_are_held_and_change_no_state(db):
    hosts = CURATED[:7]
    start = T0 + timedelta(minutes=1)
    add_reads(db, [(host, start, "no_response:connect") for host in hosts])
    result = rollup(db, T0 + timedelta(hours=1))
    assert result["held_windows"] == 1 and episodes(db) == []
    assert {(row["held"], row["hold_reason"]) for row in q(db, "SELECT held,hold_reason FROM robots_read_events")} == {(True, "simultaneous_failures")}
    assert q(db, "SELECT sum(held)::int AS h, sum(failed)::int AS f FROM robots_read_daily") == [{"h": 7, "f": 0}]
    summary = board(db, T0 + timedelta(hours=1))
    assert summary["held_windows"] == 1 and summary["held_by_reason"] == {"simultaneous_failures": 1}


def test_a_shared_infrastructure_incident_explains_the_window(db):
    with connect(db) as conn:
        assert signal_history.record_provider_status(conn, "cloudflare.com", "major", T0)
    with connect(db) as conn:
        conn.execute("UPDATE provider_status_events SET at=%s", (T0 - timedelta(minutes=10),))
    hosts = CURATED[:7]
    add_reads(db, [(host, T0 + timedelta(minutes=1), "no_response:connect") for host in hosts])
    result = rollup(db, T0 + timedelta(hours=1))
    assert result["held_windows"] == 0 and result["p_events"] == 1
    assert len(episodes(db)) == 7
    assert {(row["held"], row["hold_reason"]) for row in q(db, "SELECT held,hold_reason FROM robots_read_events")} == {(False, "infra_provider_reported")}


def test_dns_cluster_is_held(db):
    hosts = CURATED[:12]
    rows = [(host, T0 + timedelta(minutes=2), "no_response:dns") for host in hosts[:5]]
    rows += [(host, T0 + timedelta(minutes=3), "responding") for host in hosts[5:] for _ in range(5)]
    add_reads(db, rows)
    assert rollup(db, T0 + timedelta(hours=1))["held_windows"] == 1
    assert episodes(db) == []


def board(settings, now, days=7):
    with connect(settings) as conn:
        return signal_history.board_summary(conn, now=now, days=days)


def test_candidates_need_a_baseline_and_a_usual_failure_rate_under_ten_percent(db):
    steady, never, flaky = CURATED[0], CURATED[1], CURATED[2]
    day = timedelta(days=1)
    history = [(steady, T0 - 10 * day + timedelta(hours=h), "responding") for h in range(20)]
    # 2 single failures in 10 reads, each cleared by the next answer: never a period, but a usual failure rate of 20%.
    history += [(flaky, T0 - 10 * day + timedelta(hours=h), "no_response:connect" if h in (3, 7) else "responding")
                for h in range(10)]
    add_reads(db, history)
    rollup(db, T0 - 8 * day)
    for host in (steady, never, flaky):
        add_reads(db, [(host, T0 + timedelta(minutes=m), "no_response:connect") for m in (0, 60)])
    rollup(db, T0 + timedelta(hours=2))
    summary = board(db, T0 + timedelta(hours=2))
    assert [c["site_host"] for c in summary["candidates"]] == [steady]
    assert summary["candidates"][0]["ahn_check"]["kind"] == "no_response"
    assert summary["excluded_now"] == {"no_baseline": 1, "unstable": 1}
    assert 0 <= summary["empty_ratio"] < 1 and summary["coverage"]["recording_hosts"] == 3


def test_provider_changes_are_recorded_once_and_shown_while_fresh(db):
    host = next(h for h in sorted(SOURCES) if h not in signal_history.INFRA_HOSTS and SOURCES[h].kind == "statuspage")
    bodies = iter([{"page": {"updated_at": "2026-10-21T11:00:00Z"}, "status": {"indicator": "none"}},
                   {"page": {"updated_at": "2026-10-21T11:00:00Z"}, "status": {"indicator": "none"}},
                   {"page": {"updated_at": "2026-10-21T11:30:00Z"}, "status": {"indicator": "major"}}])
    with connect(db) as conn:  # registered by a lookup, as D22 requires before any feed read
        assert register_source(conn, host)
        conn.execute("INSERT INTO queried_hosts (site_host) VALUES (%s)", (host,))
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=next(bodies)))
    with httpx.Client(transport=transport) as client:
        for _ in range(3):
            assert refresh_one(db, host, client=client, min_interval=0, wait=True)
    events = q(db, "SELECT from_indicator,to_indicator FROM provider_status_events ORDER BY id")
    assert events == [{"from_indicator": None, "to_indicator": "none"}, {"from_indicator": "none", "to_indicator": "major"}]
    now = datetime.now(timezone.utc)
    rollup(db, now)
    [period] = episodes(db, "P")
    assert period["kind"] == "major" and period["ended_at"] is None
    summary = board(db, now)
    assert summary["candidates_now"] == 1 and summary["candidates"][0]["provider"]["indicator"] == "major"
    assert summary["p_episodes"] == 1 and summary["candidate_sources"] == {"ahn_check": 0, "provider": 1}
    assert summary["coverage"]["registered_feeds"] == summary["coverage"]["registered_feeds_curated"] == 1
    # A feed that is no longer fresh is not a candidate.
    with connect(db) as conn:
        conn.execute("UPDATE status_cache SET fetched_at=now() - interval '3 hours'")
    assert board(db, now)["candidates_now"] == 0


def test_a_matched_period_counts_as_ahead_of_the_feed(db):
    host = CURATED[0]
    add_reads(db, [(host, T0 - timedelta(days=3), "responding")])
    add_reads(db, [(host, T0 + timedelta(minutes=m), "no_response:connect") for m in (0, 60)])
    with connect(db) as conn:
        conn.execute("""INSERT INTO provider_status_events (site_host,at,from_indicator,to_indicator)
                        VALUES (%s,%s,'none','major')""", (host, T0 + timedelta(minutes=90)))
    rollup(db, T0 + timedelta(hours=3))
    summary = board(db, T0 + timedelta(hours=3))
    assert summary["a_episodes"] == 1 and summary["a_matched_feed"] == 1
    assert summary["a_ahead_of_feed"] == 1 and summary["a_ahead_of_feed_median_minutes"] == 30.0


def test_cleanup_keeps_raw_events_14_days_and_the_rest_90(db):
    old_raw, old_rollup = datetime.now(timezone.utc) - timedelta(days=15), datetime.now(timezone.utc) - timedelta(days=91)
    add_reads(db, [(CURATED[0], old_raw, "responding"), (CURATED[0], datetime.now(timezone.utc), "responding")])
    with connect(db) as conn:
        conn.execute("INSERT INTO provider_status_events (site_host,at,to_indicator) VALUES (%s,%s,'none'),(%s,now(),'none')",
                     (CURATED[1], old_rollup, CURATED[1]))
        conn.execute("""INSERT INTO outage_episodes (site_host,signal,kind,first_fail_at,last_event_at)
                        VALUES (%s,'A','no_response',%s,%s),(%s,'A','no_response',now(),now())""",
                     (CURATED[2], old_rollup, old_rollup, CURATED[3]))
        conn.execute("INSERT INTO robots_read_daily (site_host,day,reads) VALUES (%s,CURRENT_DATE - 90,1),(%s,CURRENT_DATE - 89,1)",
                     (CURATED[4], CURATED[4]))
    platform_metrics.prune_analytics(TEST_URL)
    assert len(q(db, "SELECT id FROM robots_read_events")) == 1
    assert len(q(db, "SELECT id FROM provider_status_events")) == 1
    assert [row["site_host"] for row in q(db, "SELECT site_host FROM outage_episodes")] == [CURATED[3]]
    assert len(q(db, "SELECT day FROM robots_read_daily")) == 1


def test_owner_board_json_and_card_use_the_owner_gate(db):
    add_reads(db, [(CURATED[0], T0, "responding")])
    owner = Settings(db.database_url, db.key_pepper, public_mode=True, reporting_enabled=True, owner_password="x" * 40)
    token = {"Authorization": "Basic " + base64.b64encode(("owner:" + "x" * 40).encode()).decode()}
    with TestClient(create_app(owner)) as client:
        assert client.get("/owner/board.json").status_code == 401
        assert client.get("/owner/board.json?days=abc").status_code == 401  # the gate comes before validation
        response = client.get("/owner/board.json", headers=token)
        assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
        body = response.json()
        for key in ("coverage", "candidates_now", "held_windows", "empty_ratio", "a_likely_false_positive", "p_episodes"):
            assert key in body
        assert body["days"] == 7 and body["coverage"]["recording_hosts"] == 0  # not rolled up yet
        assert client.get("/owner/board.json?days=31", headers=token).status_code == 422
        assert client.get("/owner/board.json?days=14", headers=token).json()["days"] == 14
        page = client.get("/owner", headers=token)
        assert page.status_code == 200 and 'id="board-signals"' in page.text and "빈 시간 비율" in page.text
