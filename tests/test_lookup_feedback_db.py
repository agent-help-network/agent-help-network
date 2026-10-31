"""Lookup feedback against the dedicated ahn_test database (migration 031, D58). No real host is read."""

from __future__ import annotations

import os
from dataclasses import replace
import base64
import threading
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
os.environ.setdefault("AHN_KEY_PEPPER", PEPPER)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import feedback, platform_metrics  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402

HOST = "demo-site.io"
OPERATOR_IP = "203.0.113.9"
CALLER_IP = "198.51.100.7"
TABLES = "lookup_feedback_hourly, lookup_feedback_used, lookup_signals_hourly, lookup_sources_daily"


def make_settings(**changes) -> Settings:
    # The pepper must be the one the app signs with: the module-level settings use the environment's.
    return replace(Settings(TEST_URL, os.environ["AHN_KEY_PEPPER"], public_mode=True, reporting_enabled=True,
                            collect_lookup_metrics=True, owner_allowed_ips=(OPERATOR_IP,)), **changes)


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


def client_for(settings):
    return TestClient(create_app(settings))  # no `with`: the background reader never starts


@pytest.fixture
def client(db):
    return client_for(db)


def lookup(client, ip=CALLER_IP, host=HOST, headers=None, **params):
    sent = {"CF-Connecting-IP": ip} if ip else {}
    return client.get("/v1/incidents/check", params={"site_host": host, **params}, headers={**sent, **(headers or {})})


def send(client, token, outcome="succeeded", path=None):
    body = {"token": token, "outcome": outcome}
    if path is not None:
        body["path"] = path
    return client.post("/v1/feedback", json=body)


def counts(settings):
    with connect(settings) as conn:
        return conn.execute("SELECT site_host,action,client_kind,outcome,path,responses,hour FROM lookup_feedback_hourly ORDER BY 1,2,3,4,5").fetchall()


def used(settings):
    with connect(settings) as conn:
        return conn.execute("SELECT token_digest, expires_at FROM lookup_feedback_used").fetchall()


# --- the token in the answer ----------------------------------------------------------------------

def test_a_blocked_lookup_gets_a_token_and_a_plain_lookup_does_not(client):
    blocked = lookup(client, error_class="site_access").json()
    assert set(blocked["feedback_token"]) == {"token", "expires_in_seconds", "outcomes", "paths"}
    assert list(blocked)[-1] == "feedback_token"
    assert "feedback_token" not in lookup(client).json()


def test_issuing_a_token_writes_nothing(db, client):
    for _ in range(3):
        assert "feedback_token" in lookup(client, error_class="site_access").json()
    assert used(db) == [] and counts(db) == []


def test_the_token_changes_nothing_else_in_the_answer(db):
    on = lookup(client_for(db), error_class="human_verification", failure_stage="access_denial_observed").json()
    off = lookup(client_for(replace(db, lookup_feedback=False)), error_class="human_verification", failure_stage="access_denial_observed").json()
    assert "feedback_token" in on and "feedback_token" not in off
    on.pop("feedback_token")
    for body in (on, off):
        body.pop("generated_at")
        body.pop("lookup_activity", None)  # moves as this test sends lookups
    assert on == off
    assert list(on) == list(off)  # same keys in the same order: recommendation, next_checks and the rest untouched


@pytest.mark.parametrize("settings_change,host", [
    ({"collect_lookup_metrics": False}, HOST),   # nothing could be counted
    ({}, "example.com"),                         # documentation example host
    ({}, "shop.tenant.demo-site.io"),            # not an eligible apex host
])
def test_no_token_where_feedback_could_not_be_counted(db, settings_change, host):
    body = lookup(client_for(replace(db, **settings_change)), host=host, error_class="site_access").json()
    assert "feedback_token" not in body


def test_the_token_names_the_apex_host_the_lookup_was_counted_under(db, client):
    body = lookup(client, host="www." + HOST, error_class="site_access").json()
    assert feedback.verify(db.key_pepper, body["feedback_token"]["token"], datetime.now(timezone.utc)).host == HOST


# --- sending it back ------------------------------------------------------------------------------

