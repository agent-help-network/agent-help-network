"""D75 against the dedicated ahn_test database (migration 035): the change history of a host's declared access policy
(recorded as short codes when a profile read or a home-page check differs from the one before), the three cause hints
that read it, and the optional `host_change` list of the offline bundle. Stub readers only; no real host is contacted."""

from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone

import pytest

from ahn import access_profile, host_access_changes as hac

# Pure parts first: they need no database.


def test_profile_codes_keep_only_definitive_answers():
    tok_a, tok_b, tok_c = hac.verdict_tokens()[1:4]
    robots = {"rules": [{"agent": tok_a, "root_allowed": False}, {"agent": tok_b, "root_allowed": True}]}
    codes = hac.profile_codes("ok", robots, "present", {"allowed": False, "reason": "robots_disallowed"})
    assert codes["robots_status"] == "ok" and codes["llms_presence"] == "present"
    assert codes[hac.VERDICT_PREFIX + hac.OUR_TOKEN] == "block"
    assert codes[hac.VERDICT_PREFIX + tok_a] == "block" and codes[hac.VERDICT_PREFIX + tok_b] == "allow"
    assert hac.VERDICT_PREFIX + tok_c not in codes          # no rule for that token: nothing to compare
    # an error, a pending read or an unknown llms state is not a reading of the policy
    assert "robots_status" not in hac.profile_codes("error", None, "unknown", None)
    assert "llms_presence" not in hac.profile_codes("ok", None, "error", None)
    # a missing robots.txt allows every token
    absent = hac.profile_codes("absent", None, "absent", {"allowed": True})
    assert absent[hac.VERDICT_PREFIX + hac.OUR_TOKEN] == "allow"
    assert all(absent[hac.VERDICT_PREFIX + token] == "allow" for token in hac.verdict_tokens()[1:])
    # a refusal that is not a robots verdict says nothing about robots
    assert hac.VERDICT_PREFIX + hac.OUR_TOKEN not in hac.profile_codes("ok", None, "absent", {"allowed": False, "reason": "other"})


def test_home_codes_skip_passing_failures_and_name_no_vendor_as_none():
    assert hac.home_codes("unreachable", "cloudflare") == {} and hac.home_codes("not_measured", None) == {}
    assert hac.home_codes("server_error", None) == {} and hac.home_codes(None, None) == {}
    assert hac.home_codes("bot_challenge", "cloudflare") == {"home_outcome": "bot_challenge", "bot_vendor": "cloudflare"}
    assert hac.home_codes("reachable", None) == {"home_outcome": "reachable", "bot_vendor": "none"}
    assert hac.home_codes("reachable", "not a short code at all, far too long") == {"home_outcome": "reachable", "bot_vendor": "none"}


def test_declared_codes_ignore_pending_and_failed_markers():
    from ahn import site_declared as sd

    assert hac.declared_codes(None) == {} and hac.declared_codes({"version": "other-version"}) == {}
    assert hac.declared_codes({"version": sd.VERSION, sd.PENDING: True}) == {}
    declared = {"version": sd.VERSION, "api_catalog": {"status": "declared"},
                "tdm": {"status": "declared", "root_reservation": 1, "any_reserved": True}}
    assert hac.declared_codes(declared) == {"site_declared_api": "declared", "tdm_root": "reserved", "site_declared_reservation": "reserved"}
    none = {"version": sd.VERSION, "api_catalog": {"status": "none_declared"}, "tdm": {"status": "none_declared"}}
    assert hac.declared_codes(none) == {"site_declared_api": "none", "tdm_root": "none", "site_declared_reservation": "none"}
    # a file that was not read leaves its fields out
    assert hac.declared_codes({"version": sd.VERSION, "api_catalog": {"status": "error"}, "tdm": {"status": "error"}}) == {}


def test_changes_compare_only_fields_present_in_both_readings():
    assert hac.changes(None, {"a": "x"}) == [] and hac.changes({}, {"a": "x"}) == []
    assert hac.changes({"a": "x", "b": "y"}, {"a": "x", "b": "z", "c": "w"}) == [("b", "y", "z")]
    assert hac.changes({"a": "x", "gone": "y"}, {"a": "x"}) == []


def test_field_names_are_a_closed_list_including_every_vendor_token():
    assert all(hac.valid_field(field) for field in hac.PLAIN_FIELDS)
    assert all(hac.valid_field(hac.VERDICT_PREFIX + token) for token in hac.verdict_tokens())
    assert not hac.valid_field("robots_txt") and not hac.valid_field("robots_verdict:") and not hac.valid_field("robots_verdict:a b")
    assert hac.verdict_tokens()[0] == hac.OUR_TOKEN


