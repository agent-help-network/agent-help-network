"""D85 against the dedicated ahn_test database: the stored measurement decides, the switch turns it off, nothing else moves.

Every lookup answers from a stored site_probe row (its next check is far off, so nothing is requested): a request to a host here
is a test failure."""

from __future__ import annotations

import itertools
import os
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from ahn import access_profile, background_reads, feedback, response_guide, service, site_probe  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.models import CheckResponse  # noqa: E402
from ahn.service import PUBLIC_REPORTER_ID  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.triage import EDGE_CODE  # noqa: E402

ALLOW = b"User-agent: *\nAllow: /\n"
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
HOSTS = [f"edge-{i}.org" for i in range(60)]
ERROR_CLASSES = (None, "site_access", "login_auth", "human_verification", "service_error")
STAGES = (None, "before_target_response", "response_observed", "access_denial_observed", "unknown")
VOLATILE = ("generated_at", "feedback_token", "lookup_activity")
HOUR = timedelta(hours=1)


def settings_with(*, edge: bool = True) -> Settings:
    return Settings(TEST_URL, PEPPER, public_mode=True, reporting_enabled=True, collect_lookup_metrics=True, edge_refusal_handoff=edge)


@pytest.fixture
def db(monkeypatch):
    settings = settings_with()
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
    monkeypatch.setattr(service, "cached_profile", access_profile.cached_profile)
    monkeypatch.setattr(service, "official_paths", lambda host: None)
    monkeypatch.setattr(site_probe, "PAUSE_SECONDS", 0.0)
    monkeypatch.setenv("AHN_SITE_PROBE", "1")

    def no_request(host):
        raise AssertionError("a request went out to " + host)

    monkeypatch.setattr(site_probe, "fetch_home", no_request)

    def reader(host, path, *, cap_bytes, **_):
        return ("ok", ALLOW, "http_200") if path == "/robots.txt" else ("absent", None, "http_404")

    monkeypatch.setattr(background_reads, "read_profile_file", reader)
    return settings


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def warm(settings, host):
    """The host has a fresh declared policy that does not block (so the stored decision is proceed) and no check yet."""
    service.check(settings, host)
    background_reads.process_due(settings, limit=50)
    with connect(settings) as conn:
        conn.execute("DELETE FROM site_probe WHERE site_host=%s", (host,))
        conn.execute("DELETE FROM background_reads")


def seed(settings, host, outcome, age, *, refusals=1, status=None):
    """The host's one stored measurement, ``age`` old, whose next check is far away."""
    warm(settings, host)
    at = now() - age
    with connect(settings) as conn:
        conn.execute("""INSERT INTO site_probe (site_host,read_host,probed_at,outcome,http_status,fingerprint_version,next_due_at,
                                                consecutive_refusals,trigger_kind)
                        VALUES (%s,%s,%s,%s,%s,'v1',%s,%s,'daily')
                        ON CONFLICT (site_host) DO UPDATE SET probed_at=EXCLUDED.probed_at, outcome=EXCLUDED.outcome,
                            http_status=EXCLUDED.http_status, next_due_at=EXCLUDED.next_due_at,
                            consecutive_refusals=EXCLUDED.consecutive_refusals""",
                     (host, host, at, outcome, status, now() + timedelta(days=30), refusals))
    return at


def look(settings, host, **kwargs):
    body = service.check(settings, host, **kwargs)
    CheckResponse(**body)  # the answer still validates against the published model
    return body


def strip(body: dict, skip) -> dict:
    out = {k: v for k, v in body.items() if k not in VOLATILE + skip}
    if isinstance(out.get("ahn_check"), dict):
        out["ahn_check"] = {k: v for k, v in out["ahn_check"].items() if k != "age_seconds"}  # two lookups a second apart
    return out


def same_but(on: dict, off: dict, *skip) -> bool:
    return strip(on, skip) == strip(off, skip)


def pair(settings, host, **kwargs):
    """The answer with the switch on and the answer with it off, for the same stored state."""
    return look(settings, host, **kwargs), look(settings_with(edge=False), host, **kwargs)


