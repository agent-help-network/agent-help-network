"""Adoption measurement against the dedicated ahn_test database (migration 028, D49). No real host is read."""

from __future__ import annotations

import base64
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
os.environ.setdefault("AHN_KEY_PEPPER", PEPPER)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import adoption, platform_metrics  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.tool_metrics import mcp_metric_token  # noqa: E402

HOST = "demo-site.io"
OPERATOR_IP = "203.0.113.9"
CALLER_IP = "198.51.100.7"
OTHER_IP = "198.51.100.8"
TABLES = "lookup_outcome_daily, lookup_signals_hourly, lookup_sources_daily"


def make_settings(**changes) -> Settings:
    return replace(Settings(TEST_URL, PEPPER, public_mode=True, reporting_enabled=True, collect_lookup_metrics=True,
                            owner_allowed_ips=(OPERATOR_IP,)), **changes)


@pytest.fixture
def db():
    settings = make_settings()
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute(f"TRUNCATE {TABLES}, lookup_metrics, request_metrics, traffic_attribution_daily, traffic_client_daily, "
                     "mcp_check_metrics, status_cache, status_sources, queried_hosts, host_access_profile, background_reads RESTART IDENTITY CASCADE")
    return settings


@pytest.fixture
def client(db):
    return TestClient(create_app(db))  # no `with`: the background reader never starts


def stored(settings):
    with connect(settings) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT client_category,adapter,mcp_host,action,profile_state,lookups,sources FROM lookup_outcome_daily ORDER BY 1,2,3,4,5")]


def lookup(client, ip=CALLER_IP, host=HOST, headers=None, **params):
    sent = {"CF-Connecting-IP": ip} if ip else {}
    return client.get("/v1/incidents/check", params={"site_host": host, **params}, headers={**sent, **(headers or {})})


def by_category(settings):
    return {r["client_category"]: r["lookups"] for r in stored(settings)}


# --- what is written -----------------------------------------------------------------------------

def test_each_kind_of_client_lands_in_its_own_category(db, client):
    agents = {
        "plugin": "ahn-claude-code-plugin/0.2.4", "ahn_client": "ahn-client/0.4.0 (+https://agenthelpnetwork.com)",
        "browser": "Mozilla/5.0 (X11; Linux)", "agent_or_script": "python-httpx/0.27", "crawler": "Googlebot/2.1", "unknown": "weird-tool",
    }
    for category, agent in agents.items():
        assert lookup(client, headers={"User-Agent": agent}, error_class="site_access").status_code == 200
    assert by_category(db) == {name: 1 for name in agents}
    for row in stored(db):
        assert (row["adapter"], row["mcp_host"]) == ("none", "none")


def test_the_adapter_label_is_counted_on_ahn_client_rows(db, client):
    lookup(client, headers={"User-Agent": "ahn-client/0.4.0 (+langchain; +https://agenthelpnetwork.com)"})
    lookup(client, headers={"User-Agent": "ahn-client/0.4.0 (+openai-agents; +https://agenthelpnetwork.com)"})
    lookup(client, headers={"User-Agent": "ahn-client/0.4.0 (+mystery; +https://agenthelpnetwork.com)"})
    assert {(r["client_category"], r["adapter"]): r["lookups"] for r in stored(db)} == {
        ("ahn_client", "langchain"): 1, ("ahn_client", "openai_agents"): 1, ("ahn_client", "other"): 1}


def hop_client(settings):
    return TestClient(create_app(settings), client=("127.0.0.1", 50000))


def hop_headers(**extra):
    return {"X-AHN-MCP-Call": mcp_metric_token(PEPPER), **extra}


