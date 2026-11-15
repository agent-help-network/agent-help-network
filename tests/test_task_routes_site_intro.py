"""Task routes site intro (PR-G): home, human, agents, nav, the human starter prompt and /agent-instructions.txt, behind AHN_TASK_ROUTES (default off).

No database and no network. With the switch off the pages must not mention the new section at all.
"""

from __future__ import annotations

import hashlib
import json
import os
from html import unescape
from datetime import date
from pathlib import Path

import pytest
from markupsafe import escape
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.main import AGENT_PROMPT, AGENT_PROMPT_TASK_ROUTES, LANG, create_app
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
SINCE = date(2026, 11, 20)
PAGES = ("/", "/human", "/agents")
NEW_KEYS = ("routes_intro_title", "routes_intro_text", "nav_routes")


def client(task_routes_on: bool) -> TestClient:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=task_routes_on,
                        task_routes_since=SINCE if task_routes_on else None)
    return TestClient(create_app(settings))  # no `with`: the lifespan (database) never runs


@pytest.fixture
def on():
    return client(True)


@pytest.fixture
def off():
    return client(False)


def test_new_keys_exist_in_both_languages():
    for lang in ("en", "ko"):
        for key in NEW_KEYS:
            assert LANG[lang][key].strip()


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("page", PAGES)
def test_off_has_no_section_and_no_nav_link(off, lang, page):
    body = off.get(f"/{lang}{page}").text
    assert 'id="task-routes"' not in body
    assert f'href="/{lang}/routes"' not in body
    assert LANG[lang]["routes_intro_title"] not in body
    assert LANG[lang]["routes_intro_text"] not in body


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("page", PAGES)
def test_on_shows_section_and_link(on, lang, page):
    response = on.get(f"/{lang}{page}")
    assert response.status_code == 200
    body = response.text
    assert body.count('id="task-routes"') == 1
    assert escape(LANG[lang]["routes_intro_title"]) in body
    assert escape(LANG[lang]["routes_intro_text"]) in body
    assert f'href="/{lang}/routes"' in body


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_on_nav_link_and_current_page(on, lang):
    nav = on.get(f"/{lang}/agents").text.split("<nav", 1)[1].split("</nav>", 1)[0]
    assert f'<a href="/{lang}/routes" >{LANG[lang]["nav_routes"]}</a>' in nav
    assert 'aria-current="page"' in on.get(f"/{lang}/routes").text.split("<nav", 1)[1].split("</nav>", 1)[0].split(f'href="/{lang}/routes"', 1)[1]


def test_existing_home_copy_is_kept_when_on(on, off):
    for lang, marker in (("en", "Six answers your agent can act on"), ("ko", "에이전트가 바로 따를 수 있는 여섯 가지 응답")):
        assert on.get(f"/{lang}/").text.count(marker) == 1
        assert off.get(f"/{lang}/").text.count(marker) == 1


def test_intro_is_autoescaped_and_states_no_guarantee():
    # The strings are rendered through Jinja autoescape; the apostrophe in the English text comes out as an entity.
    body = client(True).get("/en/").text
    section = body.split('id="task-routes"', 1)[1].split("</section>", 1)[0]
    assert "provider's status" not in section and "provider&#39;s status" in section
    for lang in ("en", "ko"):
        text = LANG[lang]["routes_intro_text"]
        for banned in ("검증", "verified", "여러 사용자", "many users", "보장해", "we guarantee", "guaranteed"):
            assert banned not in text
    assert "records, not guarantees or advice" in LANG["en"]["routes_intro_text"]
    assert "보장이나 조언이 아니며" in LANG["ko"]["routes_intro_text"]
    assert "report" not in LANG["en"]["routes_intro_text"].lower()  # writing is not announced


def test_agent_instructions_unchanged_when_off(off):
    response = off.get("/agent-instructions.txt")
    assert response.text == AGENT_PROMPT + "\n"
    assert "task-routes" not in response.text


def test_agent_instructions_appended_when_on(on):
    text = on.get("/agent-instructions.txt").text
    assert text == AGENT_PROMPT + "\n\n" + AGENT_PROMPT_TASK_ROUTES + "\n"
    assert AGENT_PROMPT_TASK_ROUTES == (ROOT / "ahn" / "agent_instructions_task_routes.txt").read_text(encoding="utf-8").strip()
    assert "/v1/task-routes" in text and "find_task_route" in text
    assert "not guarantees and not advice" in text
    assert "POST" not in AGENT_PROMPT_TASK_ROUTES  # no write path is described


@pytest.mark.parametrize("on_flag", [False, True])
def test_human_starter_prompt_matches_agent_instructions(on_flag):
    c = client(on_flag)
    expected = c.get("/agent-instructions.txt").text.strip()
    for lang in ("en", "ko"):
        page = c.get(f"/{lang}/human").text
        textarea = page.split('id="agent-start-prompt"', 1)[1].split(">", 1)[1].split("</textarea>", 1)[0]
        assert unescape(textarea).strip() == expected


def test_llms_txt_is_the_same_on_and_off(on, off):
    assert on.get("/llms.txt").text == off.get("/llms.txt").text


# Whole-response comparison with the pages as they were before this change (sha256 of each body, CRLF read as LF).
# The baseline file was produced from the commit this branch started from. If a later change to these pages is
# intended, regenerate it with the same loop below and review the diff.
BASELINE = json.loads((ROOT / "tests" / "task_routes_off_baseline.json").read_text(encoding="utf-8"))
OFF_SETTINGS = {
    "default": dict(public_mode=True, reporting_enabled=True),
    "no_reporting": dict(public_mode=True, reporting_enabled=False),
    "no_since": dict(public_mode=True, reporting_enabled=True, task_routes=True),  # switch on without a valid SINCE date: as if off
}
OFF_PAGES = ["", "human", "agents", "connect", "search", "policy", "disclaimer", "terms", "support", "contribute", "platforms", "why", "report"]


def _digest(body: bytes) -> str:
    return hashlib.sha256(body.replace(b"\r\n", b"\n")).hexdigest()


@pytest.mark.parametrize("name", sorted(OFF_SETTINGS))
def test_off_responses_are_byte_identical_to_before(name):
    c = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, **OFF_SETTINGS[name])))
    paths = [f"/{lang}/{page}" for lang in ("en", "ko") for page in OFF_PAGES] + ["/llms.txt", "/agent-instructions.txt", "/sitemap.xml"]
    assert len([k for k in BASELINE if k.startswith(name + " ")]) == len(paths)
    for path in paths:
        response = c.get(path)
        assert response.status_code == 200, path
        assert _digest(response.content) == BASELINE[f"{name} {path}"], f"{name} {path} differs from the pre-change response"