# (outcome, http status, age, consecutive refusals, changes?)
CASES = [
    ("refused", 403, 1 * HOUR, 1, True),
    ("refused", 401, 1 * HOUR, 1, True),
    ("bot_challenge", 403, 1 * HOUR, 1, True),
    ("refused", 403, timedelta(hours=23, minutes=59), 1, True),
    ("refused", 403, timedelta(hours=24, seconds=1), 1, False),
    ("bot_challenge", 403, timedelta(hours=24, seconds=1), 1, False),
    ("refused", 403, timedelta(hours=24, seconds=1), 2, True),
    ("refused", 403, timedelta(days=3), 1, False),
    ("refused", 403, timedelta(days=3), 2, True),
    ("bot_challenge", 403, timedelta(days=7, hours=23), 2, True),
    ("refused", 403, timedelta(days=8, hours=1), 5, False),          # the view drops a measurement over 8 days old
    ("reachable", 200, 1 * HOUR, 0, False),
    ("redirected_same_host", 301, 1 * HOUR, 0, False),
    ("login_wall", 401, 1 * HOUR, 2, False),
    ("rate_limited", 429, 1 * HOUR, 2, False),
    ("server_error", 503, 1 * HOUR, 2, False),
    ("server_error", 500, 1 * HOUR, 0, False),
    ("unreachable", None, 1 * HOUR, 0, False),
    ("other_status", 999, 1 * HOUR, 0, False),
    ("redirected_offsite", 302, 1 * HOUR, 0, False),
]


@pytest.mark.parametrize("index", range(len(CASES)))
def test_the_stored_measurement_decides_at_each_boundary_and_nothing_else_moves(db, index):
    outcome, status, age, refusals, changes = CASES[index]
    host = HOSTS[index]
    seed(db, host, outcome, age, refusals=refusals, status=status)
    on, off = pair(db, host, error_class="site_access")
    assert off["recommendation"]["action"] == "proceed"
    if not changes:
        assert same_but(on, off), (outcome, age)
        assert on["recommendation"]["action"] == "proceed"
        return
    assert on["recommendation"] == {"action": "hand_off_to_human", "confidence": "medium",
                                    "reasons": [EDGE_CODE, *off["recommendation"]["reasons"]]}
    assert [c["code"] for c in on["next_checks"]][:2] == ["locate_failure_stage", EDGE_CODE]
    assert [c for c in on["next_checks"] if c["code"] != EDGE_CODE] == off["next_checks"]
    assert on["next_action"]["do"] == "hand_off_to_human" and on["next_action"]["url"] is None
    # D101: a check over a day old says so in its first line; the decision is the same
    stale = on["ahn_check"]["freshness"] == "stale"
    assert on["summary_line"] == (response_guide.H3_STALE if stale else "The site's edge refused both you and AHN's own home-page check (ahn_check). Do not switch tools or user agents to retry; tell your user, who can open the page themselves.")
    # everything but the decision and the one note is the answer the switch off gives
    assert same_but(on, off, "recommendation", "next_checks", "summary_line", "next_action", "ahn_check")
    assert {k: v for k, v in on["next_action"].items() if k != "do"} == {k: v for k, v in off["next_action"].items() if k != "do"}
    assert on["ahn_check"].pop("note") == site_probe.SHOW_VIEW_NOTE_EDGE
    assert off["ahn_check"].pop("note") == site_probe.SHOW_VIEW_NOTE
    assert abs(on["ahn_check"].pop("age_seconds") - off["ahn_check"].pop("age_seconds")) <= 5
    assert on["ahn_check"] == off["ahn_check"]
    assert on.get("cause") == off.get("cause")


