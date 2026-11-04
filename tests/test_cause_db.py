"""D73 end to end over REST against the dedicated ahn_test database: the optional `cause` of a failure lookup, the
`failed_at` input, and the promise that it changes nothing else (recommendation, next_action.do) and is gone with
AHN_CAUSE_HINT=0. Rows are written straight into the tables the cause reads; nothing here contacts a real host."""

from __future__ import annotations

import itertools
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
from ahn.models import CheckResponse, ErrorClass
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


def probe(host, *, outcome="reachable", at=None, refusals=0, due=None, trigger="daily", reason=None, vendor=None, redirect=None):
    at = at or now() - timedelta(hours=1)
    return ("""INSERT INTO site_probe (site_host,read_host,probed_at,outcome,fingerprint_version,next_due_at,consecutive_refusals,trigger_kind,not_measured_reason,
                                       bot_vendor,bot_signal,bot_confidence,redirect)
               VALUES (%s,%s,%s,%s,'v1',%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT (site_host) DO UPDATE SET probed_at=EXCLUDED.probed_at, outcome=EXCLUDED.outcome, next_due_at=EXCLUDED.next_due_at,
                   consecutive_refusals=EXCLUDED.consecutive_refusals, not_measured_reason=EXCLUDED.not_measured_reason, bot_vendor=EXCLUDED.bot_vendor,
                   bot_signal=EXCLUDED.bot_signal, bot_confidence=EXCLUDED.bot_confidence, redirect=EXCLUDED.redirect""",
            (host, host, at, outcome, due or at + timedelta(hours=6), refusals, trigger, reason, vendor,
             "header" if vendor else None, "high" if vendor else None, json.dumps(redirect) if redirect is not None else None))


def observation(host, at, *, outcome="error", error_class="service_error", synthetic=False, state="published", visibility="published", withdrawn=False):
    """A report from another agent: an incident row of the host and one observation of it."""
    return [("""INSERT INTO incidents (site_host,task_step,error_class,visibility) VALUES (%s,'status_check',%s,%s)
                ON CONFLICT (site_host,task_step,error_class) DO UPDATE SET visibility=EXCLUDED.visibility""", (host, error_class, visibility)),
            ("""INSERT INTO observations (incident_id,reporter_id,observed_at,agent_family,attempt_actor,attempt_env,outcome,challenge_vendor,challenge_kind,
                                          synthetic,review_state,withdrawn_at)
                SELECT id,%s,%s,'other','agent','unknown',%s,'none','none',%s,%s,%s FROM incidents WHERE site_host=%s AND error_class=%s""",
             (PUBLIC_REPORTER_ID, at, outcome, synthetic, state, at if withdrawn else None, host, error_class))]


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

def test_every_line_is_printable_ascii_within_200_characters_with_the_longest_window():
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


def test_a_cause_never_changes_the_summary_line_or_the_next_action():
    base = {"site_host": "x.example.org", "recommendation": {"action": "use_official_path", "reasons": ["official_path_available"]},
            "access_profile": None, "provider_status": None, "official_paths": {"api_docs": "https://developer.example.org/docs"}}
    plain = response_guide.guide(dict(base))
    for branch in ("transient", "policy_closed", "undetermined"):
        assert response_guide.guide({**base, "cause": {"branch": branch, "line": cause.line_for("rate_limited")}}) == plain


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
         episode("errors-held.org", t0, held=2), episode("errors-seen.org", t0), *observation("errors-seen.org", t0 + timedelta(hours=1)))
    # a confirmed period that only AHN's own reads show is medium (D82); another agent's observation of it keeps it high
    opened = look(db, "errors-open.org", error_class="service_error")["cause"]
    assert (opened["branch"], opened["hint"], opened["confidence"]) == ("transient", "site_errors", "medium")
    assert look(db, "errors-seen.org", error_class="service_error")["cause"]["confidence"] == "high"
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


def test_site_errors_say_whose_check_it_was_and_drop_to_medium_without_anyone_else(db):
    """D82: the sentence names AHN's own checks; with nothing but AHN's measurement behind it the confidence is medium, and any
    other source that saw the host failing in the period keeps it high."""
    t0 = now() - timedelta(hours=30)
    mid = t0 + timedelta(hours=5)
    names = ("alone", "obs", "obs_other_class", "obs_blocked", "obs_synth", "obs_pending", "obs_hidden", "obs_withdrawn", "obs_before", "obs_after",
             "hourly3", "hourly2", "hourly_403", "hourly_before", "hourly_spread", "obs_www")
    hosts = {name: "own-" + name.replace("_", "-") + ".org" for name in names}
    seed(db, *(episode(host, t0) for host in hosts.values()), episode("own-unconfirmed.org", t0, confirmed=False))
    seed(db, *observation(hosts["obs"], mid))
    seed(db, *observation(hosts["obs_other_class"], mid, error_class="site_access"))
    seed(db, *observation(hosts["obs_blocked"], mid, outcome="blocked"))
    seed(db, *observation(hosts["obs_synth"], mid, synthetic=True))
    seed(db, *observation(hosts["obs_pending"], mid, state="pending"))
    seed(db, *observation(hosts["obs_hidden"], mid, visibility="hidden"))
    seed(db, *observation(hosts["obs_withdrawn"], mid, withdrawn=True))
    seed(db, *observation(hosts["obs_before"], t0 - timedelta(hours=1)))
    seed(db, *observation(hosts["obs_after"], now()))
    seed(db, *observation("www." + hosts["obs_www"], mid))   # stored under the exact host; the lookup is answered for the host without www.
    seed(db, hourly(hosts["hourly3"], mid, sources=3, error_class="service_error"), hourly(hosts["hourly2"], mid, sources=2, error_class="service_error"),
         hourly(hosts["hourly_403"], mid, sources=9, error_class="site_access", http="403"),
         hourly(hosts["hourly_before"], t0 - timedelta(hours=3), sources=9, error_class="service_error"))
    # one client looking again in three hours is three rows of sources=1: still one client, not three
    seed(db, *(hourly(hosts["hourly_spread"], mid + timedelta(hours=n), sources=1, error_class="service_error") for n in range(3)))
    expected = {"alone": "medium", "obs": "high", "obs_other_class": "medium", "obs_blocked": "medium", "obs_synth": "medium", "obs_pending": "medium",
                "obs_hidden": "medium", "obs_withdrawn": "medium", "obs_before": "medium", "obs_after": "high", "hourly3": "high", "hourly2": "medium", "hourly_spread": "medium", "obs_www": "high",
                "hourly_403": "medium", "hourly_before": "medium"}
    assert set(expected) == set(names)
    for name, confidence in expected.items():
        found = look(db, hosts[name], error_class="service_error")["cause"]
        assert (found["branch"], found["hint"], found["basis"]) == ("transient", "site_errors", ["outage_episodes"]), name
        assert found["confidence"] == confidence, name
        assert found["line"].startswith("AHN's own checks have not reached the site (since ") and "it does not tell whether your tool can reach the site." in found["line"], name
        assert "Retry" not in found["line"] and len(found["line"]) <= 200
    # an unconfirmed period was medium already, with or without anyone else
    assert look(db, "own-unconfirmed.org", error_class="service_error")["cause"]["confidence"] == "medium"
    # an episode that ended with an answer: the same sentence family, the same two confidences
    first = now() - timedelta(days=1)
    seed(db, episode("own-ended.org", first, ended=first + timedelta(hours=2), reason="answered"), *observation("own-ended.org", first + timedelta(hours=1)),
         episode("own-ended-alone.org", first, ended=first + timedelta(hours=2), reason="answered"))
    failed = iso(first + timedelta(minutes=30))
    recovered = look(db, "own-ended.org", error_class="service_error", failed_at=failed)["cause"]
    assert (recovered["hint"], recovered["confidence"]) == ("recovered", "high")
    assert recovered["line"].startswith("AHN's own checks did not reach the site (") and recovered["line"].endswith("they answer again. That can be AHN's network path alone.")
    alone = look(db, "own-ended-alone.org", error_class="service_error", failed_at=failed)["cause"]
    assert (alone["hint"], alone["confidence"], alone["basis"]) == ("recovered", "medium", ["outage_episodes"])