def test_retention_constants_match_the_prune_and_the_policy_statement():
    from ahn import platform_metrics
    import inspect

    source = inspect.getsource(platform_metrics.prune_analytics)
    assert f"DELETE FROM host_access_changes WHERE at < now() - interval '{hac.RETENTION_DAYS} days'" in source
    assert hac.RETENTION_DAYS == 90 and hac.BUNDLE_DAYS == 30


def test_the_three_cause_lines_are_printable_ascii_within_120_characters():
    from ahn import cause

    start, end = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc), datetime(2027, 1, 2, 23, 59, tzinfo=timezone.utc)
    for key in ("robots_now_disallows", "terms_signal_changed", "new_bot_defense"):
        for line in (cause.line_for(key), cause.line_for(key, start), cause.line_for(key, start, end)):
            assert 0 < len(line) <= cause.LINE_MAX and re.fullmatch(r"[ -~]+", line), (key, line)
    assert "not an access block" in cause.line_for("terms_signal_changed").lower()
    assert cause.INFO_ONLY_HINTS == {"terms_signal_changed"}


def test_bundle_host_change_entries_and_their_check():
    from ahn import bundle

    at = datetime(2026, 11, 20, 13, 45, tzinfo=timezone.utc)
    rows = [{"field": "llms_presence", "from_code": "present", "to_code": "absent", "at": at},
            {"field": "robots_verdict:AgentHelpNetwork", "from_code": "allow", "to_code": "block", "at": at - timedelta(days=3)}]
    entries = bundle.host_change_entries(rows)
    assert [e["day"] for e in entries] == ["2026-11-17", "2026-11-20"]                 # oldest first, cut to the UTC day
    assert set(entries[0]) == {"field", "from", "to", "day"}
    bundle._check_host_change(entries)
    bundle._check_host_change(None)
    for bad in ([], "x", [{**entries[0], "extra": 1}], [{**entries[0], "field": "not_a_field"}], [{**entries[0], "from": "x" * 25}],
                [{**entries[0], "day": "2026-11-17T10:00:00Z"}], [entries[0]] * (bundle.MAX_HOST_CHANGES + 1)):
        with pytest.raises(bundle.BundleError):
            bundle._check_host_change(bad)
    many = [{"field": "llms_presence", "from_code": "a", "to_code": "b", "at": at + timedelta(days=i)} for i in range(60)]
    assert len(bundle.host_change_entries(many)) == bundle.MAX_HOST_CHANGES


# Database tests.

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
needs_db = pytest.mark.skipif(not TEST_URL, reason="AHN_TEST_DATABASE_URL is required")
if TEST_URL:
    os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)  # ahn.main builds a module-level app from the environment
    os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")
    from fastapi.testclient import TestClient

    from ahn import background_reads, bundle, cause, platform_metrics, service, site_probe
    from ahn.db import connect, migrate
    from ahn.main import create_app
    from ahn.models import CheckResponse
    from ahn.profile_warm import load_seed_hosts
    from ahn.service import PUBLIC_REPORTER_ID
    from ahn.settings import Settings

PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
HOST = "changes-case.org"
ALLOW = b"User-agent: *\nAllow: /\n"
BLOCK_US = b"User-agent: AgentHelpNetwork\nDisallow: /secret-path-never-stored\n\nUser-agent: AgentHelpNetwork\nDisallow: /\n"


def settings_with(*, hint: bool = True):
    return Settings(TEST_URL, PEPPER, public_mode=True, reporting_enabled=True, collect_lookup_metrics=True,
                    response_guide=True, cause_hint=hint)


@pytest.fixture
def db(monkeypatch):
    settings = settings_with()
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, request_metrics, "
                     "traffic_attribution_daily, traffic_client_daily, mcp_check_metrics, public_intake_daily, public_intake_host_daily, "
                     "status_cache, status_sources, queried_hosts, host_access_profile, background_reads, provider_status_events, "
                     "outage_episodes, site_probe, site_probe_daily, lookup_signals_hourly, lookup_sources_daily, host_access_changes, "
                     "public_bundle RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None, **_kw: None)
    monkeypatch.setattr(service, "cached_profile", access_profile.cached_profile)
    monkeypatch.setattr(service, "official_paths", lambda host: None)
    monkeypatch.setattr(site_probe, "PAUSE_SECONDS", 0.0)
    return settings


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class Reader:
    """Stands in for the robots.txt / llms.txt / standard-file reader."""

    def __init__(self, robots=ALLOW, llms=False):
        self.robots, self.llms = robots, llms

    def __call__(self, host, path, **_):
        if path == "/robots.txt":
            return ("ok", self.robots, "http_200")
        if path == "/llms.txt":
            return ("ok", b"# notes\n", "http_200") if self.llms else ("absent", None, "http_404")
        return ("absent", None, "http_404")


