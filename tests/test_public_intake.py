"""Isolated local database tests; never submit these invented attempts to production."""

from __future__ import annotations

import os
import base64
import asyncio
from itertools import count
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from ahn.db import connect, migrate
from ahn.auto_review import review_batch
from ahn.bandwidth_guard import parse_bandwidth, run_guard
from ahn.main import create_app
from ahn.models import CheckResponse, ObservationIn
from ahn.service import PUBLIC_REPORTER_ID
from ahn.settings import Settings
from ahn.status_refresh import Source, refresh_batch, refresh_one, parse_google_gmail


TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
CLIENT_NUMBER = count(1)
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)


@pytest.fixture
def public_client():
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True, collect_lookup_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, request_metrics, traffic_attribution_daily, traffic_client_daily, mcp_check_metrics, render_metric_samples, public_intake_daily, public_intake_host_daily, bandwidth_guard_samples, bandwidth_guard_state, status_cache, status_sources, queried_hosts, host_access_profile RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name='public_intake'")
        conn.execute("UPDATE service_flags SET enabled=true WHERE name='public_publication'")
    with TestClient(create_app(settings)) as client:
        yield settings, client


def sample(**changes):
    data = {
        "schema_version": 1,
        "site_host": "example.org",
        "task_step": "site_open",
        "error_class": "site_access",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "agent_family": "other",
        "attempt_actor": "human",
        "attempt_env": "human_own_browser",
        "outcome": "error",
        "challenge_vendor": "unknown",
        "challenge_kind": "unknown",
        "synthetic": False,
        "idempotency_key": str(uuid4()),
        "consent_confirmed": True,
    }
    data.update(changes)
    return data


def post_public(client, data):
    # The edge-set header is the trusted limiter key; X-Forwarded-For is only a fallback.
    return client.post("/v1/observations", json=data, headers={"CF-Connecting-IP": f"198.51.100.{next(CLIENT_NUMBER)}"})


def test_public_contract_and_full_prompt(public_client):
    _, client = public_client
    contract = client.get("/openapi.json").json()
    assert "/v1/observations" in contract["paths"]
    assert "security" not in contract["paths"]["/v1/observations"]["post"]
    assert "/v1/observations/{observation_id}" in contract["paths"]
    guide = client.get("/agent-instructions.txt")
    assert guide.status_code == 200 and "text/plain" in guide.headers["content-type"]
    assert "follow your own user and platform authorization rules" in guide.text
    assert "follow your own user and platform authorization rules" in client.get("/en/human").text
    assert 'id="report-form"' in client.get("/en/report").text
    assert 'id="report-form"' in client.get("/ko/report").text


def test_minimal_firsthand_report_enters_private_review(public_client):
    _, client = public_client
    contract = client.get("/openapi.json").json()
    assert set(contract["components"]["schemas"]["ObservationIn"]["required"]) == {"site_host", "error_class", "outcome"}
    response = post_public(client, {"site_host": "example.org", "error_class": "site_access", "outcome": "blocked"})
    assert response.status_code == 201, response.text
    assert response.json()["review_state"] == "pending"
    assert len(response.json()["withdrawal_token"]) == 64
    assert client.get("/v1/incidents/check", params={"site_host": "example.org"}).json()["summary_code"].startswith("NO_MATCHING_REPORTS")


def test_lookup_links_to_a_host_prefilled_report_without_submitting(public_client):
    _, client = public_client
    page = client.get("/en/search", params={"site_host": "example.org"})
    assert page.status_code == 200
    assert '/en/report?site_host=example.org' in page.text
    assert "Looking up a hostname alone is not an attempt" in page.text
    report = client.get("/en/report", params={"site_host": "example.org"})
    assert 'name="site_host" value="example.org"' in report.text
    assert 'name="attempt_source" required' in report.text
    assert 'name="outcome" required' in report.text
    assert 'name="error_class" required' in report.text
    korean = client.get("/ko/search", params={"site_host": "example.org"})
    assert '/ko/report?site_host=example.org' in korean.text


def test_a_capitalised_or_pasted_hostname_is_looked_up_as_the_host(public_client):
    _, client = public_client
    # iPhone keyboards capitalise the first letter; people paste https:// and a trailing slash.
    for typed in ("Example.org", " EXAMPLE.ORG ", "https://example.org/"):
        api = client.get("/v1/incidents/check", params={"site_host": typed})
        assert api.status_code == 200 and api.json()["site_host"] == "example.org", typed
        page = client.get("/ko/search", params={"site_host": typed})
        assert page.status_code == 200 and '/ko/report?site_host=example.org' in page.text, typed
    assert client.get("/v1/incidents/check", params={"site_host": "https://example.org/path"}).status_code == 400


def test_minimal_report_retry_with_stable_key_is_idempotent(public_client):
    _, client = public_client
    report = {"site_host": "example.org", "error_class": "site_access", "outcome": "blocked", "idempotency_key": str(uuid4())}
    first = post_public(client, report)
    second = post_public(client, report)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["observation_id"] == first.json()["observation_id"]


def test_mcp_minimal_retry_omits_generated_defaults(public_client, monkeypatch):
    _, client = public_client
    from ahn import mcp_adapter

    async def local_api(_method, _path, *, payload, write):
        assert write is True
        assert "observed_at" not in payload
        response = post_public(client, payload)
        assert response.status_code in {200, 201}, response.text
        return response.json()

    monkeypatch.setattr(mcp_adapter, "call_api", local_api)
    raw = {"site_host": "example.org", "error_class": "site_access", "outcome": "blocked", "idempotency_key": str(uuid4())}
    first = asyncio.run(mcp_adapter.report_observation(ObservationIn.model_validate(raw)))
    second = asyncio.run(mcp_adapter.report_observation(ObservationIn.model_validate(raw)))
    assert first.observation_id == second.observation_id


def test_withdrawal_token_still_works_when_intake_disabled(public_client):
    settings, client = public_client
    receipt = post_public(client, sample()).json()
    paused_settings = Settings(TEST_URL, settings.key_pepper, public_mode=True, reporting_enabled=False)
    with TestClient(create_app(paused_settings)) as paused:
        response = paused.delete(
            f"/v1/observations/{receipt['observation_id']}",
            headers={"X-Withdrawal-Token": receipt["withdrawal_token"]},
        )
        assert response.status_code == 200 and response.json() == {"withdrawn": True}


def test_actual_body_size_is_limited(public_client):
    _, client = public_client
    response = client.post(
        "/v1/observations", content=b"{" + b" " * 5000 + b"}",
        headers={"Content-Type": "application/json", "Content-Length": "1"},
    )
    assert response.status_code == 413