def test_the_home_gate_sentence_has_the_age_and_the_provider_of_the_stored_check(db):
    due = now() + timedelta(days=3)
    cases = {  # host: (outcome, age, vendor, expected text between "check" and "refusal;")
        "age-dd.org": ("bot_challenge", timedelta(hours=19), "datadome", "19 h ago saw a DataDome bot-challenge"),
        "age-cf.org": ("bot_challenge", timedelta(hours=49), "cloudflare", "2 days ago saw a Cloudflare bot-challenge"),
        "age-novendor.org": ("bot_challenge", timedelta(hours=5), None, "5 h ago saw a bot-challenge"),
        "age-unknown.org": ("bot_challenge", timedelta(hours=5), "unknown", "5 h ago saw a bot-challenge"),
        "age-403.org": ("refused", timedelta(hours=47), None, "1 day ago saw an HTTP"),
        "age-login.org": ("login_wall", timedelta(minutes=2), None, "just now saw a login-wall"),
    }
    seed(db, *(probe(host, outcome=outcome, refusals=1, at=now() - age, due=due, vendor=vendor) for host, (outcome, age, vendor, _) in cases.items()))
    for host, (_outcome, _age, _vendor, text) in cases.items():
        found = look(db, host, error_class="site_access")["cause"]
        assert found["hint"] == "home_gate_unconfirmed" and found["confidence"] == "low" and found["basis"] == ["site_probe"], host
        assert found["line"] == f"Not determined: AHN's check {text} refusal; recheck_after is AHN's next check, not an instruction.", host
    # no stored next time: the date-less variant carries the same age and provider
    seed(db, probe("age-undated.org", outcome="bot_challenge", refusals=1, at=now() - timedelta(hours=7), due=now() - timedelta(hours=1), vendor="perimeterx"))
    undated = look(db, "age-undated.org", error_class="site_access")["cause"]
    assert undated["line"] == "Not determined: AHN's check 7 h ago saw a PerimeterX bot-challenge refusal; AHN checks again. That is AHN's check, not an instruction."
    assert "recheck_after" not in undated


# --- policy_closed: home_now_gated, persistent_refusal --------------------------------------------------------------------

def _refuse(db, monkeypatch, host, at):
    """A real home-page check of ``host`` that is answered with a 403 (stubbed request), stored by site_probe.measure."""
    from ahn import site_probe
    monkeypatch.setenv("AHN_SITE_PROBE", "1")
    monkeypatch.setattr(site_probe, "PAUSE_SECONDS", 0.0)
    with connect(db) as conn:
        conn.execute("INSERT INTO bandwidth_guard_state (name,period_start,checked_at,usage_gb,state) VALUES ('web',now(),now(),1,'normal') "
                     "ON CONFLICT DO NOTHING")
    access_profile.get_profile(db, host, now=at, reader=lambda site_host, path, **_: (
        ("ok", ROBOTS, "http_200") if path == "/robots.txt" else ("absent", None, "http_404")))
    body = b"<html>no</html>"
    result = {"label": None, "status": 403, "headers": {}, "cookies": [], "body": body, "complete": True, "ms": 80, "bytes_read": len(body)}
    row = site_probe.measure(db, host, "daily", now=at, reader=lambda _host: dict(result), paced=False)
    assert row is not None and row["outcome"] == "refused" and row["consecutive_refusals"] == 1
    return row


