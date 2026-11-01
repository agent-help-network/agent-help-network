"""D73 end to end over REST against the dedicated ahn_test database: the optional `cause` of a failure lookup, the
`failed_at` input, and the promise that it changes nothing else (recommendation, next_action.do) and is gone with
AHN_CAUSE_HINT=0. Rows are written straight into the tables the cause reads; nothing here contacts a real host."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402  (after the skip: ahn.main builds an app from the environment)

from ahn import access_profile, background_reads, cause, response_guide, service
from ahn.db import connect, migrate
from ahn.main import create_app
from ahn.models import CheckResponse
from ahn.service import PUBLIC_REPORTER_ID
from ahn.settings import Settings

ROBOTS = b"User-agent: *\nAllow: /\n"
PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
SECOND = timedelta(seconds=1)


def settings_with(*, hint: bool = True, guide: bool = True) -> Settings:
    return Settings(TEST_URL, PEPPER, public_mode=True, reporting_enabled=True, collect_lookup_metrics=True,
                    response_guide=guide, cause_hint=hint)


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
                     "outage_episodes, site_probe, site_probe_daily, lookup_signals_hourly, lookup_sources_daily RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
    monkeypatch.setattr(service, "cached_profile", access_profile.cached_profile)
    monkeypatch.setattr(service, "official_paths", lambda host: None)
    monkeypatch.setattr(background_reads, "read_profile_file",
                        lambda host, path, *, cap_bytes, **_: ("ok", ROBOTS, "http_200") if path == "/robots.txt" else ("absent", None, "http_404"))
    return settings


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def look(settings: Settings, host: str, **params) -> dict:
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/incidents/check", params={"site_host": host, **params})
    assert response.status_code == 200, response.text
    body = response.json()
    CheckResponse(**body)  # the answer still validates against the published model
    return body


def provider_event(host, at, to, before=None):
    return ("INSERT INTO provider_status_events (site_host,at,from_indicator,to_indicator) VALUES (%s,%s,%s,%s)", (host, at, before, to))


def episode(host, first, *, kind="server_error", confirmed=True, ended=None, reason=None, held=0, last=None):
    return ("""INSERT INTO outage_episodes (site_host,signal,kind,first_fail_at,confirmed_at,ended_at,ended_reason,last_event_at,reads,held_overlap)
               VALUES (%s,'A',%s,%s,%s,%s,%s,%s,3,%s)""",
            (host, kind, first, first + timedelta(minutes=50) if confirmed else None, ended, reason, last or ended or first + timedelta(minutes=50), held))


def probe(host, *, outcome="reachable", at=None, refusals=0, due=None, trigger="daily", reason=None):
    at = at or now() - timedelta(hours=1)
    return ("""INSERT INTO site_probe (site_host,read_host,probed_at,outcome,fingerprint_version,next_due_at,consecutive_refusals,trigger_kind,not_measured_reason)
               VALUES (%s,%s,%s,%s,'v1',%s,%s,%s,%s)
               ON CONFLICT (site_host) DO UPDATE SET probed_at=EXCLUDED.probed_at, outcome=EXCLUDED.outcome, next_due_at=EXCLUDED.next_due_at,
                   consecutive_refusals=EXCLUDED.consecutive_refusals, not_measured_reason=EXCLUDED.not_measured_reason""",
            (host, host, at, outcome, due or at + timedelta(hours=6), refusals, trigger, reason))


def probe_day(host, day, outcome="reachable", probes=1):
    return ("INSERT INTO site_probe_daily (day,site_host,outcome,bot_vendor,trigger_kind,probes) VALUES (%s,%s,%s,'','daily',%s)",
            (day, host, outcome, probes))


def hourly(host, hour, *, sources, error_class="unspecified", http="unspecified", challenge="unspecified"):
    return ("""INSERT INTO lookup_signals_hourly (hour,site_host,error_class,failure_stage,http_status_class,challenge,retry_after,via,client_kind,action,after_action,lookups,sources)
               VALUES (%s,%s,%s,'unspecified',%s,%s,'unspecified','unspecified','mcp','proceed','none',%s,%s)""",
            (hour.replace(minute=0, second=0, microsecond=0), host, error_class, http, challenge, max(sources, 1), sources))


def strip(body: dict) -> dict:
    """The answer without what differs between two identical lookups."""
    return {k: v for k, v in body.items() if k not in ("generated_at", "feedback_token", "lookup_activity")}  # moved by the lookup itself


def seed(settings: Settings, *statements) -> None:
    with connect(settings) as conn:
        for sql, params in statements:
            conn.execute(sql, params)


# --- the pure parts ----------------------------------------------------------------------------------------------------

def test_every_line_is_printable_ascii_within_120_characters_with_the_longest_window():
    start, end = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc), datetime(2027, 1, 2, 23, 59, tzinfo=timezone.utc)
    for key, template in cause.LINES.items():
        for line in (cause.line_for(key), cause.line_for(key, start), cause.line_for(key, start, end)):
            assert 0 < len(line) <= cause.LINE_MAX and re.fullmatch(r"[ -~]+", line), (key, line)
            assert "{" not in line and "}" not in line
    assert "(since 12-31 23:59 UTC)" in cause.line_for("site_errors", start)
    assert "(12-31 23:59 to 01-02 23:59 UTC)" in cause.line_for("site_errors", start, end)
    same_day = cause.line_for("site_errors", start, start.replace(hour=23, minute=59) + timedelta(seconds=0))
    assert "(12-31 23:59-23:59 UTC)" in same_day


def test_every_hint_and_basis_code_the_code_can_return_is_in_the_published_model():
    from ahn.models import Cause
    fields = Cause.model_fields
    published_hints = set(fields["hint"].annotation.__args__)
    assert {h for hints in cause.HINTS.values() for h in hints} == published_hints
    assert set(fields["basis"].annotation.__args__[0].__args__) == set(cause.BASIS)
    assert set(fields["branch"].annotation.__args__) == set(cause.BRANCHES)
    assert set(fields["confidence"].annotation.__args__) == set(cause.CONFIDENCES)


def test_failed_at_parsing_is_strict_and_never_guesses():
    at = datetime(2026, 11, 1, 12, 0, tzinfo=timezone.utc)
    parse = cause.parse_failed_at
    for blank in (None, "", "   "):
        when = parse(blank, at)
        assert when.at == at and not when.sent and not when.old
    assert parse("2026-11-01T11:00:00Z", at).at == at - timedelta(hours=1)
    assert parse("2026-11-01T11:00:00", at).at == at - timedelta(hours=1)                    # no offset: UTC
    assert parse("2026-11-01 11:00", at).at == at - timedelta(hours=1)                       # space and no seconds
    assert parse("2026-11-01T13:00:00+02:00", at).at == at - timedelta(hours=1)               # offset converted to UTC
    assert parse("2026-11-01T11:00:00.250Z", at).at == at - timedelta(hours=1) + timedelta(milliseconds=250)
    assert parse("2026-11-01T12:04:59Z", at).at == at                                          # within 5 minutes ahead: now
    assert parse("2026-11-01T12:05:01Z", at) is None                                           # further ahead: unusable
    assert parse("2026-11-01T12:05:00Z", at).at == at                                          # exactly 5 minutes ahead is still a skew
    for bad in ("yesterday", "2026-11-01", "11:00", "2026-13-01T00:00:00Z", "2026-11-01T25:00:00Z", "1730462400", "2026-11-01T11:00:00Zjunk",
                "2026-11-01T11:00:00+0200", "x" * 41, "2026-11-01T11:00:00Z; DROP TABLE", 12345):
        assert parse(bad, at) is None, bad
    fresh = parse("2026-10-18T12:00:00Z", at)                                                  # exactly 14 days: not old
    assert fresh.at == datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc) and not fresh.old
    assert parse("2026-10-18T11:59:59Z", at).old                                               # one second past 14 days


def test_a_settled_cause_leads_the_summary_line_only_when_the_whole_line_fits():
    base = {"site_host": "x.example.org", "recommendation": {"action": "use_official_path", "reasons": ["official_path_available"]},
            "access_profile": None, "provider_status": None, "official_paths": {"api_docs": "https://developer.example.org/docs"}}
    plain = response_guide.guide(dict(base))["summary_line"]
    line = cause.line_for("rate_limited")
    assert len(plain) + 1 + len(line) <= response_guide.LINE_MAX
    settled = {"branch": "transient", "line": line}
    assert response_guide.guide({**base, "cause": settled})["summary_line"] == f"{line} {plain}"
    # undetermined never leads
    assert response_guide.guide({**base, "cause": {"branch": "undetermined", "line": cause.line_for("no_measurement")}})["summary_line"] == plain
    # a sentence that would push the line past LINE_MAX is left alone
    long_base = {**base, "recommendation": {"action": "wait_provider_incident", "reasons": ["provider_scope"]},
                 "provider_status": {"indicator": "major", "source_url": "https://status.example.org"}}
    long_plain = response_guide.guide(dict(long_base))["summary_line"]
    assert len(long_plain) + 1 + len(line) > response_guide.LINE_MAX
    assert response_guide.guide({**long_base, "cause": settled})["summary_line"] == long_plain
    # every sentence of the table: the result never exceeds LINE_MAX and is either the sentence or the cause line followed by it
    for sentence in [text for _, text in response_guide.ROWS.values()]:
        for text in (cause.line_for("provider_incident", datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc), datetime(2027, 1, 2, 1, 1, tzinfo=timezone.utc)),
                     cause.line_for("persistent_refusal")):
            lead = f"{text} {sentence}"
            assert len(lead) <= response_guide.LINE_MAX or len(sentence) <= response_guide.LINE_MAX


# --- provider_incident and recovered -------------------------------------------------------------------------------------

def test_provider_incident_now_then_the_window_and_recovered_boundaries(db):
    host = "provider-case.org"
    start, end = now() - timedelta(hours=2), now() - timedelta(minutes=30)
    seed(db, provider_event(host, start, "major", "none"), provider_event(host, end, "none", "major"))
    # the failure happened inside the window: a cleared problem
    inside = look(db, host, error_class="service_error", failed_at=iso(start + timedelta(minutes=10)))["cause"]
    assert (inside["branch"], inside["hint"], inside["confidence"]) == ("transient", "recovered", "high")
    assert inside["basis"] == ["provider_status_events"]
    assert inside["window"] == {"from": iso(start), "to": iso(end)}
    assert inside["line"].startswith("Temporary: provider problem (") and "has cleared" in inside["line"]
    # the start second is inside, the second before it is not
    assert look(db, host, error_class="service_error", failed_at=iso(start))["cause"]["hint"] == "recovered"
    assert look(db, host, error_class="service_error", failed_at=iso(start - SECOND))["cause"]["hint"] == "no_measurement"
    # the end second is outside (the problem was over), the second before it is inside
    assert look(db, host, error_class="service_error", failed_at=iso(end - SECOND))["cause"]["hint"] == "recovered"
    after = look(db, host, error_class="service_error", failed_at=iso(end))
    assert after.get("cause", {}).get("branch") != "transient"
    # now: nothing is active
    assert look(db, host, error_class="service_error").get("cause", {}).get("branch") != "transient"


def test_provider_incident_that_is_still_going_on_has_a_window_without_an_end(db):
    host = "provider-open.org"
    start = now() - timedelta(minutes=40)
    seed(db, provider_event(host, start, "critical", "none"))
    for params in ({}, {"failed_at": iso(now() - timedelta(minutes=5))}):
        body = look(db, host, error_class="service_error", **params)
        assert body["cause"]["hint"] == "provider_incident" and body["cause"]["confidence"] == "high"
        assert body["cause"]["window"] == {"from": iso(start)}
        assert "(since " in body["cause"]["line"] and body["cause"]["line"].startswith("Temporary: provider status page shows a problem")
    # a minor problem is less certain; maintenance has its own sentence
    seed(db, provider_event("provider-minor.org", start, "minor", "none"), provider_event("provider-maint.org", start, "maintenance", "none"))
    minor = look(db, "provider-minor.org", error_class="service_error")["cause"]
    assert (minor["hint"], minor["confidence"]) == ("provider_incident", "medium")
    maintenance = look(db, "provider-maint.org", error_class="service_error")["cause"]
    assert maintenance["hint"] == "provider_incident" and maintenance["line"].startswith("Temporary: provider maintenance (")


def test_a_login_wall_during_a_provider_problem_is_one_step_less_certain_and_a_status_page_problem_does_not_outlive_three_days(db):
    host = "provider-login.org"
    seed(db, provider_event(host, now() - timedelta(hours=1), "major", "none"))
    assert look(db, host, error_class="login_auth")["cause"]["confidence"] == "medium"
    old = "provider-stale.org"
    seed(db, provider_event(old, now() - timedelta(days=4), "major", "none"))
    assert look(db, old, error_class="service_error").get("cause", {}).get("branch") != "transient"


def test_older_than_14_days_is_answered_from_the_provider_history_alone(db):
    host = "provider-old.org"
    at = now() - timedelta(days=20)
    seed(db, provider_event(host, at, "major", "none"), provider_event(host, at + timedelta(hours=1), "none", "major"),
         episode(host, at, ended=at + timedelta(hours=2), reason="answered"), probe_day(host, (at).date(), "reachable"))
    inside = look(db, host, error_class="service_error", failed_at=iso(at + timedelta(minutes=30)))["cause"]
    assert (inside["hint"], inside["basis"]) == ("recovered", ["provider_status_events"])
    # not covered by the provider: the other tables are not read, so the answer says the time is too old
    outside = look(db, host, error_class="service_error", failed_at=iso(at + timedelta(hours=1, minutes=30)))["cause"]
    assert (outside["branch"], outside["hint"], outside["confidence"]) == ("undetermined", "no_measurement", "low")
    assert outside["basis"] == ["provider_status_events"] and "14 days" in outside["line"] and "recheck_after" not in outside


# --- site_errors and recovered (AHN's own reads) -------------------------------------------------------------------------

def test_site_errors_open_confirmed_unconfirmed_and_ended(db):
    t0 = now() - timedelta(hours=3)
    seed(db, episode("errors-open.org", t0), episode("errors-suspect.org", t0, confirmed=False),
         episode("errors-ended.org", t0, ended=t0 + timedelta(hours=1), reason="answered"),
         episode("errors-refused.org", t0, kind="no_response", ended=t0 + timedelta(hours=1), reason="answered_refused"),
         episode("errors-held.org", t0, held=2))
    opened = look(db, "errors-open.org", error_class="service_error")["cause"]
    assert (opened["branch"], opened["hint"], opened["confidence"]) == ("transient", "site_errors", "high")
    assert opened["basis"] == ["outage_episodes"] and opened["window"] == {"from": iso(t0)}
    assert look(db, "errors-suspect.org", error_class="service_error")["cause"]["confidence"] == "medium"
    # ended with an answer, and the failure was during it: recovered; the end second is outside
    inside = iso(t0 + timedelta(minutes=30))
    recovered = look(db, "errors-ended.org", error_class="service_error", failed_at=inside)["cause"]
    assert (recovered["hint"], recovered["window"]) == ("recovered", {"from": iso(t0), "to": iso(t0 + timedelta(hours=1))})
    assert look(db, "errors-ended.org", error_class="service_error", failed_at=iso(t0 + timedelta(hours=1) - SECOND))["cause"]["hint"] == "recovered"
    assert look(db, "errors-ended.org", error_class="service_error", failed_at=iso(t0 + timedelta(hours=1))).get("cause", {}).get("branch") != "transient"
    assert look(db, "errors-ended.org", error_class="service_error", failed_at=iso(t0 - SECOND)).get("cause", {}).get("branch") != "transient"
    # the host answered with a refusal: the period ended, but that is not a recovery
    assert look(db, "errors-refused.org", error_class="service_error", failed_at=inside)["cause"]["hint"] == "site_errors"
    # a period that overlapped many hosts failing at once is held back as a problem on AHN's side
    assert look(db, "errors-held.org", error_class="service_error").get("cause", {}).get("branch") != "transient"
    # a login or challenge page is not what a connection failure looks like
    assert look(db, "errors-open.org", error_class="human_verification").get("cause", {}).get("branch") != "transient"
    # an open period nobody has read for 48 hours is not a current one
    seed(db, episode("errors-stale.org", now() - timedelta(days=5), last=now() - timedelta(days=3)))
    assert look(db, "errors-stale.org", error_class="service_error").get("cause", {}).get("branch") != "transient"


# --- policy_closed: home_now_gated, persistent_refusal --------------------------------------------------------------------

def test_two_refusals_in_a_row_are_a_policy_block_and_one_is_only_undetermined(db):
    two, one = "gate-two.org", "gate-one.org"
    due = now() + timedelta(days=7)
    seed(db, probe(two, outcome="bot_challenge", refusals=2, due=due), probe(one, outcome="login_wall", refusals=1, due=due))
    gated = look(db, two, error_class="site_access")["cause"]
    assert (gated["branch"], gated["hint"], gated["confidence"], gated["basis"]) == ("policy_closed", "home_now_gated", "medium", ["site_probe"])
    assert "window" not in gated and "recheck_after" not in gated
    first = look(db, one, error_class="site_access")["cause"]
    assert (first["branch"], first["hint"], first["confidence"]) == ("undetermined", "home_gate_unconfirmed", "low")
    assert first["recheck_after"] == iso(due)
    # a refusal measured long ago says nothing about now; a service error is not what a gate looks like
    seed(db, probe("gate-stale.org", outcome="refused", refusals=3, at=now() - timedelta(days=9)))
    assert look(db, "gate-stale.org", error_class="site_access")["cause"]["hint"] == "no_measurement"
    assert look(db, two, error_class="service_error").get("cause", {}).get("branch") != "policy_closed"


def test_a_refusal_is_never_evidence_of_a_temporary_problem(db):
    """D68: refusals (and a rate limit) of AHN's own home-page check, and 403 lookups from other clients, do not make
    the cause temporary, whatever the caller's error class."""
    host = "refusal-not-transient.org"
    seed(db, probe(host, outcome="refused", refusals=1),
         hourly(host, now(), sources=12, error_class="site_access", http="403"),
         hourly(host, now(), sources=12, error_class="service_error", http="429"))
    for error_class in ("site_access", "service_error", "human_verification", "login_auth"):
        body = look(db, host, error_class=error_class)
        assert body.get("cause", {}).get("branch") != "transient", (error_class, body.get("cause"))
    # a home-page rate limit is neither a gate nor an outage
    seed(db, probe("refusal-rate.org", outcome="rate_limited", refusals=5))
    assert "cause" not in look(db, "refusal-rate.org", error_class="site_access")  # measured, not normal, no gate: nothing is claimed


