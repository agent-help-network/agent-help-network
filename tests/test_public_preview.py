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
            assert ("Your agent hit a 403." if lang == "en" else "에이전트가 403 에 막혔습니까?") in home
            assert 'class="audience-switch"' in home
            assert 'class="ledger-search"' in home and 'name="site_host"' in home
            assert f'href="/{lang}/search?site_host=cloudflare.com"' in home
            assert 'name="site_host"' in human and 'class="audience-switch"' in human
            assert f'href="/{lang}/search?site_host=cloudflare.com"' in human
            assert "claude plugin install ahn@agent-help-network" in home and "pip install ahn-client" in home
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


def test_connect_page_embeds_the_pinned_demo_video_without_autoplay():
    import re

    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    base = "https://cdn.jsdelivr.net/gh/agent-help-network/agent-help-network@[0-9a-f]{40}/docs/media/agent-help-network-demo-v4"
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            page = client.get(f"/{lang}/connect")
            assert page.status_code == 200
            video = re.search(r"<video\b[^>]*>", page.text)
            assert video, lang
            tag = video.group(0)
            assert "controls" in tag and 'preload="metadata"' in tag and 'crossorigin="anonymous"' in tag
            assert "autoplay" not in tag
            assert re.search(rf'<source src="{base}\.webm"', page.text)
            assert re.search(rf'<track kind="captions" srclang="en" label="English" src="{base}\.vtt">', page.text)
            # The video has burned-in captions, so the track stays off until the viewer turns it on.
            assert "default" not in re.search(r"<track\b[^>]*>", page.text).group(0)
            assert "<figcaption>" in page.text
            # The page sets no Content-Security-Policy, so the CDN needs no allow-list entry.
            assert "content-security-policy" not in page.headers
    with TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=False))) as client:
        assert "<video" not in client.get("/en/connect").text


def test_registry_manifest_matches_the_published_schema_limits():
    import json

    manifest = json.loads((Path(__file__).resolve().parents[1] / "server.json").read_text(encoding="utf-8"))
    assert manifest["$schema"] == "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"
    assert 1 <= len(manifest["description"]) <= 100  # schema maxLength for the official registry
    assert manifest["version"] == "0.1.3"  # registry metadata is immutable per version: bump on every description change
    assert manifest["repository"]["source"] == "github"
    assert manifest["repository"]["url"] == "https://github.com/agent-help-network/agent-help-network"
    assert manifest["remotes"] == [{"type": "streamable-http", "url": "https://agenthelpnetwork.com/mcp"}]


def test_privacy_policy_covers_review_topics_and_tool_fields():
    # OpenAI plugin review asks for data collected, purposes, recipients,
    # retention and user controls that match the current tool inputs and outputs.
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)
    with TestClient(create_app(settings)) as client:
        en = client.get("/en/policy").text
        ko = client.get("/ko/policy").text
    for heading in ("Who we are", "Data we collect", "What the tools return", "How we use data", "Who receives data", "Retention", "Your controls"):
        assert f"<h2>{heading}</h2>" in en
    for heading in ("운영 주체", "수집하는 데이터", "도구가 돌려주는 것", "이용 목적", "데이터를 받는 곳", "보존 기간", "이용자의 선택"):
        assert f"<h2>{heading}</h2>" in ko
    for field in ("site_host", "error_class", "task_step", "failure_stage", "check_incident", "report_observation"):
        assert field in en and field in ko
    for recipient in ("Cloudflare", "Render", "Porkbun"):
        assert recipient in en and recipient in ko
    assert "14 days" in en and "90 days" in en and "30 days" in en
    assert "<title>Privacy policy" in en


def test_policy_omits_report_tool_when_reporting_is_off():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        en = client.get("/en/policy").text
    assert "check_incident" in en
    assert "report_observation" not in en


def test_operator_name_is_shown_only_when_configured():
    plain = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)
    named = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, operator_name="Example Operator")
    with TestClient(create_app(plain)) as client:
        for page in ("policy", "terms", "support"):
            assert "operated by" not in client.get(f"/en/{page}").text
    with TestClient(create_app(named)) as client:
        for lang in ("en", "ko"):
            for page in ("policy", "terms", "support"):
                assert "Example Operator" in client.get(f"/{lang}/{page}").text


def test_operator_name_from_env_rejects_control_characters(monkeypatch):
    import pytest

    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.setenv("AHN_OPERATOR_NAME", "  Example   Operator ")
    assert Settings.from_env().operator_name == "Example Operator"
    monkeypatch.setenv("AHN_OPERATOR_NAME", "Bad\x07Name")
    with pytest.raises(RuntimeError):
        Settings.from_env()
    monkeypatch.setenv("AHN_OPERATOR_NAME", "x" * 81)
    with pytest.raises(RuntimeError):
        Settings.from_env()


def test_report_retention_setting_changes_policy_copy(monkeypatch):
    import pytest

    plain = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)
    timed = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, report_retention_days=365)
    with TestClient(create_app(plain)) as client:
        en = client.get("/en/policy").text
        assert "Reports have no automatic deletion date" in en
        assert "no automatic deletion schedule yet" in en
    with TestClient(create_app(timed)) as client:
        en = client.get("/en/policy").text
        ko = client.get("/ko/policy").text
    assert "Reports are deleted 365 days after they are received" in en
    assert "no automatic deletion schedule yet" not in en
    assert "접수 365일 뒤 삭제" in ko
    assert "자동 삭제 일정은 아직 없습니다" not in ko

    from ahn.settings import report_retention_days_from_env

    monkeypatch.delenv("AHN_REPORT_RETENTION_DAYS", raising=False)
    assert report_retention_days_from_env() == 0
    monkeypatch.setenv("AHN_REPORT_RETENTION_DAYS", " 365 ")
    assert report_retention_days_from_env() == 365
    for bad in ("7", "99999", "a year"):
        monkeypatch.setenv("AHN_REPORT_RETENTION_DAYS", bad)
        with pytest.raises(RuntimeError):
            report_retention_days_from_env()