def test_on_demand_provider_status_and_bounded_lookup_activity(public_client, monkeypatch):
    settings, client = public_client
    monkeypatch.setattr("ahn.service.discover_source", lambda _host: None)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "page": {"updated_at": "2026-10-03T18:00:00Z"},
        "status": {"indicator": "none", "description": "All Systems Operational"},
    }))
    with httpx.Client(transport=transport) as remote:
        monkeypatch.setattr("ahn.service.refresh_one", lambda s, host: refresh_one(s, host, client=remote))
        for attempt in range(5):
            response = client.get("/v1/incidents/check", params={"site_host": "github.com"})
            assert response.status_code == 200
            assert response.json()["lookup_activity"]["volume_band"] == ("1-4" if attempt < 4 else "5-9")
        after = response.json()
        assert after["provider_status"]["indicator"] == "none"
        assert after["provider_status"]["freshness"] == "fresh"
        assert after["provider_source_state"] == "feed_connected"
        assert after["lookup_activity"]["volume_band"] == "5-9"
        assert after["current_site_status"] == "unknown"
        assert [item["code"] for item in after["next_checks"]] == ["locate_failure_stage", "official_path_available", "provider_clear", "reports_unknown"]
        assert after["official_paths"]["api_docs"].startswith("https://docs.github.com/")
        assert after["access_profile"] is None
        assert after["recommendation"] == {"action": "use_official_path", "confidence": "medium", "reasons": ["official_path_available"]}
        provider_page = client.get("/en/search", params={"site_host": "github.com"}).text
        assert "GitHub Status" in provider_page
        assert "Open the provider’s official status page" in provider_page
        assert "The page-level indicator may cover services unrelated to your task" in provider_page
        with connect(settings) as conn:
            assert conn.execute("SELECT count(*) AS n FROM queried_hosts WHERE site_host='github.com'").fetchone()["n"] == 1
            assert conn.execute("SELECT count(*) AS n FROM status_sources").fetchone()["n"] == 1
            conn.execute("UPDATE status_cache SET fetched_at=now()-interval '2 hours' WHERE site_host='github.com'")
        assert refresh_batch(settings, client=remote) == {"checked": 1, "updated": 1, "failed": 0}
    for attempt in range(5):
        unmapped = client.get("/v1/incidents/check", params={"site_host": "example.org"}).json()
        assert unmapped["provider_status"] is None
        assert unmapped["provider_source_state"] == "not_verified_by_current_methods"
        assert unmapped["provider_discovery_attempted_at"] is not None
        assert unmapped["lookup_activity"]["volume_band"] == ("1-4" if attempt < 4 else "5-9")
    for _ in range(5):
        unmapped = client.get("/v1/incidents/check", params={"site_host": "example.org"}).json()
    assert unmapped["lookup_activity"]["volume_band"] == "10-49"
    first_page = client.get("/en/search", params={"site_host": "newexample.net"})
    assert first_page.status_code == 200
    assert "1-4 requests" in first_page.text
    assert "Provider API availability: unconfirmed" in first_page.text
    assert "Our status feed: not connected" in first_page.text
    assert "Last automatic discovery attempt:" in first_page.text
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM queried_hosts WHERE site_host='newexample.net'").fetchone()["n"] == 1
    netflix = client.get("/v1/incidents/check", params={"site_host": "netflix.com"}).json()
    assert netflix["provider_status"] is None
    assert netflix["provider_source_state"] == "official_page_only"
    assert netflix["official_status_page"]["source_url"] == "https://help.netflix.com/en/is-netflix-down"
    korean = client.get("/ko/search", params={"site_host": "netflix.com"}).text
    assert "공식 상태 페이지:" in korean
    assert "업체 API 제공 여부: 미확인" in korean
    assert "우리 상태 피드: 미연결" in korean
    webex = client.get("/v1/incidents/check", params={"site_host": "webex.com"}).json()
    assert webex["provider_source_state"] == "official_page_only"
    assert webex["official_status_page"]["source_url"] == "https://status.webex.com/"
    for _ in range(10):
        sensitive = client.get("/v1/incidents/check", params={"site_host": "account123.example.org"}).json()
    assert sensitive["lookup_activity"] is None
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM lookup_metrics WHERE site_host='account123.example.org'").fetchone()["n"] == 0


def test_access_profile_layer_answers_without_reports(public_client, monkeypatch):
    from ahn.access_profile import get_profile

    settings, client = public_client
    monkeypatch.setattr("ahn.service.discover_source", lambda _host: None)
    robots = b"User-agent: GPTBot\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\nSitemap: https://newsite.org/sitemap.xml\n"
    reads = []
    def reader(host, path, *, cap_bytes, **_kw):
        reads.append((host, path))
        return ("ok", robots) if path == "/robots.txt" else ("absent", None)
    monkeypatch.setattr("ahn.service.get_profile", lambda s, host, **kw: get_profile(s, host, reader=reader))
    first = client.get("/v1/incidents/check", params={"site_host": "newsite.org"}).json()
    profile = first["access_profile"]
    assert profile["robots_status"] == "ok" and profile["freshness"] == "fresh" and profile["llms_txt_status"] == "absent"
    assert profile["robots"]["agents_blocked"] == ["ChatGPT-User", "GPTBot"] and profile["robots"]["sitemap"] is True
    assert profile["robots"]["wildcard_root_allowed"] is True
    assert "not an access guarantee" in profile["note"]
    assert first["recommendation"] == {"action": "stop_declared_block", "confidence": "medium", "reasons": ["robots_blocks_agents"]}
    codes = [item["code"] for item in first["next_checks"]]
    assert codes[:2] == ["locate_failure_stage", "robots_blocks_agents"] and "reports_unknown" in codes
    assert first["evidence_level"] == "no_reports"
    # Second lookup within a day reuses the cache: no new reads.
    second = client.get("/v1/incidents/check", params={"site_host": "newsite.org", "error_class": "human_verification"}).json()
    assert reads == [("newsite.org", "/robots.txt"), ("newsite.org", "/llms.txt")]
    assert second["recommendation"]["action"] == "hand_off_to_human"
    assert CheckResponse(**second).access_profile.robots.rules[0].agent == "ChatGPT-User"
    page = client.get("/en/search", params={"site_host": "newsite.org"})
    assert page.status_code == 200
    # Account-style subdomains never trigger a read.
    sensitive = client.get("/v1/incidents/check", params={"site_host": "user123.newsite.org"}).json()
    assert sensitive["access_profile"] is None and len(reads) == 2


