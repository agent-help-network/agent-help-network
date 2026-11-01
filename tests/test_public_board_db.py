"""Public "Site problems right now" board (D53) against the dedicated ahn_test database. Stub data only; no host is read.

The provider fixtures copy the shapes of the live candidates of 2026-10-23 that were false problems: cloudflare.com
(page "major" with only two regional maintenances, provider update time 57 days old), gocardless.com ("minor",
updated 16 days ago, no scope), mediavine.com ("minor", no update time at all), next to a fresh "major" incident
(the shape of zapier.com during a real outage) that must stay.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest
from markupsafe import escape

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)
os.environ.setdefault("AHN_DATABASE_URL", TEST_URL)  # ahn.main builds its module-level app from the environment
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")

from fastapi.testclient import TestClient  # noqa: E402

from ahn import feed_robots, public_board, signal_history, triage  # noqa: E402
from ahn.db import connect, migrate  # noqa: E402
from ahn.main import POLICY_UPDATED, create_app  # noqa: E402
from ahn.service import _provider_scope  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from ahn.status_refresh import register_source  # noqa: E402

PEPPER = "test-pepper-must-be-at-least-thirty-two-chars"
TABLES = "robots_read_events, provider_status_events, outage_episodes, robots_read_daily"
FRESH, STALE_HOSTS = "zapier.com", ("cloudflare.com", "gocardless.com", "mediavine.com")
SINCE = "2026-10-27"


def settings(**switches) -> Settings:
    return Settings(TEST_URL, PEPPER, public_mode=True, reporting_enabled=True, **switches)


ON = dict(public_board=True, public_board_since=datetime(2026, 10, 27).date())


@pytest.fixture
def db():
    base = settings()
    with connect(base) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(base)
    with connect(base) as conn:
        conn.execute(f"TRUNCATE {TABLES}, status_cache, status_sources, queried_hosts, host_access_profile RESTART IDENTITY")
    return base


def put_status(conn, host, indicator, *, updated, incidents=None, impact=None, incident_updated=None, maintenances=None,
               affected=None, fetched=None):
    assert register_source(conn, host)
    conn.execute(
        """INSERT INTO status_cache (site_host,indicator,source_updated_at,fetched_at,active_incidents,max_incident_impact,
                                     incident_updated_at,active_maintenances,affected)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (host, indicator, updated, fetched or datetime.now(timezone.utc), incidents, impact, incident_updated,
         maintenances, affected))


def put_atom(conn, host, *, incidents, incident_updated, blind=False, fetched=None):
    """A reading of the page's Atom history as status_refresh._store_history writes it (D52)."""
    assert register_source(conn, host)
    conn.execute(
        """INSERT INTO status_cache (site_host,indicator,source_updated_at,fetched_at,active_incidents,incident_updated_at,
                                     active_maintenances,feed_source,window_blind)
           VALUES (%s,'unknown',NULL,%s,%s,%s,NULL,'history_atom',%s)""",
        (host, fetched or datetime.now(timezone.utc), incidents, incident_updated, blind))


def live_shapes(db, now):
    with connect(db) as conn:
        put_status(conn, "cloudflare.com", "major", updated=now - timedelta(days=57), incidents=0, impact="none",
                   maintenances=2, affected=["Nairobi, Kenya - (NBO)", "Guatemala City, Guatemala - (GUA)"])
        put_status(conn, "gocardless.com", "minor", updated=now - timedelta(days=16))
        put_status(conn, "mediavine.com", "minor", updated=None)
        put_status(conn, FRESH, "major", updated=now - timedelta(minutes=20), incidents=1, impact="major",
                   incident_updated=now - timedelta(minutes=20), maintenances=0, affected=["Zaps"])


def board(db, now):
    with connect(db) as conn:
        return signal_history.board_summary(conn, now=now)


def lookup(client, host):
    body = client.get("/v1/incidents/check", params={"site_host": host}).json()
    for key in ("generated_at",):
        body.pop(key, None)
    return body


# The judgement ------------------------------------------------------------------------------------------------------

def status(indicator, *, updated, **scope_row):
    row = {"indicator": indicator, "max_incident_impact": None, **scope_row}
    return {"indicator": indicator, "source_updated_at": updated, "freshness": "fresh", **_provider_scope(row)}