def test_feedback_is_counted_once_per_token(db, client):
    body = lookup(client, error_class="site_access", headers={"User-Agent": "ahn-claude-code-plugin/0.3.7"}).json()
    token = body["feedback_token"]["token"]
    response = send(client, token, "succeeded", "official_path")
    assert response.status_code == 200 and response.json() == {"accepted": True}
    assert response.headers["cache-control"] == "no-store"
    rows = counts(db)
    assert [(r["site_host"], r["action"], r["client_kind"], r["outcome"], r["path"], r["responses"]) for r in rows] == [
        (HOST, body["recommendation"]["action"], "plugin", "succeeded", "official_path", 1)]
    assert rows[0]["hour"].minute == 0 and rows[0]["hour"].second == 0
    # Reuse: refused, and nothing more is counted (also with a different outcome).
    for outcome in ("succeeded", "gave_up"):
        again = send(client, token, outcome)
        assert again.status_code == 409 and again.json() == {"error": "feedback_token_used"}
    assert [r["responses"] for r in counts(db)] == [1]
    # The used-token row holds a fingerprint and the expiry only.
    stored = used(db)
    assert len(stored) == 1 and stored[0]["token_digest"] == feedback.fingerprint(token) and token not in stored[0]["token_digest"]


def test_two_tokens_from_two_lookups_count_twice_in_one_row(db, client):
    for _ in range(2):
        token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
        assert send(client, token, "still_blocked").status_code == 200
    rows = counts(db)
    assert len(rows) == 1 and rows[0]["responses"] == 2 and rows[0]["path"] == "unknown"


def test_a_forged_token_is_refused_and_writes_nothing(db, client):
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    body, mac = token.split(".")
    flipped = ("B" if mac[0] == "A" else "A") + mac[1:]
    other_key = feedback.issue("another-pepper-also-at-least-thirty-two-chars", HOST, "proceed", "mcp", True, datetime.now(timezone.utc))
    for bad in (body + "." + flipped, other_key, "garbage"):
        response = send(client, bad)
        assert response.status_code == 400 and response.json() == {"error": "invalid_feedback_token"}
    assert used(db) == [] and counts(db) == []


def test_an_expired_token_is_refused(db, client):
    old = feedback.issue(db.key_pepper, HOST, "proceed", "mcp", True, datetime.now(timezone.utc) - timedelta(seconds=3601))
    response = send(client, old)
    assert response.status_code == 410 and response.json() == {"error": "feedback_token_expired"}
    assert used(db) == [] and counts(db) == []


def test_a_token_for_another_host_counts_for_that_host_only(db, client):
    other = lookup(client, host="other-site.io", error_class="site_access").json()["feedback_token"]["token"]
    lookup(client, error_class="site_access")
    assert send(client, other, "handed_off").status_code == 200
    assert [r["site_host"] for r in counts(db)] == ["other-site.io"]


def test_a_wrong_enum_is_refused_with_the_allowed_values(db, client):
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    response = send(client, token, "worked")
    assert response.status_code == 400 and response.json()["field"] == "outcome"
    response = send(client, token, "succeeded", "elsewhere")
    assert response.status_code == 400 and response.json()["allowed_values"] == ["official_path", "same_site", "unknown"]
    # The token was not spent by the refused requests.
    assert send(client, token, "succeeded").status_code == 200


@pytest.mark.parametrize("marker", [{"headers": {"X-AHN-Test": "1"}}, {"ip": OPERATOR_IP}])
def test_uncounted_lookups_get_a_token_whose_feedback_is_accepted_not_counted(db, client, marker):
    token = lookup(client, error_class="site_access", **marker).json()["feedback_token"]["token"]
    assert feedback.verify(db.key_pepper, token, datetime.now(timezone.utc)).counted is False
    assert send(client, token).status_code == 200
    assert counts(db) == [] and len(used(db)) == 1  # still one-time
    assert send(client, token).status_code == 409


def test_feedback_is_not_counted_while_lookup_metrics_are_off(db, client):
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    off = client_for(replace(db, collect_lookup_metrics=False))
    assert send(off, token).status_code == 200
    assert counts(db) == []


def test_the_switch_leaves_the_token_out_but_outstanding_tokens_still_work(db, client):
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    off = client_for(replace(db, lookup_feedback=False))
    assert "feedback_token" not in lookup(off, error_class="site_access").json()
    assert send(off, token).status_code == 200 and len(counts(db)) == 1


def test_mcp_hop_feedback_is_counted_under_the_mcp_kind(db, client):
    from ahn.tool_metrics import mcp_metric_token
    hop = {"X-AHN-MCP-Call": mcp_metric_token(db.key_pepper), "x-ahn-origin-ip": CALLER_IP}
    token = client.get("/v1/incidents/check", params={"site_host": HOST, "error_class": "login_auth"}, headers=hop).json()["feedback_token"]["token"]
    assert send(client, token, "handed_off").status_code == 200
    assert [r["client_kind"] for r in counts(db)] == ["mcp"]


def respellings(text: str) -> list[str]:
    decode = lambda part: base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))  # noqa: E731
    body, mac = text.split(".")
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    found = [body + "." + mac[:-1] + c for c in alphabet if c != mac[-1] and decode(mac[:-1] + c) == decode(mac)]
    found += [body[:-1] + c + "." + mac for c in alphabet if c != body[-1] and decode(body[:-1] + c) == decode(body)]
    return found