def test_api_names_the_host_that_answered_after_the_www_fallback(public_client, monkeypatch):
    from ahn.access_profile import get_profile

    settings, client = public_client
    def reader(site_host, path, *, cap_bytes, **_kw):
        if site_host == "fallback.org":
            return ("refused", None, "http_503")
        return ("ok", b"User-agent: *\nDisallow:\n", "http_200") if path == "/robots.txt" else ("absent", None, "http_404")
    monkeypatch.setattr("ahn.service.get_profile", lambda s, host, **kw: get_profile(s, host, reader=reader))
    first = client.get("/v1/incidents/check", params={"site_host": "fallback.org"}).json()
    profile = first["access_profile"]
    assert profile["robots_status"] == "ok" and profile["read_host"] == "www.fallback.org"
    assert profile["source_urls"] == {"robots_txt": "https://www.fallback.org/robots.txt", "llms_txt": "https://www.fallback.org/llms.txt"}
    assert CheckResponse(**first).access_profile.read_host == "www.fallback.org"
    # The cached row answers the same way, and the page links to the file that was actually read.
    again = client.get("/v1/incidents/check", params={"site_host": "fallback.org"}).json()["access_profile"]
    assert again["read_host"] == "www.fallback.org" and again["freshness"] == "fresh"
    assert "https://www.fallback.org/robots.txt" in client.get("/en/search", params={"site_host": "fallback.org"}).text
    with connect(settings) as conn:
        assert conn.execute("SELECT read_host FROM host_access_profile WHERE site_host='fallback.org'").fetchone()["read_host"] == "www.fallback.org"


def test_www_lookup_answers_from_the_apex_while_reports_keep_their_own_host(public_client, monkeypatch):
    from ahn.access_profile import get_profile

    settings, client = public_client
    monkeypatch.setattr("ahn.service.discover_source", lambda _host: None)
    official = {"name": "Alias Example", "api_docs": "https://developer.aliasexample.org/", "developer_portal": None, "note": None}
    # Exact-key stand-in for the curated table, so the lookup has to ask with the apex itself.
    monkeypatch.setattr("ahn.service.official_paths", lambda host: dict(official) if host == "aliasexample.org" else None)
    reads = []
    def reader(host, path, *, cap_bytes, **_kw):
        reads.append((host, path))
        return ("ok", b"User-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n", "http_200") if path == "/robots.txt" else ("absent", None, "http_404")
    monkeypatch.setattr("ahn.service.get_profile", lambda s, host, **kw: get_profile(s, host, reader=reader))
    params = {"error_class": "site_access"}
    www = client.get("/v1/incidents/check", params={"site_host": "www.aliasexample.org", **params}).json()
    apex = client.get("/v1/incidents/check", params={"site_host": "aliasexample.org", **params}).json()
    # The answer still names the host that was asked about.
    assert www["site_host"] == "www.aliasexample.org" and apex["site_host"] == "aliasexample.org"
    assert www["access_profile"] is not None and www["access_profile"] == apex["access_profile"]
    assert www["access_profile"]["read_host"] == "aliasexample.org" and www["access_profile"]["robots"]["agents_blocked"] == ["ChatGPT-User"]
    assert www["recommendation"] == apex["recommendation"] == {"action": "use_official_path", "confidence": "high",
                                                                "reasons": ["robots_blocks_agents", "official_path_available"]}
    for key in ("official_paths", "next_checks", "lookup_activity", "provider_source_state", "official_status_page"):
        assert www[key] == apex[key], key
    assert "profile_not_read" not in [item["code"] for item in www["next_checks"]]
    assert CheckResponse(**www).access_profile.read_host == "aliasexample.org"
    # One read of the apex serves both names; nothing is read, registered or counted under www.
    assert reads == [("aliasexample.org", "/robots.txt"), ("aliasexample.org", "/llms.txt")]
    with connect(settings) as conn:
        assert [r["site_host"] for r in conn.execute("SELECT site_host FROM host_access_profile").fetchall()] == ["aliasexample.org"]
        assert [r["site_host"] for r in conn.execute("SELECT site_host FROM queried_hosts").fetchall()] == ["aliasexample.org"]
        metrics = conn.execute("SELECT site_host, sum(checks) AS n FROM lookup_metrics GROUP BY site_host").fetchall()
    assert metrics == [{"site_host": "aliasexample.org", "n": 2}]
    # Any other subdomain, and www. in front of one, still reads nothing and says so.
    for host in ("user123.aliasexample.org", "www.user123.aliasexample.org"):
        other = client.get("/v1/incidents/check", params={"site_host": host}).json()
        assert other["access_profile"] is None and other["recommendation"]["reasons"][-1:] == ["profile_not_read"]
    assert len(reads) == 2
    # Reports stay under the exact host they were made for: a published www report is not the apex's report,
    # and its incident page keeps its own host while showing the site's declared policy.
    receipt = post_public(client, sample(site_host="www.aliasexample.org")).json()
    with connect(settings) as conn:  # the operator's manual publish (ahn.cli publish-observation)
        conn.execute("UPDATE observations SET review_state='published' WHERE id=%s", (receipt["observation_id"],))
        conn.execute("UPDATE incidents SET visibility='published' WHERE id=%s", (receipt["incident_id"],))
        assert conn.execute("SELECT site_host FROM incidents WHERE id=%s", (receipt["incident_id"],)).fetchone()["site_host"] == "www.aliasexample.org"
    assert client.get("/v1/incidents/check", params={"site_host": "www.aliasexample.org"}).json()["evidence_level"] == "single_observation"
    assert client.get("/v1/incidents/check", params={"site_host": "aliasexample.org"}).json()["evidence_level"] == "no_reports"
    detail = client.get(f"/v1/incidents/{receipt['incident_id']}").json()
    assert detail["site_host"] == "www.aliasexample.org" and detail["evidence_level"] == "single_observation"
    assert detail["access_profile"]["read_host"] == "aliasexample.org"
    assert [item["site_host"] for item in client.get("/v1/incidents", params={"site_host": "www.aliasexample.org"}).json()["incidents"]] == ["www.aliasexample.org"]
    assert client.get("/v1/incidents", params={"site_host": "aliasexample.org"}).json()["incidents"] == []