def test_d83_a_first_refusal_confirmed_after_24_hours_changes_only_recheck_after(db, monkeypatch):
    """D83: the shorter first wait (from a real check, not a seeded time) moves recheck_after and nothing the caller is told."""
    short, flat = "d83-short.org", "d83-flat.org"
    at = now() - timedelta(hours=1)
    stored_short = _refuse(db, monkeypatch, short, at)
    monkeypatch.setenv("AHN_REFUSAL_CONFIRM", "0")
    stored_flat = _refuse(db, monkeypatch, flat, at)
    assert stored_short["next_due_at"] == at + timedelta(hours=24) and stored_flat["next_due_at"] == at + timedelta(days=7)
    monkeypatch.setenv("AHN_REFUSAL_CONFIRM", "1")
    a = look(db, short, error_class="site_access")
    b = look(db, flat, error_class="site_access")
    assert a["cause"]["recheck_after"] == iso(at + timedelta(hours=24)) and b["cause"]["recheck_after"] == iso(at + timedelta(days=7))
    for key in ("recommendation", "summary_line"):
        assert a[key] == b[key], key
    assert a["next_action"]["do"] == b["next_action"]["do"]
    left = {k: v for k, v in a["cause"].items() if k != "recheck_after"}
    right = {k: v for k, v in b["cause"].items() if k != "recheck_after"}
    assert left == right and left["hint"] == "home_gate_unconfirmed" and left["branch"] == "undetermined"


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
    # the sentence says whose check it was and what kind of refusal, gives no count, and is not an instruction (D78)
    assert first["line"] == "Not determined: AHN's check 1 h ago saw a login-wall refusal; recheck_after is AHN's next check, not an instruction."
    assert first["note"] == cause.NOTE == "Information only; follow next_action."
    # a refusal measured long ago says nothing about now; a service error is not what a gate looks like
    seed(db, probe("gate-stale.org", outcome="refused", refusals=3, at=now() - timedelta(days=9)))
    assert look(db, "gate-stale.org", error_class="site_access")["cause"]["hint"] == "no_measurement"
    assert look(db, two, error_class="service_error").get("cause", {}).get("branch") != "policy_closed"


LOGIN_WALL_LINE = "Likely a sign-in requirement: the site's home page showed a sign-in wall on AHN's last two checks."
D84_CASES = {  # host: (stored outcome, stored count, daily rows (outcome, days ago[, probes]), confirmed for a failure that is not login_auth)
    "lw-two.org": ("login_wall", 0, [("login_wall", 7), ("login_wall", 0)], True),
    "lw-same-day.org": ("login_wall", 0, [("login_wall", 0, 2)], True),
    "lw-after-normal.org": ("login_wall", 0, [("reachable", 10), ("login_wall", 7), ("login_wall", 0)], True),
    "lw-one.org": ("login_wall", 0, [("login_wall", 0)], False),
    "lw-none.org": ("login_wall", 0, [], False),
    "lw-between-rate.org": ("login_wall", 0, [("login_wall", 7), ("rate_limited", 3), ("login_wall", 0)], False),
    "lw-between-normal.org": ("login_wall", 0, [("login_wall", 14), ("reachable", 10), ("login_wall", 0)], False),
    "lw-between-bot.org": ("login_wall", 0, [("login_wall", 7), ("bot_challenge", 3), ("login_wall", 0)], False),
    # the run is counted after the LAST other outcome, not the first: a normal answer long ago does not rescue it
    "lw-after-normal-then-bot.org": ("login_wall", 0, [("reachable", 10), ("login_wall", 7), ("bot_challenge", 3), ("login_wall", 0)], False),
    # a not_measured row resets the stored count too (site_probe.schedule), so it ends the run here as well
    "lw-between-unmeasured.org": ("login_wall", 0, [("login_wall", 7), ("not_measured", 3), ("login_wall", 0)], False),
    "lw-between-unreachable.org": ("login_wall", 0, [("login_wall", 7), ("unreachable", 3), ("login_wall", 0)], False),
    "lw-mixed-day.org": ("login_wall", 0, [("login_wall", 0, 1), ("reachable", 0, 1)], False),
    # a day that holds another outcome is not counted: one login wall on it and one on the next day are not two in a row
    "lw-mixed-day-then-next.org": ("login_wall", 0, [("login_wall", 1, 1), ("reachable", 1, 1), ("login_wall", 0, 1)], False),
    # D91: a redirected_same_host day also ends the run (the daily counts cannot tell an unfollowed www redirect from a path move)
    "lw-between-redirect.org": ("login_wall", 0, [("login_wall", 7), ("redirected_same_host", 3), ("login_wall", 0)], False),
    "lw-now-refused.org": ("refused", 1, [("login_wall", 14), ("login_wall", 7), ("refused", 0)], False),
    "lw-now-normal.org": ("reachable", 0, [("login_wall", 14), ("login_wall", 7), ("reachable", 0)], False),
}


def seed_d84_cases(db, due):
    today = now().date()
    for host, (outcome, count, rows, _) in D84_CASES.items():
        seed(db, probe(host, outcome=outcome, refusals=count, due=due),
             *[probe_day(host, today - timedelta(days=row[1]), row[0], row[2] if len(row) > 2 else 1) for row in rows])


def test_two_login_walls_in_a_row_are_a_policy_block_and_anything_else_stays_undetermined(db):
    """D84: a login wall does not raise consecutive_refusals, so two in a row are read from site_probe_daily. Only cause
    differs from an answer with the cause off: recommendation, next_action and summary_line are the same."""
    off = settings_with(hint=False)
    due = now() + timedelta(days=7)
    seed_d84_cases(db, due)
    for host, (outcome, _, _, confirmed) in D84_CASES.items():
        for params in ({"error_class": "site_access"}, {"error_class": "human_verification"}):
            with_cause, without = look(db, host, **params), look(off, host, **params)
            found = with_cause.get("cause")
            if confirmed:
                assert (found["branch"], found["hint"], found["confidence"], found["basis"]) == (
                    "policy_closed", "home_now_gated", "medium", ["site_probe", "site_probe_daily"]), host
                assert found["line"] == LOGIN_WALL_LINE
                assert "recheck_after" not in found and "window" not in found
            elif outcome == "login_wall":
                assert (found["branch"], found["hint"], found["confidence"]) == ("undetermined", "home_gate_unconfirmed", "low"), host
                assert found["basis"] == ["site_probe"] and found["recheck_after"] == iso(due)
                assert "login-wall refusal" in found["line"]
            else:
                assert found is None or found["hint"] != "home_now_gated", host
            assert {k: v for k, v in strip(with_cause).items() if k != "cause"} == strip(without), host
            assert with_cause["recommendation"] == without["recommendation"] and with_cause["next_action"] == without["next_action"]
            assert with_cause["summary_line"] == without["summary_line"]
    # a service error is not what a login wall looks like
    assert look(db, "lw-two.org", error_class="service_error").get("cause", {}).get("branch") != "policy_closed"


