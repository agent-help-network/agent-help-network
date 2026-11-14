"""D120: the reviewed exact-URL exception to the task route link rule. Pure: no database, no network.

The exception list is empty in the repository, so every test fills it with monkeypatch and uses example.com addresses.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from ahn import task_routes_data
from ahn.task_routes import (TaskRoute, TaskRouteIn, curated_exception_problems, official_url_problem)
from ahn.task_routes_data import CURATED_URL_EXCEPTIONS, _route

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("verify_official_paths_d120", ROOT / "scripts" / "verify_official_paths.py")
V = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(V)

SITE = "example.com"
INDEX = "https://www.example.com/help"
LONG_PATH = "https://www.example.com/help/article/1234567890"  # ten digits in a row
UUID_PATH = "https://www.example.com/help/0123456789abcdef0123456789abcdef"
QUERY = "https://www.example.com/help/article?id=abc-12"
TWO_KEYS = "https://www.example.com/help/page?id=abc&docId=x-9"
LISTED = (LONG_PATH, UUID_PATH, QUERY, TWO_KEYS)


@pytest.fixture
def listed(monkeypatch):
    for url in LISTED:
        monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)


def test_the_shipped_list_is_empty_and_valid():
    assert CURATED_URL_EXCEPTIONS == {}
    assert curated_exception_problems(CURATED_URL_EXCEPTIONS) == []
    assert task_routes_data.CURATED_URL_EXCEPTIONS is CURATED_URL_EXCEPTIONS


@pytest.mark.parametrize("url", LISTED)
def test_nothing_is_excepted_while_the_list_is_empty(url):
    assert official_url_problem(SITE, url, curated=True) is not None
    assert official_url_problem(SITE, url) is not None


@pytest.mark.parametrize("url", LISTED)
def test_a_listed_url_passes_only_in_the_curated_context(listed, url):
    assert official_url_problem(SITE, url, curated=True) is None
    assert official_url_problem(SITE, url) is not None  # the default is the strict rule
    assert official_url_problem(SITE, url, curated=False) is not None


@pytest.mark.parametrize("url", [
    "https://www.example.com/help/article/1234567891",          # a different number
    "https://www.example.com/help/article/1234567890/",         # trailing slash
    "https://www.example.com/Help/article/1234567890",          # case of the path
    "https://WWW.example.com/help/article/1234567890",          # case of the host
    "https://www.example.com/help/article?ID=abc-12",           # case of the key
    "https://www.example.com/help/article?id=ABC-12",           # case of the value
    "https://www.example.com/help/article?id=abc-12&",          # trailing separator
    "https://www.example.com/help/page?docId=x-9&id=abc",       # query order
    "https://www.example.com/help/page?id=abc&docId=x-9&id=z",  # extra pair
    "https://www.example.com/help/0123456789ABCDEF0123456789abcdef",
    "https://www.example.com/help/article/1234567890?id=1",     # query added to a listed path
])
def test_a_variant_of_a_listed_url_is_not_a_match(listed, url):
    assert url not in CURATED_URL_EXCEPTIONS
    assert official_url_problem(SITE, url, curated=True) is not None


@pytest.mark.parametrize("query, why", [
    ("token=abc", "key"), ("orderId=abc", "key"), ("ID=abc", "key"), ("id=abc&id=def", "key"), ("id", "key"), ("=abc", "key"),
    ("id=", "value"), ("id=a%40b.com", "value"), ("id=a@b.com", "value"), ("id=a.b", "value"), ("id=a_b", "value"),
    ("id=" + "a" * 41, "value"),
    ("id=0123456789abcdef0123456789abcdef", "uuid"), ("id=01234567-89ab-cdef-0123-456789abcdef", "uuid"),
    ("id=1234567890", "number"), ("docId=12345678901234", "number"), ("pageId=1234-5678-90", "number"),
])
def test_a_listed_url_with_a_bad_query_is_still_refused(monkeypatch, query, why):
    url = f"https://www.example.com/help/article?{query}"
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)
    assert official_url_problem(SITE, url, curated=True) is not None, why


@pytest.mark.parametrize("query", ["id=abc", "pageId=A1-b2", "docId=" + "ab-" * 13 + "a", "id=123456789", "id=a1&pageId=b2&docId=c3"])
def test_the_allowed_query_shapes_pass(monkeypatch, query):
    url = f"https://www.example.com/help/article?{query}"
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)
    assert official_url_problem(SITE, url, curated=True) is None


@pytest.mark.parametrize("url", [
    "http://www.example.com/help/article?id=a",                # not https
    "https://www.example.com/help/article?id=a#top",           # fragment
    "https://www.example.com/help/article?id=a#",
    "https://user@www.example.com/help/article?id=a",          # userinfo
    "https://www.example.com:8443/help/article?id=a",          # port
    "https://www.elsewhere.com/help/article?id=a",             # another registrable domain
    "https://www.example.com/help/../article?id=a",            # dot segment
    "https://www.example.com/help/a@b.com?id=a",               # e-mail shaped path
    "https://www.example.com/help/" + "a" * 200 + "?id=a",     # longer than 200
])
def test_the_other_rules_still_apply_to_a_listed_url(monkeypatch, url):
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, url, INDEX)
    assert official_url_problem(SITE, url, curated=True) is not None


def test_a_non_string_is_not_a_match(listed):
    for value in (None, 12, b"https://www.example.com/", ["x"]):
        assert official_url_problem(SITE, value, curated=True) == "not a string"


# --- The model: only curated_official -------------------------------------------------------------------------------------------

def record(source_kind: str, **fields) -> dict:
    base = {"site_host": SITE, "task_kind": "cancel_subscription", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
            "route_worked": None, "basis": "own_attempt", "outcome": "no_progress", "period": "2026-Q4",
            "source_kind": source_kind, "sensitive": False}
    if source_kind == "curated_official":
        base.update(basis="official_doc", outcome="not_applicable", source_url=INDEX, verified_at="2026-11-02")
    base.update(fields)
    return {key: value for key, value in base.items() if value is not None}


@pytest.mark.parametrize("url", LISTED)
def test_a_curated_entry_may_carry_a_listed_url(listed, url):
    route = TaskRoute.model_validate(record("curated_official", official_url=url))
    assert route.model_dump(mode="json")["official_url"] == url
    TaskRoute.model_validate(record("curated_official", source_url=url))  # the page that states it may be one too


@pytest.mark.parametrize("source_kind", ["operator_panel", "external_agent", "curated_community"])
@pytest.mark.parametrize("url", LISTED)
def test_no_other_source_gets_the_exception(listed, source_kind, url):
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(record(source_kind, official_url=url))


@pytest.mark.parametrize("url", LISTED)
def test_the_operator_panel_keeps_the_plain_rule(listed, url):
    with pytest.raises(ValidationError) as error:
        TaskRoute.model_validate(record("operator_panel", official_url=url))
    assert "official_url" in str(error.value)


@pytest.mark.parametrize("url", LISTED)
def test_a_public_report_cannot_carry_any_link(listed, url):
    base = {"site_host": SITE, "task_kind": "cancel_subscription", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
            "route_worked": None, "basis": "own_attempt", "outcome": "no_progress"}
    TaskRouteIn.model_validate(base)
    for field in ("official_url", "source_url", "verified_at", "source_kind"):
        with pytest.raises(ValidationError):
            TaskRouteIn.model_validate({**base, field: url if field.endswith("_url") else "curated_official"})


def test_an_unlisted_curated_url_is_refused(listed):
    for url in ("https://www.example.com/help/other/1234567890", QUERY + "&id=zz", QUERY + "x"):
        with pytest.raises(ValidationError):
            TaskRoute.model_validate(record("curated_official", official_url=url))


def test_the_model_pattern_alone_does_not_let_a_query_through():
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(record("curated_official", official_url=QUERY))
    with pytest.raises(ValidationError):
        TaskRoute.model_validate(record("operator_panel", official_url=QUERY))


# --- The list's own rules ---------------------------------------------------------------------------------------------------------

def test_a_clean_list_has_no_problems(listed):
    assert curated_exception_problems(CURATED_URL_EXCEPTIONS) == []


@pytest.mark.parametrize("index", [
    "https://www.example.com/help?topic=1",                  # a query
    "https://www.example.com/help/1234567",                  # six digits
    "https://www.example.com/help/0123456789abcdef0123456789abcdef",
    "https://www.example.com/help#top",                      # fragment
    "http://www.example.com/help",                           # not https
    "https://www.example.com:8443/help",                     # port
    "https://www.elsewhere.com/help",                        # another domain
    QUERY,                                                    # an index page may not rely on the exception itself
    LONG_PATH,                                                # the key as its own index page
])
def test_an_index_page_must_pass_the_plain_rule_on_the_same_site(index):
    problems = curated_exception_problems({LONG_PATH: index})
    assert problems, index


def test_a_bad_key_or_a_wrong_shape_is_reported():
    assert curated_exception_problems({"https://www.example.com/a?token=1": INDEX})
    assert curated_exception_problems({"https://www.example.com/a?id=1234567890": INDEX})
    assert curated_exception_problems({"https://www.example.com/a/1234567#x": INDEX})
    assert curated_exception_problems({LONG_PATH: None})
    assert curated_exception_problems([LONG_PATH])


# --- The checker: records and live reads ----------------------------------------------------------------------------------------

def test_the_table_checker_accepts_a_listed_url_and_refuses_it_after_the_list_is_cleared(monkeypatch):
    route = _route("cancel_subscription", obstacle="wrong_channel", official_url=LONG_PATH, source_url=INDEX,
                   verified_at="2026-11-02")
    assert V.task_route_problems(SITE, route, V.date(2026, 11, 13))  # not listed yet
    monkeypatch.setitem(CURATED_URL_EXCEPTIONS, LONG_PATH, INDEX)
    assert V.task_route_problems(SITE, route, V.date(2026, 11, 13)) == []


def test_the_pages_to_read_use_the_index_page_for_a_listed_url(monkeypatch):
    plain = _route("cancel_subscription", obstacle="wrong_channel", official_url="https://www.example.com/help/cancel",
                   source_url=INDEX, verified_at="2026-11-02")
    excepted = _route("return_or_exchange", obstacle="deadline_or_window", official_url=LONG_PATH, source_url=INDEX,
                      verified_at="2026-11-02")
    monkeypatch.setitem(V.TASK_ROUTES_CURATED, SITE, [plain, excepted])
    assert V.task_route_check_pages([SITE], {}) == {SITE: {"paths": [
        {"kind": "entry_link", "url": "https://www.example.com/help/cancel", "source_url": INDEX},
        {"kind": "entry_link", "url": LONG_PATH, "source_url": INDEX}]}}
    other = "https://www.example.com/index"
    assert V.task_route_check_pages([SITE], {LONG_PATH: other}) == {SITE: {"paths": [
        {"kind": "entry_link", "url": "https://www.example.com/help/cancel", "source_url": INDEX},
        {"kind": "exception_link", "url": LONG_PATH, "source_url": other}]}}


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


def live(answers: dict, url: str = QUERY, index: str = INDEX) -> tuple[list[str], FakeFetcher]:
    fetcher = FakeFetcher({"https://www.example.com/robots.txt": ALLOW, **answers})
    checker = V.ConsumerCheck(fetcher, out=lambda line: None)
    table = {SITE: {"paths": [{"kind": "exception_link", "url": url, "source_url": index}]}}
    return checker.run(table, [SITE]), fetcher


def test_the_live_check_passes_when_the_index_links_the_exact_url():
    failures, fetcher = live({INDEX: page("/other", QUERY), QUERY: page()})
    assert failures == []
    assert fetcher.requested[0] == "https://www.example.com/robots.txt"  # robots.txt before any page
    assert fetcher.requested[1:] == [INDEX, QUERY]


def test_a_relative_link_that_resolves_to_the_exact_url_counts():
    failures, _ = live({INDEX: page("/help/article?id=abc-12"), QUERY: page()})
    assert failures == []


@pytest.mark.parametrize("href", [
    "https://www.example.com/help/article?id=ABC-12",
    "https://www.example.com/help/article/?id=abc-12",
    "https://www.example.com/help/article?id=abc-12&utm_source=x",   # looser matching of consumer links does not apply
    "https://www.example.com/help/article",
    "https://www.example.com/Help/article?id=abc-12",
])
def test_a_looser_link_on_the_index_page_is_not_enough(href):
    failures, _ = live({INDEX: page(href), QUERY: page()})
    assert failures and "not linked as written" in failures[0]


def test_the_live_check_needs_a_plain_200_at_the_exact_address():
    failures, _ = live({INDEX: page(QUERY), QUERY: (404, None, "", b"")})
    assert any("status:404" in line for line in failures)
    failures, _ = live({INDEX: page(QUERY), QUERY: (302, "https://www.example.com/help/article", "", b""),
                        "https://www.example.com/help/article": page()})
    assert any("redirects" in line for line in failures)
    failures, _ = live({INDEX: page(QUERY), QUERY: (302, "https://www.example.com/login", "", b""),
                        "https://www.example.com/login": page()})
    assert failures


def test_the_live_check_stops_at_a_robots_refusal_and_reads_nothing_else():
    failures, fetcher = live({"https://www.example.com/robots.txt": (200, None, "", b"User-agent: *\nDisallow: /help\n")})
    assert failures and fetcher.requested == ["https://www.example.com/robots.txt"]
    failures, fetcher = live({"https://www.example.com/robots.txt": (503, None, "", b"")})
    assert failures and fetcher.requested == ["https://www.example.com/robots.txt"]
    failures, fetcher = live({"https://www.example.com/robots.txt": httpx.ConnectTimeout("slow")})
    assert failures and fetcher.requested == ["https://www.example.com/robots.txt"]


def test_a_refused_index_page_stops_the_host():
    failures, fetcher = live({INDEX: (403, None, "", b"")})
    assert failures and QUERY not in fetcher.requested