def test_profile_transport_errors_retry_hourly_but_refusals_and_answers_keep_the_day(public_client):
    from ahn.access_profile import get_profile

    settings, _ = public_client
    t0 = datetime(2026, 10, 18, 1, 0, tzinfo=timezone.utc)
    mode = {"fail": True}
    calls = []
    def reader(host, path, *, cap_bytes, **_kw):
        calls.append((host, path))
        if mode["fail"]:
            return ("error", None, "dns_timeout")
        return ("ok", b"User-agent: *\nAllow: /\n", "http_200") if path == "/robots.txt" else ("absent", None, "http_404")
    first = get_profile(settings, "retry.org", now=t0, reader=reader)
    assert first["robots_status"] == "error" and first["robots_detail"] == "dns_timeout" and first["freshness"] == "pending"
    assert calls == [("retry.org", "/robots.txt"), ("www.retry.org", "/robots.txt"), ("retry.org", "/llms.txt")]
    # Within the hour the failed attempt is cached; after an hour it is retried and succeeds.
    calls.clear()
    assert get_profile(settings, "retry.org", now=t0 + timedelta(minutes=30), reader=reader)["robots_status"] == "error"
    assert calls == []
    mode["fail"] = False
    recovered = get_profile(settings, "retry.org", now=t0 + timedelta(hours=1, minutes=1), reader=reader)
    assert recovered["robots_status"] == "ok" and recovered["freshness"] == "fresh" and recovered["robots_detail"] == "http_200"
    # A refusal is a definitive answer: cached for the full window, not retried hourly.
    calls = []
    refusing = lambda host, path, *, cap_bytes, **_kw: (calls.append(path), ("refused", None, "http_403"))[1]
    refused = get_profile(settings, "edge.org", now=t0, reader=refusing)
    assert refused["robots_status"] == "refused" and refused["robots_detail"] == "http_403" and refused["freshness"] == "fresh"
    get_profile(settings, "edge.org", now=t0 + timedelta(hours=5), reader=refusing)
    assert calls == ["/robots.txt", "/robots.txt", "/llms.txt"]  # apex robots, www fallback robots, llms — read once only
    with connect(settings) as conn:
        row = conn.execute("SELECT robots_status,robots_detail,llms_detail FROM host_access_profile WHERE site_host='edge.org'").fetchone()
    assert row == {"robots_status": "refused", "robots_detail": "http_403", "llms_detail": "http_403"}


def test_mcp_tool_execution_is_counted_separately_from_http_requests(public_client, monkeypatch):
    from ahn.tool_metrics import mcp_metric_token

    settings, client = public_client
    monkeypatch.setattr("ahn.service.discover_source", lambda _host: None)
    assert client.get("/v1/incidents/check", params={"site_host": "example.org"}).status_code == 200
    assert client.get("/v1/incidents/check", params={"site_host": "example.org"}, headers={"X-AHN-MCP-Call": "0" * 64}).status_code == 200
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM mcp_check_metrics").fetchone()["n"] == 0
    response = client.get("/v1/incidents/check", params={"site_host": "example.org"}, headers={"X-AHN-MCP-Call": mcp_metric_token(settings.key_pepper)})
    assert response.status_code == 200 and response.json()["evidence_level"] == "no_reports"
    with connect(settings) as conn:
        row = conn.execute("SELECT evidence_level,provider_signal,calls FROM mcp_check_metrics").fetchone()
    assert row == {"evidence_level": "no_reports", "provider_signal": "unavailable", "calls": 1}
    english = client.get("/en/search", params={"site_host": "example.org", "error_class": "human_verification"}).text
    korean = client.get("/ko/search", params={"site_host": "example.org", "error_class": "human_verification"}).text
    assert "Next checks for this task" in english and "normal handoff" in english
    assert "이 작업의 다음 점검" in korean and "사람 확인 단계라면" in korean


def test_failure_stage_guides_lookup_but_does_not_create_observation(public_client, monkeypatch):
    settings, client = public_client
    monkeypatch.setattr("ahn.service.discover_source", lambda _host: None)
    result = client.get("/v1/incidents/check", params={"site_host": "example.org", "failure_stage": "before_target_response"})
    assert result.status_code == 200
    data = result.json()
    assert data["failure_stage"] == "before_target_response"
    assert data["next_checks"][0]["code"] == "before_target_response"
    assert data["current_site_status"] == "unknown"
    english = client.get("/en/search", params={"site_host": "example.org", "failure_stage": "access_denial_observed"}).text
    assert "An access denial was observed" in english
    assert "No verified provider source found by current methods" in english
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM observations").fetchone()["n"] == 0


def test_first_lookup_discovers_first_party_statuspage(public_client, monkeypatch):
    settings, client = public_client
    calls = []
    def fake_discover(host):
        calls.append(host)
        return (
            Source("Twilio Status", "https://status.twilio.com/", "https://status.twilio.com/api/v2/status.json", "discovered_statuspage"),
            "minor", datetime.now(timezone.utc),
        )
    monkeypatch.setattr("ahn.service.discover_source", fake_discover)
    monkeypatch.setattr("ahn.service.refresh_one", lambda *_args, **_kwargs: True)
    first = client.get("/v1/incidents/check", params={"site_host": "discovered-example.org"})
    assert first.status_code == 200
    assert first.json()["provider_status"]["indicator"] == "minor"
    assert first.json()["provider_status"]["freshness"] == "fresh"
    assert first.json()["current_site_status"] == "unknown"
    assert calls == ["discovered-example.org"]
    second = client.get("/v1/incidents/check", params={"site_host": "discovered-example.org"})
    assert second.json()["provider_status"]["source_url"] == "https://status.twilio.com/"
    assert calls == ["discovered-example.org"]
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM queried_hosts WHERE site_host='discovered-example.org'").fetchone()["n"] == 1
        assert conn.execute("SELECT count(*) AS n FROM status_sources WHERE site_host='discovered-example.org'").fetchone()["n"] == 1