def read(db, reader, at, host=HOST):
    return access_profile.get_profile(db, host, now=at, reader=reader)


def changes(db, host=HOST):
    with connect(db) as conn:
        return [(r["field"], r["from_code"], r["to_code"], r["source"]) for r in
                conn.execute("SELECT field,from_code,to_code,source FROM host_access_changes WHERE site_host=%s ORDER BY id", (host,))]


def seed_change(db, field, before, after, *, host=HOST, ago=timedelta(hours=2), source="read"):
    with connect(db) as conn:
        conn.execute("INSERT INTO host_access_changes (site_host,at,field,from_code,to_code,source) VALUES (%s,%s,%s,%s,%s,%s)",
                     (host, now() - ago, field, before, after, source))


def look(settings, host, **params):
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/incidents/check", params={"site_host": host, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    CheckResponse(**body)
    return body


def without_cause(body):
    return {k: v for k, v in body.items() if k not in ("cause", "summary_line", "generated_at", "feedback_token", "lookup_activity")}


@needs_db
def test_first_read_and_unchanged_reads_record_nothing_then_each_changed_field_one_row(db):
    t0 = now() - timedelta(days=3)
    read(db, Reader(), t0)
    assert changes(db) == []                                       # nothing to compare with
    read(db, Reader(), t0 + timedelta(hours=25))
    assert changes(db) == []                                       # the same policy again
    read(db, Reader(llms=True), t0 + timedelta(hours=50))
    assert changes(db) == [("llms_presence", "absent", "present", "read")]
    read(db, Reader(BLOCK_US), t0 + timedelta(hours=75))             # blocked: llms.txt is not read, so it is not compared
    assert changes(db)[1:] == [("robots_verdict:AgentHelpNetwork", "allow", "block", "read")]
    read(db, Reader(BLOCK_US), t0 + timedelta(hours=100))
    assert len(changes(db)) == 2                                   # nothing new


@needs_db
def test_only_short_codes_are_stored_never_text_paths_or_headers(db):
    t0 = now() - timedelta(days=2)
    read(db, Reader(), t0)
    read(db, Reader(llms=True), t0 + timedelta(hours=30))
    read(db, Reader(BLOCK_US), t0 + timedelta(hours=60))
    with connect(db) as conn:
        rows = conn.execute("SELECT * FROM host_access_changes").fetchall()
        columns = {r["column_name"] for r in conn.execute("SELECT column_name FROM information_schema.columns WHERE table_name='host_access_changes'")}
    assert columns == {"id", "site_host", "at", "field", "from_code", "to_code", "source"}
    assert rows
    blob = " ".join(str(v) for row in rows for v in row.values()).lower()
    for forbidden in ("secret-path", "disallow", "user-agent", "/", "notes"):
        assert forbidden not in blob, forbidden
    assert all(re.fullmatch(r"[a-z0-9_.-]{1,24}", row["from_code"]) and re.fullmatch(r"[a-z0-9_.-]{1,24}", row["to_code"]) for row in rows)


@needs_db
def test_a_recording_failure_never_breaks_the_read(db, monkeypatch):
    t0 = now() - timedelta(days=2)
    read(db, Reader(), t0)

    def boom(*_a, **_k):
        raise RuntimeError("comparison failed")

    monkeypatch.setattr(hac, "profile_codes", boom)
    profile = read(db, Reader(llms=True), t0 + timedelta(hours=30))
    assert profile["robots_status"] == "ok" and profile["llms_txt_status"] == "present"
    with connect(db) as conn:
        stored = conn.execute("SELECT llms_txt_status FROM host_access_profile WHERE site_host=%s", (HOST,)).fetchone()
    assert stored["llms_txt_status"] == "present"                 # the cache row stands
    assert changes(db) == []


