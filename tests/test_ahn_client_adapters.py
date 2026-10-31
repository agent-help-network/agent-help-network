"""clients/python framework adapters (LangChain, CrewAI, OpenAI Agents SDK, Claude Agent SDK).

Pure: no database, no network (the transport is replaced) and none of the frameworks needs to be installed.
The tests named ``*_real_*`` / ``*_real_agent`` run the real framework and are skipped (importorskip) when it is not installed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import io
import json
import logging
import sys
import threading
import time
import types
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "clients" / "python" / "src"))

import ahn_client as ahn  # noqa: E402
from ahn_client import _adapter, _claude_code, _client, _hosts  # noqa: E402
from ahn_client import claude_agent_sdk as ahn_claude  # noqa: E402
from ahn_client import crewai as ahn_crewai  # noqa: E402
from ahn_client import langchain as ahn_langchain  # noqa: E402
from ahn_client import openai_agents as ahn_openai  # noqa: E402

spec = importlib.util.spec_from_file_location("ahn_claude_code_hook", ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "ahn_lookup.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

ANSWER = {
    "site_host": "example.com", "current_site_status": "unknown", "evidence_level": "no_reports",
    "observations": [], "provider_status": None, "official_status_page": None,
    "access_profile": {
        "robots_status": "ok", "llms_txt_status": "absent",
        "robots": {"agents_blocked": ["Claude-User", "ChatGPT-User"], "agents_allowed": ["Googlebot"], "rules": []},
    },
    "official_paths": {"name": "Example", "api_docs": "https://developer.example.com/apis", "developer_portal": None, "note": None},
    "recommendation": {"action": "use_official_path", "confidence": "high", "reasons": ["robots_blocks_agents"]},
    "next_checks": [{"code": "response_observed", "text": "..."}],
}


class FakeResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    ahn.clear_cache()
    monkeypatch.delenv("AHN_CLIENT_DISABLE", raising=False)
    monkeypatch.delenv("AHN_CLIENT_SKIP_HOSTS", raising=False)

    def refuse(request, timeout):
        raise AssertionError("unexpected network request: " + request.full_url)

    monkeypatch.setattr(_client, "_urlopen", refuse)
    yield
    ahn.clear_cache()


@pytest.fixture
def transport(monkeypatch):
    """Replace the transport; returns the list of requests made."""
    calls = []

    def install(body=ANSWER, error=None):
        def fake(request, timeout):
            calls.append((request, timeout))
            if error:
                raise error
            return FakeResponse(json.dumps(body).encode("utf-8"))

        monkeypatch.setattr(_client, "_urlopen", fake)
        return calls

    return install


class ToolInvocationError(Exception):
    """langchain's name for a tool call whose arguments failed validation."""


class AttributeError_(Exception):
    """An exception with arbitrary attributes, such as the ones HTTP libraries set."""

    def __init__(self, **attributes):
        super().__init__("failed")
        for name, value in attributes.items():
            setattr(self, name, value)


class HTTPStatusError(Exception):
    """Stands in for httpx.HTTPStatusError: the class name and the message are all the client reads."""

    def __init__(self, message, request_url=None, status=None):
        super().__init__(message)
        if request_url:
            self.request = SimpleNamespace(url=request_url)
        if status:
            self.response = SimpleNamespace(status_code=status)


def forbidden(url="https://example.com/private/page?token=abc"):
    """What httpx raises: the message names the URL and the exception carries the request."""
    return HTTPStatusError(f"Client error '403 Forbidden' for url '{url}'", request_url=url)


def not_found(url="https://example.com/missing"):
    return HTTPStatusError(f"Client error '404 Not Found' for url '{url}'", request_url=url)


def sent(calls):
    """(host, error_class, failure_stage, user agent) of each request made."""
    rows = []
    for request, _ in calls:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        rows.append((query["site_host"][0], query.get("error_class", [None])[0], query.get("failure_stage", [None])[0],
                     request.get_header("User-agent")))
    return rows


# --- the shared core -------------------------------------------------------------------------------------

@pytest.mark.parametrize("args,expected", [
    ({"url": "https://www.Example.com/a/b?token=secret#frag"}, "example.com"),
    ('{"url": "https://example.com/x"}', "example.com"),
    (b'{"website_url": "https://example.com/x"}', "example.com"),
    ({"query": "cats", "target": "https://example.org/page"}, "example.org"),
    ({"url": "https://first.example.com/", "other": "https://second.example.com/"}, "first.example.com"),
    ({"other": "https://second.example.com/", "url": "https://first.example.com/"}, "first.example.com"),
    # Not an http(s) URL as a whole: nothing is read.
    ({"query": "see https://example.com/page for details"}, None),
    ({"url": "ftp://example.com/file"}, None),
    ({"url": "example.com/page"}, None),
    ({"conn": "postgres://user:pw@db.example.com/app"}, None),
    ({"url": 42}, None),
    ({}, None),
    (None, None),
    ("not json", None),
    ("[1, 2]", None),
    ({"url": "http://localhost:3000/x"}, "localhost"),  # extracted; check() refuses it later
])
def test_host_from_args(args, expected):
    assert _adapter.host_from_args(args) == expected


def test_host_from_error_reads_only_the_request_an_exception_carries():
    assert _adapter.host_from_error(HTTPStatusError("boom", request_url="https://api.example.com/v1?key=1")) == "api.example.com"
    assert _adapter.host_from_error(forbidden("https://www.example.net/a?b=c")) == "example.net"
    # requests puts the URL on the response, aiohttp on request_info, urllib on the error itself.
    assert _adapter.host_from_error(AttributeError_(response=SimpleNamespace(url="https://r.example.com/x"))) == "r.example.com"
    assert _adapter.host_from_error(AttributeError_(request_info=SimpleNamespace(url="https://a.example.com/x"))) == "a.example.com"
    assert _adapter.host_from_error(AttributeError_(url="https://u.example.com/x")) == "u.example.com"
    # The text of an error is never searched for URLs, whether it is an exception's message or a plain string: it links to help
    # pages (errors.pydantic.dev, docs.mistral.ai, platform.openai.com) and to other hosts.
    for text in ("GET https://example.org/page failed: 502", b"GET https://example.org/page failed: 502",
                 "see https://errors.pydantic.dev/2.11/v/missing then https://example.org/x", "see https://docs.mistral.ai/api/",
                 "see https://platform.openai.com/docs/errors"):
        assert _adapter.host_from_error(text) is None
    assert _adapter.host_from_error(RuntimeError("Timeout navigating to https://example.org/page")) is None
    assert _adapter.host_from_error(HTTPStatusError("Client error '403 Forbidden' for url 'https://example.org/'")) is None
    assert _adapter.host_from_error(RuntimeError("no address here")) is None
    assert _adapter.host_from_error(None) is None
    try:
        try:
            raise HTTPStatusError("inner", request_url="https://cause.example.com/")
        except Exception as inner:
            raise RuntimeError("outer") from inner
    except RuntimeError as outer:
        assert _adapter.host_from_error(outer) == "cause.example.com"