def test_another_spelling_of_a_used_token_is_not_counted_again(db, client):
    """B1: a different last character that decodes to the same bytes is refused (400) or seen as used (409)."""
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    assert send(client, token).status_code == 200
    variants = respellings(token)
    assert variants
    for variant in variants:
        assert send(client, variant).status_code in (400, 409)
    assert [r["responses"] for r in counts(db)] == [1] and len(used(db)) == 1


def test_another_spelling_before_first_use_is_refused_and_leaves_the_token_usable(db, client):
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    for variant in respellings(token):
        assert send(client, variant).json() == {"error": "invalid_feedback_token"}
    assert send(client, token).status_code == 200 and [r["responses"] for r in counts(db)] == [1]


def test_concurrent_reuse_is_accepted_once(db):
    app_client = client_for(db)
    token = lookup(app_client, error_class="site_access").json()["feedback_token"]["token"]
    start, codes = threading.Barrier(8), []

    def attempt():
        own = client_for(db)
        start.wait()
        codes.append(send(own, token).status_code)
    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert sorted(codes) == [200] + [409] * 7
    assert [r["responses"] for r in counts(db)] == [1] and len(used(db)) == 1


def test_a_fingerprint_is_kept_five_minutes_past_expiry(db, client):
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_feedback_used VALUES (%s,%s),(%s,%s)",
                     ("d" * 64, now - timedelta(minutes=2), "e" * 64, now - timedelta(minutes=6)))
    platform_metrics.prune_analytics(db.database_url)
    assert [r["token_digest"] for r in used(db)] == ["d" * 64]


def test_a_count_that_cannot_get_its_lock_is_skipped_and_the_feedback_still_accepted(db, client):
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    with connect(db) as holder:
        holder.execute("BEGIN")
        holder.execute("LOCK TABLE lookup_feedback_hourly IN ACCESS EXCLUSIVE MODE")
        response = send(client, token)
        holder.execute("ROLLBACK")
    assert response.status_code == 200 and response.json() == {"accepted": True}
    assert counts(db) == [] and len(used(db)) == 1
    assert send(client, token).status_code == 409


def test_a_token_response_is_not_cached_and_the_search_page_gets_none(db, client):
    response = lookup(client, error_class="site_access")
    assert "feedback_token" in response.json() and response.headers["cache-control"] == "no-store"
    assert "no-store" not in lookup(client).headers.get("cache-control", "")
    seen = []
    import ahn.service as service_module
    original = service_module.feedback.feedback_field
    service_module.feedback.feedback_field = lambda *a, **k: seen.append(a) or original(*a, **k)
    try:
        page = client.get("/en/search", params={"site_host": HOST, "error_class": "site_access"}, headers={"CF-Connecting-IP": CALLER_IP})
    finally:
        service_module.feedback.feedback_field = original
    assert page.status_code == 200 and seen == []


# --- retention ------------------------------------------------------------------------------------

def test_cleanup_removes_expired_fingerprints_and_old_counts(db, client):
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_feedback_used VALUES (%s,%s),(%s,%s)",
                     ("a" * 64, now - timedelta(minutes=6), "b" * 64, now + timedelta(minutes=30)))
        conn.execute("INSERT INTO lookup_feedback_hourly VALUES (%s,%s,'proceed','mcp','succeeded','unknown',1),(%s,%s,'proceed','mcp','succeeded','unknown',1)",
                     (now - timedelta(days=36), HOST, now - timedelta(days=34), HOST))
    platform_metrics.prune_analytics(db.database_url)
    assert [r["token_digest"] for r in used(db)] == ["b" * 64]
    assert len(counts(db)) == 1


def test_each_new_feedback_also_drops_expired_fingerprints(db, client):
    with connect(db) as conn:
        conn.execute("INSERT INTO lookup_feedback_used VALUES (%s,%s)", ("c" * 64, datetime.now(timezone.utc) - timedelta(minutes=6)))
    token = lookup(client, error_class="site_access").json()["feedback_token"]["token"]
    assert send(client, token).status_code == 200
    assert [r["token_digest"] for r in used(db)] == [feedback.fingerprint(token)]


def test_counts_past_the_hour_cap_fold_into_an_overflow_row(db, monkeypatch):
    monkeypatch.setattr(feedback, "HOUR_ROW_CAP", 1)
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        for host in ("one-site.io", "two-site.io"):
            feedback.record(conn, db.key_pepper, feedback.issue(db.key_pepper, host, "proceed", "mcp", True, now), "succeeded", "unknown", now, count=True)
    assert sorted(r["site_host"] for r in counts(db)) == ["(overflow)", "one-site.io"]