def test_provider_fetch_failure_is_not_reported_as_fresh(public_client, monkeypatch):
    settings, client = public_client
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "page": {"updated_at": "2026-10-03T18:00:00Z"}, "status": {"indicator": "none"},
    }))) as remote:
        monkeypatch.setattr("ahn.service.refresh_one", lambda s, host: refresh_one(s, host, client=remote))
        client.get("/v1/incidents/check", params={"site_host": "github.com"})
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(502))) as remote:
        monkeypatch.setattr("ahn.service.refresh_one", lambda s, host: refresh_one(s, host, client=remote, min_interval=0))
        status = client.get("/v1/incidents/check", params={"site_host": "github.com"}).json()["provider_status"]
        assert status["indicator"] == "none"
        assert status["freshness"] == "stale"
        assert status["fetched_at"] is not None
        assert status["error_detail"] == "http_502"
        page = client.get("/en/search", params={"site_host": "github.com"}).text
        assert "Feed read failed:" in page and "http_502" in page
    with connect(settings) as conn:
        assert conn.execute("SELECT last_error_detail FROM status_cache WHERE site_host='github.com'").fetchone()["last_error_detail"] == "http_502"
    # A later successful read clears the reason again.
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={
        "page": {"updated_at": "2026-10-03T18:00:00Z"}, "status": {"indicator": "none"},
    }))) as remote:
        monkeypatch.setattr("ahn.service.refresh_one", lambda s, host: refresh_one(s, host, client=remote, min_interval=0))
        status = client.get("/v1/incidents/check", params={"site_host": "github.com"}).json()["provider_status"]
        assert status["freshness"] == "fresh" and status["error_detail"] is None
    with connect(settings) as conn:
        assert conn.execute("SELECT last_error_detail FROM status_cache WHERE site_host='github.com'").fetchone()["last_error_detail"] is None


def test_gmail_official_feed_is_product_specific(public_client, monkeypatch):
    settings, client = public_client
    feed = [
        {"affected_products": [{"id": "other"}], "status_impact": "SERVICE_OUTAGE", "begin": "2026-10-03T18:00:00Z"},
        {"affected_products": [{"id": "prPt2Yra2CbGsbEm9cpC"}], "status_impact": "SERVICE_DISRUPTION", "begin": "2026-10-03T18:00:00Z"},
    ]
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=feed))) as remote:
        monkeypatch.setattr("ahn.service.refresh_one", lambda s, host: refresh_one(s, host, client=remote))
        status = client.get("/v1/incidents/check", params={"site_host": "gmail.com"}).json()["provider_status"]
    assert status["indicator"] == "major"
    assert status["source_name"].startswith("Google Workspace")
    assert parse_google_gmail([dict(feed[1], end="2026-10-03T19:00:00Z")])[0] == "none"


def test_pending_visibility_idempotency_and_withdrawal(public_client):
    settings, client = public_client
    data = sample()
    assert post_public(client, sample(site_host="legacy-field.example.org", consent_confirmed=False)).status_code == 201
    assert post_public(client, sample(site_host="private.example.org/path")).status_code == 400
    assert post_public(client, sample(site_host="demo.agenthelpnetwork.test")).status_code == 400
    assert post_public(client, sample(task_step="account_123456")).status_code == 400
    assert post_public(client, sample(synthetic=True)).status_code == 400
    first = post_public(client, data)
    assert first.status_code == 201, first.text
    receipt = first.json()
    assert receipt["review_state"] == "pending" and receipt["synthetic"] is False
    assert len(receipt["withdrawal_token"]) == 64
    retry = post_public(client, data)
    assert retry.status_code == 200 and retry.json() == receipt
    check = client.get("/v1/incidents/check", params={"site_host": "example.org"}).json()
    assert check["evidence_level"] == "no_reports" and check["current_site_status"] == "unknown"
    assert client.get("/v1/incidents", params={"site_host": "example.org"}).json()["incidents"] == []
    assert client.delete(f"/v1/observations/{receipt['observation_id']}", headers={"X-Withdrawal-Token": "0" * 64}).status_code == 404
    assert client.delete(f"/v1/observations/{receipt['observation_id']}", headers={"X-Withdrawal-Token": receipt["withdrawal_token"]}).json() == {"withdrawn": True}
    with connect(settings) as conn:
        row = conn.execute("SELECT withdrawn_at FROM observations WHERE id=%s", (receipt["observation_id"],)).fetchone()
        assert row["withdrawn_at"] is not None


def test_host_cap_and_pause(public_client):
    settings, client = public_client
    for _ in range(5):
        assert post_public(client, sample()).status_code == 201
    assert post_public(client, sample()).status_code == 429
    with connect(settings) as conn:
        conn.execute("UPDATE service_flags SET enabled=false WHERE name='public_intake'")
    assert post_public(client, sample(site_host="another.org")).status_code == 503


def test_host_cap_counts_www_with_its_apex(public_client):
    settings, client = public_client
    for host in ("capexample.org", "www.capexample.org", "capexample.org", "www.capexample.org", "capexample.org"):
        assert post_public(client, sample(site_host=host)).status_code == 201
    assert post_public(client, sample(site_host="www.capexample.org")).status_code == 429
    assert post_public(client, sample(site_host="capexample.org")).status_code == 429
    assert post_public(client, sample(site_host="shop.capexample.org")).status_code == 201
    with connect(settings) as conn:
        counters = conn.execute("SELECT site_host,reports FROM public_intake_host_daily WHERE site_host LIKE %s ORDER BY site_host", ("%capexample.org",)).fetchall()
        hosts = conn.execute("SELECT site_host,count(*) AS n FROM incidents WHERE site_host LIKE %s GROUP BY site_host ORDER BY site_host", ("%capexample.org",)).fetchall()
    assert counters == [{"site_host": "capexample.org", "reports": 5}, {"site_host": "shop.capexample.org", "reports": 1}]
    assert hosts == [{"site_host": "capexample.org", "n": 1}, {"site_host": "shop.capexample.org", "n": 1}, {"site_host": "www.capexample.org", "n": 1}]


def test_manual_review_then_hide(public_client, monkeypatch, capsys):
    from ahn.cli import main as cli_main
    import sys

    settings, client = public_client
    receipt = post_public(client, sample(site_host="another.org")).json()
    observation_id = receipt["observation_id"]
    monkeypatch.setenv("AHN_DATABASE_URL", TEST_URL)
    monkeypatch.setenv("AHN_KEY_PEPPER", settings.key_pepper)
    monkeypatch.setenv("AHN_TEST_MODE", "0")
    monkeypatch.setenv("AHN_PUBLIC_MODE", "1")
    monkeypatch.setenv("AHN_REPORTING_ENABLED", "1")

    def command(*args):
        monkeypatch.setattr(sys, "argv", ["ahn.cli", *args])
        cli_main()
        return capsys.readouterr().out

    assert observation_id in command("pending-reports")
    assert client.get("/v1/incidents/check", params={"site_host": "another.org"}).json()["evidence_level"] == "no_reports"
    assert "published" in command("publish-observation", observation_id)
    checked = client.get("/v1/incidents/check", params={"site_host": "another.org"}).json()
    assert checked["evidence_level"] == "single_observation" and checked["reporting_sources"] == 1
    assert len(client.get("/v1/incidents", params={"site_host": "another.org"}).json()["incidents"]) == 1
    assert "hidden" in command("hide-observation", observation_id)
    assert client.get("/v1/incidents/check", params={"site_host": "another.org"}).json()["evidence_level"] == "no_reports"
    assert client.get("/v1/incidents", params={"site_host": "another.org"}).json()["incidents"] == []
    assert "paused" in command("pause-intake")
    assert "enabled" in command("resume-intake")