@pytest.mark.parametrize("error,args,expected", [
    (forbidden(), {"url": "https://example.com/p"}, ("example.com", "site_access", None)),
    (HTTPStatusError("boom", status=403), {"url": "https://example.com/p"}, ("example.com", "site_access", "response_observed")),
    (forbidden(), None, ("example.com", "site_access", None)),
    (HTTPStatusError("503 Service Unavailable"), {"url": "https://example.com/p"}, ("example.com", "service_error", None)),
    (RuntimeError("Please solve the CAPTCHA"), {"url": "https://example.com/p"}, ("example.com", "human_verification", None)),
    # A site named like a class: its own name never decides the class.
    (forbidden("https://login.example.com/x"), None, ("login.example.com", "site_access", None)),
    (forbidden("https://developers.cloudflare.com/x"), None, ("developers.cloudflare.com", "site_access", None)),
    # Not a block and not an outage, or nothing to look up.
    (not_found(), {"url": "https://example.com/p"}, None),
    (HTTPStatusError("422 Unprocessable Entity"), {"url": "https://example.com/p"}, None),
    (forbidden(), {"query": "no url here"}, ("example.com", "site_access", None)),
    (RuntimeError("403 Forbidden"), {"query": "no url here"}, None),
    (RuntimeError("something odd"), {"url": "https://example.com/p"}, None),
    # A tool that was given a URL but failed for another reason: nothing web-like in the error, so no lookup.
    (PermissionError(13, "Permission denied"), {"url": "https://example.com/p"}, None),
    (RuntimeError("Task blocked on mutex"), {"url": "https://example.com/p"}, None),
    (RuntimeError("Please reset your password"), {"url": "https://example.com/p"}, None),
    (KeyError("denied"), {"url": "https://example.com/p"}, None),
    (ConnectionRefusedError("[Errno 111] Connection refused"), {"url": "https://example.com/p"}, ("example.com", "site_access", None)),
    (TimeoutError("timed out"), {"url": "https://example.com/p"}, ("example.com", "service_error", None)),
    (RuntimeError("blocked by the site"), {"url": "https://example.com/p"}, ("example.com", "site_access", None)),
    (RuntimeError("Access denied (403)"), {"url": "https://example.com/p"}, ("example.com", "site_access", None)),
    # Tool-argument validation is not a web failure, whatever URL the arguments hold.
    (ToolInvocationError("Error invoking tool 'fetch' with kwargs {'url': 'https://example.com/p'} with error: url Field required"),
     {"url": "https://example.com/p"}, None),
    (RuntimeError("1 validation error for fetch url Input should be a valid URL, got 403 "
                  "For further information visit https://errors.pydantic.dev/2.11/v/url_parsing"),
     {"url": "https://example.com/p"}, None),
])
def test_failure_target(error, args, expected):
    target = _adapter.failure_target(error, args)
    if expected is None:
        assert target is None
    else:
        assert target[0] == expected[0] and target[1] == expected[1]
        if expected[2]:
            assert target[2] == expected[2]


def test_classify_ignores_the_named_host_only_when_given():
    text = "Client error '403 Forbidden' for url 'https://login.example.com/x'"
    assert ahn.classify(text) == ("login_auth", None)  # unchanged without a host
    assert ahn.classify(text, "login.example.com") == ("site_access", None)
    assert ahn.classify(text, host="example.com") == ("site_access", None)  # subdomains of the host too


def test_note_for_sends_only_the_host_and_codes_and_names_the_adapter(transport):
    calls = transport()
    note = _adapter.note_for(forbidden("https://user:pw@www.Example.com:8443/private/page?token=abc"),
                             {"url": "https://www.Example.com/private/page?token=abc"}, adapter="langchain")
    assert note and note.startswith("AHN example.com: recommendation=use_official_path (high).")
    assert len(note) <= 700
    request, timeout = calls[0]
    assert sent(calls) == [("example.com", "site_access", None, f"ahn-client/{ahn.__version__} (+langchain; +https://agenthelpnetwork.com)")]
    for secret in ("pw", "private", "token", "abc", "8443", "user"):
        assert secret not in request.full_url
    assert request.get_method() == "GET" and request.data is None
    assert {name.lower() for name in request.headers} == {"user-agent", "accept"}
    assert timeout == _adapter.DEFAULT_TIMEOUT


def test_note_for_is_none_without_a_lookup_for_non_failures(transport):
    calls = transport()
    for error, args in [(not_found(), {"url": "https://example.com/x"}), (RuntimeError("x"), None), (None, None), (42, {"url": 1}),
                        (forbidden("http://localhost:3000/x"), {"url": "http://localhost:3000/x"}),
                        (forbidden("http://10.0.0.1/x"), None), (forbidden("https://printer.local/x"), None),
                        (forbidden("https://agenthelpnetwork.com/x"), None)]:
        assert _adapter.note_for(error, args, adapter="langchain") is None
    assert calls == []


def test_disable_and_skip_hosts_send_nothing(monkeypatch, transport):
    calls = transport()
    monkeypatch.setenv("AHN_CLIENT_SKIP_HOSTS", "corp.example.com")
    assert _adapter.note_for(forbidden("https://wiki.corp.example.com/x"), None, adapter="crewai") is None
    monkeypatch.setenv("AHN_CLIENT_DISABLE", "1")
    assert _adapter.note_for(forbidden(), None, adapter="crewai") is None
    assert calls == []


@pytest.mark.parametrize("error", [urllib.error.URLError("down"), TimeoutError("slow"), ValueError("bad"), OSError("reset")])
def test_a_failing_lookup_gives_none_not_an_exception(error, transport):
    transport(error=error)
    assert _adapter.note_for(forbidden(), None, adapter="langchain") is None


def test_a_slow_lookup_is_abandoned_after_the_timeout(monkeypatch):
    release = threading.Event()

    def slow(request, timeout):
        release.wait(5)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", slow)
    started = time.monotonic()
    assert _adapter.note_for(forbidden(), None, adapter="langchain", timeout=0.2) is None
    assert time.monotonic() - started < 2
    # The same lookup while the first still runs gets an immediate None instead of another thread.
    started = time.monotonic()
    assert _adapter.note_for(forbidden(), None, adapter="langchain", timeout=0.2) is None
    assert time.monotonic() - started < 0.15
    release.set()
    deadline = time.monotonic() + 3
    while _adapter._inflight and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not _adapter._inflight


def test_repeated_failures_are_answered_from_the_cache(transport):
    calls = transport()
    for _ in range(3):
        assert _adapter.note_for(forbidden(), None, adapter="langchain")
    assert len(calls) == 1


def test_anote_for_runs_off_the_event_loop(transport):
    calls = transport()
    assert asyncio.run(_adapter.anote_for(forbidden(), None, adapter="crewai"))
    assert sent(calls)[0][3].endswith("(+crewai; +https://agenthelpnetwork.com)")
    assert asyncio.run(_adapter.anote_for(not_found(), None, adapter="crewai")) is None


def test_the_async_wait_ends_at_the_timeout_even_when_the_default_executor_is_busy(monkeypatch):
    # run_in_executor(None, ...) would queue behind these jobs and overshoot the timeout several times over.
    release = threading.Event()

    def slow(request, timeout):
        release.wait(5)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", slow)

    async def run():
        loop = asyncio.get_running_loop()
        jobs = [loop.run_in_executor(None, time.sleep, 1.5) for _ in range(64)]
        started = time.monotonic()
        note = await _adapter.anote_for(forbidden(), None, adapter="langchain", timeout=0.2)
        elapsed = time.monotonic() - started
        release.set()
        await asyncio.gather(*jobs)
        return note, elapsed

    note, elapsed = asyncio.run(run())
    assert note is None and elapsed < 0.7


def test_the_async_loop_keeps_running_while_a_lookup_waits(monkeypatch):
    release = threading.Event()

    def slow(request, timeout):
        release.wait(5)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", slow)

    async def run():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        await _adapter.anote_for(forbidden(), None, adapter="langchain", timeout=0.3)
        task.cancel()
        release.set()
        return ticks

    assert asyncio.run(run()) >= 10


def test_keyboard_interrupt_is_not_swallowed(monkeypatch, transport):
    transport()

    class Interrupting:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise KeyboardInterrupt

    class Failing(Interrupting):
        def start(self):
            raise RuntimeError("can not start new thread")

    monkeypatch.setattr(_adapter.threading, "Thread", Interrupting)
    with pytest.raises(KeyboardInterrupt):
        _adapter.bounded_check("example.com", "site_access", None, "langchain")
    assert not _adapter._inflight  # and the lookup is not left marked as running
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(_adapter.abounded_check("example.com", "site_access", None, "langchain"))
    assert not _adapter._inflight
    # An ordinary failure to start a thread is still just "no answer".
    monkeypatch.setattr(_adapter.threading, "Thread", Failing)
    assert _adapter.bounded_check("example.com", "site_access", None, "langchain") is None
    assert not _adapter._inflight


def test_the_user_agent_format_the_server_can_count():
    # docs: UA starts with "ahn-client/", then the version, then "(+<adapter>; +https://agenthelpnetwork.com)".
    import re
    pattern = re.compile(r"^ahn-client/\d+\.\d+\.\d+ \((?:\+(?P<adapter>[a-z][a-z0-9_-]{0,23}); )?\+https://agenthelpnetwork\.com\)$")
    for adapter in (None, "langchain", "crewai", "openai-agents", "claude-agent-sdk", "browser-use"):
        match = pattern.fullmatch(_client.user_agent(adapter))
        assert match and match.group("adapter") == adapter
    assert ahn_langchain.ADAPTER == "langchain" and ahn_crewai.ADAPTER == "crewai"
    assert ahn_openai.ADAPTER == "openai-agents" and ahn_claude.ADAPTER == "claude-agent-sdk"


# --- LangChain -------------------------------------------------------------------------------------------

