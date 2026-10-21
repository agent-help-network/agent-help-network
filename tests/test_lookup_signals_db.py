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
    ({"http_status": "403", "challenge": "header", "retry_after": "60_3600", "via": "unknown"}, ("403", "header", "60_3600", "unknown")),
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
    baseline = comparable(lookup(client, ip=OPERATOR_IP, **case))  # an excluded lookup: nothing is recorded for it
    assert rows(db) == []
    for codes in CODES:
        counted = comparable(lookup(client, **case, **codes))
        assert counted == baseline, (case, codes)
    assert rows(db)  # and the codes really were recorded


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
            conn.execute("INSERT INTO lookup_sources_daily (day,site_host,source_digest,first_hour,last_at,last_group,last_action) "
                         "VALUES (CURRENT_DATE - %s,%s,repeat('a',64),now(),now(),'block','unknown')", (back, label))
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
