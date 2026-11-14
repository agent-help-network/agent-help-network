"""D120: the reviewed exact-URL exception (paths only) to the task route link rule. No database, no network.

The exception list is empty in the repository, so every test fills it with monkeypatch and uses example.com addresses.
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
from datetime import datetime, timezone
from pathlib import Path

import httpx
import jsonschema
import pytest
from pydantic import ValidationError

from ahn import task_routes, task_routes_data
from ahn.task_routes import (TaskRoute, TaskRouteIn, TaskRoutesResponse, curated_exception_problems, lookup,
                             official_url_problem)
from ahn.task_routes_data import CURATED_URL_EXCEPTIONS, _route

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_official_paths_d120", ROOT / "scripts" / "verify_official_paths.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)

SITE = "example.com"
BASE = "https://www.example.com"
INDEX = f"{BASE}/help"
LONG_PATH = f"{BASE}/help/article/1234567890"  # ten digits in a row
LISTED_OK = (
    LONG_PATH,
    f"{BASE}/help/article/123456",                       # six digits, the D117 limit
    f"{BASE}/site/product/123456789012345",              # fifteen digits
    f"{BASE}/c/pcmcat123456789012",                      # a category id with a letter prefix
    f"{BASE}/help/returns-and-orders",                   # a word, not a number
    f"{BASE}/orders",                                    # a prefix word without a digit is fine
    f"{BASE}/account-help",
    f"{BASE}/help/Invoice-faq",
    f"{BASE}/help/page-1234-5678",                       # two groups, eight digits in all
    f"{BASE}/help/Article_123456789",
    f"{BASE}/site/help/pcmcat260800050014",
    f"{BASE}/help/Account12",                            # a word with fewer than six digits is not a long number
    f"{BASE}/help/order-12345",
    f"{BASE}/help/case12",
    f"{BASE}/help/ORDERS2",
    f"{BASE}/help/ticket9",
    f"{BASE}/help/Claim-7-status",
    f"{BASE}/help/123-4567-8901",                        # eleven digits, no run of six
    f"{BASE}/x/1234567/abc",
)
LISTED_BAD = (
    f"{BASE}/help/article/1234567890123456",             # sixteen digits
    f"{BASE}/claims/CASE-2026-0012345678",
    f"{BASE}/orders/112-1234567-1234567",
    f"{BASE}/help/account-12345678",
    f"{BASE}/orders/123456789012345",                    # a case word with a long number
    f"{BASE}/my-order-123456789012",
    f"{BASE}/x/123456789012345a123456789012345",         # thirty digits, two runs
    f"{BASE}/x/1234567.1234567",                         # two runs
    f"{BASE}/x/1234567/1234567",
    f"{BASE}/view%3Border-123456789012",                 # a percent escape does not hide the word
    f"{BASE}/view%253Bticket-123456",
    f"{BASE}/INVOICE/654321",
    f"{BASE}/x/1234567890/12345678",                     # more than fifteen digits in all
    f"{BASE}/help/0123456789abcdef0123456789abcdef",     # UUID-shaped, no dashes
    f"{BASE}/help/01234567-89ab-cdef-0123-456789abcdef",
    f"{BASE}/help/article?id=abc",                       # a query is never excepted
    f"{BASE}/help/article/1234567890?id=abc",
    f"{BASE}/help/article/1234567890?",
)


@pytest.fixture
def listed(monkeypatch):
    for url in LISTED_OK + LISTED_BAD:
        monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)


def test_the_shipped_list_is_the_reviewed_set_and_valid():
    assert CURATED_URL_EXCEPTIONS == {
        "https://www.samsung.com/us/support/troubleshoot/TSG10001565/": "https://www.samsung.com/us/support/contact/",
        "https://www.target.com/help/article/000061982": "https://www.target.com/returns",
    }
    assert curated_exception_problems(CURATED_URL_EXCEPTIONS) == []
    assert task_routes_data.CURATED_URL_EXCEPTIONS is CURATED_URL_EXCEPTIONS


LONG_NUMBER_OK = [url for url in LISTED_OK if re.search(r"\d{6,}", url.split(".com", 1)[1])]


@pytest.mark.parametrize("url", LONG_NUMBER_OK)
def test_nothing_is_excepted_while_the_list_is_empty_or_in_the_plain_context(url):
    assert official_url_problem(SITE, url, curated=True) is not None
    assert official_url_problem(SITE, url) is not None


@pytest.mark.parametrize("url", LONG_NUMBER_OK)
def test_a_listed_url_is_still_refused_outside_the_curated_context(listed, url):
    assert official_url_problem(SITE, url) is not None
    assert official_url_problem(SITE, url, curated=False) is not None


@pytest.mark.parametrize("url", LISTED_OK)
def test_every_good_example_passes_when_listed(listed, url):
    assert official_url_problem(SITE, url, curated=True) is None


@pytest.mark.parametrize("url", LISTED_BAD)
def test_the_bounds_hold_even_for_a_listed_url(listed, url):
    assert official_url_problem(SITE, url, curated=True) is not None


@pytest.mark.parametrize("url", [
    f"{BASE}/help/article/1234567891",          # a different number
    f"{BASE}/help/article/1234567890/",         # trailing slash
    f"{BASE}/Help/article/1234567890",          # case of the path
    "https://WWW.example.com/help/article/1234567890",   # case of the host
    f"{BASE}/help/article/1234567890?x=1",      # an added parameter
    f"{BASE}/help/article/1234567890#top",
])
def test_a_variant_of_a_listed_url_is_not_a_match(listed, url):
    assert url not in CURATED_URL_EXCEPTIONS
    assert official_url_problem(SITE, url, curated=True) is not None


@pytest.mark.parametrize("url", [
    "http://www.example.com/help/article/1234567890",                  # not https
    "https://user@www.example.com/help/article/1234567890",            # userinfo
    "https://www.example.com:8443/help/article/1234567890",            # port
    "https://www.elsewhere.com/help/article/1234567890",               # another registrable domain
    f"{BASE}/help/../article/1234567890",                              # dot segment
    f"{BASE}/help/a@b.com/1234567890",                                 # e-mail shaped path
    f"{BASE}/help/" + "a" * 200 + "/1234567890",                       # longer than 200
    f"{BASE}/help/%61ccount-12345678",                                 # an escaped letter does not hide the word
])
def test_the_other_rules_still_apply_to_a_listed_url(monkeypatch, url):
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)
    assert official_url_problem(SITE, url, curated=True) is not None


def test_a_url_off_the_list_keeps_the_ordinary_rule_in_every_context(listed):
    for url in (f"{BASE}/help/article/1234567892", f"{BASE}/help/article?id=abc", f"{BASE}/p/0123456789abcdef0123456789abcdef"):
        assert official_url_problem(SITE, url, curated=True) is not None
        assert official_url_problem(SITE, url) is not None
    assert official_url_problem(SITE, f"{BASE}/help/account-12345") is None  # ordinary rule: five digits are fine
    assert official_url_problem(SITE, f"{BASE}/help/returns", curated=True) is None


def test_a_non_string_is_not_a_match(listed):
    for value in (None, 12, b"https://www.example.com/", ["x"]):
        assert official_url_problem(SITE, value, curated=True) == "not a string"


# --- The model: only curated_official, and the advertised schema stays true -------------------------------------------------------

def record(source_kind: str, **fields) -> dict:
    base = {"site_host": SITE, "task_kind": "cancel_subscription", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
            "route_worked": None, "basis": "own_attempt", "outcome": "no_progress", "period": "2026-Q4",
            "source_kind": source_kind, "sensitive": False}
    if source_kind == "curated_official":
        base.update(basis="official_doc", outcome="not_applicable", source_url=INDEX, verified_at="2026-11-02")
    base.update(fields)
    return {key: value for key, value in base.items() if value is not None}


@pytest.mark.parametrize("url", LISTED_OK[:4])
def test_a_curated_entry_may_carry_a_listed_url(listed, url):
    route = TaskRoute.model_validate(record("curated_official", official_url=url))
    assert route.model_dump(mode="json")["official_url"] == url
    TaskRoute.model_validate(record("curated_official", source_url=url))  # the page that states it may be one too


@pytest.mark.parametrize("source_kind", ["operator_panel", "external_agent", "curated_community"])
def test_no_other_source_gets_the_exception(listed, source_kind):
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(record(source_kind, official_url=LONG_PATH))


def test_the_operator_panel_keeps_the_plain_rule(listed):
    with pytest.raises(ValidationError) as error:
        TaskRoute.model_validate(record("operator_panel", official_url=LONG_PATH))
    assert "official_url" in str(error.value)
    TaskRoute.model_validate(record("operator_panel", official_url=f"{BASE}/help/cancel"))


def test_a_public_report_cannot_carry_any_link(listed):
    base = {"site_host": SITE, "task_kind": "cancel_subscription", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
            "route_worked": None, "basis": "own_attempt", "outcome": "no_progress"}
    TaskRouteIn.model_validate(base)
    for field in ("official_url", "source_url", "verified_at", "source_kind"):
        with pytest.raises(ValidationError):
            TaskRouteIn.model_validate({**base, field: LONG_PATH if field.endswith("_url") else "curated_official"})


def test_an_unlisted_curated_url_is_refused(listed):
    for url in (f"{BASE}/help/other/1234567890", LONG_PATH + "/", LONG_PATH.upper()):
        with pytest.raises(ValidationError):
            TaskRoute.model_validate(record("curated_official", official_url=url))


def test_the_model_field_pattern_refuses_a_query_whatever_the_list_says(monkeypatch):
    for field in ("official_url", "source_url"):
        pattern = next(item.pattern for item in TaskRoute.model_fields[field].metadata if hasattr(item, "pattern"))
        assert pattern == r"^https://[^\s?#]+$"
        advertised = TaskRoute.model_json_schema()["properties"][field]["anyOf"][0]
        assert advertised["pattern"] == pattern and advertised["maxLength"] == 200
    url = f"{BASE}/help/article?id=abc"
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(record("curated_official", official_url=url))
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(record("curated_official", source_url=url))


# --- An answer built from a listed URL validates against the published schemas -----------------------------------------------------

HOST = "example-retailer.com"
RETAIL_PATH = f"https://www.{HOST}/help/article/1234567890"
RETAIL_INDEX = f"https://www.{HOST}/help"


def retail_table(monkeypatch, official=RETAIL_PATH):
    verified = datetime.now(timezone.utc).date().isoformat()
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, RETAIL_PATH, RETAIL_INDEX)
    entry = _route("cancel_subscription", obstacle="wrong_channel", routes=["web_form"], official_url=official,
                   source_url=RETAIL_INDEX, verified_at=verified)
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", {HOST: [entry]})


def test_a_lookup_that_carries_a_listed_url_validates_against_the_response_schema(monkeypatch):
    retail_table(monkeypatch)
    answer = lookup(HOST)
    assert answer.counts.records == 1 and answer.routes[0].official_url == RETAIL_PATH
    body = answer.model_dump(mode="json")
    jsonschema.validate(body, TaskRoutesResponse.model_json_schema())
    assert TaskRoutesResponse.model_validate(body).routes[0].official_url == RETAIL_PATH


def test_the_mcp_answer_for_a_listed_url_validates_against_the_advertised_output_schema(monkeypatch):
    from mcp.server import MCPServer
    from ahn.mcp_task_routes import register

    retail_table(monkeypatch)
    server = MCPServer(name="test")
    register(server)
    listed_tool = {tool.name: tool for tool in asyncio.run(server.list_tools())}["find_task_route"]
    result = asyncio.run(server.call_tool("find_task_route", {"site_host": "www." + HOST}))
    assert result.structured_content["routes"][0]["official_url"] == RETAIL_PATH
    jsonschema.validate(result.structured_content, listed_tool.output_schema)


# --- The list's own rules ---------------------------------------------------------------------------------------------------------

def test_a_clean_list_has_no_problems(listed, monkeypatch):
    for url in LISTED_BAD:
        monkeypatch.delitem(CURATED_URL_EXCEPTIONS, url)
    assert curated_exception_problems(CURATED_URL_EXCEPTIONS) == []


def test_a_list_entry_that_breaks_a_bound_is_reported(listed):
    assert len(curated_exception_problems(CURATED_URL_EXCEPTIONS)) == len(LISTED_BAD)


@pytest.mark.parametrize("index", [
    f"{BASE}/help?topic=1",                                  # a query
    f"{BASE}/help/1234567",                                  # six digits
    f"{BASE}/help/0123456789abcdef0123456789abcdef",
    f"{BASE}/help#top",                                      # fragment
    "http://www.example.com/help",                           # not https
    f"{BASE.replace('.com', '.com:8443')}/help",             # port
    "https://www.elsewhere.com/help",                        # another domain
    LONG_PATH + "x",                                          # an index page may not rely on the exception itself
    LONG_PATH,                                                # the key as its own index page
])
def test_an_index_page_must_pass_the_plain_rule_on_the_same_site(index):
    assert curated_exception_problems({LONG_PATH: index}), index


def test_a_bad_key_or_a_wrong_shape_is_reported():
    assert curated_exception_problems({f"{BASE}/a?id=1": INDEX})
    assert curated_exception_problems({f"{BASE}/a/1234567#x": INDEX})
    assert curated_exception_problems({LONG_PATH: None})
    assert curated_exception_problems([LONG_PATH])


# --- The checker: records and live reads ----------------------------------------------------------------------------------------

def test_the_table_checker_accepts_a_listed_url_and_refuses_it_before_it_is_listed(monkeypatch):
    route = _route("cancel_subscription", obstacle="wrong_channel", official_url=LONG_PATH, source_url=INDEX,
                   verified_at="2026-11-02")
    assert V.task_route_problems(SITE, route, V.date(2026, 11, 13))  # not listed yet
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, LONG_PATH, INDEX)
    assert V.task_route_problems(SITE, route, V.date(2026, 11, 13)) == []
    bad = _route("cancel_subscription", obstacle="wrong_channel", official_url=f"{BASE}/orders/112-1234567-1234567",
                 source_url=INDEX, verified_at="2026-11-02")
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, f"{BASE}/orders/112-1234567-1234567", INDEX)
    assert V.task_route_problems(SITE, bad, V.date(2026, 11, 13))


def test_the_pages_to_read_use_the_index_page_for_a_listed_url(monkeypatch):
    plain = _route("cancel_subscription", obstacle="wrong_channel", official_url=f"{BASE}/help/cancel",
                   source_url=INDEX, verified_at="2026-11-02")
    excepted = _route("return_or_exchange", obstacle="deadline_or_window", official_url=LONG_PATH, source_url=INDEX,
                      verified_at="2026-11-02")
    monkeypatch.setitem(V.TASK_ROUTES_CURATED, SITE, [plain, excepted])
    assert V.task_route_check_pages([SITE], {}) == {SITE: {"paths": [
        {"kind": "entry_link", "url": f"{BASE}/help/cancel", "source_url": INDEX},
        {"kind": "entry_link", "url": LONG_PATH, "source_url": INDEX}]}}
    other = f"{BASE}/index"
    assert V.task_route_check_pages([SITE], {LONG_PATH: other}) == {SITE: {"paths": [
        {"kind": "entry_link", "url": f"{BASE}/help/cancel", "source_url": INDEX},
        {"kind": "exception_link", "url": LONG_PATH, "source_url": other}]}}


def test_a_listed_url_that_no_record_uses_is_reported(monkeypatch):
    route = _route("cancel_subscription", obstacle="wrong_channel", official_url=LONG_PATH, source_url=INDEX,
                   verified_at="2026-11-02")
    monkeypatch.setitem(V.TASK_ROUTES_CURATED, SITE, [route])
    spare = f"{BASE}/help/article/9876543210"
    elsewhere = "https://www.other-site.org/help/article/9876543210"
    exceptions = {LONG_PATH: INDEX, spare: INDEX, elsewhere: "https://www.other-site.org/help"}
    assert V.unused_exceptions([SITE], exceptions) == [f"unused exception (no record carries it): {spare}"]
    assert V.unused_exceptions([SITE], {LONG_PATH: INDEX}) == []
    assert V.unused_exceptions([SITE], {}) == []


ALLOW = (200, None, "", b"User-agent: *\nAllow: /\n")


def page(*hrefs: str) -> tuple:
    links = " ".join(f'<a href="{href}">link</a>' for href in hrefs)
    return 200, None, "Help", f"<html><head><title>Help</title></head><body>{'Welcome. ' * 50}{links}</body></html>".encode()


class FakeFetcher:
    def __init__(self, answers: dict) -> None:
        self.answers, self.requested, self.requests = answers, [], 0

    def public_host(self, host: str) -> bool:
        return True

    def _get(self, url: str, want_body: bool):
        self.requested.append(url)
        self.requests += 1
        answer = self.answers.get(url, (404, None, "", b""))
        if isinstance(answer, Exception):
            raise answer
        return answer


def live(answers: dict, url: str = LONG_PATH, index: str = INDEX) -> tuple[list[str], FakeFetcher]:
    fetcher = FakeFetcher({f"{BASE}/robots.txt": ALLOW, **answers})
    checker = V.ConsumerCheck(fetcher, out=lambda line: None)
    table = {SITE: {"paths": [{"kind": "exception_link", "url": url, "source_url": index}]}}
    return checker.run(table, [SITE]), fetcher


def test_the_live_check_passes_when_the_index_links_the_exact_url():
    failures, fetcher = live({INDEX: page("/other", LONG_PATH), LONG_PATH: page()})
    assert failures == []
    assert fetcher.requested[0] == f"{BASE}/robots.txt"  # robots.txt before any page
    assert fetcher.requested[1:] == [INDEX, LONG_PATH]


def test_a_relative_link_that_resolves_to_the_exact_url_counts():
    failures, _ = live({INDEX: page("/help/article/1234567890"), LONG_PATH: page()})
    assert failures == []


@pytest.mark.parametrize("href", [
    f"{BASE}/help/article/1234567890/",
    f"{BASE}/Help/article/1234567890",
    f"{BASE}/help/article/1234567890?utm_source=x",   # looser matching of consumer links does not apply
    f"{BASE}/help/article",
])
def test_a_looser_link_on_the_index_page_is_not_enough(href):
    failures, _ = live({INDEX: page(href), LONG_PATH: page()})
    assert failures and "not linked as written" in failures[0]


def test_an_index_page_that_redirects_fails():
    moved = f"{BASE}/help/index"
    failures, _ = live({INDEX: (301, moved, "", b""), moved: page(LONG_PATH), LONG_PATH: page()})
    assert any("index page redirects" in line for line in failures)


def test_the_live_check_needs_a_plain_200_at_the_exact_address():
    failures, _ = live({INDEX: page(LONG_PATH), LONG_PATH: (404, None, "", b"")})
    assert any("status:404" in line for line in failures)
    failures, _ = live({INDEX: page(LONG_PATH), LONG_PATH: (302, f"{BASE}/help/article", "", b""), f"{BASE}/help/article": page()})
    assert any("redirects" in line for line in failures)
    failures, _ = live({INDEX: page(LONG_PATH), LONG_PATH: (302, f"{BASE}/login", "", b""), f"{BASE}/login": page()})
    assert failures


def test_the_live_check_stops_at_a_robots_refusal_and_reads_nothing_else():
    for robots in ((200, None, "", b"User-agent: *\nDisallow: /help\n"), (503, None, "", b""), httpx.ConnectTimeout("slow")):
        failures, fetcher = live({f"{BASE}/robots.txt": robots})
        assert failures and fetcher.requested == [f"{BASE}/robots.txt"]


def test_a_refused_index_page_stops_the_host():
    failures, fetcher = live({INDEX: (403, None, "", b"")})
    assert failures and LONG_PATH not in fetcher.requested


FULL_WIDTH_ORDER = "%EF%BD%8F%EF%BD%92%EF%BD%84%EF%BD%85%EF%BD%92"  # "ｏｒｄｅｒ" (full-width letters), percent-encoded


def test_a_full_width_case_word_next_to_a_long_number_is_refused_even_when_listed(monkeypatch):
    # NFKC reads the full-width letters as "order", so the case-word check sees the word they look like.
    url = f"{BASE}/help/{FULL_WIDTH_ORDER}-123456789"
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)
    assert official_url_problem(SITE, url, curated=True) is not None
    assert task_routes._exception_path_problem("/help/ｏｒｄｅｒ-123456789") is not None
    # A path without a case word keeps the D120 answer.
    plain = f"{BASE}/help/article/123456789"
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, plain, INDEX)
    assert official_url_problem(SITE, plain, curated=True) is None