class FakeToolMessage:
    """The part of langchain's ToolMessage the adapter uses."""

    def __init__(self, content, tool_call_id=None, name=None, status="success"):
        self.content, self.tool_call_id, self.name, self.status = content, tool_call_id, name, status

    def model_copy(self, update=None):
        clone = FakeToolMessage(self.content, self.tool_call_id, self.name, self.status)
        for key, value in (update or {}).items():
            setattr(clone, key, value)
        return clone


@pytest.fixture
def lc(monkeypatch):
    monkeypatch.setattr(ahn_langchain, "_tool_message_cls", lambda: FakeToolMessage)
    return SimpleNamespace(
        request=SimpleNamespace(tool_call={"name": "fetch_page", "args": {"url": "https://example.com/private?x=1"}, "id": "call_1"}))


def raising(error):
    def handler(request):
        raise error

    return handler


def test_langchain_by_default_a_raised_exception_is_reraised_untouched_and_nothing_is_looked_up(lc, transport):
    calls = transport()
    try:
        raise forbidden() from None  # a tool that raises "from None": __suppress_context__ is True
    except HTTPStatusError as built:
        original = built
    with pytest.raises(HTTPStatusError) as caught:
        ahn_langchain.handle_tool_call(lc.request, raising(original))
    assert caught.value is original and caught.value.__suppress_context__ is True and caught.value.__cause__ is None
    assert calls == []  # the same whether or not Agent Help Network is reachable, so it is not even asked


def test_langchain_converted_exception_has_no_exception_text_and_the_same_shape_every_time(lc, transport):
    calls = transport()
    original = forbidden("https://example.com/private/page?token=abc")
    result = ahn_langchain.handle_tool_call(lc.request, raising(original), convert_exceptions=True)
    assert (result.status, result.tool_call_id, result.name) == ("error", "call_1", "fetch_page")
    first, _, rest = result.content.partition("\n")
    assert first == "Error: HTTPStatusError (site_access)"
    assert rest.startswith("AHN example.com: recommendation=use_official_path")
    for leaked in ("403", "Forbidden", "token", "abc", "private", "page", "Client error", repr(original)):
        assert leaked not in first
    assert sent(calls)[0][3].endswith("(+langchain; +https://agenthelpnetwork.com)")
    # Agent Help Network unreachable, slow or excluded: the same conversion, only without the line.
    for setup in ("down", "disabled"):
        ahn.clear_cache()
        if setup == "down":
            transport(error=urllib.error.URLError("down"))
        else:
            transport()
            os_env = pytest.MonkeyPatch()
            os_env.setenv("AHN_CLIENT_DISABLE", "1")
        try:
            plain = ahn_langchain.handle_tool_call(lc.request, raising(original), convert_exceptions=True)
        finally:
            if setup == "disabled":
                os_env.undo()
        assert plain.content == "Error: HTTPStatusError (site_access)" and plain.status == "error"


@pytest.mark.parametrize("error", [not_found(), RuntimeError("tool crashed"), KeyError("x"),
                                   ToolInvocationError("Error invoking tool 'fetch' with kwargs {} with error: url Field required"),
                                   PermissionError(13, "Permission denied")])
def test_langchain_exceptions_that_are_not_web_failures_propagate_even_when_converting(lc, transport, error):
    calls = transport()
    with pytest.raises(type(error)) as caught:
        ahn_langchain.handle_tool_call(lc.request, raising(error), convert_exceptions=True)
    assert caught.value is error
    assert calls == []


def test_langchain_when_the_message_cannot_be_built_the_original_exception_is_reraised_as_it_was(lc, transport, monkeypatch):
    transport()
    try:
        raise forbidden() from None
    except HTTPStatusError as built:
        original = built

    def broken():
        raise ImportError("langchain.messages")

    monkeypatch.setattr(ahn_langchain, "_tool_message_cls", broken)
    with pytest.raises(HTTPStatusError) as caught:
        ahn_langchain.handle_tool_call(lc.request, raising(original), convert_exceptions=True)
    assert caught.value is original and caught.value.__suppress_context__ is True and caught.value.__cause__ is None


def test_langchain_graph_control_flow_is_never_a_tool_failure(lc, transport):
    calls = transport()

    class GraphBubbleUp(Exception):
        pass

    class GraphInterrupt(GraphBubbleUp):
        pass

    class NodeTimeoutError(Exception):
        pass

    invalid_resume = ValueError("403 Forbidden for https://example.com/ resume value")
    invalid_resume._langgraph_invalid_resume = True  # what langgraph.errors.is_invalid_resume reads
    for signal in (GraphInterrupt("403 Forbidden for https://example.com/"), NodeTimeoutError("timed out 503"), invalid_resume):
        with pytest.raises(type(signal)) as caught:
            ahn_langchain.handle_tool_call(lc.request, raising(signal), convert_exceptions=True)
        assert caught.value is signal
        with pytest.raises(type(signal)):
            asyncio.run(ahn_langchain.ahandle_tool_call(lc.request, async_raising(signal), convert_exceptions=True))
    assert calls == []


def async_raising(error):
    async def handler(request):
        raise error

    return handler


def test_langchain_results_pass_through_unless_they_are_error_messages(lc, transport):
    calls = transport()
    ok = FakeToolMessage("HTTP 403 text that a page happened to contain", "call_1", status="success")
    command = object()  # langgraph Command: no status, no content
    assert ahn_langchain.handle_tool_call(lc.request, lambda request: ok) is ok
    assert ahn_langchain.handle_tool_call(lc.request, lambda request: command) is command
    not_a_block = FakeToolMessage("Error: 404 Not Found", "call_1", status="error")
    assert ahn_langchain.handle_tool_call(lc.request, lambda request: not_a_block) is not_a_block
    assert calls == []


def test_langchain_error_message_from_the_tool_gets_the_line_appended(lc, transport):
    transport()
    message = FakeToolMessage("Error: 429 Too Many Requests", "call_1", "fetch_page", status="error")
    result = ahn_langchain.handle_tool_call(lc.request, lambda request: message)
    assert result is not message and message.content == "Error: 429 Too Many Requests"  # the original is not modified
    assert result.content.startswith("Error: 429 Too Many Requests\nAHN example.com:")
    assert (result.status, result.tool_call_id, result.name) == ("error", "call_1", "fetch_page")
    blocks = FakeToolMessage([{"type": "text", "text": "Error: 429 Too Many Requests"}], "call_1", status="error")
    appended = ahn_langchain.handle_tool_call(lc.request, lambda request: blocks)
    assert appended.content[0] == {"type": "text", "text": "Error: 429 Too Many Requests"}
    assert appended.content[1]["text"].startswith("AHN example.com:")
    # What ToolRetryMiddleware returns after its last attempt carries the exception text, so it is read the same way.
    retry = FakeToolMessage("Tool 'fetch_page' failed after 3 attempts with HTTPStatusError: Client error '403 Forbidden'. Please try again.",
                            "call_1", status="error")
    assert ahn_langchain.handle_tool_call(lc.request, lambda request: retry).content.endswith(
        "bypassing site controls is not advised.")
    # A validation error that ToolNode turned into a message is not a web failure.
    validation = FakeToolMessage("Error invoking tool 'fetch_page' with kwargs {'url': 'https://example.com/private?x=1'} with error: "
                                 "url Field required For further information visit https://errors.pydantic.dev/2.11/v/missing",
                                 "call_1", status="error")
    assert ahn_langchain.handle_tool_call(lc.request, lambda request: validation) is validation


def test_langchain_async_twin(lc, transport):
    transport()
    original = forbidden()

    async def returning(request):
        return FakeToolMessage("Error: 503 Service Unavailable", "call_1", status="error")

    async def fine(request):
        return FakeToolMessage("ok", "call_1")

    async def run():
        converted = await ahn_langchain.ahandle_tool_call(lc.request, async_raising(original), convert_exceptions=True)
        annotated = await ahn_langchain.ahandle_tool_call(lc.request, returning)
        plain = await ahn_langchain.ahandle_tool_call(lc.request, fine)
        with pytest.raises(HTTPStatusError) as caught:
            await ahn_langchain.ahandle_tool_call(lc.request, async_raising(original))  # the default re-raises
        assert caught.value is original
        return converted, annotated, plain

    converted, annotated, plain = asyncio.run(run())
    assert converted.status == "error" and converted.content.startswith("Error: HTTPStatusError (site_access)\nAHN example.com:")
    assert annotated.content.startswith("Error: 503 Service Unavailable\nAHN example.com:")
    assert plain.content == "ok"