@needs_db
def test_record_with_the_table_missing_returns_zero_and_leaves_the_connection_usable(db):
    with connect(db) as conn:
        conn.execute("ALTER TABLE host_access_changes RENAME TO host_access_changes_away")
        try:
            assert hac.record(conn, HOST, [("llms_presence", "absent", "present")], now(), "read") == 0
            assert conn.execute("SELECT 1 AS one").fetchone()["one"] == 1
            assert hac.snapshot(conn, "never-read.example") is None
            assert bundle.host_changes(conn, [HOST], now()) == {}
        finally:
            conn.execute("ALTER TABLE host_access_changes_away RENAME TO host_access_changes")


@needs_db
def test_record_refuses_unknown_fields_codes_and_sources(db):
    with connect(db) as conn:
        assert hac.record(conn, HOST, [("surprise", "a", "b"), ("llms_presence", "A B", "present"), ("llms_presence", "x" * 25, "y")], now(), "read") == 0
        assert hac.record(conn, HOST, [("llms_presence", "absent", "present")], now(), "lookup") == 0
        assert hac.record(conn, HOST, [("llms_presence", "absent", "present")], now(), "read") == 1


@needs_db
def test_the_migration_constraints_hold(db):
    import psycopg

    for values in (("nope", "a", "b", "read"), ("llms_presence", "a", "a", "read"), ("llms_presence", "a", "b", "other"),
                   ("robots_verdict:bad token", "a", "b", "read")):
        with connect(db) as conn:
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute("INSERT INTO host_access_changes (site_host,at,field,from_code,to_code,source) VALUES (%s,now(),%s,%s,%s,%s)",
                             (HOST, *values))
    with connect(db) as conn:
        conn.execute("INSERT INTO host_access_changes (site_host,at,field,from_code,to_code,source) VALUES (%s,now(),'robots_verdict:GPTBot','allow','block','home_check')", (HOST,))
        assert conn.execute("SELECT 1 FROM pg_indexes WHERE indexname='host_access_changes_host_at'").fetchone()


@needs_db
def test_a_home_check_records_the_outcome_change_and_the_first_check_nothing(db, monkeypatch):
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    with connect(db) as conn:
        conn.execute("TRUNCATE probe_breaker_daily, site_probe_set, probe_state, bandwidth_guard_state")
        conn.execute("INSERT INTO bandwidth_guard_state (name,period_start,checked_at,usage_gb,state) VALUES ('web',now(),now(),1,'normal')")
    class Home:
        def __init__(self, status):
            self.status = status

        def __call__(self, host):
            return {"label": None, "status": self.status, "headers": {}, "cookies": [], "body": b"<html>ok</html>", "complete": True,
                    "ms": 80, "bytes_read": 15}

    t0 = now() - timedelta(days=3)
    read(db, Reader(), t0)
    assert site_probe.measure(db, HOST, "lookup", now=t0, reader=Home(200), paced=False) is not None
    assert changes(db) == []
    later = t0 + timedelta(days=30)
    read(db, Reader(), later)                                       # the robots verdict the check relies on must be fresh
    assert site_probe.measure(db, HOST, "lookup", now=later, reader=Home(403), paced=False) is not None
    found = [c for c in changes(db) if c[3] == "home_check"]
    assert found and found[0][0] == "home_outcome" and found[0][1] == "reachable" and found[0][2] != "reachable"


@needs_db
def test_prune_deletes_rows_past_90_days_only(db):
    seed_change(db, "llms_presence", "absent", "present", ago=timedelta(days=91))
    seed_change(db, "llms_presence", "present", "absent", ago=timedelta(days=89))
    platform_metrics.prune_analytics(TEST_URL)
    assert changes(db) == [("llms_presence", "present", "absent", "read")]


# The three hints.

@needs_db
def test_robots_now_disallows_needs_one_change_and_a_block_that_still_stands(db):
    seed_change(db, "robots_verdict:AgentHelpNetwork", "allow", "block", ago=timedelta(hours=3))
    got = look(db, HOST, error_class="human_verification")["cause"]
    assert (got["branch"], got["hint"], got["confidence"]) == ("policy_closed", "robots_now_disallows", "high")
    assert got["basis"] == ["host_access_changes"] and "robots.txt" in got["line"]
    assert got["window"]["from"]
    # the block was lifted again: no longer a cause
    seed_change(db, "robots_verdict:AgentHelpNetwork", "block", "allow", ago=timedelta(hours=1))
    again = look(db, HOST, error_class="human_verification").get("cause")
    assert again is None or again["hint"] != "robots_now_disallows"