def test_only_site_access_with_no_stage_or_an_access_denial_changes_and_cause_never_does(db):
    seed(db, HOSTS[30], "refused", 1 * HOUR, refusals=1, status=403)
    changed = set()
    for error_class, stage in itertools.product(ERROR_CLASSES, STAGES):
        on, off = pair(db, HOSTS[30], error_class=error_class, failure_stage=stage)
        assert on.get("cause") == off.get("cause"), (error_class, stage)
        if on["recommendation"] != off["recommendation"]:
            changed.add((error_class, stage))
            assert same_but(on, off, "recommendation", "next_checks", "summary_line", "next_action", "ahn_check")
        else:
            assert same_but(on, off), (error_class, stage)
    assert changed == {("site_access", None), ("site_access", "access_denial_observed")}


def test_a_lookup_without_a_failure_signal_and_a_plain_view_are_not_changed(db):
    seed(db, HOSTS[31], "refused", 1 * HOUR, refusals=3, status=403)
    for kwargs in ({}, {"task_step": "status_check"}, {"task_step": "preflight"}, {"error_class": "site_access", "plain_view": True},
                   {"plain_view": True}):
        on, off = pair(db, HOSTS[31], **kwargs)
        assert same_but(on, off), kwargs
        assert on["recommendation"]["action"] == off["recommendation"]["action"]
        assert EDGE_CODE not in on["recommendation"]["reasons"]


def test_a_host_with_no_check_or_a_site_probe_that_is_off_is_not_changed(db, monkeypatch):
    warm(db, HOSTS[32])
    on, off = pair(db, HOSTS[32], error_class="site_access")          # no stored measurement, nothing requested (the stub raises)
    assert same_but(on, off) and EDGE_CODE not in on["recommendation"]["reasons"]
    seed(db, HOSTS[33], "refused", 1 * HOUR, refusals=3, status=403)
    monkeypatch.delenv("AHN_SITE_PROBE")
    on, off = pair(db, HOSTS[33], error_class="site_access")
    assert "ahn_check" not in on and same_but(on, off) and on["recommendation"]["action"] == "proceed"


def test_the_switch_off_is_the_answer_before_d85_and_the_default_is_on(db):
    assert Settings(TEST_URL, PEPPER).edge_refusal_handoff is True
    seed(db, HOSTS[34], "refused", 1 * HOUR, refusals=1, status=403)
    off = look(settings_with(edge=False), HOSTS[34], error_class="site_access")
    assert off["recommendation"]["action"] == "proceed" and off["ahn_check"]["note"] == site_probe.SHOW_VIEW_NOTE
    assert look(db, HOSTS[34], error_class="site_access")["recommendation"]["action"] == "hand_off_to_human"


def test_a_stored_row_that_is_not_the_measurement_shown_does_not_count(db):
    host = HOSTS[35]
    at = seed(db, host, "refused", timedelta(days=3), refusals=2, status=403)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM site_probe WHERE site_host=%s", (host,)).fetchone()
        shown = site_probe.view(row, now())
        assert site_probe.refusal_corroborated(conn, host, shown) is True
        # a newer measurement replaced the row after the view was made: another measurement, so it is not the one shown
        conn.execute("UPDATE site_probe SET probed_at=%s WHERE site_host=%s", (at + HOUR, host))
        assert site_probe.refusal_corroborated(conn, host, shown) is False
        conn.execute("UPDATE site_probe SET probed_at=%s,outcome='bot_challenge' WHERE site_host=%s", (at, host))
        assert site_probe.refusal_corroborated(conn, host, shown) is False
        conn.execute("UPDATE site_probe SET outcome='refused',consecutive_refusals=1 WHERE site_host=%s", (host,))
        assert site_probe.refusal_corroborated(conn, host, shown) is False
        conn.execute("DELETE FROM site_probe WHERE site_host=%s", (host,))
        assert site_probe.refusal_corroborated(conn, host, shown) is False
        # a fresh view needs no row at all
        assert site_probe.refusal_corroborated(conn, host, {**shown, "freshness": "fresh"}) is True
        for odd in (None, {}, {"status": "pending"}, {**shown, "status": "not_measured"}, {**shown, "outcome": "reachable", "freshness": "fresh"},
                    {**shown, "measured_at": "yesterday"}):
            assert site_probe.refusal_corroborated(conn, host, odd) is False, odd