def test_langchain_request_without_a_tool_call_does_not_break(transport):
    transport()
    odd = SimpleNamespace(tool_call=None)
    with pytest.raises(HTTPStatusError):
        ahn_langchain.handle_tool_call(odd, raising(forbidden()))


def test_langchain_middleware_options_default_to_not_converting():
    import inspect
    assert inspect.signature(ahn_langchain.ahn_tool_failure_middleware).parameters["convert_exceptions"].default is False
    assert inspect.signature(ahn_langchain.handle_tool_call).parameters["convert_exceptions"].default is False
    assert inspect.signature(ahn_langchain.ahandle_tool_call).parameters["convert_exceptions"].default is False


def test_langchain_real_agent(transport):
    pytest.importorskip("langchain", minversion="1.0")
    from langchain.agents import create_agent
    from langchain.agents.middleware import ToolRetryMiddleware
    from langchain.tools import tool
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import StructuredTool, ToolException

    calls = transport()
    attempts = []

    class FakeModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "fake"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            tool_messages = [m for m in messages if m.type == "tool"]
            if tool_messages:
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content="saw: " + tool_messages[-1].content))])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="", tool_calls=[
                {"name": "fetch_page", "args": {"url": "https://example.com/private/page?token=abc"}, "id": "call_1"}]))])

    @tool
    def fetch_page(url: str) -> str:
        """Fetch a page."""
        attempts.append(url)
        raise forbidden(url)

    def invocations(agent):
        return (lambda: agent.invoke({"messages": [("user", "go")]}),
                lambda: asyncio.run(agent.ainvoke({"messages": [("user", "go")]})))

    def fresh():
        ahn.clear_cache()
        calls.clear()
        attempts.clear()

    # 1. Default: the exception propagates exactly as without the middleware, and Agent Help Network is not asked.
    plain_agent = create_agent(FakeModel(), tools=[fetch_page], middleware=[ahn_langchain.ahn_tool_failure_middleware()])
    for run in invocations(plain_agent):
        fresh()
        with pytest.raises(HTTPStatusError):
            run()
        assert calls == [] and len(attempts) == 1

    # 2. A tool that reports its failure as an error message (ToolException with handle_tool_error): the line is appended,
    #    the original text stays in front.
    def fetch_reporting(url: str) -> str:
        attempts.append(url)
        raise ToolException(f"HTTP 403 Forbidden for {url}")

    reporting = StructuredTool.from_function(fetch_reporting, name="fetch_page", description="Fetch a page.", handle_tool_error=True)
    node_agent = create_agent(FakeModel(), tools=[reporting], middleware=[ahn_langchain.ahn_tool_failure_middleware()])
    for run in invocations(node_agent):
        fresh()
        message = next(m for m in run()["messages"] if m.type == "tool")
        assert message.status == "error"
        assert message.content.startswith("HTTP 403 Forbidden for https://example.com/private/page?token=abc\nAHN example.com:")
        assert sent(calls) == [("example.com", "site_access", None, f"ahn-client/{ahn.__version__} (+langchain; +https://agenthelpnetwork.com)")]
        assert "token" not in calls[0][0].full_url

    # 3. Listed before ToolRetryMiddleware, retries run first and the middleware sees the final failure once.
    retry = ToolRetryMiddleware(max_retries=2, initial_delay=0.0, jitter=False)
    ordered = create_agent(FakeModel(), tools=[fetch_page], middleware=[ahn_langchain.ahn_tool_failure_middleware(), retry])
    for run in invocations(ordered):
        fresh()
        message = next(m for m in run()["messages"] if m.type == "tool")
        assert len(attempts) == 3 and len(calls) == 1
        assert message.status == "error" and "failed after 3 attempts" in message.content and "AHN example.com:" in message.content

    # 4. convert_exceptions=True, retries exhausted with on_failure="error": one conversion, no exception text in the message.
    raising_retry = ToolRetryMiddleware(max_retries=2, initial_delay=0.0, jitter=False, on_failure="error")
    converting = create_agent(FakeModel(), tools=[fetch_page],
                              middleware=[ahn_langchain.ahn_tool_failure_middleware(convert_exceptions=True), raising_retry])
    for run in invocations(converting):
        fresh()
        message = next(m for m in run()["messages"] if m.type == "tool")
        assert len(attempts) == 3
        assert message.status == "error" and message.content.startswith("Error: HTTPStatusError (site_access)\nAHN example.com:")
        assert "token" not in message.content and "Forbidden" not in message.content.split("\n")[0]

    # 5. The wrong order, converting before the retries: the first exception is converted and the retries never run.
    wrong = create_agent(FakeModel(), tools=[fetch_page],
                         middleware=[ToolRetryMiddleware(max_retries=2, initial_delay=0.0, jitter=False, on_failure="error"),
                                     ahn_langchain.ahn_tool_failure_middleware(convert_exceptions=True)])
    fresh()
    invocations(wrong)[0]()
    assert len(attempts) == 1


# --- OpenAI Agents SDK -----------------------------------------------------------------------------------

class Ctx:
    def __init__(self, arguments=None):
        self.tool_arguments = arguments


def formatter_args(error, arguments=None, kind="tool_exception", default="An error occurred while running the tool. Please try again."):
    return SimpleNamespace(kind=kind, tool_type="function", tool_name="fetch_page", call_id="call_1", default_message=default,
                           run_context=Ctx(arguments), error=error)


def test_openai_formatter_adds_the_line_after_the_default_message(transport):
    calls = transport()
    arguments = json.dumps({"url": "https://example.com/a?b=1"})
    out = asyncio.run(ahn_openai.ahn_tool_error_formatter(formatter_args(HTTPStatusError("429 Too Many Requests"), arguments)))
    assert out.startswith("An error occurred while running the tool. Please try again.\nAHN example.com: recommendation=use_official_path")
    assert sent(calls)[0][:3] == ("example.com", "service_error", None)
    assert sent(calls)[0][3].endswith("(+openai-agents; +https://agenthelpnetwork.com)")
    assert "b=1" not in calls[0][0].full_url


def test_openai_formatter_chains_the_formatter_you_already_had(transport):
    transport()
    arguments = json.dumps({"url": "https://example.com/a"})
    seen = []

    def sync_base(args):
        seen.append(args.kind)
        return "your message"

    async def async_base(args):
        return "your async message"

    for base, expected in ((sync_base, "your message"), (async_base, "your async message")):
        ahn.clear_cache()
        out = asyncio.run(ahn_openai.tool_error_formatter(base)(formatter_args(HTTPStatusError("503 Service Unavailable"), arguments)))
        assert out.startswith(expected + "\nAHN example.com:")
    assert seen == ["tool_exception"]
    # A base that returns None means "the SDK default": the line goes on the default message.
    ahn.clear_cache()
    out = asyncio.run(ahn_openai.tool_error_formatter(lambda args: None)(formatter_args(HTTPStatusError("503 Service Unavailable"), arguments)))
    assert out.startswith("An error occurred while running the tool. Please try again.\nAHN example.com:")


def test_openai_formatter_returns_exactly_what_the_base_returns_when_there_is_nothing_to_add(transport):
    calls = transport()
    arguments = json.dumps({"url": "https://example.com/a"})
    plain = ahn_openai.ahn_tool_error_formatter
    assert asyncio.run(plain(formatter_args(not_found(), arguments))) is None  # no base, no line: the SDK default
    assert asyncio.run(ahn_openai.tool_error_formatter(lambda a: "base")(formatter_args(not_found(), arguments))) == "base"
    assert asyncio.run(plain(formatter_args(forbidden("http://localhost/x"), None))) is None
    assert asyncio.run(plain(formatter_args(RuntimeError("x"), None))) is None
    assert asyncio.run(plain(formatter_args(PermissionError(13, "Permission denied"), arguments))) is None
    # Other kinds are not tool exceptions.
    for kind in ("approval_rejected", "tool_not_found"):
        args = formatter_args(None, arguments, kind=kind, default="Tool execution was not approved.")
        assert asyncio.run(plain(args)) is None
        assert asyncio.run(ahn_openai.tool_error_formatter(lambda a: "mine")(args)) == "mine"
    assert asyncio.run(plain(SimpleNamespace())) is None
    assert calls == []
    # A failed lookup, and a base that raises: the SDK default, as it would do itself.
    transport(error=urllib.error.URLError("down"))
    assert asyncio.run(plain(formatter_args(forbidden("https://example.org/a"), None))) is None

    def broken(args):
        raise RuntimeError("formatter bug")

    assert asyncio.run(ahn_openai.tool_error_formatter(broken)(formatter_args(not_found(), arguments))) is None