def test_a_step_that_failed_on_a_sign_in_or_names_no_error_class_is_answered_as_before_d84(monkeypatch, db):
    """D84 does not apply when the caller's error_class is login_auth (the site is expected to ask for a sign-in, and a
    client reads home_now_gated as "stop visiting") or empty: the whole answer is what it was before."""
    seed_d84_cases(db, now() + timedelta(days=7))
    hosts = [host for host, case in D84_CASES.items() if case[3]]
    assert hosts
    # an allow list: a class added to the enum later is not covered by D84 until it is added to D84_ERROR_CLASSES on purpose
    assert set(cause.D84_ERROR_CLASSES) == {c.value for c in ErrorClass} - {"login_auth", "service_error"}
    for params in ({"error_class": "login_auth"}, {"failure_stage": "access_denial_observed"}, {"failure_stage": "response_observed"},
                   {"failure_stage": "unknown"}, {"error_class": "login_auth", "failure_stage": "access_denial_observed"}):
        answers = {host: look(db, host, **params) for host in hosts}
        with monkeypatch.context() as patch:
            patch.setattr(cause, "_login_wall_twice", lambda conn, host: False)   # the answer before D84
            before = {host: look(db, host, **params) for host in hosts}
        for host in hosts:
            assert strip(answers[host]) == strip(before[host]), (host, params)
            found = answers[host]["cause"]
            assert (found["hint"], found["basis"]) == ("home_gate_unconfirmed", ["site_probe"]), (host, params)
    assert look(db, hosts[0]).get("cause") is None   # no failure named: no cause at all, as before


def test_a_persistent_refusal_over_several_days_wins_over_a_login_wall_confirmation(db):
    host, today = "lw-persistent.org", now().date()
    hours = [now() - timedelta(hours=1) - timedelta(days=n) for n in (0, 1, 2)]
    seed(db, probe(host, outcome="login_wall", due=now() + timedelta(days=7)), probe_day(host, today - timedelta(days=7), "login_wall"),
         probe_day(host, today, "login_wall"), *[hourly(host, hour, sources=3, error_class="site_access") for hour in hours])
    found = look(db, host, error_class="site_access")["cause"]
    assert (found["hint"], found["basis"]) == ("persistent_refusal", ["lookup_signals_hourly"])


def test_a_login_wall_after_two_counted_refusals_says_so(db):
    """The stored count is 2 or more (bot challenges or HTTP refusals) and the latest check is a login wall: the sentence
    does not claim that the last two checks were refused for the same reason."""
    due = now() + timedelta(days=7)
    seed(db, probe("lw-count-two.org", outcome="login_wall", refusals=2, due=due), probe("lw-count-two-bot.org", outcome="bot_challenge", refusals=2, due=due))
    for params in ({"error_class": "login_auth"}, {"error_class": "site_access"}, {"failure_stage": "access_denial_observed"}):
        found = look(db, "lw-count-two.org", **params)["cause"]
        assert (found["hint"], found["basis"]) == ("home_now_gated", ["site_probe"])
        assert found["line"] == cause.LINES["home_now_gated_latest_login_wall"] == (
            "Likely a policy block: the site refused AHN's home-page checks at least twice in a row; the latest showed a sign-in wall.")
    assert look(db, "lw-count-two-bot.org", error_class="site_access")["cause"]["line"] == cause.LINES["home_now_gated"]