@needs_db
def test_robots_now_disallows_window_service_error_and_vendor_token(db):
    seed_change(db, "robots_verdict:AgentHelpNetwork", "allow", "block", ago=cause_days(20))
    old = look(db, HOST, error_class="human_verification").get("cause")
    assert old is None or old["hint"] != "robots_now_disallows"                                # outside the 14 days
    # a service error is not a policy matter
    seed_change(db, "robots_verdict:AgentHelpNetwork", "block", "allow", host="svc.example.org", ago=cause_days(3))
    seed_change(db, "robots_verdict:AgentHelpNetwork", "allow", "block", host="svc.example.org", ago=timedelta(hours=1))
    svc = look(db, "svc.example.org", error_class="service_error").get("cause")
    assert svc is None or svc["hint"] != "robots_now_disallows"
    # a vendor token counts only for a caller that says it is that vendor
    vendor = access_profile.AGENT_VENDORS[0]
    token = access_profile.GOVERNING_TOKENS[vendor][0]
    seed_change(db, hac.VERDICT_PREFIX + token, "allow", "block", host="vendor.example.org", ago=timedelta(hours=2))
    none = look(db, "vendor.example.org", error_class="human_verification").get("cause")
    assert none is None or none["hint"] != "robots_now_disallows"
    hit = look(db, "vendor.example.org", error_class="human_verification", agent_vendor=vendor)["cause"]
    assert hit["hint"] == "robots_now_disallows"
    # a caller that names its vendor is not told about AHN's own token when its own token did not change
    seed_change(db, "robots_verdict:AgentHelpNetwork", "allow", "block", host="ours.example.org", ago=timedelta(hours=2))
    assert look(db, "ours.example.org", error_class="human_verification")["cause"]["hint"] == "robots_now_disallows"
    named = look(db, "ours.example.org", error_class="human_verification", agent_vendor=vendor).get("cause")
    assert named is None or named["hint"] != "robots_now_disallows"


def seed_probe(db, host, *, outcome, refusals):
    at = now() - timedelta(hours=1)
    with connect(db) as conn:
        conn.execute(
            """INSERT INTO site_probe (site_host,read_host,probed_at,outcome,fingerprint_version,next_due_at,consecutive_refusals,trigger_kind)
               VALUES (%s,%s,%s,%s,'v1',%s,%s,'daily')
               ON CONFLICT (site_host) DO UPDATE SET probed_at=EXCLUDED.probed_at, outcome=EXCLUDED.outcome,
                   consecutive_refusals=EXCLUDED.consecutive_refusals""",
            (host, host, at, outcome, at + timedelta(hours=6), refusals))


def cause_days(n):
    return timedelta(days=n)


@needs_db
def test_terms_signal_changed_is_information_and_never_leads_the_summary(db):
    seed_change(db, "llms_presence", "present", "absent", ago=timedelta(hours=5))
    body = look(db, HOST, error_class="human_verification")
    got = body["cause"]
    assert (got["hint"], got["confidence"], got["branch"]) == ("terms_signal_changed", "medium", "undetermined")
    assert "not an access block" in got["line"].lower()
    assert not body["summary_line"].startswith(got["line"])
    # reserved data-mining signal added, and still so
    seed_change(db, "tdm_root", "none", "reserved", host="tdm.example.org", ago=timedelta(hours=5))
    assert look(db, "tdm.example.org", error_class="human_verification")["cause"]["hint"] == "terms_signal_changed"
    # the signal went back: gone
    seed_change(db, "tdm_root", "reserved", "none", host="tdm.example.org", ago=timedelta(hours=1))
    back = look(db, "tdm.example.org", error_class="human_verification").get("cause")
    assert back is None or back["hint"] != "terms_signal_changed"
    # information never displaces a hint: one home refusal stays home_gate_unconfirmed, a normal check your_side_likely
    for name, refused in (("terms-gate.example.org", True), ("terms-side.example.org", False)):
        seed_change(db, "llms_presence", "present", "absent", host=name, ago=timedelta(hours=5))
        seed_probe(db, name, outcome="bot_challenge" if refused else "reachable", refusals=1 if refused else 0)
        assert look(db, name, error_class="human_verification")["cause"]["hint"] == ("home_gate_unconfirmed" if refused else "your_side_likely")
    # and it fills the place where no cause would be given: a measured home page that is neither normal nor refused
    empty = "terms-empty.example.org"
    seed_probe(db, empty, outcome="rate_limited", refusals=0)
    assert "cause" not in look(db, empty, error_class="site_access")
    seed_change(db, "llms_presence", "present", "absent", host=empty, ago=timedelta(hours=5))
    assert look(db, empty, error_class="site_access")["cause"]["hint"] == "terms_signal_changed"