def test_openai_formatter_is_async_and_does_not_block_the_loop(monkeypatch):
    assert asyncio.iscoroutinefunction(ahn_openai.ahn_tool_error_formatter)
    release = threading.Event()

    def slow(request, timeout):
        release.wait(5)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", slow)

    async def run():
        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        out = await ahn_openai.tool_error_formatter(timeout=0.3)(formatter_args(forbidden(), None))
        task.cancel()
        release.set()
        return out, ticks

    out, ticks = asyncio.run(run())
    assert out is None and ticks >= 10


def test_openai_failure_error_function_for_tools_that_set_their_own(transport):
    calls = transport()
    arguments = json.dumps({"url": "https://example.com/a?b=1"})
    wrapped = ahn_openai.with_ahn_note(lambda context, error: "base message")
    assert asyncio.iscoroutinefunction(wrapped) and asyncio.iscoroutinefunction(ahn_openai.ahn_failure_error_function)
    out = asyncio.run(wrapped(Ctx(arguments), HTTPStatusError("429 Too Many Requests")))
    assert out.startswith("base message\nAHN example.com: recommendation=use_official_path")
    assert sent(calls)[0][3].endswith("(+openai-agents; +https://agenthelpnetwork.com)")
    assert asyncio.run(wrapped(Ctx(arguments), not_found())) == "base message"
    assert asyncio.run(wrapped(object(), RuntimeError("x"))) == "base message"

    async def async_base(context, error):
        return "async base"

    out = asyncio.run(ahn_openai.with_ahn_note(async_base)(Ctx(arguments), HTTPStatusError("503 Service Unavailable")))
    assert out.startswith("async base\nAHN example.com:")
    sentinel = object()
    assert asyncio.run(ahn_openai.with_ahn_note(lambda c, e: sentinel)(Ctx(), forbidden())) is sentinel
    # The default base is the SDK's message, and never the exception text.
    ahn.clear_cache()
    default = asyncio.run(ahn_openai.ahn_failure_error_function(Ctx(arguments), forbidden("https://example.com/a?secret=1")))
    first, _, rest = default.partition("\n")
    assert first.startswith("An error occurred") and "secret" not in first and rest.startswith("AHN example.com:")


def test_openai_plain_failure_error_function_is_the_documented_signature(transport):
    import inspect
    plain = ahn_openai.with_ahn_note(lambda context, error: "base", asynchronous=False)
    assert not asyncio.iscoroutinefunction(plain)
    assert list(inspect.signature(plain).parameters) == ["context", "error"]
    transport()
    out = plain(Ctx(json.dumps({"url": "https://example.com/a"})), HTTPStatusError("429 Too Many Requests"))
    assert out.startswith("base\nAHN example.com:")
    assert plain(Ctx(None), not_found()) == "base"
    sentinel = object()  # an awaitable from an async base is passed through untouched
    assert ahn_openai.with_ahn_note(lambda c, e: sentinel, asynchronous=False)(Ctx(), forbidden()) is sentinel


def test_openai_real_function_tools_with_the_run_config_formatter(transport):
    pytest.importorskip("agents")
    from agents import RunConfig, function_tool
    from agents.tool_context import ToolContext

    calls = transport()
    seen = []

    @function_tool
    def fetch_page(url: str) -> str:
        """Fetch a page."""
        raise forbidden(url)

    @function_tool(failure_error_function=ahn_openai.with_ahn_note())
    async def fetch_page_own(url: str) -> str:
        """Fetch a page."""
        raise HTTPStatusError("Server error '503 Service Unavailable'", request_url=url)

    async def run(tool, url, run_config):
        arguments = json.dumps({"url": url})
        context = ToolContext(context=None, tool_name=tool.name, tool_call_id="call_1", tool_arguments=arguments, run_config=run_config)
        return await tool.on_invoke_tool(context, arguments), context

    default_text = "An error occurred while running the tool. Please try again."
    # The implicit default handler: the RunConfig formatter is what the SDK calls, and the SDK's own handling stays as it is.
    out, context = asyncio.run(run(fetch_page, "https://example.com/a?b=1", RunConfig(tool_error_formatter=ahn_openai.ahn_tool_error_formatter)))
    assert out.startswith(default_text + "\nAHN example.com:")
    assert getattr(context, "_function_tool_default_failure_handled", False) is True  # still the default policy: traces stay redacted
    # With your own formatter in the RunConfig, chained.
    ahn.clear_cache()

    def mine(args):
        seen.append((args.kind, args.tool_name))
        return "mine: " + args.default_message

    out, _ = asyncio.run(run(fetch_page, "https://example.com/a", RunConfig(tool_error_formatter=ahn_openai.tool_error_formatter(mine))))
    assert out.startswith("mine: " + default_text + "\nAHN example.com:") and seen == [("tool_exception", "fetch_page")]
    # No formatter configured: the SDK default, no lookup.
    calls.clear()
    out, _ = asyncio.run(run(fetch_page, "https://example.com/a", RunConfig()))
    assert out == default_text and calls == []
    # A tool with its own failure_error_function, wrapped.
    ahn.clear_cache()
    out, _ = asyncio.run(run(fetch_page_own, "https://example.com/z", None))
    assert out.startswith(default_text + "\nAHN example.com:")
    assert [row[:3] for row in sent(calls)] == [("example.com", "service_error", None)]
    assert sent(calls)[0][3].endswith("(+openai-agents; +https://agenthelpnetwork.com)")


# --- CrewAI ----------------------------------------------------------------------------------------------

def tool_failure_event(message="503 Service Unavailable", code="HTTPStatusError", tool_args=None):
    failure = SimpleNamespace(message=message, code=code, reason="exception")
    return SimpleNamespace(tool_name="scrape", tool_args={"website_url": "https://example.com/x?y=1"} if tool_args is None else tool_args,
                           failure=failure, policy="warn")


def test_crewai_note_for_event(transport):
    calls = transport()
    note = ahn_crewai.note_for_event(tool_failure_event())
    assert note and note.startswith("AHN example.com:")
    assert sent(calls)[0][:3] == ("example.com", "service_error", None)
    assert sent(calls)[0][3].endswith("(+crewai; +https://agenthelpnetwork.com)")
    # tool_args as a JSON string, as CrewAI passes them for some tools; host in the message when no argument names it
    ahn.clear_cache()
    assert ahn_crewai.note_for_event(tool_failure_event(tool_args='{"url": "https://example.org/a"}'))
    assert sent(calls)[-1][0] == "example.org"
    # A URL only in the message is not searched for: no URL argument, no lookup.
    ahn.clear_cache()
    calls.clear()
    assert ahn_crewai.note_for_event(tool_failure_event("403 Forbidden for url https://example.net/a", tool_args={})) is None
    assert calls == []


def test_crewai_events_that_are_not_blocks_get_no_lookup(transport):
    calls = transport()
    assert ahn_crewai.note_for_event(tool_failure_event("404 Not Found")) is None
    assert ahn_crewai.note_for_event(tool_failure_event("Field required", code="ValidationError")) is None
    assert ahn_crewai.note_for_event(tool_failure_event(tool_args={})) is None  # no URL anywhere
    assert ahn_crewai.note_for_event(SimpleNamespace()) is None
    assert ahn_crewai.note_for_event(None) is None
    assert calls == []


def test_crewai_listener_records_logs_and_calls_back(transport, caplog):
    transport()
    got = []
    listener = ahn_crewai.AHNToolFailureListener(on_note=lambda note, event: got.append((note, event)))
    event = tool_failure_event()
    with caplog.at_level(logging.INFO, logger="ahn_client.crewai"):
        listener.handle(None, event)
    assert len(listener.notes) == 1 and got == [(listener.notes[0], event)]
    assert any(record.getMessage() == listener.notes[0] for record in caplog.records)


def test_crewai_listener_never_raises(transport):
    transport()

    def broken(note, event):
        raise RuntimeError("callback bug")

    listener = ahn_crewai.AHNToolFailureListener(on_note=broken)
    listener.handle(None, tool_failure_event())  # the callback's error stays inside
    listener.handle(None, None)
    transport(error=urllib.error.URLError("down"))
    ahn.clear_cache()
    listener.handle(None, tool_failure_event())
    assert len(listener.notes) == 1  # only the first lookup produced a line