def test_auto_review_publishes_one_unverified_and_holds_repeats(public_client):
    settings, client = public_client
    old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    first = post_public(client, sample(site_host="autoexample.org", observed_at=old_time.isoformat())).json()
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id=%s", (first["observation_id"],))
    assert review_batch(TEST_URL) == {"published": 1, "held": 0}
    checked = client.get("/v1/incidents/check", params={"site_host": "autoexample.org"}).json()
    assert checked["evidence_level"] == "single_observation"
    assert checked["current_site_status"] == "unknown"
    with connect(settings) as conn:
        row = conn.execute("SELECT review_reason FROM observations WHERE id=%s", (first["observation_id"],)).fetchone()
    assert row["review_reason"] == "auto_unverified"

    second = post_public(client, sample(site_host="autoexample.org", observed_at=old_time.isoformat())).json()
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id=%s", (second["observation_id"],))
    assert review_batch(TEST_URL) == {"published": 0, "held": 1}
    with connect(settings) as conn:
        row = conn.execute("SELECT review_state,review_reason FROM observations WHERE id=%s", (second["observation_id"],)).fetchone()
    assert row == {"review_state": "pending", "review_reason": "recent_host_publication"}
    assert review_batch(TEST_URL) == {"published": 0, "held": 0}


def test_auto_review_holds_host_burst(public_client):
    settings, client = public_client
    old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    receipts = [post_public(client, sample(site_host="burstexample.org", observed_at=old_time.isoformat())).json() for _ in range(3)]
    with connect(settings) as conn:
        for receipt in receipts:
            conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id=%s", (receipt["observation_id"],))
    assert review_batch(TEST_URL) == {"published": 0, "held": 3}
    with connect(settings) as conn:
        reasons = conn.execute("SELECT DISTINCT review_reason FROM observations WHERE incident_id=%s", (receipts[0]["incident_id"],)).fetchall()
    assert reasons == [{"review_reason": "host_burst"}]


def test_auto_review_publishes_www_apex_under_its_exact_host(public_client):
    settings, client = public_client
    old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    www = post_public(client, sample(site_host="www.wwwexample.org", observed_at=old_time.isoformat())).json()
    tenant = post_public(client, sample(site_host="shop.wwwexample.org", observed_at=old_time.isoformat())).json()
    double = post_public(client, sample(site_host="www.www.wwwexample.org", observed_at=old_time.isoformat())).json()
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes'")
    assert review_batch(TEST_URL) == {"published": 1, "held": 2}
    with connect(settings) as conn:
        rows = {
            str(r.pop("id")): r
            for r in conn.execute(
                "SELECT o.id,o.review_state,o.review_reason,i.site_host FROM observations o JOIN incidents i ON i.id=o.incident_id"
            ).fetchall()
        }
    assert rows[www["observation_id"]] == {"review_state": "published", "review_reason": "auto_unverified", "site_host": "www.wwwexample.org"}
    assert rows[tenant["observation_id"]]["review_reason"] == "unreviewed_subdomain"
    assert rows[double["observation_id"]]["review_reason"] == "unreviewed_subdomain"
    assert client.get("/v1/incidents/check", params={"site_host": "www.wwwexample.org"}).json()["evidence_level"] == "single_observation"
    assert client.get("/v1/incidents/check", params={"site_host": "wwwexample.org"}).json()["evidence_level"] == "no_reports"


def test_auto_review_counts_www_and_apex_together(public_client):
    settings, client = public_client
    old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    apex = post_public(client, sample(site_host="pairexample.org", observed_at=old_time.isoformat())).json()
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id=%s", (apex["observation_id"],))
    assert review_batch(TEST_URL) == {"published": 1, "held": 0}
    www = post_public(client, sample(site_host="www.pairexample.org", observed_at=old_time.isoformat())).json()
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id=%s", (www["observation_id"],))
    assert review_batch(TEST_URL) == {"published": 0, "held": 1}
    with connect(settings) as conn:
        row = conn.execute("SELECT review_state,review_reason FROM observations WHERE id=%s", (www["observation_id"],)).fetchone()
    assert row == {"review_state": "pending", "review_reason": "recent_host_publication"}

    receipts = [post_public(client, sample(site_host=host, observed_at=old_time.isoformat(), outcome="blocked")).json() for host in ("burstpair.org", "burstpair.org", "www.burstpair.org")]
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id = ANY(%s)", ([r["observation_id"] for r in receipts],))
    assert review_batch(TEST_URL) == {"published": 0, "held": 3}
    with connect(settings) as conn:
        reasons = conn.execute("SELECT DISTINCT o.review_reason FROM observations o JOIN incidents i ON i.id=o.incident_id WHERE i.site_host LIKE %s", ("%burstpair.org",)).fetchall()
        hosts = conn.execute("SELECT DISTINCT site_host FROM incidents WHERE site_host LIKE %s ORDER BY site_host", ("%burstpair.org",)).fetchall()
    assert reasons == [{"review_reason": "host_burst"}]
    assert hosts == [{"site_host": "burstpair.org"}, {"site_host": "www.burstpair.org"}]


def test_auto_review_can_publish_pass_and_pause_publication(public_client):
    settings, client = public_client
    old_time = datetime.now(timezone.utc) - timedelta(minutes=20)
    failed = post_public(client, sample(site_host="balancedexample.org", observed_at=old_time.isoformat(), outcome="blocked")).json()
    passed = post_public(client, sample(site_host="balancedexample.org", observed_at=old_time.isoformat(), outcome="passed")).json()
    with connect(settings) as conn:
        conn.execute("UPDATE observations SET received_at=now()-interval '15 minutes' WHERE id IN (%s,%s)", (failed["observation_id"], passed["observation_id"]))
        conn.execute("UPDATE service_flags SET enabled=false WHERE name='public_publication'")
    assert review_batch(TEST_URL) == {"published": 0, "held": 0}
    with connect(settings) as conn:
        conn.execute("UPDATE service_flags SET enabled=true WHERE name='public_publication'")
    assert review_batch(TEST_URL) == {"published": 2, "held": 0}
    result = client.get("/v1/incidents/check", params={"site_host": "balancedexample.org"}).json()
    assert result["failures"] == 1 and result["successes"] == 1
    assert result["current_site_status"] == "unknown"


