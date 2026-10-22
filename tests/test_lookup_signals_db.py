"""Lookup signals against the dedicated ahn_test database (migration 027, D47). No real host is read."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)  # ahn.main builds its module-level app from the environment
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
os.environ.setdefault("AHN_KEY_PEPPER", PEPPER)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import lookup_signals, platform_metrics  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.lookup_signals import LookupSignal, source_digest  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.tool_metrics import mcp_metric_token  # noqa: E402

HOST = "demo-site.io"
OPERATOR_IP = "203.0.113.9"
CALLER_IP = "198.51.100.7"
OTHER_IP = "198.51.100.8"
TABLES = "lookup_signals_hourly, lookup_sources_daily"


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


def rows(settings, table="lookup_signals_hourly"):
    with connect(settings) as conn:
        return conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()


def lookup(client, ip=CALLER_IP, host=HOST, headers=None, **params):
    sent = {"CF-Connecting-IP": ip} if ip else {}
    return client.get("/v1/incidents/check", params={"site_host": host, **params}, headers={**sent, **(headers or {})})


def comparable(response):
    body = response.json()
    body.pop("generated_at")
    # The seven-day volume band counts every lookup, with or without codes (D11 era behaviour, unchanged here), so it
    # moves as this test sends lookups. Everything else must stay equal.
    body.pop("lookup_activity", None)
    return body


# --- what is written ----------------------------------------------------------------------------

def test_a_lookup_with_codes_is_counted_per_host_and_hour(db, client):
    response = lookup(client, error_class="site_access", failure_stage="access_denial_observed", http_status=503,
                      challenge="header", retry_after="lt_60", via="browser")
    assert response.status_code == 200
    (row,) = rows(db)
    hour = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    assert row["hour"] in (hour, hour - timedelta(hours=1))  # a lookup across the hour boundary is still this hour's
    assert {k: row[k] for k in row if k not in {"hour", "action"}} == {
        "site_host": HOST, "error_class": "site_access", "failure_stage": "access_denial_observed", "http_status_class": "5xx",
        "challenge": "header", "retry_after": "lt_60", "via": "browser", "client_kind": "rest_other", "after_action": "none",
        "lookups": 1, "sources": 1}
    (source,) = rows(db, "lookup_sources_daily")
    assert source["last_at"].second == 0 and source["last_at"].microsecond == 0  # kept to the minute, never finer
    assert source["last_group"] == "outage" and source["last_action"] == row["action"]
    assert row["action"] == response.json()["recommendation"]["action"]


def test_codes_the_client_did_not_send_are_stored_as_unspecified(db, client):
    assert lookup(client).status_code == 200
    (row,) = rows(db)
    for column in ("error_class", "failure_stage", "http_status_class", "challenge", "retry_after", "via"):
        assert row[column] == "unspecified", column


def test_each_status_is_kept_and_5xx_is_folded(db, client):
    for code in (401, 403, 429, 451, 500, 503, 599):
        assert lookup(client, http_status=code).status_code == 200
    with connect(db) as conn:
        classes = {r["http_status_class"]: r["lookups"] for r in conn.execute("SELECT http_status_class, sum(lookups) AS lookups FROM lookup_signals_hourly GROUP BY 1")}
    assert classes == {"401": 1, "403": 1, "429": 1, "451": 1, "5xx": 3}


def test_the_same_combination_in_one_hour_is_one_row_with_two_lookups(db, client):
    lookup(client, error_class="site_access", http_status=403)
    lookup(client, error_class="site_access", http_status=403)
    (row,) = rows(db)
    assert row["lookups"] == 2 and row["sources"] == 1  # one source, counted once for the hour


def test_two_sources_in_one_hour_are_counted_as_two_sources(db, client):
    lookup(client, ip=CALLER_IP, error_class="site_access")
    lookup(client, ip=OTHER_IP, error_class="site_access")
    (row,) = rows(db)
    assert row["lookups"] == 2 and row["sources"] == 2
    assert len(rows(db, "lookup_sources_daily")) == 2


def test_no_address_means_no_source_row_but_the_lookup_is_still_counted(db, client):
    assert lookup(client, ip=None, error_class="site_access").status_code == 200
    (row,) = rows(db)
    assert row["lookups"] == 1 and row["sources"] == 0
    assert rows(db, "lookup_sources_daily") == []


def test_stored_values_hold_no_address_and_the_digest_is_keyed_to_host_and_day(db, client):
    lookup(client, error_class="site_access")
    lookup(client, host="other-site.io", error_class="site_access")
    for table in ("lookup_signals_hourly", "lookup_sources_daily"):
        assert CALLER_IP not in repr(rows(db, table)), table
    digests = {r["site_host"]: r["source_digest"] for r in rows(db, "lookup_sources_daily")}
    today = datetime.now(timezone.utc).date()
    assert digests[HOST] == source_digest(PEPPER, today, HOST, CALLER_IP) or digests[HOST] == source_digest(PEPPER, today - timedelta(days=1), HOST, CALLER_IP)
    assert digests[HOST] != digests["other-site.io"]  # the same address on another host cannot be linked


# --- who is left out ----------------------------------------------------------------------------

def test_operator_address_is_left_out_of_the_signals_but_not_of_the_old_totals(db, client):
    response = lookup(client, ip=OPERATOR_IP, error_class="site_access")
    assert response.status_code == 200
    assert rows(db) == [] and rows(db, "lookup_sources_daily") == []
    with connect(db) as conn:  # decision #2: the existing daily totals are unchanged and still count it
        assert conn.execute("SELECT sum(checks) AS n FROM lookup_metrics WHERE site_host=%s", (HOST,)).fetchone()["n"] == 1


def test_a_test_marked_lookup_is_left_out_and_still_answered(db, client):
    response = lookup(client, headers={"X-AHN-Test": "1"}, error_class="site_access")
    assert response.status_code == 200 and rows(db) == []
    lookup(client, headers={"X-AHN-Test": "0"}, error_class="site_access")  # only the exact mark counts as a test
    assert len(rows(db)) == 1


def test_example_domains_ineligible_hosts_and_the_metrics_switch(db, client):
    for host in ("example.com", "example.net", "example.org", "www.example.org", "tenant.demo-site.io", "demo.test"):
        assert lookup(client, host=host, error_class="site_access").status_code == 200
    assert rows(db) == []
    with TestClient(create_app(make_settings(collect_lookup_metrics=False))) as off:
        assert lookup(off, error_class="site_access").status_code == 200
    assert rows(db) == []


def test_web_search_counts_as_web_and_uses_no_new_codes(db, client):
    assert client.get("/en/search", params={"site_host": HOST}, headers={"CF-Connecting-IP": CALLER_IP}).status_code == 200
    (row,) = rows(db)
    assert row["client_kind"] == "web" and row["http_status_class"] == "unspecified"
    operator = client.get("/en/search", params={"site_host": "other-site.io"}, headers={"CF-Connecting-IP": OPERATOR_IP})
    assert operator.status_code == 200 and len(rows(db)) == 1


def test_client_kind_comes_from_the_user_agent(db, client):
    lookup(client, headers={"User-Agent": "ahn-claude-code-plugin/0.2.4"}, error_class="site_access")
    lookup(client, headers={"User-Agent": "ahn-client/0.3.0"}, error_class="login_auth")
    lookup(client, headers={"User-Agent": "Mozilla/5.0"}, error_class="service_error")
    with connect(db) as conn:
        kinds = {r["error_class"]: r["client_kind"] for r in conn.execute("SELECT error_class, client_kind FROM lookup_signals_hourly")}
    assert kinds == {"site_access": "plugin", "login_auth": "ahn_client", "service_error": "rest_other"}


# --- the MCP hop --------------------------------------------------------------------------------

def hop_client(settings):
    return TestClient(create_app(settings), client=("127.0.0.1", 50000))


def hop_headers(**extra):
    return {"X-AHN-MCP-Call": mcp_metric_token(PEPPER), **extra}


def test_mcp_hop_uses_the_address_the_adapter_passed(db):
    client = hop_client(db)
    assert lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": CALLER_IP}), error_class="site_access").status_code == 200
    (row,) = rows(db)
    assert row["client_kind"] == "mcp" and row["sources"] == 1
    assert len(rows(db, "lookup_sources_daily")) == 1


def test_mcp_hop_from_the_operator_address_is_left_out(db):
    client = hop_client(db)
    assert lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": OPERATOR_IP}), error_class="site_access").status_code == 200
    assert rows(db) == []


def test_origin_header_is_ignored_without_an_authenticated_loopback_hop(db, client):
    # A direct caller cannot pick its own address, nor hide behind the operator's.
    lookup(client, ip=CALLER_IP, headers={"X-AHN-Origin-IP": OPERATOR_IP}, error_class="site_access")
    assert len(rows(db)) == 1  # not treated as the operator
    forged = TestClient(create_app(db), client=("127.0.0.1", 50000))  # loopback but no valid hop token
    lookup(forged, ip=None, headers={"X-AHN-Origin-IP": OPERATOR_IP}, error_class="login_auth")
    other = {r["error_class"]: r for r in rows(db)}["login_auth"]
    assert other["sources"] == 0  # the forged address was not used
    wrong = TestClient(create_app(db), client=("203.0.113.50", 50000))  # a hop token from a non-loopback caller
    lookup(wrong, ip=CALLER_IP, headers=hop_headers(**{"X-AHN-Origin-IP": OPERATOR_IP}), error_class="human_verification")
    assert {r["error_class"] for r in rows(db)} == {"site_access", "login_auth", "human_verification"}


def test_mcp_hop_forwards_the_test_mark(db):
    client = hop_client(db)
    assert lookup(client, ip=None, headers=hop_headers(**{"X-AHN-Origin-IP": CALLER_IP, "X-AHN-Test": "1"})).status_code == 200
    assert rows(db) == []


@pytest.mark.parametrize("sent,stored", [
    ({"http_status": "403", "challenge": "header", "retry_after": "60_3600", "via": "unknown"}, ("403", "header", "60_3600", "unspecified")),
    ({"http_status": "404", "challenge": "maybe", "retry_after": "5", "via": "curl"}, ("unspecified",) * 4),
    ({"http_status": "abc"}, ("unspecified",) * 4), ({"http_status": "600", "via": "browser"}, ("unspecified", "unspecified", "unspecified", "browser")),
    ({"http_status": ""}, ("unspecified",) * 4), ({"made_up_name": "x"}, ("unspecified",) * 4),
])
def test_a_wrong_signal_value_is_dropped_and_the_lookup_is_answered(db, client, sent, stored):
    """Recorded decision: a typo in an optional signal costs the caller nothing but that signal."""
    baseline = comparable(lookup(client, ip=OPERATOR_IP))
    response = lookup(client, **sent)
    assert response.status_code == 200 and comparable(response) == baseline
    (row,) = rows(db)
    assert (row["http_status_class"], row["challenge"], row["retry_after"], row["via"]) == stored


def test_omitted_codes_are_unknown_not_none(db, client):
    lookup(client, ip=OTHER_IP)                      # nothing sent
    lookup(client, challenge="none", retry_after="none")  # explicitly "no challenge header, no Retry-After"
    found = {(r["challenge"], r["retry_after"]) for r in rows(db)}
    assert found == {("unspecified", "unspecified"), ("none", "none")}


# --- the answer does not change -----------------------------------------------------------------

CASES = [
    {}, {"error_class": "site_access"}, {"error_class": "human_verification", "failure_stage": "access_denial_observed"},
    {"error_class": "login_auth"}, {"error_class": "service_error", "failure_stage": "before_target_response"},
]
CODES = [
    {}, {"http_status": 403}, {"http_status": 429, "retry_after": "60_3600"}, {"http_status": 503, "via": "browser"},
    {"challenge": "header", "via": "http_fetch"}, {"http_status": 451, "challenge": "none", "retry_after": "gt_3600", "via": "unknown"},
]


@pytest.mark.parametrize("case", CASES)
def test_the_answer_is_identical_with_and_without_the_codes(db, client, case):
    """Twin hosts get the same lookups in the same order, one with codes and one without. The two runs end in the same
    aggregate state at every step, so the whole answer (the seven-day volume band and trend included) must match."""
    twin_a, twin_b = "twin-one.io", "twin-two.io"
    for step in range(14):  # crosses the 1-4, 5-9 and 10-49 volume bands
        codes = CODES[step % len(CODES)]
        with_codes = lookup(client, host=twin_a, **case, **codes).json()
        without = lookup(client, host=twin_b, **case).json()
        for body, host in ((with_codes, twin_a), (without, twin_b)):
            assert body.pop("site_host") == host
            body.pop("generated_at")
        assert with_codes == without, (case, codes, step)
    assert with_codes["lookup_activity"]["volume_band"] == "10-49"  # the volume really moved through the bands
    assert rows(db)


def test_recommendation_and_next_checks_ignore_the_codes_after_many_lookups(db, client):
    first = lookup(client, error_class="site_access").json()
    for _ in range(5):
        for ip in (CALLER_IP, OTHER_IP):
            lookup(client, ip=ip, error_class="site_access", http_status=503, via="browser")
    last = lookup(client, error_class="site_access", http_status=403).json()
    for field in ("recommendation", "next_checks", "evidence_level", "summary_code", "observations", "failures", "reporting_sources"):
        assert last[field] == first[field], field
    assert "lookup_signals" not in last  # shadow mode: nothing is shown


# --- the lookup never fails because of the signals ----------------------------------------------

def test_a_failing_signal_write_does_not_change_the_lookup(db, client, monkeypatch):
    baseline = comparable(lookup(client, ip=OPERATOR_IP, error_class="site_access"))

    def broken(conn, *_args, **_kwargs):
        conn.execute("INSERT INTO lookup_signals_hourly (hour) VALUES (now())")  # violates NOT NULL inside the savepoint
    monkeypatch.setattr(lookup_signals, "record", broken)
    response = lookup(client, error_class="site_access", http_status=503)
    assert response.status_code == 200 and comparable(response) == baseline
    assert rows(db) == []
    with connect(db) as conn:  # the earlier bookkeeping of the same lookup was kept
        assert conn.execute("SELECT sum(checks) AS n FROM lookup_metrics WHERE site_host=%s", (HOST,)).fetchone()["n"] == 2


# --- re-lookup counter (window boundaries) ------------------------------------------------------

T0 = datetime(2026, 10, 21, 12, 7, 31, tzinfo=timezone.utc)


def record(conn, minutes, *, error_class="site_access", http_status=None, host=HOST, ip=CALLER_IP, action="use_official_path", start=T0):
    return lookup_signals.record(conn, PEPPER, host, error_class, None,
                                 LookupSignal(client_kind="rest_other", source_address=ip, http_status=http_status),
                                 action, start + timedelta(minutes=minutes))


@pytest.mark.parametrize("minutes,counted", [(0, False), (5, False), (9, False), (10, True), (11, True), (59, True), (60, True), (61, False), (180, False)])
def test_relookup_window_edges(db, minutes, counted):
    with connect(db) as conn:
        assert record(conn, 0) == "none"
        assert record(conn, minutes, action="wait_provider_incident") == ("use_official_path" if counted else "none")
        found = conn.execute("SELECT after_action, action, sum(lookups) AS n FROM lookup_signals_hourly GROUP BY 1,2 ORDER BY 1,2").fetchall()
    if counted:
        assert any(r["after_action"] == "use_official_path" and r["action"] == "wait_provider_incident" and r["n"] == 1 for r in found)
    else:
        assert all(r["after_action"] == "none" for r in found)


def test_window_is_measured_between_minutes_not_seconds(db):
    # The earlier lookup is stored at 12:07 and this one at 12:17 (:59 seconds in): ten minutes apart for the counter.
    with connect(db) as conn:
        record(conn, 0)
        assert record(conn, 10, start=T0.replace(second=59)) == "use_official_path"


def test_relookup_needs_a_blocked_lookup_before_and_after_from_the_same_source_and_host(db):
    with connect(db) as conn:
        record(conn, 0, error_class=None)                               # earlier lookup was not a blocked one
        assert record(conn, 20) == "none"
        record(conn, 100)
        assert record(conn, 120, error_class=None) == "none"            # this one is not a blocked one
        record(conn, 200)
        assert record(conn, 220, ip=OTHER_IP) == "none"                 # another source
        assert record(conn, 220, host="other-site.io") == "none"        # another host
        record(conn, 300, error_class=None, http_status=429)            # a status alone makes it a blocked lookup
        assert record(conn, 320, error_class=None, http_status=503) == "use_official_path"


def test_each_lookup_replaces_the_remembered_recommendation(db):
    with connect(db) as conn:
        record(conn, 0, action="use_official_path")
        assert record(conn, 15, action="wait_provider_incident") == "use_official_path"
        assert record(conn, 30, action="hand_off_to_human") == "wait_provider_incident"
        assert record(conn, 45) == "hand_off_to_human"
        assert record(conn, 50) == "none"  # five minutes after the last one


def test_a_source_does_not_carry_over_to_the_next_utc_day(db):
    start = datetime(2026, 10, 21, 23, 50, tzinfo=timezone.utc)
    with connect(db) as conn:
        record(conn, 0, start=start)
        assert record(conn, 20, start=start) == "none"  # 00:10 the next day: a different digest, nothing to compare with
        assert conn.execute("SELECT count(*) AS n FROM lookup_sources_daily").fetchone()["n"] == 2


def test_relookups_are_their_own_rows_and_still_count_as_lookups(db):
    with connect(db) as conn:
        record(conn, 0)
        record(conn, 20)
        found = conn.execute("SELECT after_action, lookups, sources FROM lookup_signals_hourly ORDER BY after_action").fetchall()
    assert [(r["after_action"], r["lookups"]) for r in found] == [("none", 1), ("use_official_path", 1)]
    assert sum(r["sources"] for r in found) == 1  # still one source in that hour


# --- retention ----------------------------------------------------------------------------------

def test_cleanup_keeps_35_days_of_hourly_counts_and_2_days_of_digests(db):
    cols = "(hour,site_host,error_class,failure_stage,http_status_class,challenge,retry_after,via,client_kind,action,after_action,lookups,sources)"
    with connect(db) as conn:
        for label, age in (("keep_new", "(interval '35 days' - interval '1 minute')"), ("drop_old", "(interval '35 days' + interval '1 minute')"),
                           ("keep_today", "interval '0'")):
            conn.execute(f"INSERT INTO lookup_signals_hourly {cols} VALUES (now() - {age},%s,'a','b','c','d','e','f','g','h','i',1,0)", (label,))
        for label, back in (("today", 0), ("yesterday", 1), ("two_days", 2), ("long_ago", 30)):
            conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                         "VALUES ((now() AT TIME ZONE 'UTC')::date - %s,%s,repeat('a',64),now(),'block','unknown')", (back, label))
    platform_metrics.prune_analytics(db.database_url)
    assert {r["site_host"] for r in rows(db)} == {"keep_new", "keep_today"}
    assert {r["site_host"] for r in rows(db, "lookup_sources_daily")} == {"today", "yesterday"}
    assert (lookup_signals.HOURLY_RETENTION_DAYS, lookup_signals.SOURCES_RETENTION_DAYS) == (35, 2)


def test_lookup_signal_cleanup_does_not_touch_the_other_tables(db):
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_metrics (day,site_host,error_class,checks) VALUES (CURRENT_DATE - 400,%s,'site_access',3)", (HOST,))
    platform_metrics.prune_analytics(db.database_url)
    with connect(db) as conn:
        assert conn.execute("SELECT checks FROM lookup_metrics WHERE site_host=%s", (HOST,)).fetchone()["checks"] == 3


def test_cleanup_uses_the_utc_date_whatever_the_session_time_zone(db):
    with connect(db) as conn:
        conn.execute("SET TIME ZONE 'Pacific/Kiritimati'")  # UTC+14: its date is ahead of UTC for 14 hours a day
        for label, back in (("yesterday", 1), ("two_days", 2)):
            conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                         "VALUES ((now() AT TIME ZONE 'UTC')::date - %s,%s,repeat('b',64),now(),'block','unknown')", (back, label))
    with connect(db) as conn:
        conn.execute("SET TIME ZONE 'Pacific/Kiritimati'")
        conn.execute("DELETE FROM lookup_sources_daily WHERE day < (now() AT TIME ZONE 'UTC')::date - 1")
        assert {r["site_host"] for r in conn.execute("SELECT site_host FROM lookup_sources_daily")} == {"yesterday"}
    # and the cleanup itself, run from a connection whose database default is that zone, keeps the same two days
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                     "VALUES ((now() AT TIME ZONE 'UTC')::date - 2,'two_days',repeat('b',64),now(),'block','unknown')")
        conn.execute("ALTER DATABASE ahn_test SET timezone TO 'Pacific/Kiritimati'")
    try:
        platform_metrics.prune_analytics(db.database_url)
    finally:
        with connect(db) as conn:
            conn.execute("ALTER DATABASE ahn_test RESET timezone")
    assert {r["site_host"] for r in rows(db, "lookup_sources_daily")} == {"yesterday"}


def test_minute_precision_is_cut_to_the_hour_after_61_minutes_without_changing_the_relookup_verdict(db):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    old = (now - timedelta(minutes=62)).replace(minute=37) if now.minute != 37 else (now - timedelta(minutes=63))
    old = now - timedelta(minutes=62)
    recent = now - timedelta(minutes=60)
    with connect(db) as conn:
        for label, when in (("old", old), ("recent", recent), ("on_the_hour", now.replace(minute=0) - timedelta(hours=3))):
            conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                         "VALUES ((now() AT TIME ZONE 'UTC')::date,%s,repeat('c',64),%s,'block','unknown')", (label, when))
    platform_metrics.prune_analytics(db.database_url)
    kept = {r["site_host"]: r["last_at"] for r in rows(db, "lookup_sources_daily")}
    assert kept["old"] == old.replace(minute=0)           # older than 61 minutes: now to the hour
    assert kept["recent"] == recent                        # inside the window: still to the minute
    assert kept["on_the_hour"] == now.replace(minute=0) - timedelta(hours=3)
    assert lookup_signals.MINUTE_PRECISION_MINUTES == 61 > lookup_signals.RELOOKUP_MAX_MINUTES


@pytest.mark.parametrize("minutes_ago", [9, 10, 37, 60, 61, 62, 90])
def test_cutting_the_minute_never_changes_whether_a_lookup_is_a_relookup(db, minutes_ago):
    """Same verdict from a row cut to the hour by the cleanup as from the exact row (the window is at most 60 minutes)."""
    start = datetime(2026, 10, 21, 12, 29, tzinfo=timezone.utc)
    verdicts = []
    for cut in (False, True):
        with connect(db) as conn:
            conn.execute("TRUNCATE lookup_sources_daily, lookup_signals_hourly")
            record(conn, 0, start=start)
            if cut and minutes_ago > 61:  # the cleanup only touches rows older than 61 minutes at the time it runs
                conn.execute("UPDATE lookup_sources_daily SET last_at = date_trunc('hour', last_at)")
            verdicts.append(record(conn, minutes_ago, start=start))
    assert verdicts[0] == verdicts[1]
    assert (verdicts[0] != "none") == (10 <= minutes_ago <= 60)


# --- size bounds (A1) ---------------------------------------------------------------------------

def many_combinations(conn, host, hour_start, count, *, ip=CALLER_IP):
    kinds = ["site_access", "login_auth", "human_verification", "service_error", None]
    stages = ["before_target_response", "response_observed", "access_denial_observed", "unknown", None]
    statuses = [401, 402, 403, 407, 408, 429, 451, 503, None]
    n = 0
    for error_class in kinds:
        for stage in stages:
            for status in statuses:
                for challenge in ("header", "none", None):
                    for retry in ("none", "lt_60", "60_3600", "gt_3600", None):
                        if n >= count:
                            return
                        lookup_signals.record(conn, PEPPER, host, error_class, stage,
                                              LookupSignal(client_kind="rest_other", source_address=ip, http_status=status,
                                                           challenge=challenge, retry_after=retry),
                                              "unknown", hour_start)
                        n += 1


def test_one_source_cannot_grow_a_host_beyond_the_per_host_hour_cap(db):
    hour = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0)
    with connect(db) as conn:
        many_combinations(conn, HOST, hour, 110)
        found = conn.execute("SELECT error_class, lookups FROM lookup_signals_hourly WHERE site_host=%s", (HOST,)).fetchall()
    assert len(found) == lookup_signals.HOST_HOUR_ROW_CAP + 1  # the cap, plus the one overflow row
    assert sum(r["lookups"] for r in found) == 110              # nothing is lost, only folded
    overflow = [r for r in found if r["error_class"] == lookup_signals.OVERFLOW]
    assert len(overflow) == 1 and overflow[0]["lookups"] == 110 - lookup_signals.HOST_HOUR_ROW_CAP


def test_an_existing_combination_still_counts_after_the_cap_is_reached(db):
    hour = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0)
    with connect(db) as conn:
        many_combinations(conn, HOST, hour, 40)
        before = conn.execute("SELECT count(*) AS n FROM lookup_signals_hourly").fetchone()["n"]
        many_combinations(conn, HOST, hour, 1)  # the first combination again
        after = conn.execute("SELECT count(*) AS n, max(lookups) AS most FROM lookup_signals_hourly").fetchone()
    assert after["n"] == before and after["most"] >= 2


def test_the_whole_hour_is_bounded_across_many_hosts(db, monkeypatch):
    monkeypatch.setattr(lookup_signals, "HOUR_ROW_CAP", 40)
    monkeypatch.setattr(lookup_signals, "HOST_HOUR_ROW_CAP", 5)
    hour = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0)
    with connect(db) as conn:
        for number in range(30):
            many_combinations(conn, f"host-{number}.io", hour, 4)
        total = conn.execute("SELECT count(*) AS n, sum(lookups) AS lookups FROM lookup_signals_hourly").fetchone()
        folded = conn.execute("SELECT lookups FROM lookup_signals_hourly WHERE site_host=%s", (lookup_signals.OVERFLOW_HOST,)).fetchall()
    assert total["n"] <= 41 and total["lookups"] == 120
    assert len(folded) == 1 and folded[0]["lookups"] == 120 - 40


def test_a_new_hour_starts_with_fresh_caps(db):
    hour = datetime.now(timezone.utc).replace(minute=5, second=0, microsecond=0)
    with connect(db) as conn:
        many_combinations(conn, HOST, hour, 50)
        many_combinations(conn, HOST, hour + timedelta(hours=1), 3)
        n = conn.execute("SELECT count(*) AS n FROM lookup_signals_hourly WHERE hour=%s", ((hour + timedelta(hours=1)).replace(minute=0),)).fetchone()["n"]
    assert n == 3


def test_the_source_table_is_bounded_per_day_and_lookups_are_still_counted(db, monkeypatch):
    monkeypatch.setattr(lookup_signals, "SOURCES_DAY_ROW_CAP", 3)
    with connect(db) as conn:
        for number in range(8):
            record(conn, 0, ip=f"198.51.100.{number + 1}")
        assert conn.execute("SELECT count(*) AS n FROM lookup_sources_daily").fetchone()["n"] == 3
        assert conn.execute("SELECT sum(lookups) AS n, sum(sources) AS s FROM lookup_signals_hourly").fetchone() == {"n": 8, "s": 3}


def test_through_the_route_a_hundred_combinations_make_a_bounded_number_of_rows(db, client):
    for number in range(110):
        lookup(client, error_class=("site_access", "login_auth", "human_verification", "service_error")[number % 4],
               failure_stage=("before_target_response", "response_observed", "access_denial_observed", "unknown")[number // 4 % 4],
               http_status=(401, 403, 429, 503, 451, 402, 407, 408)[number // 16 % 8], challenge=("header", "none")[number // 2 % 2])
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM lookup_signals_hourly").fetchone()["n"] <= lookup_signals.HOST_HOUR_ROW_CAP + 2
        assert conn.execute("SELECT sum(lookups) AS n FROM lookup_signals_hourly").fetchone()["n"] == 110


def test_public_mode_register_gate_applies_to_signals(db, monkeypatch):
    """A host outside the bounded lookup register is answered but not counted (the same gate as lookup_metrics)."""
    monkeypatch.setattr("ahn.service.QUERIED_HOST_CAP", 1)
    client = TestClient(create_app(db))
    assert lookup(client, host="first-site.io", error_class="site_access").status_code == 200   # takes the only slot
    assert lookup(client, host="second-site.io", error_class="site_access").status_code == 200  # register is full
    assert {r["site_host"] for r in rows(db)} == {"first-site.io"}
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) AS n FROM lookup_metrics WHERE site_host='second-site.io'").fetchone()["n"] == 0


# --- IPv6, concurrency, locks, savepoint, queue ---------------------------------------------------

def test_ipv6_addresses_of_one_subscriber_count_as_one_source(db, client):
    for number in range(1, 6):
        lookup(client, ip=f"2001:db8:1:2::{number}", error_class="site_access")
    (row,) = rows(db)
    assert row["lookups"] == 5 and row["sources"] == 1
    assert len(rows(db, "lookup_sources_daily")) == 1
    lookup(client, ip="2001:db8:1:3::1", error_class="site_access")
    assert sum(r["sources"] for r in rows(db)) == 2


def test_two_first_lookups_of_one_source_count_it_once(db):
    """Concurrent first lookups: whichever inserts the source row counts it; the other sees it already there."""
    with connect(db) as first, connect(db) as second:
        a = lookup_signals._new_source(first, T0.date(), HOST, "d" * 64, T0, "block", "unknown")
        first.commit()
        b = lookup_signals._new_source(second, T0.date(), HOST, "d" * 64, T0, "block", "unknown")
    assert (a, b) == (True, False)
    with connect(db) as one, connect(db) as two:
        record(one, 0)
        one.commit()
        record(two, 1)
        two.commit()
    assert sum(r["sources"] for r in rows(db)) == 1


def test_a_locked_table_does_not_stall_the_lookup(db, client):
    import time
    holder = connect(db)
    try:
        holder.execute("LOCK TABLE lookup_signals_hourly IN ACCESS EXCLUSIVE MODE")
        started = time.monotonic()
        response = lookup(client, error_class="site_access")
        elapsed = time.monotonic() - started
    finally:
        holder.rollback()
        holder.close()
    assert response.status_code == 200
    assert elapsed < 3, elapsed  # gave up after 200 ms instead of waiting for the lock
    with connect(db) as conn:  # the bookkeeping before it was kept
        assert conn.execute("SELECT sum(checks) AS n FROM lookup_metrics WHERE site_host=%s", (HOST,)).fetchone()["n"] == 1
    assert rows(db) == []


def test_a_signal_failure_after_some_writes_leaves_none_of_them(db, client, monkeypatch):
    """The savepoint claim: a write that succeeded before the failure is undone with it, and the lookup is unaffected."""
    def half_then_fail(conn, pepper, host, *_args, **_kwargs):
        conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                     "VALUES ((now() AT TIME ZONE 'UTC')::date,%s,repeat('e',64),now(),'block','unknown')", (host,))
        raise RuntimeError("fails after a write")
    monkeypatch.setattr(lookup_signals, "record", half_then_fail)
    response = lookup(client, error_class="site_access")
    assert response.status_code == 200
    assert rows(db, "lookup_sources_daily") == [] and rows(db) == []


def test_queued_reads_are_committed_before_the_worker_is_woken(db, monkeypatch):
    from ahn import access_profile, background_reads
    monkeypatch.setattr("ahn.service.cached_profile", access_profile.cached_profile)  # let the lookup queue a first read
    seen = []

    def wake_spy():
        with connect(db) as other:  # another connection, as the worker's would be
            seen.append(other.execute("SELECT count(*) AS n FROM background_reads WHERE site_host=%s", (HOST,)).fetchone()["n"])
    monkeypatch.setattr(background_reads, "wake", wake_spy)
    assert lookup(TestClient(create_app(db)), error_class="site_access").status_code == 200
    with connect(db) as conn:
        queued = conn.execute("SELECT count(*) AS n FROM background_reads WHERE site_host=%s", (HOST,)).fetchone()["n"]
    assert queued >= 1 and seen == [queued]  # everything queued was already visible when the worker was woken


# --- review round 3 -----------------------------------------------------------------------------

def test_a_known_source_is_found_by_key_without_the_cap_count(db, monkeypatch):
    with connect(db) as conn:
        record(conn, 0)
        monkeypatch.setattr(lookup_signals, "_new_source", lambda *a, **k: pytest.fail("the cap count ran for a known source"))
        monkeypatch.setattr(lookup_signals, "SOURCES_DAY_ROW_CAP", 1)  # the table is "full": a known source still updates
        assert record(conn, 20) == "use_official_path"
        assert conn.execute("SELECT count(*) AS n FROM lookup_sources_daily").fetchone()["n"] == 1


def test_the_cut_to_the_hour_runs_even_when_another_cleanup_step_fails(db, monkeypatch):
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    old = now - timedelta(minutes=70)
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                     "VALUES ((now() AT TIME ZONE 'UTC')::date,'cutme',repeat('f',64),%s,'block','unknown')", (old,))
    def broken(*_a, **_k):
        raise RuntimeError("an earlier cleanup step failed")
    monkeypatch.setattr(platform_metrics, "prune_reports", broken)
    with pytest.raises(RuntimeError):
        platform_metrics.prune_analytics(db.database_url)
    assert rows(db, "lookup_sources_daily")[0]["last_at"] == old.replace(minute=0)


def test_the_cut_to_the_hour_is_utc_whatever_the_session_time_zone(db):
    old = (datetime.now(timezone.utc) - timedelta(minutes=70)).replace(second=0, microsecond=0)
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,last_at,last_group,last_action) "
                     "VALUES ((now() AT TIME ZONE 'UTC')::date,'tz',repeat('g',64),%s,'block','unknown')", (old,))
        conn.execute("ALTER DATABASE ahn_test SET timezone TO 'Asia/Kolkata'")  # UTC+5:30: a local cut would land on :30 UTC
    try:
        platform_metrics.cut_source_minutes(db.database_url)
    finally:
        with connect(db) as conn:
            conn.execute("ALTER DATABASE ahn_test RESET timezone")
    assert rows(db, "lookup_sources_daily")[0]["last_at"] == old.replace(minute=0)
