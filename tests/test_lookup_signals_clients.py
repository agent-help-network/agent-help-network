"""Lookup signals sent by the Claude Code hook and by ahn-client: http_status, challenge, retry_after, via.

Pure: no database and no network (both transports are replaced). The enum lists and the rules are the ones of
docs/api-contract.en.md; the hook and ahn-client must give the same codes for the same input.
"""

from __future__ import annotations

import ast
import asyncio
import importlib.util
import io
import json
import sys
import types
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))

import ahn_client as ahn  # noqa: E402
from ahn_client import _adapter, _claude_code, _client, _signals  # noqa: E402
from ahn_client import claude_agent_sdk as ahn_claude  # noqa: E402
from ahn_client import langchain as ahn_langchain  # noqa: E402

HOOK_FILE = ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "ahn_lookup.py"
spec = importlib.util.spec_from_file_location("ahn_claude_code_hook_signals", HOOK_FILE)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

ANSWER = {
    "site_host": "example.com", "current_site_status": "unknown", "evidence_level": "no_reports",
    "observations": [], "provider_status": None, "official_status_page": None,
    "access_profile": {"robots_status": "ok", "llms_txt_status": "absent",
                       "robots": {"agents_blocked": ["Claude-User"], "agents_allowed": [], "rules": []}},
    "official_paths": {"name": "Example", "api_docs": "https://developer.example.com/apis", "developer_portal": None, "note": None},
    "recommendation": {"action": "use_official_path", "confidence": "high", "reasons": ["robots_blocks_agents"]},
    "next_checks": [{"code": "response_observed", "text": "..."}],
}
# Design table (parameter names and values) the server implements; a change here is a contract change.
ALLOWED_PARAMETERS = {"site_host", "error_class", "failure_stage", "http_status", "challenge", "retry_after", "via", "agent_vendor"}
STATUSES = {401, 402, 403, 407, 408, 429, 451, *range(500, 600)}
CHALLENGES = {"header", "none"}
BANDS = {"none", "lt_60", "60_3600", "gt_3600"}
VIAS = {"http_fetch", "browser", "unknown"}


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    ahn.clear_cache()
    for name in ("AHN_HOOK_DISABLE", "AHN_HOOK_SKIP_HOSTS", "AHN_HOOK_DEBUG", "AHN_HOOK_FAKE_RESPONSE",
                 "AHN_CLIENT_DISABLE", "AHN_CLIENT_SKIP_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLAUDE_PLUGIN_DATA", str(tmp_path / "plugin-data"))

    def refuse(request, timeout):
        raise AssertionError("unexpected network request: " + request.full_url)

    monkeypatch.setattr(hook, "urlopen", refuse)
    monkeypatch.setattr(_client, "_urlopen", refuse)
    yield
    ahn.clear_cache()


@pytest.fixture
def hook_requests(monkeypatch):
    requests = []

    def fake(request, timeout):
        requests.append(request)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(hook, "urlopen", fake)
    return requests


@pytest.fixture
def client_requests(monkeypatch):
    requests = []

    def fake(request, timeout):
        requests.append(request)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", fake)
    return requests


def query_of(request):
    return {name: values[0] for name, values in urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query).items()}