def test_persistent_refusal_needs_three_days_each_with_several_clients(db):
    today = now()
    host = "persistent.org"
    days = [today - timedelta(days=n) for n in (0, 1, 2)]
    seed(db, *[hourly(host, d.replace(hour=10), sources=2, error_class="site_access") for d in days])
    hit = look(db, host, error_class="site_access")["cause"]
    assert (hit["branch"], hit["hint"], hit["confidence"], hit["basis"]) == ("policy_closed", "persistent_refusal", "medium", ["lookup_signals_hourly"])
    # one client looping over many hours is not several clients
    one = "persistent-one.org"
    seed(db, *[hourly(one, d.replace(hour=h), sources=1, error_class="site_access") for d in days for h in (8, 9, 10)])
    assert look(db, one, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # only two days
    two = "persistent-two.org"
    seed(db, *[hourly(two, d.replace(hour=10), sources=3, error_class="site_access") for d in days[:2]])
    assert look(db, two, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # a 403 class counts without an error class; a 5xx class does not, even with error_class site_access
    status = "persistent-403.org"
    seed(db, *[hourly(status, d.replace(hour=10), sources=2, http="403") for d in days])
    assert look(db, status, error_class="site_access")["cause"]["hint"] == "persistent_refusal"
    outage = "persistent-5xx.org"
    seed(db, *[hourly(outage, d.replace(hour=10), sources=2, error_class="site_access", http="5xx") for d in days])
    assert look(db, outage, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # the caller's own failure must be an access one
    assert look(db, host, error_class="service_error").get("cause", {}).get("hint") != "persistent_refusal"


# --- rate_limited, others_failing_same_hour ------------------------------------------------------------------------------

def test_rate_limited_comes_from_the_callers_own_codes(db):
    host = "rate.org"
    seed(db, provider_event(host, now() - timedelta(hours=1), "major", "none"))  # the caller's own 429 is the more direct evidence
    for params in ({"http_status": "429"}, {"retry_after": "lt_60"}, {"retry_after": "60_3600"}, {"retry_after": "gt_3600"}):
        body = look(db, host, error_class="site_access", **params)
        assert (body["cause"]["hint"], body["cause"]["confidence"], body["cause"]["basis"]) == ("rate_limited", "medium", ["caller_codes"])
        assert "window" not in body["cause"]
    for params in ({"retry_after": "none"}, {"http_status": "503"}, {"http_status": "404"}, {"retry_after": "5"}, {}):
        assert look(db, host, error_class="site_access", **params)["cause"]["hint"] != "rate_limited"
    seed(db, hourly(host, now(), sources=2, http="429"))
    sure = look(db, host, error_class="site_access", http_status="429")["cause"]
    assert (sure["hint"], sure["confidence"], sure["basis"]) == ("rate_limited", "high", ["caller_codes", "lookup_signals_hourly"])


def test_others_failing_in_the_same_hour_counts_distinct_sources_in_that_hour_only(db):
    now_ = now()
    seed(db, hourly("others-low.org", now_, sources=3, error_class="service_error"),
         hourly("others-many.org", now_, sources=6, error_class="service_error"), hourly("others-many.org", now_, sources=4, http="5xx"),
         hourly("others-two.org", now_, sources=2, error_class="service_error"),
         hourly("others-prev.org", now_ - timedelta(hours=1), sources=9, error_class="service_error"),
         hourly("others-403.org", now_, sources=9, error_class="service_error", http="403"))
    low = look(db, "others-low.org", error_class="service_error")["cause"]
    assert (low["branch"], low["hint"], low["confidence"], low["basis"]) == ("transient", "others_failing_same_hour", "low", ["lookup_signals_hourly"])
    assert look(db, "others-many.org", error_class="service_error")["cause"]["confidence"] == "medium"
    for host in ("others-two.org", "others-prev.org", "others-403.org"):
        assert look(db, host, error_class="service_error").get("cause", {}).get("branch") != "transient", host
    # the same hour in the past, named by failed_at
    past = (now_ - timedelta(hours=1)).replace(minute=30)
    assert look(db, "others-prev.org", error_class="service_error", failed_at=iso(past))["cause"]["hint"] == "others_failing_same_hour"
    # a caller that was refused is not helped by others' service errors
    assert look(db, "others-low.org", error_class="login_auth").get("cause", {}).get("hint") != "others_failing_same_hour"


# --- undetermined ----------------------------------------------------------------------------------------------------------

def test_your_side_likely_needs_a_normal_check_and_a_signal_that_points_at_the_caller(db):
    seed(db, probe("side.org"), probe("side-day.org", at=now() - timedelta(days=3), due=now() + timedelta(hours=2)),
         probe_day("side-day.org", (now() - timedelta(days=3)).date(), "reachable", 2))
    near = look(db, "side.org", failure_stage="before_target_response")["cause"]
    assert (near["branch"], near["hint"], near["confidence"], near["basis"]) == ("undetermined", "your_side_likely", "low", ["site_probe"])
    assert "AHN reads no page content" in near["line"] and "recheck_after" in near
    assert look(db, "side.org", error_class="login_auth")["cause"]["hint"] == "your_side_likely"
    assert look(db, "side.org", error_class="human_verification")["cause"]["hint"] == "your_side_likely"
    # the caller saw a plain service error and AHN's check was normal: nothing is claimed
    assert "cause" not in look(db, "side.org", error_class="service_error")
    # a failure three days ago: the day's own record describes it
    past = look(db, "side-day.org", failure_stage="before_target_response", failed_at=iso(now() - timedelta(days=3)))["cause"]
    assert past["hint"] == "your_side_likely"
    # not normal that day: no claim either
    seed(db, probe_day("side-bad.org", (now() - timedelta(days=2)).date(), "reachable"), probe_day("side-bad.org", (now() - timedelta(days=2)).date(), "other_status"))
    assert "cause" not in look(db, "side-bad.org", failure_stage="before_target_response", failed_at=iso(now() - timedelta(days=2)))


def test_no_measurement_names_the_next_measurement_only_when_there_is_one(db):
    none = look(db, "nothing.org", error_class="site_access")["cause"]
    assert (none["branch"], none["hint"], none["confidence"], none["basis"]) == ("undetermined", "no_measurement", "low", [])
    assert "recheck_after" not in none
    due = now() + timedelta(hours=5)
    seed(db, probe("later.org", outcome="not_measured", at=now() - timedelta(days=2), due=due, reason="backoff"))
    later = look(db, "later.org", error_class="site_access")["cause"]
    assert later["hint"] == "no_measurement" and later["basis"] == ["site_probe"] and later["recheck_after"] == iso(due)
    # a check due in the past is not a time to come back at
    seed(db, probe("overdue.org", outcome="not_measured", at=now() - timedelta(days=2), due=now() - timedelta(hours=1), reason="budget"))
    assert "recheck_after" not in look(db, "overdue.org", error_class="site_access")["cause"]


# --- failed_at on the wire --------------------------------------------------------------------------------------------------

def test_failed_at_that_cannot_be_used_gives_no_cause_and_the_rest_of_the_answer_is_unchanged(db):
    host = "badtime.org"
    seed(db, provider_event(host, now() - timedelta(hours=1), "major", "none"))
    reference = look(db, host, error_class="service_error", failed_at=iso(now() - timedelta(minutes=1)))
    assert reference["cause"]["hint"] == "provider_incident"
    for bad in ("yesterday", "2026-11-01", iso(now() + timedelta(hours=1)), "x" * 200, "1730462400"):
        body = look(db, host, error_class="service_error", failed_at=bad)
        assert "cause" not in body, bad
        assert strip(body) == strip(look(settings_with(hint=False), host, error_class="service_error")), bad
    # a time a few minutes ahead is a clock difference
    assert look(db, host, error_class="service_error", failed_at=iso(now() + timedelta(minutes=3)))["cause"]["hint"] == "provider_incident"
    # an empty value is not sent
    assert look(db, host, error_class="service_error", failed_at="")["cause"]["hint"] == "provider_incident"


def test_failed_at_is_not_stored_counted_or_echoed(db):
    host = "unstored.org"
    stamp = "2026-10-30T04:05:06Z"
    body = look(db, host, error_class="service_error", failed_at=stamp)
    assert stamp not in json.dumps(body)
    with connect(db) as conn:
        for table in ("lookup_signals_hourly", "lookup_sources_daily", "lookup_metrics", "queried_hosts", "background_reads", "mcp_check_metrics",
                      "request_metrics", "traffic_attribution_daily", "traffic_client_daily", "audit_events"):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()  # noqa: S608  (fixed table names)
            assert stamp not in json.dumps(rows, default=str) and "04:05:06" not in json.dumps(rows, default=str), table


# --- where the cause appears ------------------------------------------------------------------------------------------------

def test_only_a_failure_lookup_gets_a_cause(db):
    host = "onlyfailure.org"
    seed(db, provider_event(host, now() - timedelta(hours=1), "major", "none"))
    assert look(db, host, error_class="service_error")["cause"]["hint"] == "provider_incident"
    assert look(db, host, failure_stage="unknown")["cause"]["hint"] == "provider_incident"
    for params in ({}, {"task_step": "status_check"}, {"task_step": "preflight"}, {"task_step": "site_open"}, {"window_hours": 1}):
        assert "cause" not in look(db, host, **params), params
    with TestClient(create_app(db)) as client:  # the human search page is a plain view, not a failure
        assert client.get("/en/search", params={"site_host": host}).status_code == 200


def test_the_switch_off_gives_the_answer_before_the_cause_and_on_changes_nothing_else(db):
    on, off = db, settings_with(hint=False)
    t0 = now() - timedelta(hours=3)
    seed(db, provider_event("same-provider.org", t0, "major", "none"), episode("same-errors.org", t0),
         probe("same-gate.org", outcome="refused", refusals=2), probe("same-side.org"),
         hourly("same-others.org", now(), sources=4, error_class="service_error"))
    cases = [("same-provider.org", {"error_class": "service_error"}), ("same-errors.org", {"error_class": "service_error"}),
             ("same-gate.org", {"error_class": "site_access"}), ("same-side.org", {"failure_stage": "before_target_response"}),
             ("same-others.org", {"error_class": "service_error"}), ("same-rate.org", {"error_class": "site_access", "http_status": "429"}),
             ("same-none.org", {"error_class": "login_auth"}), ("same-provider.org", {"error_class": "service_error", "failed_at": iso(t0 + timedelta(minutes=1))})]
    seen_hints = set()
    for host, params in cases:
        with_cause, without = look(on, host, **params), look(off, host, **params)
        assert "cause" not in without
        if "cause" in with_cause:
            seen_hints.add(with_cause["cause"]["hint"])
        # the verdict, the step to take and everything but the cause and a summary line that leads with it are the same
        for key in ("recommendation", "next_action", "next_checks", "access_profile", "official_paths", "provider_status", "links"):
            assert with_cause.get(key) == without.get(key), (host, key)
        assert with_cause["next_action"]["do"] == without["next_action"]["do"]
        assert {k: v for k, v in strip(with_cause).items() if k not in ("cause", "summary_line")} == \
               {k: v for k, v in strip(without).items() if k != "summary_line"}
        assert with_cause["summary_line"] == without["summary_line"] or with_cause["summary_line"].endswith(without["summary_line"])
        assert len(with_cause["summary_line"]) <= 200
    assert seen_hints >= {"provider_incident", "site_errors", "home_now_gated", "your_side_likely", "others_failing_same_hour", "rate_limited",
                          "no_measurement"}
    # with the guide off the switch changes nothing visible either
    assert "cause" not in look(settings_with(hint=False, guide=False), "same-provider.org", error_class="service_error")
    assert look(settings_with(hint=True, guide=False), "same-provider.org", error_class="service_error")["cause"]["hint"] == "provider_incident"


def test_the_summary_line_leads_with_a_settled_cause_when_it_fits_and_is_otherwise_untouched(db):
    off = settings_with(hint=False)
    seed(db, provider_event("lead-provider.org", now() - timedelta(hours=1), "major", "none"))
    long_case = look(db, "lead-provider.org", error_class="service_error")
    assert long_case["cause"]["branch"] == "transient"
    plain = look(off, "lead-provider.org", error_class="service_error")["summary_line"]
    if len(plain) + 1 + len(long_case["cause"]["line"]) <= 200:
        assert long_case["summary_line"] == f"{long_case['cause']['line']} {plain}"
    else:
        assert long_case["summary_line"] == plain
    # a settled cause with a short sentence (an official path is listed, no robots block): the line leads
    short = look(db, "lead-rate.org", error_class="site_access", http_status="429")
    short_plain = look(off, "lead-rate.org", error_class="site_access", http_status="429")["summary_line"]
    assert short["cause"]["hint"] == "rate_limited"
    assert short["summary_line"] in (short_plain, f"{short['cause']['line']} {short_plain}")
    # undetermined never leads
    nothing = look(db, "lead-none.org", error_class="site_access")
    assert nothing["cause"]["branch"] == "undetermined"
    assert nothing["summary_line"] == look(off, "lead-none.org", error_class="site_access")["summary_line"]


# --- nothing from a site or a host in the output -----------------------------------------------------------------------------

def test_no_host_name_and_no_site_text_reaches_a_cause(db):
    t0 = now() - timedelta(hours=3)
    hosts = ["leak-provider.example-bank.org", "leak-errors.example-bank.org", "leak-gate.example-bank.org", "leak-none.example-bank.org"]
    seed(db, provider_event(hosts[0], t0, "major", "none"), episode(hosts[1], t0), probe(hosts[2], outcome="bot_challenge", refusals=2))
    templates = [re.compile("^" + re.escape(text).replace(re.escape("{win}"), r"(\(since [0-9: -]+ UTC\)|\([0-9: -]+(?:-| to )[0-9: -]+ UTC\))") + "$")
                 for text in cause.LINES.values()] + [re.compile("^" + re.escape(text.replace(" {win}", "")) + "$") for text in cause.LINES.values() if "{win}" in text]
    for host in hosts:
        for params in ({"error_class": "service_error"}, {"error_class": "site_access"}, {"failure_stage": "before_target_response"},
                       {"error_class": "login_auth", "failed_at": iso(t0 + timedelta(minutes=1))}):
            found = look(db, host, **params).get("cause")
            if not found:
                continue
            blob = json.dumps(found)
            assert host not in blob and "example-bank" not in blob and "leak-" not in blob
            assert set(found) <= {"branch", "hint", "confidence", "basis", "window", "line", "recheck_after"}
            assert any(t.fullmatch(found["line"]) for t in templates), found["line"]


# --- MCP -----------------------------------------------------------------------------------------------------------------------

def test_mcp_passes_failed_at_on_as_text_and_publishes_the_input():
    import asyncio

    from ahn import mcp_adapter
    sent = {}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        sent.update(params)
        return {"site_host": "example.org", "generated_at": iso(now()), "synthetic": False, "window_hours": 24, "data_horizon_hours": 168,
                "current_site_status": "unknown", "evidence_level": "no_reports", "summary_code": "x", "summary": "x", "failures": 0,
                "successes": 0, "reporting_sources": 0, "observations": [], "provider_source_state": "not_verified_by_current_methods"}
    mcp_adapter.call_api, original = fake, mcp_adapter.call_api
    try:
        asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "example.org", "error_class": "service_error",
                                                                  "failed_at": " 2026-11-01T07:10:00Z "}))
        assert sent["failed_at"] == "2026-11-01T07:10:00Z"
        sent.clear()
        asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "example.org", "error_class": "service_error", "failed_at": ""}))
        assert "failed_at" not in sent
        sent.clear()
        asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "example.org", "error_class": "service_error", "failed_at": 5}))
        assert "failed_at" not in sent
    finally:
        mcp_adapter.call_api = original
    tool = next(t for t in asyncio.run(mcp_adapter.mcp.list_tools()) if t.name == "check_incident")
    assert tool.input_schema["properties"]["failed_at"]["type"] == "string" and "failed_at" not in tool.input_schema.get("required", [])
    assert "cause" in tool.output_schema["properties"] and "cause" not in tool.output_schema.get("required", [])