def test_crewai_notes_are_bounded():
    listener = ahn_crewai.AHNToolFailureListener(max_notes=3)
    for index in range(10):
        listener.notes.append(str(index))
    assert list(listener.notes) == ["7", "8", "9"]


def test_crewai_register_listener_subscribes_with_the_documented_call(monkeypatch, transport):
    transport()
    registered = {}

    class FakeBus:
        def on(self, event_type):
            def decorator(handler):
                registered[event_type] = handler
                return handler
            return decorator

    class ToolFailureDetectedEvent:
        pass

    package = types.ModuleType("crewai")
    events = types.ModuleType("crewai.events")
    events.crewai_event_bus = FakeBus()
    events.ToolFailureDetectedEvent = ToolFailureDetectedEvent
    package.events = events
    monkeypatch.setitem(sys.modules, "crewai", package)
    monkeypatch.setitem(sys.modules, "crewai.events", events)
    listener = ahn_crewai.register_listener()
    assert registered == {ToolFailureDetectedEvent: listener.handle}
    other = FakeBus()
    listener2 = ahn_crewai.register_listener(event_bus=other)
    assert isinstance(listener2, ahn_crewai.AHNToolFailureListener)


def test_crewai_after_tool_call_hook_only_touches_declared_failures(transport):
    calls = transport()
    failure = SimpleNamespace(message="HTTP 403 Forbidden", code="forbidden", reason="tool_reported")
    context = SimpleNamespace(raw_tool_result=failure, tool_result="HTTP 403 Forbidden (code: forbidden)",
                              tool_input={"website_url": "https://example.com/x"})
    out = ahn_crewai.append_note_to_result(context)
    assert out.startswith("HTTP 403 Forbidden (code: forbidden)\nAHN example.com:")
    ahn.clear_cache()
    calls.clear()
    plain = SimpleNamespace(raw_tool_result="plain page text with 403", tool_result="plain page text with 403",
                            tool_input={"website_url": "https://example.com/x"})
    assert ahn_crewai.append_note_to_result(plain) is None
    assert ahn_crewai.append_note_to_result(SimpleNamespace()) is None
    assert calls == []


def test_crewai_listener_on_the_real_event_bus(transport):
    pytest.importorskip("crewai")
    from crewai.events import ToolFailureDetectedEvent, crewai_event_bus
    from crewai.tools.tool_failure import ToolFailurePolicy, failure_from_exception

    calls = transport()
    got = []
    with crewai_event_bus.scoped_handlers():
        listener = ahn_crewai.register_listener(on_note=lambda note, event: got.append(note))
        event = ToolFailureDetectedEvent(tool_name="scrape", tool_args={"website_url": "https://example.com/x?y=1"},
                                         failure=failure_from_exception(RuntimeError("503 Service Unavailable")),
                                         policy=ToolFailurePolicy.WARN)
        crewai_event_bus.emit(None, event)
        crewai_event_bus.flush(timeout=10)
    assert len(got) == 1 and got == list(listener.notes)
    assert sent(calls)[0][:3] == ("example.com", "service_error", None)


# --- Claude Agent SDK ------------------------------------------------------------------------------------

PAYLOADS = [
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a?q=1"},
     "error": "Request failed with status code 403"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://www.example.com/a"},
     "error": "Request failed with status code 404"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "error": "Request failed with status code 429 Too Many Requests"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://login.example.com/a"},
     "error": "403 Forbidden"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/"},
     "error": "Please complete the CAPTCHA"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {},
     "error": "Claude Code is unable to fetch from example.org"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "error": "getaddrinfo ENOTFOUND example.com"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "error": "403 Forbidden", "is_interrupt": True},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__playwright__browser_navigate", "tool_input": {"url": "https://example.com/a"},
     "error": "net::ERR_CONNECTION_REFUSED"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__fetch__fetch", "tool_input": {"href": "https://example.com/a"},
     "error": "502 Bad Gateway"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__fetch__fetch", "tool_input": {"query": "cats"},
     "error": "502 Bad Gateway"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__browser__goto", "tool_input": {"url": "example.com/path"},
     "error": "401 Unauthorized"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "mcp__browser__goto", "tool_input": {"url": "about:blank"},
     "error": "Timeout 30000ms exceeded navigating to https://example.org/x"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "WebSearch", "tool_input": {"query": "x"}, "error": "403 Forbidden"},
    {"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "tool_input": {"command": "curl https://example.com"},
     "error": "exit 22: 403 Forbidden"},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 403, "codeText": "Forbidden", "result": "Access denied"}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 403, "headers": {"CF-Mitigated": "challenge"}}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 403, "result": "Just a moment... verify you are human"}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 404}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 200, "result": "ok"}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 503}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"},
     "tool_response": {"code": 401}},
    {"hook_event_name": "PostToolUse", "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"}, "tool_response": "text"},
    {"hook_event_name": "Stop", "tool_name": "WebFetch"},
    {}, None, "text", 5, [],
]


@pytest.mark.parametrize("payload", PAYLOADS)
def test_claude_decision_matches_the_plugin(payload):
    assert _claude_code.failure(payload) == hook.failure(payload)


def test_claude_copy_of_the_plugin_rules_is_in_step():
    for name in ("URL_IN_TEXT", "REFUSAL", "FULL_URL", "SCHEMELESS", "HUMAN_VERIFICATION", "BEFORE_RESPONSE", "HOST_LABEL", "HOST_LIKE"):
        assert getattr(_claude_code, name).pattern == getattr(hook, name).pattern, name
        assert getattr(_claude_code, name).flags == getattr(hook, name).flags, name
    assert [(n, p.pattern, p.flags) for n, p in _claude_code.CLASSES] == [(n, p.pattern, p.flags) for n, p in hook.CLASSES]
    assert _claude_code.IDNA_DEVIATIONS == hook.IDNA_DEVIATIONS
    assert _claude_code.URL_FIELDS == hook.URL_FIELDS
    assert _claude_code.BODY_FIELDS == hook.BODY_FIELDS and _claude_code.BODY_SCAN_CHARS == hook.BODY_SCAN_CHARS


@pytest.mark.parametrize("value", ["https://example.com/x", "example.com/x", "about:blank", "javascript:alert(1)", "mailto:a@b.co",
                                   "http://[::1]:80/", "https://münchen.de/", "https://x.ß.com/", "ftp://example.com", 5, None, ""])
def test_claude_host_reading_matches_the_plugin(value):
    assert _claude_code.url_host(value) == hook.url_host(value)
    assert _claude_code.hostname(value) == hook.hostname(value)


