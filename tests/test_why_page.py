"""D108: the /en/why and /ko/why form, its script, and the one extra line a failed check_incident call gets. No database, no network."""

import asyncio
import json
import os
import re
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

import test_d63_decision_order as d63
from ahn import explain as E, explain_text as X, mcp_adapter
from ahn.main import create_app
from ahn.models import CheckResponse
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "ahn/static/why.js").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "ahn/templates/why.html").read_text(encoding="utf-8")
CHOICES = {
    "agent_platform": E.PLATFORMS, "agent_env": E.AGENT_ENVS, "external_browser": E.EXTERNAL_BROWSER, "stop_point": E.STOP_POINTS,
    "screen_message": E.SCREEN_MESSAGES, "since_kind": E.SINCE_KINDS, "task_context": E.TASK_CONTEXTS,
}


def client():
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)))


@pytest.fixture(scope="module")
def pages():
    with client() as c:
        return {lang: c.get(f"/{lang}/why") for lang in ("en", "ko")}


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_page_renders_in_both_languages(pages, lang):
    page = pages[lang]
    assert page.status_code == 200 and f'<html lang="{lang}">' in page.text
    assert '<script defer src="/static/why.js?v=1"></script>' in page.text
    assert 'id="why-form"' in page.text and f'data-lang="{lang}"' in page.text
    assert page.headers["content-type"].startswith("text/html")


def test_an_unknown_language_is_a_404():
    with client() as c:
        assert c.get("/xx/why").status_code == 404


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_nav_links_to_the_page_and_the_sitemap_lists_it(lang):
    with client() as c:
        home = c.get(f"/{lang}/platforms").text
        sitemap = c.get("/sitemap.xml").text
    assert f'<a href="/{lang}/why"' in home
    assert "https://agenthelpnetwork.com/en/why" in sitemap and "https://agenthelpnetwork.com/ko/why" in sitemap


def test_the_page_is_the_current_page_in_its_own_nav(pages):
    assert re.search(r'<a href="/en/why" aria-current="page">', pages["en"].text)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_form_has_no_free_text_except_one_hostname(pages, lang):
    html = pages[lang].text
    assert "<textarea" not in html
    inputs = re.findall(r"<input\b[^>]*>", html)
    texts = [i for i in inputs if 'type="date"' not in i and 'type="checkbox"' not in i and 'type="hidden"' not in i]
    assert len(texts) == 1 and 'id="why-host"' in texts[0] and 'name="site_host"' in texts[0]
    # the one other input is the day of "it worked before" (a date picker), shown only for that choice
    assert [i for i in inputs if 'type="date"' in i] and 'id="why-since-date-row" hidden' in html


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_every_select_offers_exactly_the_codes_the_endpoint_accepts(pages, lang):
    html = pages[lang].text
    for name, allowed in CHOICES.items():
        select = re.search(rf'<select id="why-{name}" name="{name}">(.*?)</select>', html, re.S)
        assert select, name
        values = re.findall(r'<option value="([^"]*)"', select.group(1))
        assert values[0] == "" and sorted(values[1:]) == sorted(allowed) and len(values) == len(set(values)), name


def test_the_script_names_the_same_fields_as_the_form():
    listed = re.search(r"const CHOICE_FIELDS = \[(.*?)\];", JS, re.S).group(1)
    assert re.findall(r'"(\w+)"', listed) == list(CHOICES)


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_page_hands_the_script_the_service_tables_for_cutting_a_subdomain(pages, lang):
    from ahn.service import PUBLIC_AGGREGATE_TLDS, PUBLIC_SUFFIXES_2

    raw = re.search(r"data-rules='([^']*)'", pages[lang].text).group(1)
    rules = json.loads(unescape(raw))
    assert set(rules["suffixes2"]) == PUBLIC_SUFFIXES_2 and set(rules["tlds"]) == PUBLIC_AGGREGATE_TLDS


def test_the_script_never_puts_a_string_in_as_markup():
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "localStorage", "sessionStorage", "document.cookie"):
        assert banned not in JS, banned
    # the only request is the lookup, with a GET and no body
    assert JS.count("fetch(") == 1 and 'fetch("/v1/why?"' in JS


def test_the_script_sends_only_the_hostname_the_choices_and_the_language():
    sent = set(re.findall(r'params\.set\("(\w+)"', JS)) | {"agent_platform"}
    assert sent == {"site_host", "lang", "since_date", "agent_platform"}


def test_the_lookups_own_sentence_is_shown_as_given_and_labelled():
    assert 'server: "Server summary (as given)"' in JS and 'server: "서버 요약",' in JS and 'server_en: "서버 요약(서버 원문, 영어)"' in JS
    assert "line.lang = \"en\"" in JS


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_pages_own_words_use_none_of_the_words_the_answer_may_not_use(pages, lang):
    body = re.search(r"const WHY_COPY = \{(.*?)\n\};", JS, re.S).group(1)
    block = re.search(rf"\n  {lang}: \{{(.*?)\n  \}},?", body, re.S).group(1)
    strings = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    html = re.sub(r"<[^>]+>", " ", pages[lang].text)
    text = " ".join(strings) + " " + unescape(html)
    assert X.forbidden_in(text, lang) == []
    for word in ("the cause is", "원인은", "no person needed", "사람 없이", "bypass", "우회"):
        assert word not in text.lower(), word