def post_tool_use(code, headers=None, tool="WebFetch", url="https://example.com/a", **response):
    body = {"code": code, "codeText": "x", **response}
    if headers is not None:
        body["headers"] = headers
    return {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": {"url": url}, "tool_response": body}


def failure_event(error, tool="WebFetch", url="https://example.com/a"):
    return {"hook_event_name": "PostToolUseFailure", "tool_name": tool, "tool_input": {"url": url}, "error": error}


def run_hook(payload):
    # The default environment: the autouse fixture points CLAUDE_PLUGIN_DATA at a temporary directory.
    return hook.run(json.dumps(payload).encode("utf-8"))


# --- the hook: which codes a payload yields ------------------------------------------------------------

# (status, response headers or None for "no headers object", expected codes)
TABLE = [
    (401, None, {"http_status": 401, "via": "http_fetch"}),
    (402, {}, {"http_status": 402, "via": "http_fetch"}),  # an empty headers object is "not seen", not "no header"
    (403, {"server": "x"}, {"http_status": 403, "challenge": "none", "via": "http_fetch"}),
    (403, {}, {"http_status": 403, "via": "http_fetch"}),
    (403, {"cf-mitigated": "challenge"}, {"http_status": 403, "challenge": "header", "via": "http_fetch"}),
    (403, {"CF-Mitigated": "Challenge"}, {"http_status": 403, "challenge": "header", "via": "http_fetch"}),
    (403, {"cf-mitigated": "other"}, {"http_status": 403, "challenge": "none", "via": "http_fetch"}),
    (407, None, {"http_status": 407, "via": "http_fetch"}),
    (408, {}, {"http_status": 408, "via": "http_fetch"}),
    (451, {"cf-mitigated": "challenge"}, {"http_status": 451, "challenge": "header", "via": "http_fetch"}),
    (429, {"Retry-After": "30"}, {"http_status": 429, "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"}),
    (429, {"retry-after": "59"}, {"http_status": 429, "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"}),
    (429, {"Retry-After": "60"}, {"http_status": 429, "challenge": "none", "retry_after": "60_3600", "via": "http_fetch"}),
    (429, {"Retry-After": " 3600 "}, {"http_status": 429, "challenge": "none", "retry_after": "60_3600", "via": "http_fetch"}),
    (429, {"Retry-After": "3601"}, {"http_status": 429, "challenge": "none", "retry_after": "gt_3600", "via": "http_fetch"}),
    (429, {"Retry-After": 120}, {"http_status": 429, "challenge": "none", "retry_after": "60_3600", "via": "http_fetch"}),
    (429, {"Retry-After": "0"}, {"http_status": 429, "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"}),
    (429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}, {"http_status": 429, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (429, {"Retry-After": "soon"}, {"http_status": 429, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (429, {"Retry-After": "-5"}, {"http_status": 429, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (429, {"Retry-After": "12345678901"}, {"http_status": 429, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (429, {"Retry-After": ""}, {"http_status": 429, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (429, {}, {"http_status": 429, "via": "http_fetch"}),
    (429, {"x": "y"}, {"http_status": 429, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (429, {"Retry-After": ["30", "99999"]}, {"http_status": 429, "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"}),
    (429, {b"Retry-After": b"7200"}, {"http_status": 429, "challenge": "none", "retry_after": "gt_3600", "via": "http_fetch"}),
    (429, {"Retry-After": b"\xff\xfe"}, {"http_status": 429, "challenge": "none", "via": "http_fetch"}),
    (429, {"Retry-After": object()}, {"http_status": 429, "challenge": "none", "via": "http_fetch"}),
    (429, {"Retry-After": None}, {"http_status": 429, "challenge": "none", "via": "http_fetch"}),
    (403, {b"CF-Mitigated": b"challenge"}, {"http_status": 403, "challenge": "header", "via": "http_fetch"}),
    (403, {"cf-mitigated": ["other", "challenge"]}, {"http_status": 403, "challenge": "header", "via": "http_fetch"}),
    (403, {"cf-mitigated": ["other"]}, {"http_status": 403, "challenge": "none", "via": "http_fetch"}),
    (403, {"cf-mitigated": object()}, {"http_status": 403, "via": "http_fetch"}),
    (403, {"cf-mitigated": 5}, {"http_status": 403, "challenge": "none", "via": "http_fetch"}),
    (403, {b"\xff": "x"}, {"http_status": 403, "via": "http_fetch"}),
    (403, {7: "x"}, {"http_status": 403, "via": "http_fetch"}),
    (429, None, {"http_status": 429, "via": "http_fetch"}),
    (503, {"Retry-After": "120"}, {"http_status": 503, "challenge": "none", "retry_after": "60_3600", "via": "http_fetch"}),
    (503, {"Retry-After": "7200", "cf-mitigated": "challenge"},
     {"http_status": 503, "challenge": "header", "retry_after": "gt_3600", "via": "http_fetch"}),
    (503, {}, {"http_status": 503, "via": "http_fetch"}),
    (500, {"Retry-After": "30"}, {"http_status": 500, "challenge": "none", "via": "http_fetch"}),
    (502, {}, {"http_status": 502, "via": "http_fetch"}),
    (504, None, {"http_status": 504, "via": "http_fetch"}),
    (599, None, {"http_status": 599, "via": "http_fetch"}),
    # Not looked up at all (the hook sends nothing), and no status code is reported for them.
    (404, {}, {"via": "http_fetch"}),
    (400, None, {"via": "http_fetch"}),
]


@pytest.mark.parametrize("status,headers,expected", TABLE)
def test_hook_codes_for_a_webfetch_result(status, headers, expected):
    assert hook.signals(post_tool_use(status, headers)) == expected


@pytest.mark.parametrize("status,headers,expected", TABLE)
def test_ahn_client_gives_the_same_codes_for_the_same_answer(status, headers, expected):
    """The same status and headers as an exception's response, and as a Claude Agent SDK payload."""
    payload = post_tool_use(status, headers)
    assert _claude_code.signals(payload) == hook.signals(payload) == expected
    error = Exception("failed")
    error.response = types.SimpleNamespace(status_code=status, headers=headers) if headers is not None \
        else types.SimpleNamespace(status_code=status)
    assert ahn.signals(error, via="http_fetch") == expected


def test_a_status_in_an_error_text_is_never_a_code():
    for error in ("Request failed with status code 403", "HTTP 429 Too Many Requests, Retry-After: 30", "503 Service Unavailable"):
        assert hook.signals(failure_event(error)) == {"via": "http_fetch"}
        assert _claude_code.signals(failure_event(error)) == {"via": "http_fetch"}
        assert ahn.signals(error) == {}


@pytest.mark.parametrize("headers", ["cf-mitigated: challenge", ["Retry-After", "30"], 5, None])
def test_headers_that_are_not_an_object_add_no_header_codes(headers):
    payload = post_tool_use(429)
    payload["tool_response"]["headers"] = headers
    assert hook.signals(payload) == {"http_status": 429, "via": "http_fetch"}


@pytest.mark.parametrize("tool,via", [
    ("WebFetch", "http_fetch"),
    ("mcp__fetch__fetch", "http_fetch"),
    ("mcp__Claude_Browser__navigate", "browser"),
    ("mcp__remote-devices__Claude_Browser__navigate", "browser"),
    ("mcp__claude-in-chrome__navigate", "browser"),
    ("mcp__playwright__browser_navigate", "browser"),
    ("mcp__plugin_playwright_playwright__browser_navigate", "browser"),
    ("mcp__puppeteer__puppeteer_navigate", "browser"),
    ("mcp__chrome-devtools__navigate_page", "browser"),
    ("mcp__browser-fetch__fetch", "browser"),
    ("mcp__github__get_file", None),
    ("mcp__github__fetch_issue", None),
    ("mcp__memory__browser_history", None),
    ("mcp__fetch", "http_fetch"),
    ("Bash", None),
    ("WebFetchX", None),
    ("", None),
])
def test_via_comes_from_the_tool_name(tool, via):
    assert hook.via_for(tool) == via == _claude_code.via_for(tool)
    expected = {"via": via} if via else {}
    assert hook.signals(failure_event("timed out", tool=tool)) == expected
    assert _claude_code.signals(failure_event("timed out", tool=tool)) == expected


@pytest.mark.parametrize("payload", [None, "text", 5, [], {}, {"hook_event_name": "PostToolUse"},
                                     {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_response": "403"},
                                     {"hook_event_name": "PostToolUse", "tool_name": 5, "tool_response": {"code": True}}])
def test_odd_payloads_give_no_codes_and_no_error(payload):
    assert hook.signals(payload) == _claude_code.signals(payload)
    assert "http_status" not in hook.signals(payload)


# --- the hook: what goes on the wire -------------------------------------------------------------------

def serializable(value):
    try:
        json.dumps(value)
        return True
    except (TypeError, ValueError):
        return False


@pytest.mark.parametrize("status,headers,expected", [row for row in TABLE if row[0] not in (400, 404) and serializable(row[1])])
def test_the_request_carries_the_codes_and_nothing_else(hook_requests, status, headers, expected):
    output, _ = run_hook(post_tool_use(status, headers))
    (request,) = hook_requests
    query = query_of(request)
    assert set(query) <= ALLOWED_PARAMETERS
    assert {name: value for name, value in query.items() if name in expected} == {name: str(value) for name, value in expected.items()}
    assert query["site_host"] == "example.com" and query["failure_stage"] == "response_observed"
    assert request.get_method() == "GET" and request.data is None
    # The "Full JSON" link in the line has no codes: following it must not count the lookup twice.
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "http_status" not in context and "via=" not in context and "challenge=" not in context


def test_no_path_query_text_or_header_value_is_sent(hook_requests):
    secret = "SECRETVALUE9"
    payload = post_tool_use(
        403, {"Set-Cookie": f"session={secret}", "Authorization": f"Bearer {secret}", "X-Debug": secret, "Retry-After": secret,
              "cf-mitigated": f"challenge {secret}"},
        url=f"https://user:{secret}@Example.com:8443/account/{secret}?token={secret}#{secret}",
        result=f"Page text {secret}", body=secret, message=secret)
    run_hook(payload)
    (request,) = hook_requests
    assert set(query_of(request)) <= ALLOWED_PARAMETERS
    assert secret not in request.full_url and "account" not in request.full_url and "8443" not in request.full_url
    assert request.get_method() == "GET" and request.data is None
    for name, value in query_of(request).items():
        if name == "http_status":
            assert int(value) in STATUSES
        elif name == "challenge":
            assert value in CHALLENGES
        elif name == "retry_after":
            assert value in BANDS
        elif name == "via":
            assert value in VIAS


def test_a_thrown_error_sends_via_and_never_a_status(hook_requests):
    run_hook(failure_event("Request failed with status code 403 for /account/claims/12345"))
    (request,) = hook_requests
    assert query_of(request) == {"site_host": "example.com", "error_class": "site_access", "via": "http_fetch", "agent_vendor": "anthropic"}


def test_a_browser_tool_failure_sends_via_browser(hook_requests):
    run_hook(failure_event("net::ERR_CONNECTION_TIMED_OUT at https://example.com/x", tool="mcp__playwright__browser_navigate",
                           url="https://example.com/x"))
    (request,) = hook_requests
    assert query_of(request) == {"site_host": "example.com", "error_class": "service_error",
                                 "failure_stage": "before_target_response", "via": "browser"}


def test_a_503_retry_after_reaches_the_line_and_the_band_the_request(hook_requests):
    output, _ = run_hook(post_tool_use(503, {"Retry-After": "90"}))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.startswith("AHN example.com: HTTP 503, the site asks to retry after 90 s. recommendation=")
    assert query_of(hook_requests[0])["retry_after"] == "60_3600" and "90" not in hook_requests[0].full_url.split("?", 1)[1]


def test_values_outside_the_lists_are_dropped_before_sending():
    url = hook.lookup_url("example.com", "service_error", "response_observed", {
        "http_status": 404, "challenge": "yes", "retry_after": "30", "via": "curl", "extra": "x"})
    assert url == hook.lookup_url("example.com", "service_error", "response_observed")
    url = hook.lookup_url("example.com", "service_error", None, {"http_status": True, "challenge": ["none"], "via": 5})
    assert url == hook.lookup_url("example.com", "service_error", None)
    ok = {"via": "browser", "http_status": 503, "retry_after": "gt_3600", "challenge": "none"}
    assert hook.lookup_url("example.com", "service_error", None, ok).endswith(
        "&http_status=503&challenge=none&retry_after=gt_3600&via=browser")


def test_the_hook_still_only_reads():
    """The user promise: no POST. The lookup is one GET with query parameters and no body."""
    tree = ast.parse(HOOK_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Request":
            names = {keyword.arg for keyword in node.keywords}
            assert "data" not in names and "method" not in names and len(node.args) == 1
    source = HOOK_FILE.read_text(encoding="utf-8")
    assert "POST" not in source.replace("PostToolUse", "")
    readme = (ROOT / "plugins" / "claude-code" / "ahn" / "README.md").read_text(encoding="utf-8")
    assert "The hook never reports observations. It makes no POST requests and never calls `report_observation`." in readme


# --- parity between the hook and ahn-client -------------------------------------------------------------

def test_the_enum_lists_are_the_hooks():
    assert hook.SIGNAL_STATUSES == _signals.HTTP_STATUSES == frozenset(STATUSES)
    assert tuple(hook.CHALLENGES) == _signals.CHALLENGES and set(_signals.CHALLENGES) == CHALLENGES
    assert tuple(hook.RETRY_AFTER_BANDS) == _signals.RETRY_AFTER_BANDS and set(_signals.RETRY_AFTER_BANDS) == BANDS
    assert tuple(hook.VIAS) == _signals.VIAS and set(_signals.VIAS) == VIAS
    assert hook.RETRY_AFTER_SECONDS.pattern == _signals.RETRY_AFTER_SECONDS.pattern
    for name in ("BROWSER_TOOL", "FETCH_TOOL"):
        assert getattr(hook, name).pattern == getattr(_claude_code, name).pattern
        assert getattr(hook, name).flags == getattr(_claude_code, name).flags
    assert set(_signals.SIGNAL_NAMES) == ALLOWED_PARAMETERS - {"site_host", "error_class", "failure_stage", "agent_vendor"}


@pytest.mark.parametrize("value", ["0", "1", "59", "60", "61", "3599", "3600", "3601", "999999", "9999999999", "99999999999", "", " 7 ",
                                   "7.5", "1e3", "+5", "-1", "٣٠", "Wed, 21 Oct 2026 07:28:00 GMT", "30; x=1", 30, None, 3.5])
def test_retry_after_bands_agree(value):
    headers = {"Retry-After": value}
    assert hook.retry_after_code(headers) == _signals.retry_after_code(headers)
    assert hook.retry_after_code(headers) in BANDS | {None}


@pytest.mark.parametrize("headers", [{"cf-mitigated": "challenge"}, {"CF-MITIGATED": "CHALLENGE"}, {"cf-mitigated": "other"},
                                     {"cf-mitigated": 1}, {1: "challenge"}, {}, {"x-cf-mitigated": "challenge"},
                                     {"cf-mitigated": ["challenge"]}, {b"cf-mitigated": b"challenge"}, {"cf-mitigated": object()},
                                     {"cf-mitigated": [object(), "challenge"]}])
def test_challenge_headers_agree(headers):
    assert hook.challenge_code(headers) == _signals.challenge_code(headers)
    assert hook.header_entries(headers) == _signals.header_entries(headers)


@pytest.mark.parametrize("signals", [
    {}, {"via": "browser"}, {"http_status": 429, "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"},
    {"via": "curl", "http_status": 404}, {"retry_after": "gt_3600", "http_status": 503},
])
def test_lookup_urls_agree(signals):
    assert _client.lookup_url("example.com", "service_error", "response_observed", signals) == \
        hook.lookup_url("example.com", "service_error", "response_observed", signals)
    assert _signals.valid_signals(signals) == hook.valid_signals(signals)


PAYLOADS = [row for status, headers, _ in TABLE for row in (post_tool_use(status, headers),)] + [
    failure_event("403 Forbidden"), failure_event("timed out", tool="mcp__playwright__browser_navigate"),
    failure_event("timed out", tool="mcp__fetch__fetch"), failure_event("x", tool="Bash"), {}, None, "text",
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_the_claude_agent_sdk_copy_gives_the_hooks_codes(payload):
    assert _claude_code.signals(payload) == hook.signals(payload)


# --- ahn-client ----------------------------------------------------------------------------------------

def http_error(status, headers=None, module="httpx"):
    cls = type("HTTPStatusError", (Exception,), {"__module__": module})
    error = cls("failed")
    response = types.SimpleNamespace(status_code=status)
    if headers is not None:
        response.headers = headers
    error.response = response
    return error


@pytest.mark.parametrize("status,headers,expected", [
    (403, {"cf-mitigated": "challenge"}, {"http_status": 403, "challenge": "header", "via": "http_fetch"}),
    (403, {"server": "x"}, {"http_status": 403, "challenge": "none", "via": "http_fetch"}),
    (403, None, {"http_status": 403, "via": "http_fetch"}),
    (429, {"Retry-After": "300"}, {"http_status": 429, "challenge": "none", "retry_after": "60_3600", "via": "http_fetch"}),
    (503, {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, {"http_status": 503, "challenge": "none", "retry_after": "none", "via": "http_fetch"}),
    (404, {}, {"via": "http_fetch"}),
    (200, {}, {"via": "http_fetch"}),
])
def test_signals_from_an_http_library_error(status, headers, expected):
    assert ahn.signals(http_error(status, headers)) == expected


def test_signals_know_a_browser_error_by_its_module():
    assert ahn.signals(http_error(0, module="playwright._impl._errors")) == {"via": "browser"}
    assert ahn.signals(http_error(0, module="selenium.common.exceptions")) == {"via": "browser"}
    assert ahn.signals(http_error(0, module="aiohttp.client_exceptions")) == {"via": "http_fetch"}
    assert ahn.signals(http_error(0, module="mylib.errors")) == {}


def test_signals_via_can_be_given_and_wins():
    assert ahn.signals(http_error(0, module="httpx"), via="browser") == {"via": "browser"}
    assert ahn.signals(ValueError("x"), via="http_fetch") == {"via": "http_fetch"}
    assert ahn.signals("403 Forbidden", via="curl") == {}


@pytest.mark.parametrize("error", [ValueError("403"), "403 Forbidden", b"429", None, 5, KeyError("x")])
def test_signals_of_something_without_a_response_are_empty(error):
    assert ahn.signals(error) == {}


def test_signals_never_raise_on_odd_objects():
    class Boom(Exception):
        @property
        def response(self):
            raise RuntimeError("no response")

    class BadHeaders:
        status_code = 429

        @property
        def headers(self):
            raise RuntimeError("no headers")

    class ItemsRaise:
        status_code = 429
        headers = types.SimpleNamespace(items=lambda: 1 / 0)

    assert ahn.signals(Boom("x")) == {}
    for response in (BadHeaders(), ItemsRaise()):
        error = Exception("x")
        error.response = response
        assert ahn.signals(error) == {"http_status": 429}


def test_signals_read_the_headers_of_an_aiohttp_or_urllib_style_error():
    error = Exception("x")
    error.status = 503
    error.headers = {"Retry-After": "10"}
    assert ahn.signals(error) == {"http_status": 503, "challenge": "none", "retry_after": "lt_60"}


def test_signals_have_no_header_value_in_them():
    error = http_error(429, {"Retry-After": "30", "Set-Cookie": "session=SECRETVALUE9", "cf-mitigated": "challenge"})
    assert "SECRET" not in repr(ahn.signals(error)) and "30" not in repr(ahn.signals(error))


def test_check_sends_the_codes(client_requests):
    answer = ahn.check("https://example.com/private?x=1", "service_error", "response_observed",
                       http_status=429, challenge="none", retry_after="lt_60", via="http_fetch")
    assert answer is not None
    (request,) = client_requests
    assert query_of(request) == {"site_host": "example.com", "error_class": "service_error", "failure_stage": "response_observed",
                                 "http_status": "429", "challenge": "none", "retry_after": "lt_60", "via": "http_fetch"}
    assert request.get_method() == "GET" and request.data is None
    assert request.get_header("User-agent") == f"ahn-client/{ahn.__version__} (+https://agenthelpnetwork.com)"


def test_check_without_codes_sends_what_it_sent_before(client_requests):
    ahn.check("example.com", "site_access", "response_observed")
    assert query_of(client_requests[0]) == {"site_host": "example.com", "error_class": "site_access", "failure_stage": "response_observed"}


@pytest.mark.parametrize("bad", [{"http_status": 404}, {"http_status": True}, {"http_status": "429"}, {"http_status": 99}, {"challenge": "yes"},
                                 {"challenge": True}, {"retry_after": "30"}, {"retry_after": 30}, {"via": "curl"}, {"via": ["browser"]}])
def test_check_drops_a_code_outside_the_lists_but_still_looks_up(client_requests, bad):
    assert ahn.check("example.com", "site_access", None, **bad) is not None
    (request,) = client_requests
    assert query_of(request) == {"site_host": "example.com", "error_class": "site_access"}


def test_codes_do_not_split_the_cache(client_requests):
    ahn.check("example.com", "site_access", "response_observed", http_status=403)
    ahn.check("example.com", "site_access", "response_observed", http_status=451)
    assert len(client_requests) == 1


def test_check_still_never_raises_and_gives_none_without_a_host(client_requests):
    assert ahn.check("localhost", "site_access", http_status=403) is None
    assert ahn.check("example.com", "bogus", http_status=403) is None
    assert client_requests == []


def test_the_langchain_adapter_sends_the_codes_of_the_tool_exception(client_requests):
    error = http_error(429, {"Retry-After": "30"})
    error.request = types.SimpleNamespace(url="https://example.com/private/page?token=abc")
    note = _adapter.note_for(error, {"url": "https://example.com/private/page?token=abc"}, adapter="langchain")
    assert note and note.startswith("AHN example.com:")
    (request,) = client_requests
    query = query_of(request)
    assert query["site_host"] == "example.com" and query["http_status"] == "429" and query["retry_after"] == "lt_60"
    assert query["via"] == "http_fetch" and query["challenge"] == "none"
    assert "private" not in request.full_url and "abc" not in request.full_url
    assert request.get_header("User-agent").startswith(f"ahn-client/{ahn.__version__} (+langchain;")


def test_an_adapter_error_without_a_response_sends_no_codes(client_requests):
    error = Exception("Client error '403 Forbidden' for url 'https://example.com/x'")
    note = _adapter.note_for(error, {"url": "https://example.com/x"}, adapter="crewai")
    assert note
    assert query_of(client_requests[0]) == {"site_host": "example.com", "error_class": "site_access"}


def test_the_original_error_is_untouched_by_reading_its_codes():
    error = http_error(429, {"Retry-After": "30"})
    before = (error.args, error.response.status_code, dict(error.response.headers))
    ahn.signals(error)
    _adapter.failure_signals(error)
    assert (error.args, error.response.status_code, dict(error.response.headers)) == before


def test_a_failed_lookup_with_codes_adds_nothing(monkeypatch):
    def fail(request, timeout):
        raise OSError("down")

    monkeypatch.setattr(_client, "_urlopen", fail)
    error = http_error(503, {"Retry-After": "5"})
    assert _adapter.note_for(error, {"url": "https://example.com/x"}, adapter="langchain") is None
    assert ahn.check("example.com", "service_error", http_status=503) is None


def test_the_claude_agent_sdk_hook_sends_the_codes_of_the_payload(client_requests):
    hook_callback = ahn_claude.make_hook()
    result = asyncio.run(hook_callback(post_tool_use(429, {"Retry-After": "7200"}), None, None))
    assert result["hookSpecificOutput"]["additionalContext"].startswith("AHN example.com:")
    (request,) = client_requests
    assert query_of(request) == {"site_host": "example.com", "error_class": "service_error", "failure_stage": "response_observed",
                                 "http_status": "429", "challenge": "none", "retry_after": "gt_3600", "via": "http_fetch"}


def test_the_timeout_stays_two_seconds(monkeypatch):
    assert _adapter.DEFAULT_TIMEOUT == 2.0
    seen = []

    def fake(request, timeout):
        seen.append(timeout)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", fake)
    _adapter.note_for(http_error(403, {}), {"url": "https://example.com/x"}, adapter="langchain")
    assert seen == [2.0]


# --- versions and documents ---------------------------------------------------------------------------

def test_versions_and_the_documents_name_the_codes():
    manifest = json.loads((ROOT / "plugins/claude-code/ahn/.claude-plugin/plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))
    assert manifest["version"] == market["plugins"][0]["version"] == "0.3.3"
    assert ahn.__version__ == "0.5.0"
    plugin_readme = (ROOT / "plugins/claude-code/ahn/README.md").read_text(encoding="utf-8")
    client_readme = (ROOT / "clients/python/README.md").read_text(encoding="utf-8")
    assert "version 0.3.3 |" in plugin_readme
    for name in ("http_status", "challenge", "retry_after", "via"):
        assert f"`{name}`" in plugin_readme and f"`{name}`" in client_readme
    for value in ("lt_60", "60_3600", "gt_3600", "http_fetch", "browser", "header"):
        assert f"`{value}`" in plugin_readme and f"`{value}`" in client_readme
    assert "ahn-client/0.5.0" in client_readme and "ahn-client/0.4.0" not in client_readme
    # Nothing is promised that does not exist yet.
    for text in (plugin_readme, client_readme):
        assert "AHN_HOOK_FEEDBACK" not in text and "feedback token" not in text


# --- review round: Full JSON link, guards, text path, mutants --------------------------------------------

FULL_JSON = ("Full JSON: https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com&error_class=service_error"
             "&failure_stage=response_observed.")


def test_the_client_result_link_has_no_codes_and_the_cache_reuses_it(client_requests):
    first = ahn.check("example.com", "service_error", "response_observed", http_status=429, challenge="none",
                      retry_after="lt_60", via="http_fetch")
    assert "http_status=429" in client_requests[0].full_url
    assert first.url == "https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com&error_class=service_error&failure_stage=response_observed"
    assert FULL_JSON in first.summary()
    for name in ("http_status", "challenge", "retry_after", "via="):
        assert name not in first.summary() and name not in first.url
    second = ahn.check("example.com", "service_error", "response_observed", http_status=503, via="browser")
    assert second is first and len(client_requests) == 1  # the key is host, class and stage: the codes do not split the cache


def test_the_adapter_and_sdk_lines_have_no_codes_in_their_link(client_requests):
    error = http_error(429, {"x": "y"})
    note = _adapter.note_for(error, {"url": "https://example.com/x"}, adapter="langchain")
    assert "retry_after=" in client_requests[0].full_url
    assert FULL_JSON in note and "http_status" not in note and "via=" not in note
    ahn.clear_cache()
    client_requests.clear()
    result = asyncio.run(ahn_claude.make_hook()(post_tool_use(429, {"x": "y"}), None, None))
    line = result["hookSpecificOutput"]["additionalContext"]
    assert "http_status=429" in client_requests[0].full_url
    assert FULL_JSON in line and "http_status" not in line
    assert "challenge=" not in line and "via=" not in line and len(line) <= 700


def test_the_hook_and_client_lines_are_the_same_for_the_same_answer(hook_requests, client_requests):
    output, _ = run_hook(post_tool_use(429, {"x": "y"}))
    line = output["hookSpecificOutput"]["additionalContext"]
    result = asyncio.run(ahn_claude.make_hook()(post_tool_use(429, {"x": "y"}), None, None))
    # The plugin's link also carries agent_vendor=anthropic for WebFetch (D48); the client adapter sends no vendor.
    assert result["hookSpecificOutput"]["additionalContext"].split("Full JSON:")[1] == line.split("Full JSON:")[1].replace("&agent_vendor=anthropic", "")


class LowerRaises(str):
    def lower(self):
        raise RuntimeError("no lower")

    def strip(self, *args):
        raise RuntimeError("no strip")


def test_a_header_value_that_raises_costs_no_lookup_and_no_exception(hook_requests, client_requests):
    bad = {"Retry-After": LowerRaises("30"), "cf-mitigated": LowerRaises("challenge")}
    error = http_error(429, bad)
    assert ahn.signals(error) == {}
    assert _adapter.failure_signals(error) == {}
    assert hook.signals(post_tool_use(429, bad)) == {} and _claude_code.signals(post_tool_use(429, bad)) == {}
    assert ahn.check("example.com", "service_error") is not None


def test_an_async_adapter_sends_the_codes(client_requests):
    error = http_error(503, {"Retry-After": "5000"})
    note = asyncio.run(_adapter.anote_for(error, {"url": "https://example.com/x"}, adapter="openai-agents"))
    assert note and note.startswith("AHN example.com:")
    query = query_of(client_requests[0])
    assert query["http_status"] == "503" and query["retry_after"] == "gt_3600" and query["via"] == "http_fetch"


def test_a_failing_signal_reader_costs_the_adapter_nothing(client_requests, monkeypatch):
    def broken(error, **kwargs):
        raise RuntimeError("signals broke")

    monkeypatch.setattr(_adapter, "signals", broken)
    error = http_error(429, {"Retry-After": "5"})
    assert _adapter.failure_signals(error) == {}
    note = _adapter.note_for(error, {"url": "https://example.com/x"}, adapter="langchain")
    assert note and set(query_of(client_requests[0])) <= {"site_host", "error_class", "failure_stage"}
    ahn.clear_cache()
    client_requests.clear()
    assert asyncio.run(_adapter.anote_for(error, {"url": "https://example.com/x"}, adapter="langchain"))
    assert set(query_of(client_requests[0])) <= {"site_host", "error_class", "failure_stage"}


@pytest.mark.parametrize("error", [
    Exception("playwright browser timed out at https://example.com"),
    TimeoutError("selenium chrome headless timed out"),
    "browser playwright timeout",
])
def test_via_is_never_decided_from_a_message(error):
    assert ahn.signals(error) == {}
    assert "via" not in hook.signals(failure_event(error if isinstance(error, str) else str(error), tool="Bash"))


@pytest.mark.parametrize("error,expected", [
    ("Request to https://example.com/api/429?retry_after=99 failed: 503 Retry-After: 45", "HTTP 503, the site asks to retry after 45 s."),
    ("failed https://example.com/err/503 Retry-After: 30", ""),
    ("GET https://example.com/429/retry-after-30 returned an error", ""),
    ("503.example.com is down, retry-after 9", ""),
    ("429 Too Many Requests, retry after 12", "HTTP 429, the site asks to retry after 12 s."),
])
def test_the_text_retry_note_ignores_numbers_in_urls_and_the_host_name(error, expected):
    assert hook.retry_note(failure_event(error), "503.example.com" if "503.example.com is" in error else "example.com") == expected


def test_a_failing_hook_signal_reader_still_looks_up(hook_requests, monkeypatch):
    def broken(payload):
        raise RuntimeError("broke")

    monkeypatch.setattr(hook, "_signals", broken)
    run_hook(post_tool_use(429, {"x": "y"}))
    assert query_of(hook_requests[0]) == {"site_host": "example.com", "error_class": "service_error", "failure_stage": "response_observed",
                                                 "agent_vendor": "anthropic"}