def test_bandwidth_guard_warns_then_suspends_once(public_client, monkeypatch):
    settings, _ = public_client
    from ahn import bandwidth_guard

    # Fixed early-month clock: an empty DB is refused after day 6 of the month.
    now = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
    values = [{"timestamp": (now - timedelta(hours=2)).isoformat(), "value": 11}]
    resource = "srv-db0hnkfavr4c73fp6et0"
    def handler(request):
        assert request.url.path == "/v1/metrics/bandwidth"
        assert request.url.params["resource"] == resource
        assert request.url.params["startTime"].endswith("Z")
        assert request.url.params["endTime"].endswith("Z")
        return httpx.Response(200, json=[{"labels": [{"field": "service", "value": resource}], "values": values, "unit": "GB"}])
    client = httpx.Client(base_url="https://api.render.com/v1", transport=httpx.MockTransport(handler))
    stopped, calls = [], []  # stopped = live Render state; calls = guard suspend requests
    monkeypatch.setattr(bandwidth_guard, "_suspend_service", lambda key, service_id: (calls.append(service_id), stopped.append(service_id)))
    monkeypatch.setattr(bandwidth_guard, "_is_service_suspended", lambda key, service_id: service_id in stopped)
    first = run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)
    assert first["action"] == "warning" and first["coverage_gap"] is False and first["coverage_gap_new"] is False
    assert run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)["action"] == "none"
    values.append({"timestamp": (now - timedelta(hours=1)).isoformat(), "value": 10})
    stopped.append(resource)  # an operator suspended the service by hand before the first crossing
    assert run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)["action"] == "suspended_externally"
    assert calls == []
    assert run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)["action"] == "already_suspended"
    assert calls == []
    stopped.clear()  # the manual suspension was reversed on Render
    assert run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)["action"] == "re_suspended"
    assert calls == [resource] and stopped == [resource]
    assert run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)["action"] == "already_suspended"
    assert calls == [resource]
    with connect(settings) as conn:
        state = conn.execute("SELECT usage_gb,state,suspended_at,coverage_gap_since,warning_gb,stop_gb FROM bandwidth_guard_state WHERE name='web'").fetchone()
    assert state["usage_gb"] == 21 and state["state"] == "suspended" and state["suspended_at"] == now
    assert state["coverage_gap_since"] is None and state["warning_gb"] == 10 and state["stop_gb"] == 20


def test_bandwidth_guard_api_error_redacts_key(public_client):
    key = "fake-sensitive-test-key"
    client = httpx.Client(base_url="https://api.render.com/v1", transport=httpx.MockTransport(
        lambda request: httpx.Response(400, text=f"invalid time range; token={key}")
    ))
    with pytest.raises(RuntimeError, match="invalid time range") as error:
        run_guard(TEST_URL, key, "srv-db0hnkfavr4c73fp6et0",
                  now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc), client=client)
    assert key not in str(error.value)


def _guard_window_client(resource, now, gigabytes):
    """Two hourly samples inside the retained window, totalling `gigabytes`."""
    values = [{"timestamp": (now - timedelta(hours=2)).isoformat(), "value": str(gigabytes - 1)},
              {"timestamp": (now - timedelta(hours=1)).isoformat(), "value": "1"}]
    return httpx.Client(base_url="https://api.render.com/v1", transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json=[{"labels": [{"field": "service", "value": resource}], "values": values, "unit": "GB"}])
    ))


def test_bandwidth_guard_detects_previous_month_state_gap(public_client, monkeypatch):
    settings, _ = public_client
    from ahn import bandwidth_guard

    resource = "srv-db0hnkfavr4c73fp6et0"
    now = datetime(2026, 10, 20, 12, tzinfo=timezone.utc)
    month_start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    stopped, calls = [], []
    monkeypatch.setattr(bandwidth_guard, "_suspend_service", lambda key, service_id: (calls.append(service_id), stopped.append(service_id)))
    monkeypatch.setattr(bandwidth_guard, "_is_service_suspended", lambda key, service_id: service_id in stopped)
    with connect(settings) as conn:
        conn.execute(
            """INSERT INTO bandwidth_guard_state
               (name,period_start,checked_at,usage_gb,state)
               VALUES ('web','2026-09-01T00:00:00Z','2026-09-30T23:00:00Z',1,'normal')"""
        )
    # First run of a billing month after day 6: evaluated as a lower bound, flagged once, not raised.
    client = _guard_window_client(resource, now, Decimal("3"))
    result = run_guard(TEST_URL, "fake-test-key", resource, now=now, client=client)
    assert result == {"state": "normal", "usage_gb": 3, "action": "none", "coverage_gap": True, "coverage_gap_new": True}
    assert calls == []
    with connect(settings) as conn:
        state = conn.execute("SELECT period_start,checked_at,usage_gb,state,coverage_gap_since FROM bandwidth_guard_state WHERE name='web'").fetchone()
    assert state["period_start"] == month_start and state["checked_at"] == now and state["usage_gb"] == 3
    assert state["state"] == "normal" and state["coverage_gap_since"] == now
    # The next hourly run keeps the sticky flag without a second notification.
    later = now + timedelta(hours=1)
    again = run_guard(TEST_URL, "fake-test-key", resource, now=later, client=_guard_window_client(resource, later, Decimal("3")))
    assert again["coverage_gap"] is True and again["coverage_gap_new"] is False and again["action"] == "none"
    with connect(settings) as conn:
        state = conn.execute("SELECT checked_at,coverage_gap_since FROM bandwidth_guard_state WHERE name='web'").fetchone()
    assert state["checked_at"] == later and state["coverage_gap_since"] == now

    # Same-month gap, window over the stop threshold: suspend from the lower bound.
    with connect(settings) as conn:
        conn.execute("TRUNCATE bandwidth_guard_samples, bandwidth_guard_state")
        conn.execute(
            """INSERT INTO bandwidth_guard_state
               (name,period_start,checked_at,usage_gb,state)
               VALUES ('web',%s,%s,1,'normal')""",
            (month_start, now - timedelta(days=7)),
        )
    result = run_guard(TEST_URL, "fake-test-key", resource, now=now, client=_guard_window_client(resource, now, Decimal("20")))
    assert result["action"] == "suspended" and result["coverage_gap"] is True and result["coverage_gap_new"] is True
    assert result["state"] == "suspended" and result["usage_gb"] == 20 and calls == [resource]
    with connect(settings) as conn:
        state = conn.execute("SELECT checked_at,usage_gb,state,suspended_at,coverage_gap_since FROM bandwidth_guard_state WHERE name='web'").fetchone()
    assert state["checked_at"] == now and state["usage_gb"] == 20 and state["state"] == "suspended"
    assert state["suspended_at"] == now and state["coverage_gap_since"] == now
    assert run_guard(TEST_URL, "fake-test-key", resource, now=now, client=_guard_window_client(resource, now, Decimal("20")))["action"] == "already_suspended"
    assert calls == [resource]

    # Same-month gap, window under the stop threshold: flag only, no suspend.
    calls.clear()
    stopped.clear()
    with connect(settings) as conn:
        conn.execute("TRUNCATE bandwidth_guard_samples, bandwidth_guard_state")
        conn.execute(
            """INSERT INTO bandwidth_guard_state
               (name,period_start,checked_at,usage_gb,state)
               VALUES ('web',%s,%s,1,'normal')""",
            (month_start, now - timedelta(days=7)),
        )
    result = run_guard(TEST_URL, "fake-test-key", resource, now=now, client=_guard_window_client(resource, now, Decimal("12")))
    assert result == {"state": "warning", "usage_gb": 12, "action": "warning", "coverage_gap": True, "coverage_gap_new": True}
    assert calls == []
    with connect(settings) as conn:
        state = conn.execute("SELECT checked_at,state,suspended_at,coverage_gap_since FROM bandwidth_guard_state WHERE name='web'").fetchone()
    assert state["checked_at"] == now and state["state"] == "warning"
    assert state["suspended_at"] is None and state["coverage_gap_since"] == now