def test_report_pruning_is_off_without_a_retention_period():
    from ahn.platform_metrics import prune_reports

    class NoQueries:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("pruning must not touch the database when retention is off")

    assert prune_reports(NoQueries(), 0) == 0


def test_agentboard_verify_path_serves_only_the_exact_handle():
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        ok = client.get("/.well-known/agentboard-verify/agenthelpnetworkhelper")
        assert ok.status_code == 200
        assert ok.text == "agenthelpnetworkhelper\n"
        assert ok.headers["content-type"] == "text/plain; charset=utf-8"
        assert ok.headers["cache-control"] == "public, max-age=300"
        for other in ("someoneelse", "AgentHelpNetworkHelper", "agenthelpnetworkhelper2", ""):
            assert client.get(f"/.well-known/agentboard-verify/{other}").status_code == 404
        # Existing well-known paths are unaffected.
        assert client.get("/.well-known/mcp-registry-auth").status_code == 200
        assert client.get("/.well-known/openai-apps-challenge").status_code == 200


CARD = {"site_host": "github.com", "summary_line": "An official developer path is listed for this site.", "do": "use_official_path",
        "url": "docs.github.com/en/rest", "confidence": "medium", "robots_status": "ok", "llms_txt_status": "present",
        "status": "none", "read_on": "2026-10-22"}


def _main(html: str) -> str:
    return html[html.index("<main"):html.index("</main>")]


def test_home_shows_the_real_answer_card_only_when_the_cache_gave_one(monkeypatch):
    calls = []
    monkeypatch.setattr("ahn.main.home_example", lambda _settings: calls.append(1) or dict(CARD))
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            page = client.get(f"/{lang}/").text
            assert 'class="home-live"' in page
            assert "use_official_path" in page and "docs.github.com/en/rest" in page and "2026-10-22" in page
            assert "robots ok · llms.txt present · status: none" in page
            assert "home-hero-solo" not in page
        assert len(calls) == 1  # kept for a minute in the process, not read per page view
    monkeypatch.setattr("ahn.main.home_example", lambda _settings: None)
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            page = client.get(f"/{lang}/").text
            assert page.count("home-live") == 0 and "Bytespider" not in page and "home-hero-solo" in page
            assert 'class="ledger-search"' in page and "claude plugin install" in page


def test_home_survives_a_failing_cache_read_and_makes_no_lookup(monkeypatch):
    def boom(_settings):
        raise RuntimeError("database down")
    monkeypatch.setattr("ahn.main.home_example", boom)
    monkeypatch.setattr("ahn.main.check", lambda *a, **k: (_ for _ in ()).throw(AssertionError("home must not run a lookup")))
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        page = client.get("/en/")
        assert page.status_code == 200 and "home-live" not in page.text


def test_home_layout_and_policy_paragraphs_moved_to_the_agents_page(monkeypatch):
    monkeypatch.setattr("ahn.main.home_example", lambda _settings: None)
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            home = _main(client.get(f"/{lang}/").text)
            agents = client.get(f"/{lang}/agents").text
            assert "02 / " not in home and "ledger-access-grid" not in home
            for name in ("stop_declared_block", "use_official_path", "hand_off_to_human", "wait_provider_incident", "proceed", "unknown"):
                assert f"<code>{name}</code>" in home
            for text in ("tab-claude", "tab-mcp", "tab-python", "tab-rest", "https://agenthelpnetwork.com/mcp", 'ahn-client[langchain]'):
                assert text in home
            assert "robots_wildcard_disallow" not in home and "QUERIED" not in home
            assert f'href="/{lang}/agents#how-we-read"' in home and 'id="how-we-read"' in agents
            assert "robots_wildcard_disallow" in agents and "ledger-note" not in agents
            assert f'href="/{lang}/connect#demo"' in home
            if lang == "ko":
                for ending in ("세요", "나요", "까요", "어요", "아요", "해요"):
                    assert ending not in home, ending


def test_home_links_never_start_a_counted_lookup_for_a_crawler(monkeypatch):
    # /v1/incidents/check and /{lang}/search both run check(), which registers the host and counts a lookup, so a crawler
    # following a home link would be counted. The card links to the static OpenAPI document instead, and the example
    # hostnames are nofollow.
    import re
    monkeypatch.setattr("ahn.main.home_example", lambda _settings: dict(CARD))
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)
    with TestClient(create_app(settings)) as client:
        for lang in ("en", "ko"):
            page = _main(client.get(f"/{lang}/").text)
            assert "/v1/incidents/check?" not in page.replace("GET /v1/incidents/check?site_host=example.org", "").replace(
                "https://agenthelpnetwork.com/v1/incidents/check?site_host=github.com", "")
            card = page[page.index('class="home-live"'):page.index("</aside>")]
            assert 'href="/openapi.json"' in card and "site_host" not in card
            for tag in re.findall(r"<a [^>]*search\?site_host=[^>]*>", page):
                assert 'rel="nofollow"' in tag, tag