# --- the one extra line of a failed check_incident call ------------------------------------------------------------------
def check_response():
    result = d63.answer(d63.WITH_CONSUMER, d63.ALLOWS)
    return CheckResponse.model_validate({**d63.envelope(result), **d63.guide(result)})


def test_a_failed_lookup_gets_one_more_line_in_the_first_block_and_still_two_blocks():
    params = {"site_host": "login-fixture-host.com", "error_class": "login_auth", "task_step": "login", "http_status": "403", "agent_vendor": "openai",
              "via": "browser", "ahn_test": "1", "failed_at": "2026-11-07T00:00:00Z"}
    link = mcp_adapter.why_link(params)
    assert link.startswith("https://agenthelpnetwork.com/v1/why?")
    # the codes /v1/why takes are all in the link (task_step since D110); failed_at is not an argument of /v1/why and is left out
    assert parse_qs(urlparse(link).query) == {"site_host": ["login-fixture-host.com"], "error_class": ["login_auth"], "http_status": ["403"],
                                              "task_step": ["login"], "via": ["browser"], "agent_vendor": ["openai"], "ahn_test": ["1"]}
    response = check_response()
    plain = mcp_adapter.check_result(response)
    linked = mcp_adapter.check_result(response, why_url=link)
    assert len(plain.content) == 2 and len(linked.content) == 2
    assert linked.content[0].text == plain.content[0].text + "\n" + "Why it stopped and whether a person must act: " + link
    # the last block is the JSON, the same bytes with or without the line, and the structured content is unchanged
    assert linked.content[-1].text == linked.content[1].text == plain.content[1].text
    assert linked.structured_content == plain.structured_content
    assert json.loads(linked.content[-1].text) == linked.structured_content
    assert json.dumps(linked.structured_content, sort_keys=True) == json.dumps(plain.structured_content, sort_keys=True)


def test_the_real_check_incident_path_puts_the_line_in_the_first_block(monkeypatch):
    result = d63.answer(d63.WITH_CONSUMER, d63.ALLOWS)
    payload = {**d63.envelope(result), **d63.guide(result)}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        return payload
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    args = {"site_host": "login-fixture-host.com", "http_status": "403", "via": "browser", "agent_vendor": "openai", "task_step": "login", "test": True}
    failed = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", args))
    plain = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "login-fixture-host.com"}))
    assert len(failed.content) == 2 and len(plain.content) == 2
    lines = failed.content[0].text.splitlines()
    assert lines[-1].startswith(mcp_adapter.WHY_LEAD + "https://agenthelpnetwork.com/v1/why?")
    query = parse_qs(urlparse(lines[-1].rsplit(" ", 1)[1]).query)
    assert query == {"site_host": ["login-fixture-host.com"], "http_status": ["403"], "task_step": ["login"], "via": ["browser"],
                     "agent_vendor": ["openai"], "ahn_test": ["1"]}
    assert mcp_adapter.WHY_LEAD not in plain.content[0].text
    assert failed.content[-1].text == plain.content[-1].text and json.loads(failed.content[-1].text) == failed.structured_content


@pytest.mark.parametrize("params", [
    {"site_host": "login-fixture-host.com"},
    {"site_host": "login-fixture-host.com", "task_step": "status_check"},
    {"site_host": "login-fixture-host.com", "challenge": "none", "retry_after": "none"},
    {"site_host": "login-fixture-host.com", "via": "browser", "agent_vendor": "openai"},
])
def test_a_lookup_without_a_failure_code_gets_no_link(params):
    assert mcp_adapter.why_link(params) is None


def test_the_link_is_only_a_string_and_the_answer_is_not_read_for_it():
    source = (ROOT / "ahn/mcp_adapter.py").read_text(encoding="utf-8")
    assert not re.search(r"from \.(explain|explain_text|why_models)\b|import (explain|explain_text|why_models)\b", source)
    assert "def why_link(params: dict)" in source


def test_the_link_line_fits_the_text_a_tool_result_may_carry():
    longest = {"site_host": "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + ".com", "error_class": "human_verification", "failure_stage": "access_denial_observed",
               "http_status": "403", "challenge": "header", "retry_after": "gt_3600"}
    assert len(mcp_adapter.WHY_LEAD + mcp_adapter.why_link(longest)) < 500


def test_the_tool_definition_text_is_not_touched_by_the_link():
    from ahn.mcp_adapter import mcp

    tools = asyncio.run(mcp.list_tools())
    description = next(t.description for t in tools if t.name == "check_incident")
    assert "v1/why" not in description and len(description) <= 2048
    assert "v1/why" not in (mcp.instructions or "") and len(mcp.instructions or "") <= 2048


def test_the_contracts_and_the_decision_describe_the_page_and_the_line():
    for name in ("docs/api-contract.en.md", "docs/api-contract.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "/en/why" in text and "/ko/why" in text and "Why it stopped and whether a person must act" in text, name
    decision = next(line for line in (ROOT / "docs/decisions.md").read_text(encoding="utf-8").splitlines() if line.startswith("| D108 "))
    assert "/en/why" in decision and "셋째 텍스트 블록" in decision
    assert "/en/why" in (ROOT / "ahn/llms.txt").read_text(encoding="utf-8")