NOW = datetime(2026, 10, 24, 3, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("provider, expected", [
    (status("major", updated=NOW - timedelta(days=57), active_incidents=0, max_incident_impact="none", active_maintenances=2),
     "stale_update"),                                          # cloudflare.com on 2026-10-23
    (status("minor", updated=NOW - timedelta(days=16)), "stale_update"),  # gocardless.com
    (status("minor", updated=None), "no_update_time"),         # mediavine.com
    (status("major", updated=NOW - timedelta(hours=1), active_incidents=1, max_incident_impact="major",
            incident_updated_at=NOW - timedelta(days=30), active_maintenances=0), "stale_incidents"),  # D45
    (status("minor", updated=NOW, active_incidents=0, active_maintenances=0), "not_a_wait"),  # components only
    (status("major", updated=NOW - timedelta(minutes=20), active_incidents=1, max_incident_impact="major",
            incident_updated_at=NOW - timedelta(minutes=20), active_maintenances=0), None),  # zapier.com-like
    (status("minor", updated=NOW - timedelta(days=7)), None),  # exactly 7 days: still listed
    (status("minor", updated=NOW - timedelta(days=7, seconds=1)), "stale_update"),
    # A stale page time with a fresh incident update: the latest time the statement carries decides.
    (status("major", updated=NOW - timedelta(days=40), active_incidents=1, max_incident_impact="major",
            incident_updated_at=NOW - timedelta(hours=2), active_maintenances=0), None),
    # Open incidents that stopped updating 20 days ago stay stale on the board whatever the maintenance count, even
    # though D45 lets the lookup keep waiting when the count is unknown or not zero.
    (status("minor", updated=NOW - timedelta(hours=1), active_incidents=1, max_incident_impact="minor",
            incident_updated_at=NOW - timedelta(days=20), active_maintenances=1), "stale_incidents"),
    (status("minor", updated=NOW - timedelta(hours=1), active_incidents=1, max_incident_impact="minor",
            incident_updated_at=NOW - timedelta(days=20)), "stale_incidents"),
    # A time from the future is no time: it would otherwise stay listed for ever.
    (status("major", updated=NOW + timedelta(days=400)), "no_update_time"),
    (status("major", updated=NOW + timedelta(minutes=30)), None),  # clock skew within FUTURE_SLACK
    (status("major", updated=NOW - timedelta(days=30), active_incidents=1, max_incident_impact="major",
            incident_updated_at=NOW + timedelta(days=400), active_maintenances=0), "stale_update"),
])
def test_board_provider_reasons(provider, expected):
    assert signal_history.board_provider(provider, NOW) == expected


@pytest.mark.parametrize("indicator", ["minor", "major", "critical", "maintenance"])
@pytest.mark.parametrize("age", [timedelta(minutes=5), timedelta(days=3), timedelta(days=9), None])
@pytest.mark.parametrize("basis", [dict(), dict(active_incidents=0, active_maintenances=0),
                                   dict(active_incidents=2, max_incident_impact="minor", incident_updated_at=NOW - timedelta(days=20),
                                        active_maintenances=0)])
def test_a_board_line_always_implies_a_lookup_wait(indicator, age, basis):
    """The board is never louder than the lookup answer: both use triage.provider_wait_reason."""
    provider = status(indicator, updated=NOW - age if age else None, **basis)
    if signal_history.board_provider(provider, NOW) is None:
        assert triage.provider_wait_reason(provider, NOW) is not None
        decision = triage.recommendation(None, provider, None, None, now=NOW)
        assert decision["action"] == "wait_provider_incident"


# Switch off: nothing changes ----------------------------------------------------------------------------------------

def test_switch_off_by_default_and_the_paths_do_not_exist(db, monkeypatch):
    for name in ("AHN_PUBLIC_BOARD", "AHN_PUBLIC_BOARD_SINCE", "AHN_PUBLIC_BOARD_AHN_CHECK"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AHN_PUBLIC_BOARD_AHN_CHECK", "1")  # ignored while the board itself is off
    from_env = Settings.from_env()
    assert not from_env.public_board and from_env.public_board_since is None and not from_env.public_board_ahn_check
    live_shapes(db, datetime.now(timezone.utc))
    with TestClient(create_app(settings())) as client:
        for path in ("/v1/now", "/en/now", "/ko/now"):
            assert client.get(path).status_code == 404, path
        assert "Site problems" not in client.get("/llms.txt").text
        assert "/now<" not in client.get("/sitemap.xml").text
        for lang in ("en", "ko"):
            assert str(escape(public_board.POLICY_BEFORE[lang])) in client.get(f"/{lang}/policy").text
        assert "/now" not in client.get("/en/").text
        for lang in ("en", "ko"):  # no entrance links to a page that would 404
            assert f'href="/{lang}/now"' not in client.get(f"/{lang}/").text
            assert f'href="/{lang}/now"' not in client.get(f"/{lang}/search").text


def test_entrance_links_show_only_while_the_board_is_on(db):
    with TestClient(create_app(settings(**ON))) as client:
        for lang, nav, home in (("en", "Site status", "Sites with a problem right now →"), ("ko", "지금 문제", "지금 문제가 있는 사이트 →")):
            link = f'href="/{lang}/now"'
            page = client.get(f"/{lang}/").text
            assert f'<a {link} >{nav}</a>' in page and f'<a {link}>{home}</a>' in page
            assert page.index(f'href="/{lang}/search"') < page.index(f'<a {link} >') < page.index(f'href="/{lang}/report"')
            assert f'<a {link} aria-current="page">{nav}</a>' in client.get(f"/{lang}/now").text
            assert link in client.get(f"/{lang}/search").text


def test_switch_needs_its_policy_date(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("AHN_PUBLIC_BOARD", "1")
    monkeypatch.delenv("AHN_PUBLIC_BOARD_SINCE", raising=False)
    with pytest.raises(RuntimeError, match="AHN_PUBLIC_BOARD_SINCE"):
        Settings.from_env()
    monkeypatch.setenv("AHN_PUBLIC_BOARD_SINCE", SINCE)
    on = Settings.from_env()
    assert on.public_board and on.public_board_since.isoformat() == SINCE and not on.public_board_ahn_check


def test_the_lookup_answer_is_the_same_with_the_switch_on_or_off(db):
    live_shapes(db, datetime.now(timezone.utc))
    with TestClient(create_app(settings())) as off, TestClient(create_app(settings(**ON, public_board_ahn_check=True))) as on:
        for host in (FRESH, *STALE_HOSTS):
            before, after = lookup(off, host), lookup(on, host)
            assert before == after, host
        # The board switch narrows the board only. The lookup still waits on gocardless.com (an incident.io page time,
        # which D54 does not judge) and waits, less sure, on mediavine.com (no update time, D54).
        assert lookup(off, "gocardless.com")["recommendation"] == {"action": "wait_provider_incident",
                                                                   "confidence": "medium", "reasons": ["provider_scope"]}
        assert lookup(off, "mediavine.com")["recommendation"]["confidence"] == "low"


# Switch on ----------------------------------------------------------------------------------------------------------

def test_live_false_problems_are_left_out_and_a_fresh_major_stays(db):
    now = datetime.now(timezone.utc)
    live_shapes(db, now)
    summary = board(db, now)
    assert [c["site_host"] for c in summary["candidates"]] == [FRESH]
    assert summary["provider_excluded_now"] == {"stale_incidents": 0, "stale_update": 2, "no_update_time": 1,
                                                "not_a_wait": 0, "feed_not_shown": 0, "window_blind": 0}
    with TestClient(create_app(settings(**ON))) as client:
        body = client.get("/v1/now").json()
        assert body["schema"] == "ahn-now/1"
        assert [line["site_host"] for line in body["problems"]] == [FRESH] and body["maintenance"] == []
        assert body["problems"][0]["provider"]["indicator"] == "major"
        assert body["stats_7d"]["provider_left_out_now"]["stale_update"] == 2
        assert lookup(client, FRESH)["recommendation"]["action"] == "wait_provider_incident"


def test_a_feed_its_robots_txt_hides_is_not_listed(db, monkeypatch):
    now = datetime.now(timezone.utc)
    live_shapes(db, now)
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "disallowed")
    summary = board(db, now)
    assert summary["candidates"] == [] and summary["provider_excluded_now"]["feed_not_shown"] == 4


def test_ahn_check_column_is_hidden_unless_its_own_switch_is_on(db):
    now = datetime.now(timezone.utc)
    live_shapes(db, now)
    host = next(h for h in sorted(signal_history.board_hosts())
                if h not in signal_history.INFRA_HOSTS and h not in (FRESH, *STALE_HOSTS) and len(h.split(".")) == 2)
    with connect(db) as conn:
        for at, outcome in ((now - timedelta(days=3), "responding"), (now - timedelta(minutes=90), "no_response:connect"),
                            (now - timedelta(minutes=30), "no_response:connect")):
            conn.execute(
                """INSERT INTO robots_read_events (site_host,read_at,read_host,robots_status,detail,a_outcome,origin_operator,vantage)
                   VALUES (%s,%s,%s,%s,NULL,%s,'agenthelpnetwork.com','{}'::jsonb)""",
                (host, at, host, "ok" if outcome == "responding" else "error", outcome))
        signal_history.rollup(conn, now=now, thresholds=signal_history.STAGES["1"])
    assert host in {c["site_host"] for c in board(db, now)["candidates"]}  # the owner view has it
    with TestClient(create_app(settings(**ON))) as client:
        raw = client.get("/v1/now").text
        assert host not in raw and "ahn_check" not in raw and "a_likely_false_positive" not in raw
        for lang in ("en", "ko"):
            page = client.get(f"/{lang}/now").text
            assert host not in page and "robots.txt" not in page.split("<main", 1)[1].split("</main>")[0]
    with TestClient(create_app(settings(**ON, public_board_ahn_check=True))) as client:
        body = client.get("/v1/now").json()
        line = next(line for line in body["problems"] if line["site_host"] == host)
        assert line["ahn_check"]["result"] == "no_response" and "provider" not in line
        assert "a_likely_false_positive" in body["stats_7d"] and "checks_paused" in body
        assert "robots.txt 판독 응답 없음" in client.get("/ko/now").text


def test_refused_reads_never_reach_the_public_board(db):
    now = datetime.now(timezone.utc)
    host = next(h for h in sorted(signal_history.board_hosts()) if h not in signal_history.INFRA_HOSTS and len(h.split(".")) == 2)
    with connect(db) as conn:
        for minutes in (200, 120, 60, 10):
            conn.execute(
                """INSERT INTO robots_read_events (site_host,read_at,read_host,robots_status,detail,a_outcome,origin_operator,vantage)
                   VALUES (%s,%s,%s,'refused','http_403','refused','agenthelpnetwork.com','{}'::jsonb)""",
                (host, now - timedelta(minutes=minutes), host))
        signal_history.rollup(conn, now=now, thresholds=signal_history.STAGES["1"])
    with TestClient(create_app(settings(**ON, public_board_ahn_check=True))) as client:
        body = client.get("/v1/now").json()
        raw = client.get("/v1/now").text + client.get("/en/now").text + client.get("/ko/now").text
        assert host not in raw and body["problems"] == [] and body["stats_7d"]["a_episodes"] == 0


def test_pages_in_both_languages_quote_outage_only_from_the_provider(db):
    live_shapes(db, datetime.now(timezone.utc))
    with TestClient(create_app(settings(**ON))) as client:
        en, ko = client.get("/en/now"), client.get("/ko/now")
        assert en.status_code == ko.status_code == 200
        assert "<h1>Site problems right now</h1>" in en.text and "<h1>지금 문제가 있는 사이트</h1>" in ko.text
        assert "Provider reports a problem (its status indicator “major”)" in en.text
        assert "업체 발표: 문제 있음 (업체 상태 값 “major”)" in ko.text
        for page in (en.text, ko.text):
            assert FRESH in page and not any(host in page for host in STALE_HOSTS)
            main = page.split("<main", 1)[1].split("</main>")[0]
            # The provider's own value is quoted as it is; "outage"/"장애" is never our word for it.
            assert "outage" not in main.lower() and "장애" not in main
        assert en.headers["cache-control"] == "public, max-age=60"


def test_empty_board_says_absence_is_not_health(db):
    with TestClient(create_app(settings(**ON))) as client:
        assert "A site missing from this list is not known to be working." in client.get("/en/now").text
        assert "이 목록에 없다고 사이트가 정상이라는 뜻은 아니에요." in client.get("/ko/now").text
        body = client.get("/v1/now").json()
        assert body["problems"] == [] and body["note"] == public_board.NOT_LISTED_NOTE


def test_json_etag_and_cache(db):
    live_shapes(db, datetime.now(timezone.utc))
    with TestClient(create_app(settings(**ON))) as client:
        first = client.get("/v1/now")
        assert first.headers["cache-control"] == "public, max-age=60" and first.headers["etag"]
        again = client.get("/v1/now", headers={"If-None-Match": first.headers["etag"]})
        assert again.status_code == 304 and again.content == b""


def test_policy_llms_and_sitemap_describe_the_board_only_while_on(db):
    with TestClient(create_app(settings(**ON))) as client:
        for lang in ("en", "ko"):
            text = client.get(f"/{lang}/policy").text
            old, new = public_board.policy_sentences(False)[lang]
            assert str(escape(old)) not in text and str(escape(new)) in text
        shown = max(POLICY_UPDATED, ON["public_board_since"])  # the board date moves "Last updated" only when it is later
        assert f"Last updated {shown:%B} {shown.day}, {shown.year}" in client.get("/en/policy").text
        llms = client.get("/llms.txt").text
        assert "## Site problems now" in llms and "/v1/now" in llms and "hosts AHN could not connect to" not in llms
        assert "https://agenthelpnetwork.com/ko/now" in client.get("/sitemap.xml").text
    with TestClient(create_app(settings(**ON, public_board_ahn_check=True))) as client:
        assert str(escape(public_board.policy_sentences(True)["en"][1])) in client.get("/en/policy").text
        assert "hosts AHN could not connect to" in client.get("/llms.txt").text


def test_the_time_shown_is_the_time_judged(db):
    """A page updated 30 days ago with an incident updated an hour ago is listed on the incident time, and shows it."""
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        put_status(conn, FRESH, "major", updated=now - timedelta(days=30), incidents=1, impact="major",
                   incident_updated=now - timedelta(hours=1), maintenances=0)
    with TestClient(create_app(settings(**ON))) as client:
        provider = client.get("/v1/now").json()["problems"][0]["provider"]
        assert provider["updated_at"] == signal_history._iso(now - timedelta(hours=1))
        assert provider["source_updated_at"] == signal_history._iso(now - timedelta(days=30))
        page = client.get("/en/now").text
        assert f"Updated by the provider <time datetime=\"{provider['updated_at']}\">" in page


def test_a_page_raised_by_maintenance_only_is_listed_as_maintenance(db):
    """The cloudflare shape with a fresh time: page "major" from maintenances only (D43 basis) is not a problem line."""
    now = datetime.now(timezone.utc)
    with connect(db) as conn:
        put_status(conn, "cloudflare.com", "major", updated=now - timedelta(minutes=10), incidents=0, impact="none",
                   maintenances=2, affected=["Nairobi, Kenya - (NBO)"])
    with TestClient(create_app(settings(**ON))) as client:
        body = client.get("/v1/now").json()
        assert body["problems"] == [] and [line["site_host"] for line in body["maintenance"]] == ["cloudflare.com"]
        assert body["maintenance"][0]["provider"]["basis"] == "maintenance"
        page = client.get("/en/now").text
        assert "No problem is confirmed right now" in page and "scheduled maintenance in progress" in page


def _board_shape(candidates, provider_candidates, truncated):
    return {"candidates": candidates, "provider_candidates": provider_candidates, "truncated": truncated, "days": 7,
            "coverage": {"registered_feeds_curated": 1, "target_hosts": 1}, "p_episodes": 0, "held_now": False,
            "provider_excluded_now": dict.fromkeys(signal_history.PROVIDER_EXCLUSIONS, 0),
            "a_episodes": 0, "a_matched_feed": 0, "a_ahead_of_feed": 0, "a_likely_false_positive": 0}


def test_ahn_check_only_candidates_do_not_cut_or_flag_the_provider_column():
    """The owner list is cut at CANDIDATE_LIST_MAX with AHN-check-only lines ahead of maintenance lines. With the
    AHN-check column off, the public board reads the uncut provider lines and says nothing about the others."""
    checks = [{"site_host": f"h{i}.example", "provider": None, "ahn_check": {"kind": "no_response"}}
              for i in range(signal_history.CANDIDATE_LIST_MAX)]
    maint = {"site_host": "m.example", "ahn_check": None,
             "provider": {"indicator": "maintenance", "source_name": "M", "source_url": "https://status.m.example/"}}
    body = public_board.snapshot(_board_shape(checks, [maint], True), ahn_check=False, now=NOW)
    assert body["maintenance"][0]["site_host"] == "m.example" and body["problems"] == []
    assert body["truncated"] is False


def test_the_board_needs_the_public_lookup(db):
    """With the public lookup off a lookup answer carries no provider status, so the board would say more than it."""
    with TestClient(create_app(Settings(TEST_URL, PEPPER, public_mode=False, **ON))) as client:
        assert client.get("/v1/now").status_code == 404 and client.get("/en/now").status_code == 404


def test_the_owner_view_names_the_left_out_hosts_and_the_public_board_does_not(db):
    now = datetime.now(timezone.utc)
    live_shapes(db, now)
    left_out = {item["site_host"]: item for item in board(db, now)["provider_excluded_hosts"]}
    assert set(left_out) == set(STALE_HOSTS)
    assert left_out["mediavine.com"]["reason"] == "no_update_time" and left_out["mediavine.com"]["lookup_waits"] is True
    # D54 does not judge gocardless.com's page time (incident.io): the lookup still waits, the board reason is unchanged.
    assert left_out["gocardless.com"]["reason"] == "stale_update" and left_out["gocardless.com"]["lookup_waits"] is True
    with TestClient(create_app(settings(**ON))) as client:
        raw = client.get("/v1/now").text
        assert "provider_excluded_hosts" not in raw and not any(host in raw for host in STALE_HOSTS)


def test_the_lookup_answers_the_left_out_minor_pages_as_the_board_judged_them(db):
    """D54, from stored rows: kagi.com (Instatus, minor, its active items last updated 16 days ago) no longer makes the
    lookup wait; gocardless.com (incident.io, minor, page time 16 days old) still waits exactly as before, because an
    incident.io page time does not follow its incidents; mediavine.com (Instatus, minor, no update time) still waits,
    with low confidence. cloudflare.com (maintenances in progress) and the fresh major incident are unchanged."""
    now = datetime.now(timezone.utc)
    live_shapes(db, now)
    with connect(db) as conn:
        put_status(conn, "kagi.com", "minor", updated=now - timedelta(days=16))
    with TestClient(create_app(settings())) as client:
        released = lookup(client, "kagi.com")
        assert released["provider_status"]["source_time"] == "active_items"
        assert released["recommendation"]["action"] != "wait_provider_incident"
        checks = {item["code"]: item["text"] for item in released["next_checks"]}
        assert "provider_scope" not in checks and checks["provider_incident_stale"] == triage.PAGE_STALE_CHECK
        page_time = lookup(client, "gocardless.com")
        assert "source_time" not in page_time["provider_status"]
        assert page_time["recommendation"] == {"action": "wait_provider_incident", "confidence": "medium",
                                               "reasons": ["provider_scope"]}
        assert [item["code"] for item in page_time["next_checks"] if item["code"].startswith("provider_")] == ["provider_scope"]
        unknown = lookup(client, "mediavine.com")
        assert unknown["provider_status"]["source_time"] == "active_items"
        assert unknown["recommendation"]["action"] == "wait_provider_incident"
        assert unknown["recommendation"]["confidence"] == "low"
        assert unknown["recommendation"]["reasons"] == ["provider_scope", "provider_update_time_unknown"]
        assert "provider_update_time_unknown" in [item["code"] for item in unknown["next_checks"]]
        assert lookup(client, "cloudflare.com")["recommendation"]["action"] == "wait_provider_incident"
        assert lookup(client, FRESH)["recommendation"] == {"action": "wait_provider_incident", "confidence": "medium",
                                                           "reasons": ["provider_scope"]}
    left_out = {item["site_host"]: item for item in board(db, now)["provider_excluded_hosts"]}
    assert left_out["kagi.com"] == {"site_host": "kagi.com", "reason": "stale_update", "lookup_waits": False}
    # Left out of the board, all three: a board line still implies a lookup wait (D53), never the other way round.
    assert left_out["gocardless.com"]["lookup_waits"] is True and left_out["mediavine.com"]["lookup_waits"] is True


# D57: an open incident read from a page's Atom history ------------------------------------------------------------

def test_d57_an_open_incident_from_the_atom_history_is_listed_without_a_level_and_the_lookup_waits(db, monkeypatch):
    """brevo.com and sendinblue.com share one page (the same incident on both hosts, as for any shared page);
    github.com's history lists two open incidents. Shown as the lookup shows them: API disallowed, Atom allowed."""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "disallowed")
    monkeypatch.setattr(feed_robots, "history_allowed", lambda _conn, _api_url: True)
    with connect(db) as conn:
        put_atom(conn, "github.com", incidents=2, incident_updated=now - timedelta(minutes=30))
        for host in ("brevo.com", "sendinblue.com"):
            put_atom(conn, host, incidents=1, incident_updated=now - timedelta(hours=3))
    summary = board(db, now)
    assert [c["site_host"] for c in summary["provider_candidates"]] == ["github.com", "brevo.com", "sendinblue.com"]  # newest update first
    with TestClient(create_app(settings(**ON))) as client:
        body = client.get("/v1/now").json()
        assert body["maintenance"] == []
        line = next(item for item in body["problems"] if item["site_host"] == "github.com")
        provider = line["provider"]
        assert "indicator" in provider and provider["indicator"] is None  # said, not left to guess
        assert provider["source_kind"] == "history_atom" and provider["active_incidents"] == 2
        assert provider["updated_at"] == signal_history._iso(now - timedelta(minutes=30))
        assert provider["source_name"] == "GitHub Status" and "basis" not in provider and "source_updated_at" not in provider
        for host in ("github.com", "brevo.com", "sendinblue.com"):
            answer = lookup(client, host)
            assert answer["recommendation"] == {"action": "wait_provider_incident", "confidence": "low",
                                                "reasons": ["provider_scope", "provider_history_incident"]}, host
        en, ko = client.get("/en/now").text, client.get("/ko/now").text
        assert "Provider announced an open incident (its incident history; no severity level given)" in en
        assert "업체 발표: 진행 중 사건 있음(사건 이력 피드, 등급 정보 없음)" in ko
        assert "“None”" not in en and "“None”" not in ko


def test_d57_atom_readings_the_lookup_does_not_wait_on_are_left_out_by_reason(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "disallowed")
    monkeypatch.setattr(feed_robots, "history_allowed", lambda _conn, _api_url: True)
    with connect(db) as conn:
        put_atom(conn, "github.com", incidents=1, incident_updated=now - timedelta(days=8))           # older than the week
        put_atom(conn, "atlassian.com", incidents=1, incident_updated=None)                          # no time
        put_atom(conn, "discord.com", incidents=1, incident_updated=now - timedelta(minutes=5), blind=True)
        put_atom(conn, "dropbox.com", incidents=1, incident_updated=now + timedelta(hours=3))         # far ahead: no time
        put_atom(conn, "figma.com", incidents=0, incident_updated=None)                              # nothing open: unknown
    summary = board(db, now)
    assert summary["provider_candidates"] == []
    assert summary["provider_excluded_now"] == {"stale_incidents": 1, "stale_update": 0, "no_update_time": 2,
                                                "not_a_wait": 0, "feed_not_shown": 0, "window_blind": 1}
    left_out = {item["site_host"]: item for item in summary["provider_excluded_hosts"]}
    assert set(left_out) == {"github.com", "atlassian.com", "discord.com", "dropbox.com"}  # figma.com: not a statement
    assert not any(item["lookup_waits"] for item in left_out.values())
    with TestClient(create_app(settings(**ON))) as client:
        assert client.get("/v1/now").json()["problems"] == []
        for host in ("github.com", "atlassian.com", "discord.com", "dropbox.com", "figma.com"):
            answer = lookup(client, host)
            assert answer["provider_status"]["source_kind"] == "history_atom", host
            assert answer["recommendation"]["action"] != "wait_provider_incident", host
            assert "provider_unknown" in [c["code"] for c in answer["next_checks"]], host


def test_d57_an_atom_reading_the_lookup_hides_is_not_listed(db, monkeypatch):
    """If the Atom history itself is no longer allowed, the lookup shows no reading; the board does not either."""
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "disallowed")
    monkeypatch.setattr(feed_robots, "history_allowed", lambda _conn, _api_url: False)
    with connect(db) as conn:
        put_atom(conn, "github.com", incidents=1, incident_updated=now - timedelta(minutes=10))
    summary = board(db, now)
    assert summary["provider_candidates"] == [] and summary["provider_excluded_now"]["feed_not_shown"] == 1
    with TestClient(create_app(settings(**ON))) as client:
        answer = lookup(client, "github.com")
        assert not answer.get("provider_status") and answer["recommendation"]["action"] != "wait_provider_incident"


def test_d57_a_stale_atom_read_is_not_listed(db, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "disallowed")
    monkeypatch.setattr(feed_robots, "history_allowed", lambda _conn, _api_url: True)
    with connect(db) as conn:
        put_atom(conn, "github.com", incidents=1, incident_updated=now - timedelta(hours=3), fetched=now - timedelta(hours=3))
    assert board(db, now)["provider_candidates"] == []