@needs_db
def test_new_bot_defense_only_when_the_vendor_was_not_seen_in_the_14_days_before(db):
    seed_change(db, "bot_vendor", "none", "cloudflare", ago=timedelta(hours=4), source="home_check")
    # one refusal is never a policy change (D68): it stays undetermined
    seed_probe(db, HOST, outcome="bot_challenge", refusals=1)
    assert look(db, HOST, error_class="human_verification")["cause"]["hint"] == "home_gate_unconfirmed"
    seed_probe(db, HOST, outcome="bot_challenge", refusals=2)
    got = look(db, HOST, error_class="human_verification")["cause"]
    assert (got["hint"], got["confidence"]) == ("new_bot_defense", "medium")
    assert got["basis"] == ["host_access_changes", "site_probe_daily"]
    # the same vendor was already in the daily counts a few days before: not new
    seen = "seen.example.org"
    seed_change(db, "bot_vendor", "none", "cloudflare", host=seen, ago=timedelta(hours=4), source="home_check")
    seed_probe(db, seen, outcome="bot_challenge", refusals=2)
    with connect(db) as conn:
        conn.execute("INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes) VALUES (%s,%s,'bot_challenge','cloudflare','daily',1)",
                     ((now() - timedelta(days=5)).date(), seen))
    old = look(db, seen, error_class="human_verification").get("cause")
    assert old is None or old["hint"] != "new_bot_defense"
    assert old["hint"] == "home_now_gated"


@needs_db
def test_the_hints_change_neither_the_recommendation_nor_the_next_action(db):
    plain = look(db, HOST, error_class="human_verification")
    for field, before, after in (("robots_verdict:AgentHelpNetwork", "allow", "block"), ("llms_presence", "present", "absent"),
                                 ("bot_vendor", "none", "cloudflare")):
        seed_change(db, field, before, after, ago=timedelta(hours=2))
    with_rows = look(db, HOST, error_class="human_verification")
    assert with_rows["cause"]["hint"] in ("robots_now_disallows", "terms_signal_changed", "new_bot_defense")
    assert with_rows["recommendation"] == plain["recommendation"]
    assert with_rows["next_action"]["do"] == plain["next_action"]["do"]
    assert without_cause(with_rows) == without_cause(plain)
    off = look(settings_with(hint=False), HOST, error_class="human_verification")
    assert "cause" not in off and without_cause(off) == without_cause(plain)


# The bundle.

@needs_db
def test_bundle_carries_changes_of_curated_hosts_only_and_only_the_last_30_days(db, monkeypatch):
    seed = load_seed_hosts()[0]
    looked_up = "looked-up-only.example"
    with connect(db) as conn:
        for host in (seed, looked_up):
            conn.execute("""INSERT INTO host_access_profile (site_host,fetched_at,robots_status,robots,llms_txt_status,robots_detail,llms_detail,
                                                             read_host,origin_operator)
                            VALUES (%s, now() - interval '2 hours', 'ok', '{"rules": []}', 'absent', 'http_200', 'http_404', %s,
                                    'agenthelpnetwork.com')""", (host, host))
    seed_change(db, "llms_presence", "present", "absent", host=seed, ago=timedelta(days=2))
    seed_change(db, "robots_verdict:AgentHelpNetwork", "allow", "block", host=seed, ago=timedelta(days=45))     # older than 30 days
    seed_change(db, "llms_presence", "present", "absent", host=looked_up, ago=timedelta(days=2))              # not curated
    monkeypatch.setenv("AHN_OPERATOR_NAME", "Example Operator")
    monkeypatch.delenv("AHN_EXPORT_SIGNING_KEY", raising=False)
    with connect(db) as conn:
        built = bundle.build_from_db(conn)
    records = bundle.read_records(built.body)
    profiles = {r["payload"]["site_host"]: r["payload"] for r in records if r["kind"] == "access_profile"}
    assert set(profiles) == {seed}
    change = profiles[seed]["host_change"]
    assert len(change) == 1 and change[0]["field"] == "llms_presence" and set(change[0]) == {"field", "from", "to", "day"}
    assert looked_up.encode() not in __import__("gzip").decompress(built.body)
    # a host with no recent change carries no such field at all
    with connect(db) as conn:
        conn.execute("DELETE FROM host_access_changes")
        built = bundle.build_from_db(conn)
    assert all("host_change" not in r["payload"] for r in bundle.read_records(built.body) if r["kind"] == "access_profile")