def test_claude_matchers_are_the_plugins():
    config = json.loads((ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    assert config["PostToolUseFailure"][0]["matcher"] == ahn_claude.FAILURE_MATCHER
    assert config["PostToolUse"][0]["matcher"] == ahn_claude.SUCCESS_MATCHER


def run_hook(payload, timeout=2.0):
    return asyncio.run(ahn_claude.make_hook(timeout=timeout)(payload, "toolu_1", {}))


def test_claude_hook_returns_additional_context_for_a_failed_fetch(transport):
    calls = transport()
    out = run_hook(PAYLOADS[0])
    assert list(out) == ["hookSpecificOutput"]
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    text = out["hookSpecificOutput"]["additionalContext"]
    assert text.startswith("AHN example.com: recommendation=use_official_path (high).") and len(text) <= 700
    assert sent(calls) == [("example.com", "site_access", None, f"ahn-client/{ahn.__version__} (+claude-agent-sdk; +https://agenthelpnetwork.com)")]
    assert "q=1" not in calls[0][0].full_url


def test_claude_hook_for_a_webfetch_status_code(transport):
    calls = transport()
    out = run_hook(PAYLOADS[15])
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert out["hookSpecificOutput"]["additionalContext"].startswith("AHN example.com:")
    assert sent(calls)[0][:3] == ("example.com", "site_access", "response_observed")


@pytest.mark.parametrize("index", [1, 7, 13, 14, 18, 19, 22, 23, 24, 25])
def test_claude_hook_does_nothing_for_other_payloads(index, transport):
    calls = transport()
    assert run_hook(PAYLOADS[index]) == {}
    assert calls == []


@pytest.mark.parametrize("payload", [{}, None, "text", 5, [], {"hook_event_name": "PostToolUseFailure"},
                                     {"hook_event_name": "PostToolUseFailure", "tool_input": "str", "error": 7}])
def test_claude_hook_never_raises(payload, transport):
    transport()
    assert run_hook(payload) == {}


def test_claude_hook_gives_an_empty_result_when_the_lookup_fails_or_is_slow(monkeypatch, transport):
    transport(error=urllib.error.URLError("down"))
    assert run_hook(PAYLOADS[0]) == {}
    ahn.clear_cache()
    release = threading.Event()

    def slow(request, timeout):
        release.wait(5)
        return FakeResponse(json.dumps(ANSWER).encode("utf-8"))

    monkeypatch.setattr(_client, "_urlopen", slow)
    started = time.monotonic()
    assert run_hook(PAYLOADS[0], timeout=0.2) == {}
    assert time.monotonic() - started < 2
    release.set()


def test_claude_hook_skips_excluded_hosts(transport):
    calls = transport()
    payload = dict(PAYLOADS[0], tool_input={"url": "http://localhost:3000/x"})
    assert run_hook(payload) == {}
    assert calls == []


def test_claude_ahn_hooks_builds_the_documented_options_dict(monkeypatch):
    @dataclasses.dataclass
    class HookMatcher:
        matcher: str = None
        hooks: list = dataclasses.field(default_factory=list)
        timeout: float = None

    sdk = types.ModuleType("claude_agent_sdk")
    sdk.HookMatcher = HookMatcher
    monkeypatch.setitem(sys.modules, "claude_agent_sdk", sdk)
    mine = HookMatcher(matcher="Bash", hooks=[lambda *a: None])
    built = ahn_claude.ahn_hooks({"PreToolUse": [mine], "PostToolUseFailure": [HookMatcher(matcher="Own")]})
    assert built["PreToolUse"] == [mine]
    assert [m.matcher for m in built["PostToolUseFailure"]] == ["Own", ahn_claude.FAILURE_MATCHER]
    assert [m.matcher for m in built["PostToolUse"]] == ["WebFetch"]
    for matcher in (built["PostToolUseFailure"][1], built["PostToolUse"][0]):
        assert len(matcher.hooks) == 1 and asyncio.iscoroutinefunction(matcher.hooks[0]) and matcher.timeout == 10
    assert ahn_claude.ahn_hooks(matcher="^WebFetch$")["PostToolUseFailure"][0].matcher == "^WebFetch$"


def test_claude_hooks_with_the_real_sdk_types(transport):
    pytest.importorskip("claude_agent_sdk")
    from claude_agent_sdk import ClaudeAgentOptions

    transport()
    options = ClaudeAgentOptions(hooks=ahn_claude.ahn_hooks())
    assert set(options.hooks) == {"PostToolUseFailure", "PostToolUse"}
    callback = options.hooks["PostToolUseFailure"][0].hooks[0]
    typed_input = {"hook_event_name": "PostToolUseFailure", "session_id": "s", "transcript_path": "t", "cwd": ".",
                   "tool_name": "WebFetch", "tool_input": {"url": "https://example.com/a"}, "tool_use_id": "toolu_1",
                   "error": "Request failed with status code 403"}
    out = asyncio.run(callback(typed_input, "toolu_1", {"signal": None}))
    assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUseFailure"
    assert out["hookSpecificOutput"]["additionalContext"].startswith("AHN example.com:")


# --- packaging -------------------------------------------------------------------------------------------

def test_adapters_import_without_their_frameworks():
    # Importing an adapter must not need the framework: it is read when the adapter is used. Block each one
    # (a None entry in sys.modules makes `import x` raise ImportError) and import every adapter in a clean process.
    import subprocess
    names = ("langchain", "langchain_core", "langgraph", "crewai", "agents", "claude_agent_sdk", "browser_use")
    code = chr(10).join([
        "import sys",
        f"for name in {names!r}:",
        "    sys.modules[name] = None",
        f"sys.path.insert(0, {str(ROOT / 'clients' / 'python' / 'src')!r})",
        "import ahn_client.langchain, ahn_client.crewai, ahn_client.openai_agents, ahn_client.claude_agent_sdk",
        "from ahn_client.openai_agents import ahn_failure_error_function",
        "print('ok')",
    ])
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout.strip() == "ok", done.stderr


def test_extras_are_declared_and_the_base_install_has_no_dependencies():
    pyproject = (ROOT / "clients" / "python" / "pyproject.toml").read_text(encoding="utf-8")
    head, _, extras = pyproject.partition("[project.optional-dependencies]")
    assert "dependencies = []" in head
    extras = extras.split("[project.urls]")[0]
    # Lower bounds are the versions the adapters were run against: openai-agents 0.24 is the first with RunConfig.tool_error_formatter
    # for tool exceptions, claude-agent-sdk 0.1.27 the oldest whose PostToolUseFailure hook was run (see the README).
    for line in ('langchain = ["langchain>=1.0"]', 'crewai = ["crewai>=1.15.9"]', 'openai-agents = ["openai-agents>=0.24"]',
                 'claude-agent-sdk = ["claude-agent-sdk>=0.1.27"]'):
        assert line in extras


def test_package_version_and_exports():
    assert ahn.__version__ == "0.6.2"
    assert set(ahn.__all__) == {"check", "classify", "clear_cache", "diagnose", "signals", "Result", "NextAction", "__version__"}
    assert (ROOT / "clients" / "python" / "src" / "ahn_client" / "__init__.py").read_text(encoding="utf-8").count('__version__ = "0.6.2"') == 1


def test_examples_parse_and_name_the_browser_use_adapter():
    import ast
    examples = ROOT / "clients" / "python" / "examples"
    for path in sorted(examples.glob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    text = (examples / "browser_use_hook_example.py").read_text(encoding="utf-8")
    assert 'ADAPTER = "browser-use"' in text and "adapter=ADAPTER" in text
    assert "on_step_end" in text and "@tools.action" not in text  # the action lives in examples/browser-use/
    action = (ROOT / "examples" / "browser-use" / "ahn_browser_use.py").read_text(encoding="utf-8")
    assert 'ADAPTER = "browser-use"' in action and "adapter=ADAPTER" in action and "@tools.action(" in action


# --- hosts that are never looked up -----------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "web.default.svc", "redis.prod.cluster", "api.service.consul", "x.svc", "db.cluster",
    "10.0.0.1.nip.io", "10-0-0-1.sslip.io", "app.192.168.1.5.nip.io", "x.xip.io", "www.nip.io",
    "192-168-1-5.example.com", "ec2-10-0-0-1.compute.example.com", "10.0.0.1.example.com",
])
def test_cluster_and_address_names_are_never_looked_up(value, transport):
    calls = transport()
    assert hook.skip_host(hook.normalize_host(value)), "the plugin refuses the same names"
    assert ahn.check(value) is None
    assert ahn.check(f"https://{value}/x", "site_access") is None
    assert _adapter.note_for(forbidden(f"https://{value}/x"), None, adapter="langchain") is None
    assert calls == []


@pytest.mark.parametrize("value", ["example.com", "svc.example.com", "cluster.example.org", "consul.io", "nipio.net", "1-2-3.example.com",
                                   "v1.2.3.example.com", "api.v2.example.com"])
def test_ordinary_names_with_similar_parts_are_still_looked_up(value, transport):
    calls = transport()
    assert not hook.skip_host(hook.normalize_host(value)), "the plugin looks up the same names"
    assert ahn.check(value) is not None
    assert len(calls) == 1


@pytest.mark.parametrize("value", ["fa\u00df.de", "https://fa\u00df.de/x", "fa\u03c2.de", "a\u200db.example.com", "a\u200cb.example.com"])
def test_idna_2003_deviation_names_are_refused_like_the_plugin_does(value, transport):
    calls = transport()
    assert _hosts.normalize_host(value) is None and hook.normalize_host(value) is None
    assert ahn.check(value) is None
    assert calls == []
    assert _claude_code.hostname(value) is None and hook.hostname(value) is None
    assert _hosts.IDNA_DEVIATIONS == hook.IDNA_DEVIATIONS


def test_a_name_without_a_deviation_character_still_works_in_idna(transport):
    calls = transport()
    assert _hosts.normalize_host("https://m\u00fcnchen.de/x") == "xn--mnchen-3ya.de" == hook.normalize_host("https://m\u00fcnchen.de/x")
    assert ahn.check("m\u00fcnchen.de") is not None and len(calls) == 1


@pytest.mark.parametrize("value", ["https://evil.com\\@good.com", "https://evil.com\\@good.com/path", "http://good.com\\.evil.com/", "evil.com\\good.com"])
def test_a_backslash_has_two_readings_so_no_host_is_looked_up(value, transport):
    # urlsplit reads the host after the "@" (good.com); a browser reads the backslash as a slash (evil.com).
    calls = transport()
    assert _hosts.normalize_host(value) is None and hook.normalize_host(value) is None and hook.url_host(value) is None
    assert ahn.check(value) is None
    assert _adapter.host_from_args({"url": value}) is None
    assert _adapter.host_from_error(f"GET {value} failed") is None
    assert _claude_code.url_host(value) is None and _claude_code.hostname(value) is None
    failed = {"hook_event_name": "PostToolUseFailure", "tool_name": "WebFetch", "tool_input": {"url": value}, "error": "403 Forbidden"}
    assert _claude_code.failure(failed) is None and hook.failure(failed) is None
    assert calls == []


# --- the Browser Use example -----------------------------------------------------------------------------

def load_browser_use_example(monkeypatch):
    package = types.ModuleType("browser_use")

    package.Agent = object
    monkeypatch.setitem(sys.modules, "browser_use", package)
    spec = importlib.util.spec_from_file_location("browser_use_hook_example_under_test",
                                                  ROOT / "clients" / "python" / "examples" / "browser_use_hook_example.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("title,expected", [
    ("S&P 500 - Yahoo Finance", None), ("500 Startups", None), ("Top 403 Songs", None), ("Example Domain", None), ("", None), (None, None),
    ("Pricing - 429 Dollars", None), ("The 502 bus", None),
    ("403 Forbidden", "site_access"), ("Access Denied", "site_access"), ("Error 403", "site_access"),
    ("Just a moment...", "human_verification"), ("Attention Required! | Cloudflare", "human_verification"),
    ("Verify you are human", "human_verification"), ("Captcha", "human_verification"),
    ("429 Too Many Requests", "service_error"), ("Error 503", "service_error"), ("500 Internal Server Error", "service_error"),
    ("502 Bad Gateway", "service_error"), ("504 Gateway Time-out", "service_error"), ("503 Service Unavailable", "service_error"),
])
def test_browser_use_example_block_titles(monkeypatch, title, expected):
    assert load_browser_use_example(monkeypatch).block_class(title) == expected


