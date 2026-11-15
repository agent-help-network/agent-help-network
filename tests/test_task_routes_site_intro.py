"""Task routes site intro (PR-G): home, human, agents, nav, the human starter prompt and /agent-instructions.txt, behind AHN_TASK_ROUTES (default off).

No database and no network. With the switch off the pages must not mention the new section at all.
"""

from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from html import unescape
from datetime import date
from pathlib import Path

import jinja2
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


# Flag off = the pages as they would be without the task routes blocks. Instead of pinning response hashes (which break on any
# unrelated template edit), render the same pages through the same Jinja environment twice: once from the templates as they are
# and once from their source with the `{% if task_routes %}...{% endif %}` blocks cut out. Both renders use the flag-off context.
OFF_SETTINGS = {
    "default": dict(public_mode=True, reporting_enabled=True),
    "no_reporting": dict(public_mode=True, reporting_enabled=False),
    "no_since": dict(public_mode=True, reporting_enabled=True, task_routes=True),  # switch on without a valid SINCE date: as if off
}
OFF_PAGES = ["", "human", "agents", "connect", "search", "policy", "disclaimer", "terms", "support", "contribute", "platforms", "why", "report"]
# Templates that gained one block each with the site intro. policy.html has its own, older flag-gated sentences; they are not cut.
BLOCKS_PER_TEMPLATE = {"base.html": 1, "home.html": 1, "human.html": 1, "agents.html": 1, "connect.html": 1}
OPEN = re.compile(r"(\s*)\{%(-?)\s*if\s+task_routes\s*(-?)%\}")
TAG = re.compile(r"\{%(-?)\s*(\w+)[^%]*?(-?)%\}")
OPENERS = {"if", "for", "block", "macro", "call", "filter", "with"}
JINJA = re.compile(r"\{%.*?%\}|\{\{.*?\}\}|\{#.*?#\}", re.DOTALL)
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
# The exact lines of each block as #239 wrote them (a `-` more or less changes the whitespace of the flag-off page).
SHAPES = {
    "base.html": re.compile(r"[ ]{8}<a href=\"/\{\{ lang \}\}/why\" [^\n]*</a>\{% if task_routes %\}\n[ ]{8}<a href=\"/\{\{ lang \}\}/routes\" [^\n]*</a>\{% endif %\}\n"),
    "home.html": re.compile(r"\{% if task_routes %\}\n(?:(?!\{%)[^\n]*\n)+\{% endif -%\}\n"),
    "human.html": re.compile(r"\{% if task_routes %\}\n(?:(?!\{%)[^\n]*\n)+\{% endif -%\}\n"),
    "agents.html": re.compile(r"[ ]{2}\{%- if task_routes %\}\n(?:(?!\{%)[^\n]*\n)+[ ]{2}\{% endif %\}\n"),
    "connect.html": re.compile(r"\{%- if task_routes %\}\n(?:(?!\{%)[^\n]*\n)+\{% endif -%\}\n"),
}


def find_blocks(source: str) -> list[dict]:
    """Locate each `{% if task_routes %}...{% endif %}` block (nested tags balanced)."""
    found, pos = [], 0
    while True:
        m = OPEN.search(source, pos)
        if not m:
            return found
        depth, scan = 1, m.end()
        while depth:
            t = TAG.search(source, scan)
            assert t, "unterminated task_routes block"
            scan = t.end()
            if t.group(2) in OPENERS:
                depth += 1
            elif t.group(2) in {"end" + w for w in OPENERS}:
                depth -= 1
        found.append(dict(open=m, body=source[m.end():t.start()], end=t, end_pos=scan))
        pos = scan


def cut_blocks(source: str) -> tuple[str, int]:
    """Remove each task_routes block, keeping the whitespace its `-` markers would not have stripped."""
    out, pos = [], 0
    blocks = find_blocks(source)
    for b in blocks:
        m, t, scan = b["open"], b["end"], b["end_pos"]
        trailing = re.match(r"\s*", source[scan:]).group(0)
        out.append(source[pos:m.start()] + ("" if m.group(2) == "-" else m.group(1)) + ("" if t.group(3) == "-" else trailing))
        pos = scan + len(trailing)
    out.append(source[pos:])
    return "".join(out), len(blocks)


def block_lines(source: str, block: dict) -> str:
    """The whole source lines the block touches, from the start of the opening line to the end of the closing line."""
    start = source.rfind("\n", 0, block["open"].start() + len(block["open"].group(1))) + 1
    end = source.find("\n", block["end_pos"])
    return source[start:end + 1]