def test_mcp_hop_counts_as_mcp_host_with_the_forwarded_family(db):
    client = hop_client(db)
    lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": CALLER_IP, "X-AHN-Origin-Client": "claude"}))
    lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": OTHER_IP, "X-AHN-Origin-Client": "cursor"}))
    lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": OTHER_IP}))                                  # no label: unknown_mcp
    lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": OTHER_IP, "X-AHN-Origin-Client": "made up"}))  # not on the list: unknown_mcp
    lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": OTHER_IP, "X-AHN-Origin-Client": "other_mcp"}))  # a host the list does not name
    assert {(r["client_category"], r["mcp_host"]): r["lookups"] for r in stored(db)} == {
        ("mcp_host", "claude"): 1, ("mcp_host", "cursor"): 1, ("mcp_host", "unknown_mcp"): 2, ("mcp_host", "other_mcp"): 1}


def test_a_caller_cannot_choose_its_own_mcp_host_label(db, client):
    # Outside the authenticated hop the header is not read at all: a plain request stays what its User-Agent says.
    lookup(client, headers={"User-Agent": "Mozilla/5.0", "X-AHN-Origin-Client": "claude"})
    assert [(r["client_category"], r["mcp_host"]) for r in stored(db)] == [("browser", "none")]


def test_a_valid_hop_token_from_a_non_loopback_address_cannot_set_the_label(db):
    # The token is right but the request is not the adapter's loopback hop: Origin-Client and Origin-IP are both ignored.
    client = TestClient(create_app(db), client=("198.51.100.99", 50000))
    lookup(client, headers=hop_headers(**{"User-Agent": "Mozilla/5.0", "X-AHN-Origin-Client": "claude", "X-AHN-Origin-IP": OTHER_IP}))
    # (the token still marks the call as an MCP one for the category, but the label it asks for is not taken)
    assert [(r["client_category"], r["mcp_host"]) for r in stored(db)] == [("mcp_host", "unknown_mcp")]


def test_the_web_search_page_is_stored_as_web(db, client):
    assert client.get("/en/search", params={"site_host": HOST}, headers={"CF-Connecting-IP": CALLER_IP, "User-Agent": "Mozilla/5.0"}).status_code == 200
    assert by_category(db) == {"web": 1}


def test_action_and_profile_state_are_those_of_the_answer(db, client):
    body = lookup(client, error_class="site_access").json()
    (row,) = stored(db)
    assert row["action"] == body["recommendation"]["action"]
    assert row["profile_state"] == "none"          # the default test setup has no cached profile layer


def test_a_first_lookup_with_a_read_queued_is_counted_as_pending(db, client, monkeypatch):
    monkeypatch.setattr("ahn.service.cached_profile", lambda _conn, _host, **_kw: (None, True))
    body = lookup(client, error_class="site_access").json()
    assert body["access_profile"]["freshness"] == "pending"
    (row,) = stored(db)
    assert (row["profile_state"], row["action"]) == ("pending", body["recommendation"]["action"])


def test_same_combination_is_one_row_and_sources_count_once_per_host_and_day(db, client):
    lookup(client, ip=CALLER_IP, error_class="site_access")
    lookup(client, ip=CALLER_IP, error_class="site_access")
    lookup(client, ip=OTHER_IP, error_class="site_access")
    lookup(client, ip=CALLER_IP, host="other-site.io", error_class="site_access")   # the same address on another host is another source
    (row,) = stored(db)
    assert (row["lookups"], row["sources"]) == (4, 3)


def test_no_address_means_no_source_but_the_lookup_is_counted(db, client):
    assert lookup(client, ip=None, error_class="site_access").status_code == 200
    (row,) = stored(db)
    assert (row["lookups"], row["sources"]) == (1, 0)


def test_a_host_without_a_digest_row_never_adds_a_source(db, client):
    # tenant.demo-site.io is not an eligible apex host: no source digest is kept for it, so there is nothing to compare with,
    # and counting it as new every time would inflate M2. It still counts as a lookup.
    for ip in (CALLER_IP, OTHER_IP):
        assert lookup(client, ip=ip, host="tenant.demo-site.io").status_code == 200
    (row,) = stored(db)
    assert (row["lookups"], row["sources"]) == (2, 0)


def test_stored_rows_hold_no_host_address_or_digest(db, client):
    lookup(client, error_class="site_access")
    with connect(db) as conn:
        columns = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='lookup_outcome_daily'")}
    assert columns == {"day", "client_category", "adapter", "mcp_host", "action", "profile_state", "lookups", "sources"}
    assert CALLER_IP not in repr(stored(db)) and HOST not in repr(stored(db))