# --- the README ------------------------------------------------------------------------------------------

def test_client_readme_links_are_absolute_so_they_work_on_pypi():
    import re
    text = (ROOT / "clients" / "python" / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)\s]+)\)", text)
    assert links
    relative = [link for link in links if not link.startswith(("https://", "http://", "#"))]
    assert relative == [], relative


# --- block pages and login walls are web failures on their own wording (a regression against 0.3.0 before the review) -----

BLOCK_PAGE_TEXTS = [
    ("Sorry, you have been blocked. You are unable to access example.com. Cloudflare Ray ID: 8a1b2c3d4e5f", "human_verification"),
    ("Attention Required! | Cloudflare", "human_verification"),
    ("Access to this page has been denied.", "site_access"),
    ("Your request has been blocked by our security system", "site_access"),
    ("Request blocked.", "site_access"),
    ("ERROR: The request could not be satisfied. Request blocked. We can't connect to the server for this app or website at this time.",
     "site_access"),
    ("Login required", "login_auth"),
    ("You must sign in to view this page", "login_auth"),
]


@pytest.mark.parametrize("text,expected_class", BLOCK_PAGE_TEXTS)
def test_block_page_wording_is_a_web_failure_by_itself(text, expected_class, transport):
    calls = transport()
    args = {"url": "https://example.com/p"}
    # As an exception without a request, as an exception from an HTTP library, and as a tool's error message.
    for error in (RuntimeError(text), HTTPStatusError(text, request_url="https://example.com/p"), text):
        target = _adapter.failure_target(error, args)
        assert target is not None and target[:2] == ("example.com", expected_class), (text, error)
    ahn.clear_cache()
    assert _adapter.note_for(RuntimeError(text), args, adapter="langchain").startswith("AHN example.com:")
    assert sent(calls)[0][1] == expected_class


@pytest.mark.parametrize("error", [
    PermissionError(13, "Permission denied"), PermissionError("[Errno 13] Permission denied: '/var/data/out.csv'"),
    RuntimeError("Task blocked on mutex"), RuntimeError("Thread 4 is blocked waiting for a lock"),
    RuntimeError("Please reset your password"), RuntimeError("Reset the counter"), RuntimeError("Request denied by policy engine"),
    RuntimeError("The arrayid was missing"), RuntimeError("the pipe has been blocked"), KeyError("login"),
    ValueError("could not log in to the database as root"),
])
def test_loose_words_alone_are_still_not_a_web_failure(error, transport):
    calls = transport()
    args = {"url": "https://example.com/p"}
    assert _adapter.failure_target(error, args) is None
    assert _adapter.failure_target(str(error), args) is None
    assert _adapter.note_for(error, args, adapter="langchain") is None
    assert calls == []


# --- an error whose .request raises (httpx) ------------------------------------------------------------------

class RequestlessReadTimeout(Exception):
    """httpx.ReadTimeout built without a request: reading .request raises RuntimeError instead of returning None."""

    @property
    def request(self):
        raise RuntimeError("The .request property has not been set.")


def test_a_property_that_raises_does_not_break_the_attribute_reads(transport):
    calls = transport()
    error = RequestlessReadTimeout("The read operation timed out")
    assert _adapter.host_from_error(error) is None
    target = _adapter.failure_target(error, {"url": "https://example.com/p"})
    assert target is not None and target[:2] == ("example.com", "service_error")
    assert _adapter.note_for(error, {"url": "https://example.com/p"}, adapter="langchain").startswith("AHN example.com:")
    assert _adapter._attribute(error, "request") is None
    assert calls and sent(calls)[0][:2] == ("example.com", "service_error")


def test_langchain_requestless_timeout_is_handled_when_converting_and_reraised_when_not(lc, transport):
    transport()
    error = RequestlessReadTimeout("The read operation timed out")
    converted = ahn_langchain.handle_tool_call(lc.request, raising(error), convert_exceptions=True)
    assert converted.status == "error" and converted.content.startswith("Error: RequestlessReadTimeout (service_error)\nAHN example.com:")
    converted = asyncio.run(ahn_langchain.ahandle_tool_call(lc.request, async_raising(error), convert_exceptions=True))
    assert converted.status == "error"
    with pytest.raises(RequestlessReadTimeout) as caught:
        ahn_langchain.handle_tool_call(lc.request, raising(error))
    assert caught.value is error


def test_langchain_when_reading_the_error_fails_the_original_exception_goes_on(lc, transport, monkeypatch):
    transport()

    def broken(error, args):
        raise RuntimeError("The .request property has not been set.")

    monkeypatch.setattr(ahn_langchain, "failure_target", broken)
    error = forbidden()
    with pytest.raises(HTTPStatusError) as caught:
        ahn_langchain.handle_tool_call(lc.request, raising(error), convert_exceptions=True)
    assert caught.value is error
    with pytest.raises(HTTPStatusError) as caught:
        asyncio.run(ahn_langchain.ahandle_tool_call(lc.request, async_raising(error), convert_exceptions=True))
    assert caught.value is error


def test_a_real_httpx_read_timeout_without_a_request(lc, transport):
    httpx = pytest.importorskip("httpx")
    transport()
    error = httpx.ReadTimeout("The read operation timed out")
    with pytest.raises(RuntimeError):
        error.request  # noqa: B018 - the premise: httpx raises here instead of returning None
    converted = ahn_langchain.handle_tool_call(lc.request, raising(error), convert_exceptions=True)
    assert converted.content.startswith("Error: ReadTimeout (service_error)\nAHN example.com:")
    assert asyncio.run(_adapter.anote_for(error, {"url": "https://example.com/p"}, adapter="crewai"))
    request = httpx.Request("GET", "https://api.example.net/v1/x?token=abc")
    with_request = httpx.ReadTimeout("The read operation timed out", request=request)
    assert _adapter.failure_target(with_request, None)[:2] == ("api.example.net", "service_error")


def test_a_url_only_in_the_message_is_not_looked_up(transport):
    calls = transport()
    for error in (RuntimeError("403 Forbidden for https://docs.mistral.ai/api/"), "403 Forbidden for https://docs.mistral.ai/api/",
                  ToolInvocationError("403 https://errors.pydantic.dev/2.11/v/missing")):
        assert _adapter.note_for(error, {"query": "no url argument"}, adapter="langchain") is None
        assert _adapter.note_for(error, None, adapter="langchain") is None
    assert calls == []