class _Balance(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack, self.problems = [], []

    def handle_starttag(self, tag, attrs):
        if tag not in VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if tag in self.stack:
            while self.stack[-1] != tag:
                self.problems.append(f"<{self.stack.pop()}> is not closed before </{tag}>")
            self.stack.pop()
        else:
            self.problems.append(f"</{tag}> closes a tag opened outside the block")


def unbalanced_html(body: str) -> list[str]:
    """Problems in the block's own HTML once Jinja tags are removed: tags left open, or closing tags with no opener."""
    parser = _Balance()
    parser.feed(JINJA.sub("", body))
    parser.close()
    return parser.problems + [f"<{t}> is never closed" for t in parser.stack]


def _template_env():
    from ahn.main import templates
    return templates.env


def test_blocks_are_counted_shaped_and_self_contained():
    env = _template_env()
    total = 0
    for name, expected in BLOCKS_PER_TEMPLATE.items():
        source = env.loader.get_source(env, name)[0]
        blocks = find_blocks(source)
        assert len(blocks) == expected, f"{name}: {len(blocks)} task_routes blocks, expected {expected}"
        assert "task_routes" not in cut_blocks(source)[0], f"{name}: a task_routes reference is left outside the counted blocks"
        for block in blocks:
            lines = block_lines(source, block)
            assert SHAPES[name].fullmatch(lines), f"{name}: the block is not in the expected line shape:\n{lines}"
            assert unbalanced_html(block["body"]) == [], f"{name}: the block's HTML does not stand on its own"
        total += len(blocks)
    assert total == 5
    for name in env.list_templates():  # nothing else may start using the flag outside the cut blocks without being counted here
        if name not in BLOCKS_PER_TEMPLATE and name != "policy.html":
            assert "task_routes" not in env.loader.get_source(env, name)[0], name


def test_unbalanced_html_detection():
    assert unbalanced_html('<section id="x"><p>a<br><img src="y"></p></section>') == []
    assert unbalanced_html("<section><p>a</p>") == ["<section> is never closed"]
    assert unbalanced_html("<p>a</p></section>") == ["</section> closes a tag opened outside the block"]
    assert unbalanced_html('<a href="/{{ lang }}/x" {% if page == "x" %}aria-current="page"{% endif %}>{{ t.k }}</a>') == []


def test_cut_blocks_handles_whitespace_markers():
    assert cut_blocks("a {% if task_routes %}X{% endif %} b") == ("a  b", 1)
    assert cut_blocks("a {%- if task_routes %}X{% endif %} b") == ("a b", 1)
    assert cut_blocks("a {% if task_routes %}X{% endif -%} b") == ("a b", 1)
    assert cut_blocks("a {%- if task_routes -%}X{%- endif -%} b") == ("ab", 1)
    assert cut_blocks("a {% if x %}X{% endif %} b") == ("a {% if x %}X{% endif %} b", 0)


def _render_all(c: TestClient, paths: list[str]) -> dict[str, bytes]:
    out = {}
    for path in paths:
        response = c.get(path)
        assert response.status_code == 200, path
        out[path] = response.content.replace(b"\r\n", b"\n")
    return out


@pytest.mark.parametrize("name", sorted(OFF_SETTINGS))
def test_off_pages_equal_templates_without_the_blocks(name):
    env = _template_env()
    original_loader = env.loader
    cuts = {}

    def source_without_blocks(template_name):
        source, filename, uptodate = original_loader.get_source(env, template_name)
        if template_name in BLOCKS_PER_TEMPLATE:
            source, found = cut_blocks(source)
            cuts[template_name] = found
        return source, filename, uptodate

    c = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, **OFF_SETTINGS[name])))
    paths = [f"/{lang}/{page}" for lang in ("en", "ko") for page in OFF_PAGES]
    try:
        env.cache.clear()
        as_is = _render_all(c, paths)
        env.loader = jinja2.FunctionLoader(source_without_blocks)
        env.cache.clear()
        without_blocks = _render_all(c, paths)
    finally:
        env.loader = original_loader
        env.cache.clear()
    assert cuts == BLOCKS_PER_TEMPLATE, "every template with a block must have been loaded and cut"
    for path in paths:
        assert as_is[path] == without_blocks[path], f"{name} {path}: the flag-off page differs from the template without its task_routes blocks"


def test_off_text_responses_carry_no_additions():
    from ahn.main import LLMS_TXT
    c = client(False)
    assert c.get("/llms.txt").text == LLMS_TXT + "\n"
    assert c.get("/agent-instructions.txt").text == AGENT_PROMPT + "\n"


def test_off_sitemap_has_no_routes():
    for lang in ("en", "ko"):
        for kwargs in OFF_SETTINGS.values():
            body = TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, **kwargs))).get("/sitemap.xml").text
            assert "/routes" not in body
