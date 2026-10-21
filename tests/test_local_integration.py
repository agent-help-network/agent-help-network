"""Synthetic-only integration tests. Set AHN_TEST_DATABASE_URL to a dedicated ahn_test DB."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
import httpx
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from ahn.db import connect, create_reporter, migrate
from ahn.models import CheckResponse, Receipt
from ahn.settings import Settings
from ahn.platform_metrics import collect_render_metrics, prune_analytics


TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required for PostgreSQL integration tests", allow_module_level=True)

os.environ["AHN_DATABASE_URL"] = TEST_URL
os.environ.setdefault("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")
os.environ["AHN_TEST_MODE"] = "1"
from ahn.main import create_app  # noqa: E402
from ahn.monitor import create_monitor_app  # noqa: E402


class PrivateEnv(tuple):
    def __repr__(self):
        return "<private synthetic test environment>"


@pytest.fixture
def env():
    settings = Settings(TEST_URL, os.environ["AHN_KEY_PEPPER"], test_mode=True, collect_lookup_metrics=True, max_reports_per_hour=12, collect_request_metrics=True)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to run destructive test reset outside ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, request_metrics, traffic_attribution_daily, traffic_client_daily, mcp_check_metrics, render_metric_samples, host_access_profile RESTART IDENTITY CASCADE")
    first = create_reporter(settings, "test-muse")
    second = create_reporter(settings, "test-dots")
    with TestClient(create_app(settings)) as client:
        yield PrivateEnv((settings, client, first, second))


def payload(**changes):
    data = {
        "schema_version": 1,
        "site_host": "demo.agenthelpnetwork.test",
        "task_step": "login_verification",
        "error_class": "human_verification",
        "observed_at": (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat(),
        "agent_family": "muse",
        "attempt_actor": "agent",
        "attempt_env": "agent_cloud_browser",
        "outcome": "blocked",
        "challenge_vendor": "unknown",
        "challenge_kind": "loop",
        "synthetic": True,
        "idempotency_key": str(uuid4()),
        "consent_confirmed": True,
    }
    data.update(changes)
    return data


def post(client, key, data):
    return client.post("/v1/observations", json=data, headers={"X-API-Key": key})


def test_health_and_bilingual_pages(env):
    _, client, _, _ = env
    assert client.get("/health").json() == {"status": "ok"}
    for route in ("/en/", "/ko/", "/en/human", "/ko/human", "/en/search", "/ko/search", "/en/connect", "/ko/connect", "/en/agents", "/ko/agents", "/en/policy", "/ko/policy"):
        response = client.get(route)
        assert response.status_code == 200, route
        assert f'lang="{route[1:3]}"' in response.text
    contract = client.get("/openapi.json").json()
    assert "securitySchemes" in contract["components"]
    assert contract["paths"]["/v1/incidents/check"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/CheckResponse")
    post_schema = contract["paths"]["/v1/observations"]["post"]
    assert post_schema["security"] == [{"APIKeyHeader": []}]
    assert post_schema["responses"]["201"]["content"]["application/json"]["schema"]["$ref"].endswith("/Receipt")
    assert "consent_confirmed" not in contract["components"]["schemas"]["ObservationIn"]["required"]


def test_bilingual_search_zero_one_multiple_and_escaping(env, monkeypatch):
    _, client, _, _ = env
    host = "demo.agenthelpnetwork.test"
    for lang in ("en", "ko"):
        empty = client.get(f"/{lang}/search", params={"site_host": host})
        assert empty.status_code == 200 and 'class="ledger-empty" role="status"' in empty.text
        assert "no_reports" in empty.text
        assert 'class="empty-icon"' not in empty.text
        all_types = client.get(f"/{lang}/search", params={"site_host": host, "error_class": ""})
        assert all_types.status_code == 200 and 'class="ledger-empty" role="status"' in all_types.text
        invalid_filter = client.get(f"/{lang}/search", params={"site_host": host, "error_class": "unknown_type"})
        assert invalid_filter.status_code == 200 and 'role="alert"' in invalid_filter.text
        invalid = client.get(f"/{lang}/search", params={"site_host": "<script>alert(1)</script>.org"})
        assert invalid.status_code == 200 and "<script>" not in invalid.text
    fixtures = [
        {"id": str(uuid4()), "site_host": host, "task_step": "login_verification", "error_class": "human_verification", "observations": 1},
        {"id": str(uuid4()), "site_host": host, "task_step": "site_open", "error_class": "site_access", "observations": 2},
    ]
    monkeypatch.setattr("ahn.main.public_incidents", lambda *_args, **_kwargs: fixtures[:1])
    one = client.get("/en/search", params={"site_host": host})
    assert one.text.count('class="result-card"') == 1
    monkeypatch.setattr("ahn.main.public_incidents", lambda *_args, **_kwargs: fixtures)
    many = client.get("/ko/search", params={"site_host": host})
    assert many.text.count('class="result-card"') == 2


def test_roundtrip_idempotency_and_evidence(env):
    settings, client, muse, dots = env
    host = "demo.agenthelpnetwork.test"
    no_reports = client.get("/v1/incidents/check", params={"site_host": host}).json()
    assert no_reports["evidence_level"] == "no_reports"
    assert "unknown" in no_reports["summary"]
    assert no_reports["current_site_status"] == "unknown"
    assert no_reports["data_horizon_hours"] == 168
    CheckResponse.model_validate(no_reports)
    first = payload()
    created = post(client, muse, first)
    assert created.status_code == 201
    Receipt.model_validate(created.json())
    repeated = post(client, muse, first)
    assert repeated.status_code == 200
    assert repeated.json()["observation_id"] == created.json()["observation_id"]
    assert post(client, muse, {**first, "outcome": "passed"}).status_code == 409
    checked = client.get("/v1/incidents/check", params={"site_host": host}).json()
    assert checked["synthetic"] is True
    assert (checked["failures"], checked["reporting_sources"]) == (1, 1)
    assert checked["evidence_level"] == "single_observation"
    passed = payload(
        agent_family="dots", attempt_actor="human", attempt_env="human_in_agent_browser",
        outcome="passed", observed_at=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
    )
    assert post(client, dots, passed).status_code == 201
    checked = client.get("/v1/incidents/check", params={"site_host": host}).json()
    assert checked["evidence_level"] == "later_success_observed"
    assert (checked["failures"], checked["successes"], checked["reporting_sources"]) == (1, 1, 2)
    assert "does not establish general recovery" in checked["summary"]
    assert client.get("/v1/incidents").json() == {"incidents": []}
    assert 'class="ledger-empty" role="status"' in client.get(f"/en/search?site_host={host}").text
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM observations").fetchone()["n"] == 2


def test_same_reporter_two_attempts_not_two_sources(env):
    _, client, muse, _ = env
    assert post(client, muse, payload()).status_code == 201
    human = payload(attempt_actor="human", attempt_env="human_in_agent_browser", observed_at=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat())
    assert post(client, muse, human).status_code == 201
    result = client.get("/v1/incidents/check", params={"site_host": human["site_host"]}).json()
    assert len(result["observations"]) == 2
    assert result["reporting_sources"] == 1


def test_multiple_sources_and_stale_evidence(env):
    _, client, muse, dots = env
    host = "evidence.agenthelpnetwork.test"
    older = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    assert post(client, muse, payload(site_host=host, observed_at=older)).status_code == 201
    assert post(client, dots, payload(site_host=host, observed_at=older, agent_family="dots")).status_code == 201
    recent_window = client.get("/v1/incidents/check", params={"site_host": host}).json()
    assert recent_window["evidence_level"] == "stale_reports"
    assert recent_window["reporting_sources"] == 2
    expanded = client.get("/v1/incidents/check", params={"site_host": host, "window_hours": 48}).json()
    assert expanded["evidence_level"] == "multiple_observations"
    assert (expanded["failures"], expanded["reporting_sources"]) == (2, 2)
    assert "independence is not verified" in expanded["summary"]
    conflicting_host = "conflict.agenthelpnetwork.test"
    earlier_pass = (datetime.now(timezone.utc) - timedelta(minutes=4)).isoformat()
    later_block = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
    assert post(client, muse, payload(site_host=conflicting_host, outcome="passed", observed_at=earlier_pass)).status_code == 201
    assert post(client, dots, payload(site_host=conflicting_host, agent_family="dots", outcome="blocked", observed_at=later_block)).status_code == 201
    conflicting = client.get("/v1/incidents/check", params={"site_host": conflicting_host}).json()
    assert conflicting["evidence_level"] == "multiple_observations"
    assert (conflicting["failures"], conflicting["successes"]) == (1, 1)


def test_validation_authority_compatibility_and_ownership(env):
    settings, client, muse, dots = env
    assert post(client, muse, payload(site_host="https://example.org/private?id=1")).status_code == 400
    injection = post(client, muse, payload(site_host="example.org'; DROP TABLE observations;--"))
    assert injection.status_code == 400 and "DROP TABLE" not in injection.text
    script = post(client, muse, payload(site_host="<script>alert(1)</script>.org"))
    assert script.status_code == 400 and "<script>" not in script.text
    assert post(client, muse, payload(unexpected="account-123")).status_code == 400
    assert post(client, muse, payload(consent_confirmed=False)).status_code == 201
    omitted_consent = payload()
    omitted_consent.pop("consent_confirmed")
    missing = post(client, muse, omitted_consent)
    assert missing.status_code == 201
    assert post(client, muse, payload(consent_confirmed="true")).status_code == 400
    assert post(client, "wrong", payload()).status_code == 401
    assert post(client, muse, payload(synthetic=False)).status_code == 403
    assert post(client, muse, payload(observed_at=(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat())).status_code == 400
    assert client.post("/v1/observations", content="{bad", headers={"X-API-Key": muse, "Content-Type": "application/json"}).status_code == 400
    assert client.get("/v1/incidents/check", params={"site_host": "example.org", "window_hours": 1000}).status_code == 400
    assert client.get("/v1/incidents", params={"limit": 1000}).status_code == 400
    assert client.get("/health").json() == {"status": "ok"}
    created = post(client, muse, payload()).json()
    outcome = payload()
    outcome.pop("site_host")
    outcome.pop("task_step")
    outcome.pop("error_class")
    outcome["observed_at"] = datetime.now(timezone.utc).isoformat()
    assert client.post(f"/v1/observations/{created['observation_id']}/outcomes", json=outcome, headers={"X-API-Key": dots}).status_code == 404
    assert client.post(f"/v1/observations/{created['observation_id']}/outcomes", json=outcome, headers={"X-API-Key": muse}).status_code == 201
    assert client.delete(f"/v1/observations/{created['observation_id']}", headers={"X-API-Key": dots}).status_code == 404
    assert client.delete(f"/v1/observations/{created['observation_id']}", headers={"X-API-Key": muse}).json() == {"withdrawn": True}
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM observations WHERE withdrawn_at IS NOT NULL").fetchone()["n"] == 1


def test_lookup_metrics_are_not_reports(env):
    settings, client, _, _ = env
    for _ in range(2):
        response = client.get("/v1/incidents/check", params={"site_host": "example.org", "error_class": "login_auth"})
        assert response.json()["evidence_level"] == "no_reports"
    with connect(settings) as conn:
        assert conn.execute("SELECT checks FROM lookup_metrics WHERE site_host='example.org' AND error_class='login_auth'").fetchone()["checks"] == 2
        assert conn.execute("SELECT count(*) AS n FROM observations").fetchone()["n"] == 0


def test_report_quota_and_hide(env):
    settings, client, muse, _ = env
    for _ in range(settings.max_reports_per_hour):
        assert post(client, muse, payload()).status_code == 201
    blocked = post(client, muse, payload())
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "3600"
    with connect(settings) as conn:
        row = conn.execute("SELECT id FROM observations LIMIT 1").fetchone()
        conn.execute("UPDATE observations SET review_state='hidden' WHERE id=%s", (row["id"],))
    result = client.get("/v1/incidents/check", params={"site_host": "demo.agenthelpnetwork.test"}).json()
    assert len(result["observations"]) == settings.max_reports_per_hour - 1


def test_monitor_metrics_and_loopback_only(env):
    settings, client, _, _ = env
    assert client.get("/v1/incidents/check", params={"site_host": "example.org"}).status_code == 200
    assert client.get("/v1/incidents/check", params={"site_host": "bad/path"}).status_code == 400
    assert client.post("/v1/observations", content=b"x" * 4097).status_code == 413
    with connect(settings) as conn:
        metrics = conn.execute("SELECT route_code,status_class,sum(requests) AS n FROM request_metrics GROUP BY route_code,status_class").fetchall()
    assert any(row["route_code"] == "/v1/incidents/check" and row["status_class"] == "2xx" for row in metrics)
    assert any(row["route_code"] == "/v1/incidents/check" and row["status_class"] == "4xx" for row in metrics)
    assert any(row["status_class"] == "4xx" for row in metrics)
    assert client.get("/en/agents?utm_source=moltychan&utm_medium=community&utm_campaign=beta_launch",
                      headers={"CF-Connecting-IP": "198.51.100.23", "User-Agent": "Mozilla/5.0"}).status_code == 200
    with connect(settings) as conn:
        tagged = conn.execute("""SELECT requests FROM traffic_attribution_daily
                                 WHERE source='moltychan' AND medium='community'
                                   AND campaign='beta_launch' AND route_code='/{lang}/agents'""").fetchone()
        assert tagged["requests"] == 1
        daily_key = conn.execute("SELECT client_digest,requests FROM traffic_client_daily").fetchone()
        assert len(daily_key["client_digest"]) == 64 and daily_key["requests"] == 1
        assert "198.51.100.23" not in daily_key["client_digest"]
        conn.execute("INSERT INTO render_metric_samples (metric,sample_at,value,unit) VALUES ('cpu',now(),12.5,'percent')")
    monitor = create_monitor_app(settings)
    with TestClient(monitor, client=("127.0.0.1", 4321)) as local:
        response = local.get("/")
        assert response.status_code == 200
        assert "운영 모니터" in response.text
        assert "example.org" in response.text
        assert "moltychan" in response.text
        assert "12.50" in response.text
        assert response.headers["Cache-Control"] == "no-store"
    with TestClient(monitor, client=("198.51.100.8", 1234)) as remote:
        assert remote.get("/").status_code == 403


def test_owner_network_is_aggregated_separately_without_storing_address(env):
    settings, _, _, _ = env
    owner_settings = replace(settings, owner_allowed_ips=("198.51.100.23",))
    with TestClient(create_app(owner_settings)) as client:
        assert client.get("/en/", headers={"CF-Connecting-IP": "198.51.100.23"}).status_code == 200
        assert client.get("/en/", headers={"CF-Connecting-IP": "198.51.100.24"}).status_code == 200
    with connect(settings) as conn:
        rows = conn.execute("SELECT source,sum(requests)::bigint AS requests FROM traffic_attribution_daily GROUP BY source").fetchall()
    assert {row["source"]: row["requests"] for row in rows} == {"owner_network": 1, "unknown_direct": 1}
    monitor = create_monitor_app(settings)
    with TestClient(monitor, client=("127.0.0.1", 4321)) as local:
        body = local.get("/").text
        assert "일별 핵심 활동" in body and "운영자 네트워크" in body


def test_client_kinds_are_stored_as_categories_and_summarised_on_owner(env):
    settings, _, _, _ = env
    from ahn.tool_metrics import mcp_metric_token
    owner_settings = replace(settings, owner_allowed_ips=("198.51.100.23",))
    check = {"site_host": "example.org"}
    plugin = {"User-Agent": "ahn-claude-code-plugin/0.2"}
    with TestClient(create_app(owner_settings)) as client:
        for _ in range(3):
            assert client.get("/v1/incidents/check", params=check, headers=plugin).status_code == 200
        # Same plugin from the operator's network: counted in the first number only.
        assert client.get("/v1/incidents/check", params=check, headers={**plugin, "CF-Connecting-IP": "198.51.100.23"}).status_code == 200
        assert client.get("/v1/incidents/check", params=check, headers={"User-Agent": "ahn-client/0.2 (+https://agenthelpnetwork.com)"}).status_code == 200
        assert client.get("/v1/incidents/check", params=check, headers={"User-Agent": "python-httpx/0.28.1", "X-AHN-MCP-Call": mcp_metric_token(settings.key_pepper)}).status_code == 200
        assert client.get("/v1/incidents/check", params=check, headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
        assert client.get("/v1/incidents/check", params=check).status_code == 200
        assert client.get("/v1/incidents/check", params={"site_host": "bad/path"}, headers=plugin).status_code == 400  # not a 2xx lookup
        assert client.get("/en/search", headers={"User-Agent": "Mozilla/5.0"}).status_code == 200
        assert client.get("/en/search", headers={"User-Agent": "Mozilla/5.0", "CF-Connecting-IP": "198.51.100.23"}).status_code == 200
    with connect(settings) as conn:
        kinds = {row["client_type"]: row["n"] for row in conn.execute(
            "SELECT client_type,sum(requests)::bigint AS n FROM traffic_attribution_daily WHERE route_code='/v1/incidents/check' AND status_class='2xx' GROUP BY client_type")}
        assert kinds == {"plugin_hint": 4, "ahn_client_hint": 1, "mcp_hint": 1, "browser_hint": 1, "unknown": 1}
    expected = ("총 8건, 운영자 네트워크 제외 7건 — 플러그인 4→3 · ahn-client 1→1 · MCP 1→1 · 브라우저 1→1 · unknown 1→1 · 기타 0→0 "
                "(전체→운영자 제외). 웹 검색 페이지 요청 2→1건")
    with TestClient(create_monitor_app(owner_settings), client=("127.0.0.1", 4321)) as local:
        assert expected in local.get("/").text
    # The same line is on the hosted /owner page, behind the same gate, and carries no address or per-client key.
    with TestClient(create_app(owner_settings)) as client:
        page = client.get("/owner", headers={"CF-Connecting-IP": "198.51.100.23"})
        assert page.status_code == 200 and expected in page.text
        assert client.get("/owner", headers={"CF-Connecting-IP": "198.51.100.24"}).status_code == 404


def test_render_metrics_snapshot_and_client_key_retention(env):
    settings, _, _, _ = env
    now = datetime.now(timezone.utc)
    stamp = now.replace(minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")
    def reply(request):
        assert request.url.params["resource"] == "srv-test"
        return httpx.Response(200, json=[{
            "labels": [{"field": "service", "value": "srv-test"}],
            "unit": "percent" if request.url.path.endswith("cpu") else "count",
            "values": [{"timestamp": stamp, "value": 12.5}],
        }])
    with httpx.Client(transport=httpx.MockTransport(reply), base_url="https://api.render.com/v1") as fake:
        result = collect_render_metrics(settings.database_url, "test-key", "srv-test", now=now, client=fake)
    assert result == {"cpu": "ok", "memory": "ok", "http_requests": "ok"}
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM render_metric_samples").fetchone()["n"] == 3
        conn.execute("INSERT INTO traffic_client_daily (day,client_digest,client_type,requests) VALUES (CURRENT_DATE-20,repeat('a',64),'unknown',1)")
    prune_analytics(settings.database_url)
    with connect(settings) as conn:
        assert conn.execute("SELECT count(*) AS n FROM traffic_client_daily WHERE day=CURRENT_DATE-20").fetchone()["n"] == 0


def test_not_found_and_database_outage(env):
    _, client, _, _ = env
    missing = str(uuid4())
    response = client.get(f"/ko/incidents/{missing}")
    assert response.status_code == 404
    assert "이 공개 사건은 제공되지 않습니다" in response.text
    bad_url = make_conninfo(TEST_URL, port=1, connect_timeout=1)
    bad_settings = replace(env[0], database_url=bad_url)
    with TestClient(create_app(bad_settings), raise_server_exceptions=False) as unavailable:
        assert unavailable.get("/health").json() == {"status": "unavailable"}
        api = unavailable.get("/v1/incidents/check", params={"site_host": "example.org"})
        assert api.status_code == 503 and api.json() == {"error": "service_unavailable"}
        page = unavailable.get("/ko/search", params={"site_host": "example.org"})
        assert page.status_code == 503 and "잠시 후 다시 시도해 주세요" in page.text
        assert "postgresql" not in page.text.lower()