def test_d84_changes_only_the_login_wall_hosts_it_names_over_a_fixed_set_of_inputs(monkeypatch, db):
    """The fixed replay behind the numbers in the D84 row: the same hosts and failure shapes with and without the rule."""
    due, today = now() + timedelta(days=7), now().date()
    histories = {"none": [], "lw1": [("login_wall", 0, 1)], "lw2": [("login_wall", 7, 1), ("login_wall", 0, 1)],
                 "lw2same": [("login_wall", 0, 2)], "mixed": [("login_wall", 7, 1), ("bot_challenge", 3, 1), ("login_wall", 0, 1)],
                 "okthen": [("reachable", 10, 1), ("login_wall", 7, 1), ("login_wall", 0, 1)],
                 "lwthenok": [("login_wall", 14, 1), ("login_wall", 7, 1), ("reachable", 0, 1)],
                 "sameday": [("login_wall", 0, 1), ("reachable", 0, 1)]}
    hosts = {}
    for n, (outcome, count, name) in enumerate(itertools.product(("login_wall", "bot_challenge", "refused", "rate_limited", "reachable", None),
                                                                 (0, 1, 2), histories)):
        host = f"replay{n}.org"
        hosts[host] = (outcome, count, name)
        seed(db, *([probe(host, outcome=outcome, refusals=count, due=due)] if outcome else []),
             *[probe_day(host, today - timedelta(days=days), o, k) for o, days, k in histories[name]])
    shapes = list(itertools.product((None, "login_auth", "site_access", "human_verification", "service_error"),
                                    (None, "access_denial_observed", "before_target_response")))
    at = datetime.now(timezone.utc)
    when = cause.parse_failed_at(None, at)
    with connect(settings_with()) as conn:
        def run():
            return {(host, ec, st): cause.compute(conn, host, at, when, error_class=ec, failure_stage=st) for host in hosts for ec, st in shapes}
        after = run()
        with monkeypatch.context() as patch:
            patch.setattr(cause, "_login_wall_twice", lambda conn, host: False)
            before = run()
    assert len(after) == len(hosts) * len(shapes) == 144 * 15
    changed = [key for key in after if after[key] != before[key]]
    for host, ec, st in changed:
        outcome, count, name = hosts[host]
        # a stored login wall, a stored count below 2, two login walls in a row in the daily totals, and a failure that
        # names an error class other than login_auth (service_error never reaches the gate)
        assert outcome == "login_wall" and count < 2 and name in ("lw2", "lw2same", "okthen"), (host, ec, st)
        assert ec in ("site_access", "human_verification"), (host, ec, st)
        assert before[(host, ec, st)]["hint"] == "home_gate_unconfirmed"
        assert (after[(host, ec, st)]["hint"], after[(host, ec, st)]["line"]) == ("home_now_gated", LOGIN_WALL_LINE)
    assert len(changed) == 36


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
    base = now() - timedelta(hours=1)  # an hour that is already past whatever the time of day
    host = "persistent.org"
    days = [base - timedelta(days=n) for n in (0, 1, 2)]
    seed(db, *[hourly(host, d, sources=3, error_class="site_access") for d in days])
    hit = look(db, host, error_class="site_access")["cause"]
    assert (hit["branch"], hit["hint"], hit["confidence"], hit["basis"]) == ("policy_closed", "persistent_refusal", "medium", ["lookup_signals_hourly"])
    # one client looping over many hours is not several clients
    one = "persistent-one.org"
    seed(db, *[hourly(one, d - timedelta(hours=h), sources=1, error_class="site_access") for d in days for h in (0, 1, 2)])
    assert look(db, one, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # two clients a day are not several
    pair = "persistent-pair.org"
    seed(db, *[hourly(pair, d, sources=2, error_class="site_access") for d in days])
    assert look(db, pair, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # only two days
    two = "persistent-two.org"
    seed(db, *[hourly(two, d, sources=3, error_class="site_access") for d in days[:2]])
    assert look(db, two, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # a 403 class counts without an error class; a 5xx class does not, even with error_class site_access
    status = "persistent-403.org"
    seed(db, *[hourly(status, d, sources=3, http="403") for d in days])
    assert look(db, status, error_class="site_access")["cause"]["hint"] == "persistent_refusal"
    outage = "persistent-5xx.org"
    seed(db, *[hourly(outage, d, sources=3, error_class="site_access", http="5xx") for d in days])
    assert look(db, outage, error_class="site_access")["cause"]["hint"] != "persistent_refusal"
    # the caller's own failure must be an access one
    assert look(db, host, error_class="service_error").get("cause", {}).get("hint") != "persistent_refusal"


# --- rate_limited, others_failing_same_hour ------------------------------------------------------------------------------

def test_rate_limited_needs_several_other_clients_and_never_reads_the_callers_own_codes(db):
    host = "rate.org"
    # the caller's own 429 or Retry-After is never evidence (D47): the answer is the same with or without them
    for params in ({"http_status": "429"}, {"retry_after": "lt_60"}, {"retry_after": "gt_3600"}):
        body = look(db, host, error_class="site_access", **params)
        assert body.get("cause") == look(db, host, error_class="site_access").get("cause"), params
        assert body.get("cause", {}).get("hint") != "rate_limited"
    few = "rate-few.org"
    seed(db, hourly(few, now(), sources=2, http="429"))
    assert look(db, few, error_class="site_access").get("cause", {}).get("hint") != "rate_limited"
    many = "rate-many.org"
    seed(db, hourly(many, now(), sources=3, http="429"))
    sure = look(db, many, error_class="site_access")["cause"]
    assert (sure["branch"], sure["hint"], sure["confidence"], sure["basis"]) == ("transient", "rate_limited", "medium", ["lookup_signals_hourly"])
    assert "window" not in sure
    # another hour does not count
    late = "rate-late.org"
    seed(db, hourly(late, now() - timedelta(hours=2), sources=5, http="429"))
    assert look(db, late, error_class="site_access").get("cause", {}).get("hint") != "rate_limited"


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


def test_unfollowed_www_redirect_is_no_measurement_not_a_normal_check(db):
    """D88: a home-page check that ended at a redirect to the other name (www/apex) AHN did not follow says nothing about the
    home page."""
    due = now() + timedelta(hours=5)
    www = {"followed_www": False, "to_relation": "www_alias", "path_class": "other"}
    seed(db, probe("www-open.org", outcome="redirected_same_host", due=due, redirect=www),
         probe("www-followed.org", outcome="redirected_same_host", redirect={**www, "followed_www": True}),
         probe("www-locale.org", outcome="redirected_same_host", redirect={"followed_www": False, "to_relation": "same_host", "path_class": "locale"}),
         probe("www-old.org", outcome="redirected_same_host"),
         probe("www-day.org", outcome="redirected_same_host", redirect=www),
         probe_day("www-day.org", now().date(), "redirected_same_host", 2))
    for params in ({"failure_stage": "before_target_response"}, {"error_class": "login_auth"}, {"error_class": "human_verification"}):
        open_ = look(db, "www-open.org", **params)["cause"]
        assert (open_["branch"], open_["hint"], open_["basis"]) == ("undetermined", "no_measurement", ["site_probe"]), params
        # the next check may end at the same redirect, so no home-page measurement is promised
        assert "recheck_after" not in open_, params
        assert "your side" not in open_["line"]
        # the day's counts keep no redirect record, so they do not stand in for the check
        assert look(db, "www-day.org", **params)["cause"]["hint"] == "no_measurement", params
        # a followed www answer, a path or locale redirect on the same host and a row without a redirect record stay as they were
        for host in ("www-followed.org", "www-locale.org", "www-old.org"):
            assert look(db, host, **params)["cause"]["hint"] == "your_side_likely", (host, params)


def test_unfollowed_www_redirect_current_row_of_any_age_keeps_the_days_redirect_count_out_of_the_normal_evidence(db):
    """D88: the daily path. The current row is an unfollowed redirect, so the day's redirected_same_host count is no normal
    check, whether the row is 13 hours old or only 1 hour old with a failed_at 20 hours back."""
    www = {"followed_www": False, "to_relation": "www_alias", "path_class": "other"}
    today = now()
    seed(db, probe("www-late.org", outcome="redirected_same_host", at=today - timedelta(hours=13), redirect=www),
         probe_day("www-late.org", today.date(), "redirected_same_host", 1),
         probe("www-failed.org", outcome="redirected_same_host", at=today - timedelta(hours=1), redirect=www),
         probe_day("www-failed.org", (today - timedelta(hours=20)).date(), "redirected_same_host", 1),
         # the day's other outcomes are still read: a day that also holds a real normal check keeps its meaning (the limit)
         probe("www-mixed.org", outcome="redirected_same_host", at=today - timedelta(hours=13), redirect=www),
         probe_day("www-mixed.org", today.date(), "redirected_same_host", 1), probe_day("www-mixed.org", today.date(), "reachable", 1))
    for host, extra in (("www-late.org", {}), ("www-failed.org", {"failed_at": iso(today - timedelta(hours=20))})):
        cause_ = look(db, host, error_class="human_verification", **extra)["cause"]
        assert (cause_["hint"], cause_["basis"]) == ("no_measurement", ["site_probe"]), host
        assert "recheck_after" not in cause_, host
    assert look(db, "www-mixed.org", error_class="human_verification")["cause"]["hint"] == "your_side_likely"


def test_d91_the_days_redirect_count_is_no_normal_evidence_whatever_the_current_row_is(db, monkeypatch):
    """D91: failed_at three days ago, that day's counts hold only redirected_same_host (possibly unfollowed www redirects), the
    current row is a normal check (older than 12 hours), a not_measured row or has no redirect record: AHN did not see the home
    page that day, so no your_side_likely. A day that also holds a real normal check is still read."""
    today = now()
    day = (today - timedelta(days=3)).date()
    failed = iso(today - timedelta(days=3))
    seed(db, probe("d91-reach.org", outcome="reachable", at=today - timedelta(hours=13)), probe_day("d91-reach.org", day, "redirected_same_host", 2),
         probe("d91-notmeas.org", outcome="not_measured", at=today - timedelta(hours=1), reason="robots_unreadable"),
         probe_day("d91-notmeas.org", day, "redirected_same_host", 1),
         probe("d91-mixed.org", outcome="reachable", at=today - timedelta(hours=13)),
         probe_day("d91-mixed.org", day, "redirected_same_host", 1), probe_day("d91-mixed.org", day, "reachable", 1))
    for host in ("d91-reach.org", "d91-notmeas.org"):
        body = look(db, host, failure_stage="before_target_response", failed_at=failed)
        assert (body["cause"]["branch"], body["cause"]["hint"], body["cause"]["basis"]) == ("undetermined", "no_measurement", ["site_probe"]), host
    assert look(db, "d91-mixed.org", failure_stage="before_target_response", failed_at=failed)["cause"]["hint"] == "your_side_likely"
    # the same stored row answered with the D88 rule (the daily filter applied only while the current row is an unfollowed
    # redirect): recommendation, next_action and summary_line are the same, only cause differs
    def d88_measurement(conn, host, probe_row, when):
        if probe_row is not None and probe_row["outcome"] != "not_measured" and abs(probe_row["probed_at"] - when.at) <= cause.MEASURE_WINDOW:
            return True, probe_row["outcome"] in cause.NORMAL_OUTCOMES
        rows = conn.execute("SELECT outcome FROM site_probe_daily WHERE site_host=%s AND day=%s AND outcome<>'not_measured' AND probes>0",
                            (host, when.at.astimezone(timezone.utc).date())).fetchall()
        return (True, all(r["outcome"] in cause.NORMAL_OUTCOMES for r in rows)) if rows else (False, False)

    for params in ({"failure_stage": "before_target_response"}, {"error_class": "login_auth"}, {"error_class": "human_verification"}):
        new = look(db, "d91-reach.org", failed_at=failed, **params)
        monkeypatch.setattr(cause, "_measurement", d88_measurement)
        old = look(db, "d91-reach.org", failed_at=failed, **params)
        monkeypatch.undo()
        assert old["cause"]["hint"] == "your_side_likely" and new["cause"]["hint"] == "no_measurement", params
        for field in ("recommendation", "next_action", "summary_line"):
            assert new.get(field) == old.get(field), (field, params)


def test_unfollowed_www_redirect_gives_site_access_and_service_error_lookups_a_cause_they_lacked(db, monkeypatch):
    """D88: lookups that named site_access or service_error (no failure_stage) got no cause from a normal-looking check; with an
    unfollowed redirect they now carry no_measurement. Everything the decision says stays the same."""
    www = {"followed_www": False, "to_relation": "www_alias", "path_class": "other"}
    seed(db, probe("www-sa.org", outcome="redirected_same_host", redirect=www))
    for params in ({"error_class": "site_access"}, {"error_class": "service_error"}):
        monkeypatch.setattr(cause, "unfollowed_www_redirect", lambda outcome, redirect: False)
        assert "cause" not in look(db, "www-sa.org", **params), params   # the old behaviour
        monkeypatch.undo()
        body = look(db, "www-sa.org", **params)
        assert (body["cause"]["branch"], body["cause"]["hint"]) == ("undetermined", "no_measurement"), params


def test_unfollowed_www_redirect_only_cause_differs_from_the_old_behaviour(db, monkeypatch):
    """D88: the same stored row, answered with the helper behaving as before (never matches) and as now: recommendation,
    next_action and summary_line are the same; only cause differs."""
    www = {"followed_www": False, "to_relation": "www_alias", "path_class": "other"}
    seed(db, probe("www-cmp.org", outcome="redirected_same_host", redirect=www))
    cases = ({"failure_stage": "before_target_response"}, {"error_class": "login_auth"}, {"error_class": "human_verification"},
             {"error_class": "site_access"}, {"error_class": "service_error"})
    now_answers = [look(db, "www-cmp.org", **params) for params in cases]
    monkeypatch.setattr(cause, "unfollowed_www_redirect", lambda outcome, redirect: False)
    old_answers = [look(db, "www-cmp.org", **params) for params in cases]
    for params, new, old in zip(cases, now_answers, old_answers):
        assert new["recommendation"] == old["recommendation"], params
        assert new.get("next_action") == old.get("next_action"), params
        assert new["summary_line"] == old["summary_line"], params
        assert new["cause"]["hint"] == "no_measurement", params
        assert old.get("cause", {}).get("hint") != "no_measurement", params
    assert old_answers[0]["cause"]["hint"] == "your_side_likely"
    assert "cause" not in old_answers[-1] and "cause" not in old_answers[-2]   # site_access, service_error: no cause before


def test_no_measurement_names_the_next_measurement_only_when_there_is_one(db):
    none = look(db, "nothing.org", error_class="site_access")["cause"]
    assert (none["branch"], none["hint"], none["confidence"], none["basis"]) == ("undetermined", "no_measurement", "low", [])
    assert "recheck_after" not in none
    due = now() + timedelta(hours=5)
    seed(db, probe("later.org", outcome="not_measured", at=now() - timedelta(days=2), due=due, reason="robots_unreadable"))
    later = look(db, "later.org", error_class="site_access")["cause"]
    assert later["hint"] == "no_measurement" and later["basis"] == ["site_probe"] and later["recheck_after"] == iso(due)
    # a check due in the past is not a time to come back at
    seed(db, probe("overdue.org", outcome="not_measured", at=now() - timedelta(days=2), due=now() - timedelta(hours=1), reason="robots_unreadable"))
    assert "recheck_after" not in look(db, "overdue.org", error_class="site_access")["cause"]
    # the general sentence says "near that time" and gives no count of hours (D78): the absence test reads the latest row alone
    # and the daily row of the same UTC day, so "within 12 hours" could be false
    assert later["line"] == "Not determined: AHN has no usable home-page check of this site near that time."
    assert "hours" not in later["line"]   # the absence test is weaker than "none within N hours", so no count is claimed


def test_a_host_that_is_not_eligible_gets_the_general_sentence_and_no_recheck_after(db):
    due = now() + timedelta(hours=6)
    seed(db, probe("noteligible.org", outcome="not_measured", at=now() - timedelta(hours=1), due=due, reason="not_eligible"),
         probe("unreadable.org", outcome="not_measured", at=now() - timedelta(hours=1), due=due, reason="robots_unreadable"))
    not_eligible = look(db, "noteligible.org", error_class="site_access")["cause"]
    assert (not_eligible["branch"], not_eligible["hint"], not_eligible["confidence"], not_eligible["basis"]) == (
        "undetermined", "no_measurement", "low", ["site_probe"])
    assert "recheck_after" not in not_eligible and not_eligible["line"] == cause.LINES["no_measurement"]
    # robots.txt is read again, so its stored time stays
    assert look(db, "unreadable.org", error_class="site_access")["cause"]["recheck_after"] == iso(due)


def test_a_host_robots_txt_keeps_ahn_from_measuring_gets_its_own_sentence_and_no_recheck(db):
    due = now() + timedelta(hours=6)
    seed(db, probe("robots-no.org", outcome="not_measured", at=now() - timedelta(hours=1), due=due, reason="robots_disallowed"),
         probe("robots-403.org", outcome="not_measured", at=now() - timedelta(hours=1), due=due, reason="robots_refused"),
         probe("robots-unreadable.org", outcome="not_measured", at=now() - timedelta(hours=1), due=due, reason="robots_unreadable"))
    no, refused, unreadable = (look(db, host, error_class="site_access")["cause"]
                               for host in ("robots-no.org", "robots-403.org", "robots-unreadable.org"))
    for found in (no, refused):
        assert (found["branch"], found["hint"], found["confidence"], found["basis"]) == ("undetermined", "no_measurement", "low", ["site_probe"])
        assert "recheck_after" not in found and found["note"] == cause.NOTE
    assert no["line"] == "Not determined: AHN does not measure this site because its robots.txt disallows AHN's check."
    assert refused["line"] == "Not determined: AHN does not measure this site because the site refused AHN's request for robots.txt."
    # an unreadable robots.txt is a temporary state of AHN's read, not a stated ban: the general sentence and the stored time stay
    assert unreadable["line"] == cause.LINES["no_measurement"] and unreadable["recheck_after"] == iso(due)


def test_the_wording_change_leaves_the_judgment_and_the_codes_as_they_were(db):
    """D78: the same inputs give the same recommendation, next_action, branch, hint, confidence and basis with the cause on or off."""
    off = settings_with(hint=False)
    due = now() + timedelta(days=7)
    seed(db, probe("wording-bot.org", outcome="bot_challenge", refusals=1, due=due), probe("wording-403.org", outcome="refused", refusals=1, due=due),
         probe("wording-login.org", outcome="login_wall", refusals=1, due=due),
         probe("wording-robots.org", outcome="not_measured", at=now() - timedelta(hours=1), reason="robots_disallowed"),
         probe("wording-backoff.org", outcome="not_measured", at=now() - timedelta(hours=1), reason="robots_unreadable"))
    expected = {"wording-bot.org": ("undetermined", "home_gate_unconfirmed", "low", ["site_probe"]),
                "wording-403.org": ("undetermined", "home_gate_unconfirmed", "low", ["site_probe"]),
                "wording-login.org": ("undetermined", "home_gate_unconfirmed", "low", ["site_probe"]),
                "wording-robots.org": ("undetermined", "no_measurement", "low", ["site_probe"]),
                "wording-backoff.org": ("undetermined", "no_measurement", "low", ["site_probe"]),
                "wording-none.org": ("undetermined", "no_measurement", "low", [])}
    for host, codes in expected.items():
        with_cause, without = look(db, host, error_class="site_access"), look(off, host, error_class="site_access")
        found = with_cause["cause"]
        assert (found["branch"], found["hint"], found["confidence"], found["basis"]) == codes, host
        assert {k: v for k, v in strip(with_cause).items() if k != "cause"} == strip(without), host
        assert with_cause["recommendation"] == without["recommendation"] and with_cause["next_action"] == without["next_action"]
        assert set(found) <= {"branch", "hint", "confidence", "basis", "line", "note", "recheck_after"}
        assert 1 <= len(found["line"]) <= cause.LINE_MAX and found["line"].isascii() and found["line"].isprintable()
    assert "saw an HTTP refusal" in cause.gate_line("refused", True, timedelta(hours=2)) and " a HTTP" not in " ".join(cause.LINES.values())
    assert all(1 <= len(text) <= cause.LINE_MAX and text.isascii() and text.isprintable() for text in cause.LINES.values())
    # a date-less refusal sentence never points at a recheck_after that is not there
    seed(db, probe("wording-overdue.org", outcome="refused", refusals=1, due=now() - timedelta(hours=1)))
    overdue = look(db, "wording-overdue.org", error_class="site_access")["cause"]
    assert "recheck_after" not in overdue and "recheck_after" not in overdue["line"] and overdue["line"].endswith("not an instruction.")


def test_a_robots_reason_never_offers_recheck_after_and_names_the_present_only_while_the_row_is_recent(db):
    due = now() + timedelta(hours=6)
    # the row was overwritten by a robots reason after a normal measurement the same UTC day: the answer is your_side_likely,
    # and the stored time is the next robots.txt look, which is not a measurement
    seed(db, probe("robots-day.org", outcome="not_measured", at=now() - timedelta(hours=1), due=due, reason="robots_disallowed"),
         probe_day("robots-day.org", now().date(), "reachable"))
    side = look(db, "robots-day.org", failure_stage="before_target_response")["cause"]
    assert (side["branch"], side["hint"], side["basis"]) == ("undetermined", "your_side_likely", ["site_probe"])
    assert "recheck_after" not in side
    # a robots row older than a day does not say what is so now: the general sentence, still without recheck_after
    seed(db, probe("robots-old.org", outcome="not_measured", at=now() - timedelta(hours=30), due=due, reason="robots_refused"))
    old = look(db, "robots-old.org", error_class="site_access")["cause"]
    assert (old["hint"], old["basis"]) == ("no_measurement", ["site_probe"]) and "recheck_after" not in old
    assert old["line"] == cause.LINES["no_measurement"]


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


def test_d73_cause_and_d69_status_summary_never_share_an_answer(db):
    host = "bothfeatures.org"
    seed(db, provider_event(host, now() - timedelta(hours=1), "major", "none"))
    failed = look(db, host, task_step="status_check", error_class="service_error")
    assert failed["cause"]["hint"] == "provider_incident"
    assert failed["summary_line"] not in response_guide.STATUS_ROWS.values()
    plain = look(db, host, task_step="status_check")
    assert "cause" not in plain


def test_the_switch_off_gives_the_answer_before_the_cause_and_on_changes_nothing_else(db):
    on, off = db, settings_with(hint=False)
    t0 = now() - timedelta(hours=3)
    seed(db, provider_event("same-provider.org", t0, "major", "none"), episode("same-errors.org", t0),
         probe("same-gate.org", outcome="refused", refusals=2), probe("same-side.org"),
         hourly("same-others.org", now(), sources=4, error_class="service_error"), hourly("same-rate.org", now(), sources=3, http="429"))
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
        # everything but the cause is the same, the summary line included (D51)
        assert {k: v for k, v in strip(with_cause).items() if k != "cause"} == strip(without), host
    assert seen_hints >= {"provider_incident", "site_errors", "home_now_gated", "your_side_likely", "others_failing_same_hour", "rate_limited",
                          "no_measurement"}
    # with the guide off the switch changes nothing visible either
    assert "cause" not in look(settings_with(hint=False, guide=False), "same-provider.org", error_class="service_error")
    assert look(settings_with(hint=True, guide=False), "same-provider.org", error_class="service_error")["cause"]["hint"] == "provider_incident"


# --- nothing from a site or a host in the output -----------------------------------------------------------------------------

def test_no_host_name_and_no_site_text_reaches_a_cause(db):
    t0 = now() - timedelta(hours=3)
    hosts = ["leak-provider.example-bank.org", "leak-errors.example-bank.org", "leak-gate.example-bank.org", "leak-none.example-bank.org"]
    seed(db, provider_event(hosts[0], t0, "major", "none"), episode(hosts[1], t0), probe(hosts[2], outcome="bot_challenge", refusals=2))
    templates = [re.compile("^" + re.escape(text).replace(re.escape("{win}"), r"(\(since [0-9: -]+ UTC\)|\([0-9: -]+(?:-| to )[0-9: -]+ UTC\))") + "$")
                 for text in cause.LINES.values()] + [re.compile("^" + re.escape(text.replace(" {win}", "")) + "$") for text in cause.LINES.values() if "{win}" in text]
    templates.append(re.compile(r"Not determined: AHN's check [0-9a-z ]+ saw an? (?:[A-Za-z ]+ )?[a-z-]+ refusal; .*"))
    for host in hosts:
        for params in ({"error_class": "service_error"}, {"error_class": "site_access"}, {"failure_stage": "before_target_response"},
                       {"error_class": "login_auth", "failed_at": iso(t0 + timedelta(minutes=1))}):
            found = look(db, host, **params).get("cause")
            if not found:
                continue
            blob = json.dumps(found)
            assert host not in blob and "example-bank" not in blob and "leak-" not in blob
            assert set(found) <= {"branch", "hint", "confidence", "basis", "window", "line", "note", "recheck_after"}
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


def test_d91_with_a_terms_signal_only_cause_changes_and_the_signal_does_not_displace_it(db, monkeypatch):
    """D91 with a terms signal on the host: the day's redirect count is no normal evidence, so the cause line is the terms
    signal (it fills the place of no_measurement) instead of your_side_likely; recommendation, next_action and summary_line
    are the same as when the daily count is read as a normal check (the D88 rule)."""
    today = now()
    day = (today - timedelta(days=3)).date()
    failed = iso(today - timedelta(days=3))
    seed(db, probe("d91-terms.org", outcome="reachable", at=today - timedelta(hours=13)), probe_day("d91-terms.org", day, "redirected_same_host", 2))
    with connect(db) as conn:
        conn.execute("INSERT INTO host_access_changes (site_host,at,field,from_code,to_code,source) VALUES (%s,%s,%s,%s,%s,%s)",
                     ("d91-terms.org", today - timedelta(hours=5), "llms_presence", "present", "absent", "read"))

    def d88_measurement(conn, host, probe_row, when):
        rows = conn.execute("SELECT outcome FROM site_probe_daily WHERE site_host=%s AND day=%s AND outcome<>'not_measured' AND probes>0",
                            (host, when.at.astimezone(timezone.utc).date())).fetchall()
        return (True, all(r["outcome"] in cause.NORMAL_OUTCOMES for r in rows)) if rows else (False, False)

    new = look(db, "d91-terms.org", failure_stage="before_target_response", failed_at=failed)
    monkeypatch.setattr(cause, "_measurement", d88_measurement)
    old = look(db, "d91-terms.org", failure_stage="before_target_response", failed_at=failed)
    monkeypatch.undo()
    assert new["cause"]["hint"] == "no_measurement" and old["cause"]["hint"] == "your_side_likely"
    for field in ("recommendation", "next_action", "summary_line"):
        assert new.get(field) == old.get(field), field
