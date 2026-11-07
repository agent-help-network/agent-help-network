"""The agents page Playwright MCP pairing snippet must match the canonical agent instructions."""
from __future__ import annotations

import html as htmllib
import re

import pytest
from fastapi.testclient import TestClient

from ahn.main import create_app
from ahn.settings import Settings


def _page(lang: str) -> str:
    client = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)))
    return client.get(f"/{lang}/agents").text


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_pairing_card_is_playwright_only(lang):
    page = _page(lang)
    card = page[page.index('id="browser-agents"'):]
    card = card[:card.index("</article>")]
    assert "Playwright" in card
    assert "Stagehand" not in card and "CrewAI" not in card
    assert "Cursor" in card


def test_pairing_paragraph_matches_canonical_instructions():
    page = _page("en")
    card = page[page.index('id="browser-agents"'):]
    pre = htmllib.unescape(re.findall(r"<pre class=\"ledger-code\"><code>(When an authorized.*?)</code>", card, re.S)[0])
    for needle in ("error_class", "failure_stage", "404", "robots.txt", "agent-instructions.txt", "once"):
        assert needle in pre, needle

@pytest.mark.parametrize("lang", ["en", "ko"])
def test_pairing_paragraph_covers_login_verification(lang):
    page = _page(lang)
    card = page[page.index('id="browser-agents"'):]
    card = card[:card.index("</article>")]
    start = "When an authorized" if lang == "en" else "승인된 웹 단계"
    pre = htmllib.unescape(re.findall(r"<pre class=\"ledger-code\"><code>(" + start + r".*?)</code>", card, re.S)[0])
    assert "error_class=login_auth" in pre
    if lang == "en":
        assert "sign-in" in pre and "verification" in pre
        assert "tell your user which step stopped you" in pre
        assert "never send passwords or codes" in pre
    else:
        assert "추가 인증" in pre and "어느 단계에서 막혔고" in pre
        assert "비밀번호나 코드는 보내지 말 것" in pre


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_when_card_mentions_login_failure(lang):
    page = _page(lang)
    when = page[page.index("03 / WHEN"):]
    when = when[:when.index("</article>")] if "</article>" in when else when[:3000]
    assert ("extra verification" in when) if lang == "en" else ("추가 인증" in when)