# --- who is left out -----------------------------------------------------------------------------

def test_operator_test_marked_and_test_host_lookups_are_left_out_and_still_answered(db, client):
    assert lookup(client, ip=OPERATOR_IP).status_code == 200
    assert lookup(client, headers={"X-AHN-Test": "1"}).status_code == 200
    for host in ("example.com", "www.example.org", "agenthelpnetwork.com", "api.agenthelpnetwork.com", "demo.test"):
        assert lookup(client, host=host).status_code == 200
    assert stored(db) == []
    lookup(client, headers={"X-AHN-Test": "0"})          # only the exact mark counts as a test
    assert by_category(db) == {"unknown": 1}


def test_operator_on_the_mcp_hop_is_left_out(db):
    client = hop_client(db)
    assert lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": OPERATOR_IP})).status_code == 200
    assert stored(db) == []


def test_the_metrics_switch_turns_it_off(db):
    with TestClient(create_app(make_settings(collect_lookup_metrics=False))) as off:
        assert lookup(off, error_class="site_access").status_code == 200
    assert stored(db) == []


def test_the_answer_is_the_same_with_the_record_and_without_the_table(db, client):
    def comparable(response):
        body = response.json()
        body.pop("generated_at")
        body.pop("lookup_activity", None)    # the seven-day volume band moves with every lookup, as in the D47 tests
        body.pop("feedback_token", None)           # a fresh one-time token per answer (D58)
        return body
    first = lookup(client, error_class="site_access", http_status=403)
    assert len(stored(db)) == 1
    with connect(db) as conn:
        conn.execute("ALTER TABLE lookup_outcome_daily RENAME TO lookup_outcome_daily_away")
    try:
        second = lookup(client, error_class="site_access", http_status=403)
        assert second.status_code == 200 and comparable(second) == comparable(first)   # a missing table never touches the answer
        # the D47 counts are written all the same: the adoption record has its own savepoint
        with connect(db) as conn:
            assert conn.execute("SELECT sum(lookups)::int AS n FROM lookup_signals_hourly").fetchone()["n"] == 2
    finally:
        with connect(db) as conn:
            conn.execute("ALTER TABLE lookup_outcome_daily_away RENAME TO lookup_outcome_daily")


def test_the_d47_counts_are_unchanged_by_the_adoption_record(db, client):
    lookup(client, error_class="site_access", http_status=429)
    with connect(db) as conn:
        (row,) = conn.execute("SELECT client_kind,lookups,sources FROM lookup_signals_hourly").fetchall()
    assert dict(row) == {"client_kind": "rest_other", "lookups": 1, "sources": 1}


# --- retention and the day boundary --------------------------------------------------------------

def test_cleanup_keeps_today_and_the_89_days_before_and_nothing_older(db):
    with connect(db) as conn:
        for back in (0, 1, 88, 89, 90, 91, 400):
            conn.execute("INSERT INTO lookup_outcome_daily (day,client_category,adapter,mcp_host,action,profile_state,lookups) "
                         "VALUES (CURRENT_DATE - %s,'browser','none','none','proceed','ready',1)", (back,))
    platform_metrics.prune_analytics(db.database_url)
    with connect(db) as conn:
        left = sorted(conn.execute("SELECT (CURRENT_DATE - day) AS back FROM lookup_outcome_daily").fetchall(), key=lambda r: r["back"])
    assert [r["back"] for r in left] == [0, 1, 88, 89]
    assert adoption.RETENTION_DAYS == 90


def test_cleanup_does_not_touch_the_other_tables(db, client):
    lookup(client, error_class="site_access")
    platform_metrics.prune_analytics(db.database_url)
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM lookup_signals_hourly").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM lookup_outcome_daily").fetchone()["n"] == 1


