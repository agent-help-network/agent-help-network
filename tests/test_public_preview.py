"""Public preview behavior without a live database."""

import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.main import create_app
from ahn.settings import Settings


def test_public_preview_exposes_reads_only_and_enforces_request_cap():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        api = client.get("/openapi.json").json()
        assert "/v1/incidents/check" in api["paths"]
        assert "/v1/observations" not in api["paths"]
        assert "read-only preview" in client.get("/en/connect").text.lower()
        assert "읽기 전용" in client.get("/ko/connect").text
        rejected = client.post("/v1/observations", json={})
        assert rejected.status_code in (400, 403)
        # Crossing a limiter window can permit another full bucket. More than
        # two buckets in quick succession still has to hit the cap.
        responses = [client.get("/en/") for _ in range(241)]
        limited = next(response for response in responses if response.status_code == 429)
        assert limited.headers["retry-after"] == "60"


def test_public_discovery_points_to_read_only_connections_without_stack_details():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        proof = client.get("/.well-known/mcp-registry-auth")
        assert proof.status_code == 200
        assert proof.text.startswith("v=MCPv1; k=ed25519; p=")
        assert "text/plain" in proof.headers["content-type"]
        challenge = client.get("/.well-known/openai-apps-challenge")
        assert challenge.status_code == 200
        expected_challenge = (Path(__file__).resolve().parents[1] / "ahn" / "openai_apps_challenge").read_text(encoding="ascii").strip()
        assert challenge.text.strip() == expected_challenge
        robots = client.get("/robots.txt")
        assert robots.status_code == 200
        assert "https://agenthelpnetwork.com/sitemap.xml" in robots.text
        sitemap = client.get("/sitemap.xml")
        assert sitemap.status_code == 200
        assert "https://agenthelpnetwork.com/en/agents" in sitemap.text
        assert "https://agenthelpnetwork.com/en/human" in sitemap.text
        assert "https://agenthelpnetwork.com/ko/connect" in sitemap.text
        assert "https://agenthelpnetwork.com/en/disclaimer" in sitemap.text
        assert "https://agenthelpnetwork.com/ko/disclaimer" in sitemap.text
        assert "https://agenthelpnetwork.com/en/terms" in sitemap.text
        assert "https://agenthelpnetwork.com/ko/support" in sitemap.text
        for lang in ("en", "ko"):
            home = client.get(f"/{lang}/").text
            human = client.get(f"/{lang}/human").text
            connect = client.get(f"/{lang}/connect").text
            assert "PostgreSQL" not in home
            assert "https://agenthelpnetwork.com/openapi.json" in connect
            assert "https://agenthelpnetwork.com/mcp" in connect
            assert home.index('class="language"') > home.index('<footer class="site-footer">')
            assert 'hreflang="en"' in home
            assert 'hreflang="ko"' in home
            assert ("Access observations for web tasks" if lang == "en" else "웹 작업을 위한 접근 관찰 기록") in home
            assert 'class="audience-switch"' in home
            assert 'class="ledger-search"' in home and 'name="site_host"' in home
            assert f'href="/{lang}/search?site_host=cloudflare.com"' in home
            assert 'name="site_host"' in human and 'class="audience-switch"' in human
            assert f'href="/{lang}/search?site_host=cloudflare.com"' in human
            assert "GET /v1/incidents/check?site_host=example.org" in home
            assert (("current status is unknown" in human.lower()) if lang == "en" else ("현재 상태는 미상" in human))
            policy = client.get(f"/{lang}/policy").text
            assert '<div class="audience-switch"' in policy
            assert 'class="active"' not in policy
            disclaimer = client.get(f"/{lang}/disclaimer")
            assert disclaimer.status_code == 200
            assert f'href="/{lang}/disclaimer"' in home
            assert ("Published agent reports are unverified" if lang == "en" else "미검증 관찰") in disclaimer.text
            terms = client.get(f"/{lang}/terms")
            support = client.get(f"/{lang}/support")
            assert terms.status_code == 200
            assert support.status_code == 200
            assert "support@agenthelpnetwork.com" in terms.text
            assert "support@agenthelpnetwork.com" in support.text
            assert f'href="/{lang}/terms"' in home
            assert f'href="/{lang}/support"' in home