def test_bandwidth_guard_converts_live_mb_unit():
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    resource = "srv-db0hnkfavr4c73fp6et0"
    series = [{"labels": [{"field": "service", "value": resource}],
               "values": [{"timestamp": now.isoformat(), "value": 1500}], "unit": "mb"}]
    assert parse_bandwidth(series, resource, now - timedelta(hours=1), now) == [(now, Decimal("1.5"))]
    series[0]["unit"] = "MB"
    assert parse_bandwidth(series, resource, now - timedelta(hours=1), now) == [(now, Decimal("1.5"))]


def test_owner_dashboard_requires_secret(public_client):
    settings, _ = public_client
    owner_settings = Settings(settings.database_url, settings.key_pepper, public_mode=True,
                              reporting_enabled=True, owner_password="x" * 40)
    with TestClient(create_app(owner_settings)) as client:
        assert client.get("/owner").status_code == 401
        token = base64.b64encode(("owner:" + "x" * 40).encode()).decode()
        response = client.get("/owner", headers={"Authorization": "Basic " + token})
        assert response.status_code == 200
        assert "이번 달 웹 전송량" in response.text
        assert response.headers["cache-control"] == "private, no-store"


def test_owner_dashboard_home_ip_has_no_password_prompt(public_client):
    settings, _ = public_client
    owner_settings = Settings(
        settings.database_url, settings.key_pepper, public_mode=True,
        reporting_enabled=True, owner_password="x" * 40,
        owner_allowed_ips=("198.51.100.7",),
    )
    with TestClient(create_app(owner_settings)) as client:
        home = client.get("/owner", headers={"CF-Connecting-IP": "198.51.100.7"})
        assert home.status_code == 200
        assert "www-authenticate" not in home.headers
        assert home.headers["cache-control"] == "private, no-store"

        token = base64.b64encode(("owner:" + "x" * 40).encode()).decode()
        for headers in ({}, {"CF-Connecting-IP": "203.0.113.8"},
                        {"CF-Connecting-IP": "198.51.100.7, 203.0.113.8"},
                        {"Authorization": "Basic " + token}):
            denied = client.get("/owner", headers=headers)
            assert denied.status_code == 404
            assert "www-authenticate" not in denied.headers


def test_owner_allowed_ip_setting_rejects_ranges(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://localhost/ahn_test")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 40)
    monkeypatch.setenv("AHN_OWNER_ALLOWED_IPS", "198.51.100.7, 2001:db8::1")
    assert Settings.from_env().owner_allowed_ips == ("198.51.100.7", "2001:db8::1")
    monkeypatch.setenv("AHN_OWNER_ALLOWED_IPS", "198.51.100.0/24")
    with pytest.raises(RuntimeError, match="individual IP addresses"):
        Settings.from_env()


def test_prune_reports_deletes_expired_and_long_withdrawn_reports_only(public_client):
    from ahn.platform_metrics import prune_reports

    settings, _client = public_client
    with connect(settings) as conn:
        incident = conn.execute(
            "INSERT INTO incidents (site_host,task_step,error_class) VALUES ('example.org','site_open','site_access') RETURNING id"
        ).fetchone()["id"]

        def add(received_days_ago, withdrawn_days_ago=None, supersedes=None):
            row = conn.execute(
                """INSERT INTO observations (incident_id,reporter_id,observed_at,received_at,agent_family,attempt_actor,attempt_env,
                       outcome,challenge_vendor,challenge_kind,supersedes_id,withdrawn_at)
                   VALUES (%s,%s,now(),now() - make_interval(days => %s),'other','agent','unknown','error','none','none',%s,
                       CASE WHEN %s::int IS NULL THEN NULL ELSE now() - make_interval(days => %s::int) END) RETURNING id""",
                (incident, PUBLIC_REPORTER_ID, received_days_ago, supersedes, withdrawn_days_ago, withdrawn_days_ago),
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO report_submissions (reporter_id,idempotency_key,request_hash,observation_id) VALUES (%s,%s,repeat('a',64),%s)",
                (PUBLIC_REPORTER_ID, uuid4(), row),
            )
            return row

        expired = add(400)
        fresh = add(10)
        follow_up = add(5, supersedes=expired)
        long_withdrawn = add(60, withdrawn_days_ago=45)
        recently_withdrawn = add(20, withdrawn_days_ago=3)

        assert prune_reports(conn, 0) == 0
        assert prune_reports(conn, 365) == 2
        left = {row["id"]: row["supersedes_id"] for row in conn.execute("SELECT id,supersedes_id FROM observations")}
        assert set(left) == {fresh, follow_up, recently_withdrawn}
        assert left[follow_up] is None
        assert {row["observation_id"] for row in conn.execute("SELECT observation_id FROM report_submissions")} == set(left)
        assert long_withdrawn not in left