def test_the_day_is_the_utc_date(db, client):
    lookup(client, error_class="site_access")
    with connect(db) as conn:
        conn.execute("SET TIME ZONE 'Pacific/Kiritimati'")  # a session zone far ahead of UTC must not move the day
        row = conn.execute("SELECT day, (now() AT TIME ZONE 'UTC')::date AS utc FROM lookup_outcome_daily").fetchone()
    assert row["day"] == row["utc"] or row["day"] == row["utc"] - timedelta(days=1)  # a lookup across midnight UTC is still that day's


def test_summary_reads_what_the_lookups_wrote(db, client):
    lookup(client, headers={"User-Agent": "ahn-claude-code-plugin/0.2.4"})
    lookup(client, ip="198.51.100.20", headers={"User-Agent": "Googlebot/2.1"})
    lookup(client, ip=OTHER_IP, headers={"User-Agent": "Mozilla/5.0"})
    with connect(db) as conn:
        out = adoption.summary(conn, 7, datetime.now(timezone.utc).date())
    assert out["m1"] == 2 and out["m1_by_category"]["plugin"] == 1 and out["m1_by_category"]["browser"] == 1
    assert out["left_out"] == {"crawler": 1, "web": 0}
    assert out["m2_sources_upper_bound"] == 2 and out["m3"]["by_profile_state"]["none"] == 2


# --- concurrency: the count never holds the lookup, and a count that cannot be written is itself counted ------------

def test_a_held_lock_on_the_daily_table_neither_blocks_nor_hides_the_lookup(db, client):
    assert lookup(client, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
    before = adoption.dropped_since_start()
    holder = connect(db)
    try:
        holder.execute("LOCK TABLE lookup_outcome_daily IN ACCESS EXCLUSIVE MODE")  # connection A, transaction left open
        response = lookup(client, ip=OTHER_IP, headers={"User-Agent": "Mozilla/5.0"})
        assert response.status_code == 200                                         # the answer is unaffected
        assert adoption.dropped_since_start() == before + 1                        # B could not count: the attempt is counted
    finally:
        holder.rollback()
        holder.close()
    assert sum(r["lookups"] for r in stored(db)) == 1
    assert lookup(client, ip=OTHER_IP, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
    assert sum(r["lookups"] for r in stored(db)) == 2
    assert adoption.dropped_since_start() == before + 1


class _Logged:
    """A connection proxy that records every statement and the savepoint boundaries, and passes them on."""

    def __init__(self, conn):
        self.conn, self.log = conn, []

    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))
        return self.conn.execute(sql, params)

    def transaction(self):
        self.log.append("<savepoint>")
        return self.conn.transaction()


ROW = (datetime.now(timezone.utc).date(), "browser", "none", "none", "proceed", "ready", 1)


def table_is_unlocked(settings) -> bool:
    """True when nothing holds a lock on the daily table: an ACCESS EXCLUSIVE lock without waiting is granted."""
    with connect(settings) as other:
        try:
            other.execute("SET LOCAL lock_timeout = '100ms'")
            other.execute("LOCK TABLE lookup_outcome_daily IN ACCESS EXCLUSIVE MODE NOWAIT")
            return True
        except Exception:
            return False


def test_the_write_is_one_upsert_between_the_timeout_statements_and_nothing_else(db):
    with connect(db) as conn:
        proxy = _Logged(conn)
        assert adoption.write(proxy, ROW) is True
        assert len(proxy.log) == 4 and proxy.log[0] == "<savepoint>"
        assert proxy.log[1].startswith("SET LOCAL lock_timeout = '200ms'")
        assert proxy.log[2].startswith("INSERT INTO lookup_outcome_daily") and proxy.log[2].count("lookup_outcome_daily") == 3
        assert proxy.log[3] == "SET LOCAL lock_timeout = DEFAULT"
        conn.commit()
    assert table_is_unlocked(db)