def test_report_prefill_only_accepts_a_public_hostname():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            page = client.get(f"/{lang}/report", params={"site_host": "example.org"})
            assert page.status_code == 200
            assert 'name="site_host" value="example.org"' in page.text
            assert 'name="attempt_source" required' in page.text
            assert 'name="error_class" required' in page.text
            assert 'name="outcome" required' in page.text
            assert '<details class="report-more">' in page.text
            invalid = client.get(f"/{lang}/report", params={"site_host": "secret.example.org/path?token=1"})
            assert 'name="site_host" value=""' in invalid.text
            assert "secret.example.org/path" not in invalid.text


def test_query_validation_explains_allowed_values_without_reflecting_input():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        invalid = client.get("/v1/incidents/check", params={"site_host": "example.org", "error_class": "private_secret_value"})
        assert invalid.status_code == 400
        assert invalid.json() == {"error": "invalid_request", "field": "error_class",
                                  "allowed_values": ["site_access", "login_auth", "human_verification", "service_error"]}
        assert "private_secret_value" not in invalid.text
        invalid_stage = client.get("/v1/incidents/check", params={"site_host": "example.org", "failure_stage": "private_secret_value"})
        assert invalid_stage.status_code == 400
        assert invalid_stage.json()["field"] == "failure_stage"
        assert "private_secret_value" not in invalid_stage.text


def test_connect_page_install_section_in_both_languages():
    import base64
    import html
    import json
    import re

    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        pages = {lang: client.get(f"/{lang}/connect") for lang in ("en", "ko")}
        for lang, page in pages.items():
            assert page.status_code == 200
            text = page.text
            assert 'id="install"' in text
            assert "claude plugin marketplace add agent-help-network/agent-help-network" in text
            assert "claude plugin install ahn@agent-help-network" in text
            assert "HostedMCPTool" in text and '"allowed_tools": ["check_incident"]' in text
            assert '"mcpServers"' in text and "https://agenthelpnetwork.com/mcp" in text
            for doc in ("https://code.claude.com/docs/en/plugin-marketplaces",
                        "https://cursor.com/docs/context/mcp/install-links",
                        "https://openai.github.io/openai-agents-python/mcp/"):
                assert doc in text
            link = re.search(r'href="(cursor://anysphere\.cursor-deeplink/mcp/install\?[^"]+)"', text)
            assert link, lang
            query = html.unescape(link.group(1)).split("?", 1)[1]
            params = dict(part.split("=", 1) for part in query.split("&"))
            assert params["name"] == "agent-help-network"
            assert json.loads(base64.b64decode(params["config"])) == {"url": "https://agenthelpnetwork.com/mcp"}
        assert "Install" in pages["en"].text and "Add to Cursor" in pages["en"].text
        assert "설치" in pages["ko"].text and "Cursor 에 추가" in pages["ko"].text
        assert "Four ways" not in pages["ko"].text
        # The install text never presents these as official integrations of any vendor.
        assert "none is an official integration" in pages["en"].text.lower()
        assert "공식 통합이 아니" in pages["ko"].text


def test_registry_manifest_matches_the_published_schema_limits():
    import json

    manifest = json.loads((Path(__file__).resolve().parents[1] / "server.json").read_text(encoding="utf-8"))
    assert manifest["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    assert 1 <= len(manifest["description"]) <= 100  # schema maxLength for the official registry
    assert manifest["version"] == "0.1.1"
    assert manifest["repository"]["source"] == "github"
    assert manifest["repository"]["url"] == "https://github.com/agent-help-network/agent-help-network"
    assert manifest["remotes"] == [{"type": "streamable-http", "url": "https://agenthelpnetwork.com/mcp"}]
