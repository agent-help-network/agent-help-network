"""D107: the official platform policy table, GET /v1/platforms and the /en|ko/platforms pages. No database, no network."""

import copy
import os
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import platform_policies as pp
from ahn.main import create_app
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]


def client():
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)), follow_redirects=False)


def table():
    return copy.deepcopy(pp.PLATFORMS)


def test_the_shipped_table_is_sound():
    assert pp.problems() == []


def test_the_table_covers_the_surfaces_the_curation_found():
    names = {p["platform"] for p in pp.PLATFORMS}
    assert names == {"claude_in_chrome", "claude_desktop_browser", "chatgpt_agent", "gemini", "meta_muse", "perplexity_comet"}
    urls = [d["url"] for p in pp.PLATFORMS for d in p["docs"]]
    assert len(urls) == len(set(urls))


def test_every_page_is_on_a_vendor_host_over_https_and_every_quotation_is_short():
    for p in pp.PLATFORMS:
        for d in p["docs"]:
            assert d["url"].startswith("https://")
            assert re.match(r"https://([^/]+)/", d["url"]).group(1) in pp.VENDOR_HOSTS[p["vendor"]]
            for q in d["quotes"]:
                assert 0 < len(q["text"]) <= 200 and q["tag"] in pp.TAGS


def test_a_page_that_was_not_read_has_no_quotation_and_no_verified_date():
    unread = [d for p in pp.PLATFORMS for d in p["docs"] if d["status"] == "unread"]
    assert unread
    for d in unread:
        assert d["quotes"] == [] and "verified_at" not in d and d["unread_reason"] in pp.UNREAD_REASONS


def test_muse_has_no_site_restriction_quotation_and_says_so():
    muse = next(p for p in pp.PLATFORMS if p["platform"] == "meta_muse")
    assert "site_restrictions" in muse["not_found"]
    assert not any(q["tag"] in ("blocked_category", "confirm_required") for d in muse["docs"] for q in d["quotes"])


def test_the_two_claude_in_chrome_pages_that_differ_are_both_shown_as_a_conflict():
    docs = [d for p in pp.PLATFORMS if p["platform"] == "claude_in_chrome" for d in p["docs"] if d.get("conflict")]
    assert len(docs) == 2 and docs[0]["conflict_peer_url"] == docs[1]["url"] and docs[1]["conflict_peer_url"] == docs[0]["url"]


@pytest.mark.parametrize("mutate,expected", [
    (lambda t: t[0]["docs"][0].update(url="https://example.com/claude"), "vendor's own host"),
    (lambda t: t[0]["docs"][0].update(url="http://support.claude.com/en/articles/1"), "vendor's own host"),
    (lambda t: t[0]["docs"][0].update(url="https://support.claude.com.evil.test/a"), "vendor's own host"),
    (lambda t: t[0]["docs"][0]["quotes"][0].update(text="x" * 201), "longer than"),
    (lambda t: t[0]["docs"][0]["quotes"][0].update(tag="rumour"), "unknown quotation tag"),
    (lambda t: t[0]["docs"][0]["quotes"][0].update(text="write to a@b.test"), "address"),
    (lambda t: t[0]["docs"][0].update(verified_at="11/06/2026"), "verified_at is not"),
    (lambda t: t[0]["docs"][0].update(verified_at="2999-01-01"), "future"),
    (lambda t: t[0]["docs"][0].update(quotes=[]), "without a quotation"),
    (lambda t: t[0]["docs"][0].pop("conflict_peer_url"), "conflict without"),
    (lambda t: t[0].update(vendor="press"), "unknown vendor"),
    (lambda t: next(d for d in t[2]["docs"] if d["status"] == "unread").update(verified_at="2026-11-06"), "unread page with"),
    (lambda t: next(d for d in t[2]["docs"] if d["status"] == "unread").update(unread_reason="guessed"), "known reason"),
])
def test_problems_catches_each_rule(mutate, expected):
    t = table()
    mutate(t)
    assert any(expected in line for line in pp.problems(t, today=date(2026, 11, 6)))


def test_a_page_not_read_for_ninety_days_loses_its_quotations_but_keeps_its_link():
    old = pp.view(today=date(2027, 3, 1))
    docs = [d for p in old["platforms"] for d in p["docs"]]
    assert all(d["quotes"] == [] and d["status"] == "unread" and "verified_at" not in d for d in docs)
    assert all(d["url"] for d in docs)
    fresh = pp.view(today=date(2026, 11, 7))
    assert any(d["quotes"] for p in fresh["platforms"] for d in p["docs"])


def test_the_fixed_texts_hold_no_word_that_reads_as_a_way_around_a_platform():
    low = [pp.NOTICE["en"], pp.NOTICE["ko"], *(v for n in pp.NOTES.values() for v in n.values())]
    for text in low:
        assert not any(w in text.lower() for w in pp.FORBIDDEN), text
    assert "does not judge whether a site falls under" in pp.NOTICE["en"] and "판단하지 않고" in pp.NOTICE["ko"]


def test_the_api_returns_the_table_and_is_in_the_openapi_schema():
    with client() as c:
        got = c.get("/v1/platforms")
        spec = c.get("/openapi.json").json()
    assert got.status_code == 200
    body = got.json()
    assert body["schema_version"] == "ahn-platforms/1" and body["notice"] == pp.NOTICE["en"]
    assert [p["platform"] for p in body["platforms"]] == [p["platform"] for p in pp.PLATFORMS]
    assert "/v1/platforms" in spec["paths"] and "PlatformsResponse" in spec["components"]["schemas"]
    assert None not in str(body).split() and '"verified_at": null' not in got.text  # absent fields are left out, not null
    chatgpt = next(p for p in body["platforms"] if p["platform"] == "chatgpt_agent")
    assert any(d["status"] == "unread" and d["unread_reason"] == "http_403" for d in chatgpt["docs"])


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_the_page_shows_every_page_every_quotation_and_the_notice(lang):
    with client() as c:
        page = c.get(f"/{lang}/platforms")
    assert page.status_code == 200
    html = page.text
    from html import unescape
    text = unescape(html)
    for p in pp.PLATFORMS:
        assert f'id="{p["platform"]}"' in html
        for d in p["docs"]:
            assert d["url"] in text
            for q in d["quotes"]:
                assert q["text"] in text
    assert pp.NOTICE[lang] in text


def test_an_unknown_language_is_a_404():
    with client() as c:
        assert c.get("/xx/platforms").status_code == 404


def test_the_table_is_read_by_no_module_that_decides_an_answer():
    # Information only: the verdict code must not depend on the platform table.
    for name in ("triage.py", "service.py", "response_guide.py", "mcp_adapter.py", "models.py"):
        assert "platform_policies" not in (ROOT / "ahn" / name).read_text(encoding="utf-8"), name


def test_the_contracts_and_the_decision_describe_the_endpoint():
    for name in ("docs/api-contract.en.md", "docs/api-contract.md"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "/v1/platforms" in text and "ahn-platforms/1" in text and "D107" in text, name
    assert "| D107 " in (ROOT / "docs/decisions.md").read_text(encoding="utf-8")
    assert "/en/platforms" in (ROOT / "ahn/llms.txt").read_text(encoding="utf-8")