def test_no_lock_on_the_daily_table_is_held_before_the_write_or_after_the_lookup(db, client, monkeypatch):
    # Before the write nothing of this lookup holds the table (so the lock is taken by the upsert alone); after the
    # lookup returns nothing holds it. A statement ahead of the upsert that touched the table would fail the first check.
    seen = []
    real = adoption.write
    def spy(conn, row, **kw):
        seen.append(table_is_unlocked(db))
        return real(conn, row, **kw)
    monkeypatch.setattr(adoption, "write", spy)
    assert lookup(client, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
    assert seen == [True]
    assert table_is_unlocked(db)
    assert sum(r["lookups"] for r in stored(db)) == 1


def test_a_lock_wait_ends_inside_a_second(db, client):
    import time
    assert lookup(client, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
    holder = connect(db)
    try:
        holder.execute("LOCK TABLE lookup_outcome_daily IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        assert lookup(client, ip=OTHER_IP, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
        assert time.monotonic() - started < 1.5     # lock_timeout is 200 ms; a count never holds a lookup for seconds
    finally:
        holder.rollback()
        holder.close()


def test_a_failed_source_read_is_counted_as_a_drop_and_the_lookup_is_answered(db, client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("source table unavailable")
    monkeypatch.setattr(adoption, "_source_exists", boom)
    before = adoption.dropped_since_start()
    assert lookup(client, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
    assert adoption.dropped_since_start() == before + 1
    assert stored(db) == []


def test_the_owner_json_shows_the_drop_counter(db):
    owner = replace(db, owner_allowed_ips=(OPERATOR_IP,))
    body = TestClient(create_app(owner)).get("/owner/adoption.json", headers={"CF-Connecting-IP": OPERATOR_IP}).json()
    assert body["dropped_since_start"] == adoption.dropped_since_start()


# --- the owner view ------------------------------------------------------------------------------

def test_owner_adoption_json_uses_the_owner_gate_and_validates_days(db):
    lookup(TestClient(create_app(db)), headers={"User-Agent": "Mozilla/5.0"})
    owner = replace(db, owner_allowed_ips=(), owner_password="x" * 40)
    token = {"Authorization": "Basic " + base64.b64encode(("owner:" + "x" * 40).encode()).decode()}
    with TestClient(create_app(owner)) as client:
        assert client.get("/owner/adoption.json").status_code == 401
        assert client.get("/owner/adoption.json?days=abc").status_code == 401        # the gate comes before validation
        response = client.get("/owner/adoption.json", headers=token)
        assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
        body = response.json()
        assert body["days"] == 7 and body["m1"] == 1 and body["m1_by_category"]["browser"] == 1
        assert set(body["m1_by_category"]) == {"plugin", "ahn_client", "mcp_host", "agent_or_script", "browser", "unknown"}
        assert client.get("/owner/adoption.json?days=14", headers=token).json()["days"] == 14
        assert client.get("/owner/adoption.json?days=31", headers=token).json()["days"] == 31
        assert client.get("/owner/adoption.json?days=90", headers=token).json()["days"] == 90
        too_long = client.get("/owner/adoption.json?days=91", headers=token)
        assert too_long.status_code == 422 and too_long.json()["max"] == 90
        assert client.get("/owner/adoption.json?days=0", headers=token).status_code == 422
        page = client.get("/owner", headers=token)
        assert page.status_code == 200 and 'id="adoption"' in page.text and "M1" in page.text


def test_owner_adoption_json_is_not_found_from_a_network_that_is_not_the_operator_s(db):
    allowed = TestClient(create_app(db))
    assert allowed.get("/owner/adoption.json", headers={"CF-Connecting-IP": CALLER_IP}).status_code == 404
    assert allowed.get("/owner/adoption.json", headers={"CF-Connecting-IP": OPERATOR_IP}).status_code == 200


def test_owner_adoption_json_without_the_table_is_503_not_a_crash(db):
    with connect(db) as conn:
        conn.execute("ALTER TABLE lookup_outcome_daily RENAME TO lookup_outcome_daily_away")
    try:
        response = TestClient(create_app(db)).get("/owner/adoption.json", headers={"CF-Connecting-IP": OPERATOR_IP})
        assert response.status_code == 503
    finally:
        with connect(db) as conn:
            conn.execute("ALTER TABLE lookup_outcome_daily_away RENAME TO lookup_outcome_daily")