def test_the_check_never_fails_a_lookup(db, monkeypatch):
    host = HOSTS[36]
    seed(db, host, "refused", timedelta(days=3), refusals=2, status=403)

    class Broken:
        def transaction(self):
            raise RuntimeError("database gone")

        def execute(self, *_a, **_k):
            raise RuntimeError("database gone")

    with connect(db) as conn:
        row = conn.execute("SELECT * FROM site_probe WHERE site_host=%s", (host,)).fetchone()
    assert site_probe.refusal_corroborated(Broken(), host, site_probe.view(row, now())) is False

    def boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(site_probe, "refusal_corroborated", boom)
    answer = look(db, host, error_class="site_access")
    assert answer["recommendation"]["action"] == "proceed" and EDGE_CODE not in answer["recommendation"]["reasons"]
    assert answer["ahn_check"]["note"] == site_probe.SHOW_VIEW_NOTE


def test_the_database_is_asked_only_by_a_lookup_that_can_change(db, monkeypatch):
    asked = []
    original = site_probe.refusal_corroborated
    monkeypatch.setattr(site_probe, "refusal_corroborated", lambda *a, **k: (asked.append(a[1]), original(*a, **k))[1])
    seed(db, HOSTS[37], "refused", timedelta(days=3), refusals=2, status=403)
    for kwargs in ({}, {"error_class": "service_error"}, {"error_class": "login_auth"}, {"error_class": "human_verification"},
                   {"error_class": "site_access", "failure_stage": "response_observed"}, {"error_class": "site_access", "plain_view": True},
                   {"task_step": "status_check"}):
        look(db, HOSTS[37], **kwargs)
    assert asked == []
    look(db, HOSTS[37], error_class="site_access")
    look(db, HOSTS[37], error_class="site_access", failure_stage="access_denial_observed")
    assert asked == [HOSTS[37], HOSTS[37]]


def test_the_feedback_token_and_the_adoption_record_carry_the_action_the_answer_gave(db):
    seed(db, HOSTS[38], "refused", 1 * HOUR, refusals=1, status=403)
    body = service.check(db, HOSTS[38], error_class="site_access", issue_feedback=True)
    assert body["recommendation"]["action"] == "hand_off_to_human"
    assert feedback.verify(PEPPER, body["feedback_token"]["token"], datetime.now(timezone.utc)).action == "hand_off_to_human"
    off = service.check(settings_with(edge=False), HOSTS[38], error_class="site_access", issue_feedback=True)
    assert feedback.verify(PEPPER, off["feedback_token"]["token"], datetime.now(timezone.utc)).action == "proceed"


def test_a_provider_wait_a_robots_block_or_an_official_path_come_before_the_edge_rule(db, monkeypatch):
    # The edge rule only replaces a proceed: with an official developer path the answer stays use_official_path.
    seed(db, HOSTS[39], "refused", 1 * HOUR, refusals=3, status=403)
    monkeypatch.setattr(service, "official_paths",
                        lambda host: {"name": "Example", "api_docs": "https://developer.example.com/docs", "developer_portal": None, "note": None})
    on, off = pair(db, HOSTS[39], error_class="site_access")
    assert on["recommendation"]["action"] == off["recommendation"]["action"] == "use_official_path" and same_but(on, off)
    # a curated customer page rides along on the hand-off exactly where the proceed answer carried it
    monkeypatch.setattr(service, "official_paths", lambda host: {
        "name": "Example", "api_docs": None, "developer_portal": None, "note": None,
        "consumer": {"paths": [{"kind": "customer_help", "url": "https://www.example.com/help", "source_url": "https://www.example.com/"}],
                     "verified_at": "2026-10-31"}})
    on, off = pair(db, HOSTS[39], error_class="site_access")
    assert on["recommendation"]["action"] == "hand_off_to_human" and off["recommendation"]["action"] == "proceed"
    assert on["next_action"]["customer_url"] == off["next_action"]["customer_url"] == "https://www.example.com/help"
    assert on["summary_line"].startswith("The site's edge refused both you and AHN's own check (ahn_check).")
