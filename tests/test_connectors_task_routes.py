"""Task routes in the connection means (PR-I1): MCP instructions, Claude Code plugin, ChatGPT app manifest,
install documents and the connect page. Everything is conditional on the tool being served; no write path is announced."""

import json
import os
from pathlib import Path

from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.main import create_app
from ahn.settings import Settings
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "claude-code" / "ahn"
CHATGPT = ROOT / "plugins" / "agent-help-network" / "plugin.json"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def flat(text: str) -> str:
    return " ".join(text.split())


def test_claude_code_manifest_and_marketplace_carry_the_same_task_routes_phrase():
    manifest = json.loads(read(PLUGIN / ".claude-plugin" / "plugin.json"))
    entry = json.loads(read(ROOT / ".claude-plugin" / "marketplace.json"))["plugins"][0]
    assert manifest["version"] == entry["version"] == "0.3.18"
    phrase = "If the bundled server lists find_task_route, it can also return recorded task routes for a site (records, not advice)."
    assert phrase in manifest["description"] and phrase in entry["description"]


def test_skill_uses_the_tool_only_when_listed_and_the_readme_says_so():
    skill = flat(read(PLUGIN / "skills" / "ahn-lookup" / "SKILL.md"))
    section = skill.split("## Task routes", 1)[1].split("## Reporting", 1)[0]
    assert "only when it is in your tool list" in section and "carry on as usual" in section
    assert "Not a guarantee and not advice" in section and "The hook never calls it" in section
    readme = flat(read(PLUGIN / "README.md"))
    assert "## Task routes (from 0.3.18)" in read(PLUGIN / "README.md")
    assert "only when the server lists it" in readme and "never calls `find_task_route`" in readme


def test_the_hook_is_unchanged_and_drops_keys_it_does_not_know():
    import importlib.util

    spec = importlib.util.spec_from_file_location("ahn_lookup_hook", PLUGIN / "hooks" / "ahn_lookup.py")
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    answer = {"recommendation": {"action": "use_official_path", "confidence": "high"}, "access_profile": None,
              "official_paths": None, "provider_status": None, "evidence_level": "no_reports", "next_checks": []}
    extra = {**answer, "task_routes_available": True, "task_routes": [{"route": "x"}], "find_task_route": "https://x.example/"}
    assert hook.summary("example.com", extra, "https://x") == hook.summary("example.com", answer, "https://x")
    source = read(PLUGIN / "hooks" / "ahn_lookup.py") + read(PLUGIN / "hooks" / "hooks.json")
    assert "task_route" not in source


def test_chatgpt_manifest_has_the_line_and_the_next_patch_version():
    manifest = json.loads(read(CHATGPT))
    interface = manifest["extensions"]["com.openai"]["interface"]
    assert manifest["version"] == "0.1.7"
    assert "Read recorded task routes for a site, where the server lists them" in interface["capabilities"]
    assert interface["longDescription"].endswith("It is an independent service, not affiliated with the sites it describes.")
    assert "Where the server also lists task routes" in interface["longDescription"] and "records and not advice" in interface["longDescription"]


def test_connect_page_paragraph_follows_the_switch():
    def page(lang, on):
        kwargs = dict(public_mode=True, reporting_enabled=True)
        if on:
            kwargs.update(task_routes=True, task_routes_since=date(2026, 11, 20))
        return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, **kwargs))).get(f"/{lang}/connect").text

    for lang in ("en", "ko"):
        assert "find_task_route" not in page(lang, False) and "/v1/task-routes" not in page(lang, False)
        on_ = page(lang, True)
        assert "find_task_route" in on_ and "/v1/task-routes?site_host=ikea.com" in on_
    assert "records, not a guarantee and not advice" in page("en", True)
    assert "find_task_route does not change what check_incident recommends." in page("en", True)
    assert "check_incident 의 권고도 바꾸지 않습니다." in page("ko", True)


def test_install_documents_name_the_tool_conditionally_and_no_write_path():
    install = flat(read(ROOT / "llms-install.md"))
    cursor = flat(read(ROOT / "plugins" / "cursor" / "agent-help-network" / "README.md"))
    assert "If `find_task_route` is listed, call it once when you start a customer task on a site" in install and "document submission" in install
    assert install.index("Declared policy is not an access guarantee.") < install.index("If `find_task_route` is listed") < install.index("## Tool notes")
    assert "Blocked, or not sure" not in read(ROOT / "ahn" / "mcp_task_routes.py") and "If it is not listed, carry on as usual" in install
    assert "If the server also lists `find_task_route`, call it once when you start a customer task on a site" in cursor and "If it is not listed, carry on as usual" in cursor
    assert "does not change when to call `check_incident` or what `check_incident` recommends" in install
    assert "does not change when to call `check_incident` or what `check_incident` recommends" in cursor
    assert "`find_task_route` does not change when to call `check_incident` or what `check_incident` recommends. The hook is unchanged" in read(PLUGIN / "README.md")
    skill = read(PLUGIN / "skills" / "ahn-lookup" / "SKILL.md")
    assert "When the find_task_route tool is listed, that tool returns recorded task routes for a site (records, not advice)." in skill
    assert "The answer never changes check_incident's `recommendation.action`." in skill
    from ahn.mcp_task_routes import INSTRUCTIONS_ADDENDUM
    assert INSTRUCTIONS_ADDENDUM.endswith("The answer does not change when to call check_incident or what check_incident recommends.")
    assert "Call find_task_route once when you start a customer task" in INSTRUCTIONS_ADDENDUM and "when a web step is blocked" not in INSTRUCTIONS_ADDENDUM
    connectors = [ROOT / "llms-install.md", CHATGPT, ROOT / ".claude-plugin" / "marketplace.json",
                  PLUGIN / ".claude-plugin" / "plugin.json", PLUGIN / "README.md", PLUGIN / "skills" / "ahn-lookup" / "SKILL.md",
                  ROOT / "plugins" / "cursor" / "agent-help-network" / "README.md", ROOT / "ahn" / "templates" / "connect.html"]
    for path in connectors:
        text = read(path)
        assert "report_task_route" not in text, path
