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
from ahn_client import _adapter, _claude_code, _client  # noqa: E402
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


class HTTPStatusError(Exception):
    """Stands in for httpx.HTTPStatusError: the class name and the message are all the client reads."""

    def __init__(self, message, request_url=None, status=None):
        super().__init__(message)
        if request_url:
            self.request = SimpleNamespace(url=request_url)
        if status:
            self.response = SimpleNamespace(status_code=status)


def forbidden(url="https://example.com/private/page?token=abc"):
    return HTTPStatusError(f"Client error '403 Forbidden' for url '{url}'")


def not_found(url="https://example.com/missing"):
    return HTTPStatusError(f"Client error '404 Not Found' for url '{url}'")


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


def test_host_from_error_reads_the_request_then_the_message():
    assert _adapter.host_from_error(HTTPStatusError("boom", request_url="https://api.example.com/v1?key=1")) == "api.example.com"
    assert _adapter.host_from_error(forbidden("https://www.example.net/a?b=c")) == "example.net"
    assert _adapter.host_from_error("GET https://example.org/page failed: 502") == "example.org"
    assert _adapter.host_from_error(b"GET https://example.org/page failed: 502") == "example.org"
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
    (HTTPStatusError("Client error '403 Forbidden' for url 'https://login.example.com/x'"), None, ("login.example.com", "site_access", None)),
    (HTTPStatusError("Client error '403 Forbidden' for url 'https://developers.cloudflare.com/x'"), None,
     ("developers.cloudflare.com", "site_access", None)),
    # Not a block and not an outage, or nothing to look up.
    (not_found(), {"url": "https://example.com/p"}, None),
    (HTTPStatusError("422 Unprocessable Entity"), {"url": "https://example.com/p"}, None),
    (forbidden(), {"query": "no url here"}, ("example.com", "site_access", None)),
    (RuntimeError("403 Forbidden"), {"query": "no url here"}, None),
    (RuntimeError("something odd"), {"url": "https://example.com/p"}, None),
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


def test_langchain_exception_becomes_an_error_message_with_the_line(lc, transport):
    calls = transport()
    original = forbidden()

    def handler(request):
        raise original

    result = ahn_langchain.handle_tool_call(lc.request, handler)
    assert result.status == "error" and result.tool_call_id == "call_1" and result.name == "fetch_page"
    assert result.content.startswith("Error: " + repr(original) + "\n")  # the original error is there, unchanged
    assert result.content.endswith("bypassing site controls is not advised.")
    assert "AHN example.com: recommendation=use_official_path" in result.content
    assert sent(calls)[0][3].endswith("(+langchain; +https://agenthelpnetwork.com)")


@pytest.mark.parametrize("error", [not_found(), RuntimeError("tool crashed"), KeyError("x")])
def test_langchain_other_exceptions_pass_through_untouched(lc, transport, error):
    calls = transport()

    def handler(request):
        raise error

    with pytest.raises(type(error)) as caught:
        ahn_langchain.handle_tool_call(lc.request, handler)
    assert caught.value is error
    assert calls == []


def test_langchain_exception_is_reraised_when_the_lookup_fails(lc, transport):
    transport(error=urllib.error.URLError("down"))
    original = forbidden()

    def handler(request):
        raise original

    with pytest.raises(HTTPStatusError) as caught:
        ahn_langchain.handle_tool_call(lc.request, handler)
    assert caught.value is original


def test_langchain_convert_exceptions_false_never_swallows(lc, transport):
    calls = transport()
    original = forbidden()

    def handler(request):
        raise original

    with pytest.raises(HTTPStatusError) as caught:
        ahn_langchain.handle_tool_call(lc.request, handler, convert_exceptions=False)
    assert caught.value is original
    assert calls == []


def test_langchain_graph_control_flow_is_never_a_tool_failure(lc, transport):
    calls = transport()

    class GraphBubbleUp(Exception):
        pass

    class GraphInterrupt(GraphBubbleUp):
        pass

    signal = GraphInterrupt("403 Forbidden for https://example.com/")

    def handler(request):
        raise signal

    with pytest.raises(GraphInterrupt):
        ahn_langchain.handle_tool_call(lc.request, handler)
    assert calls == []


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


def test_langchain_async_twin(lc, transport):
    transport()
    original = forbidden()

    async def failing(request):
        raise original

    async def returning(request):
        return FakeToolMessage("Error: 503 Service Unavailable", "call_1", status="error")

    async def fine(request):
        return FakeToolMessage("ok", "call_1")

    async def run():
        converted = await ahn_langchain.ahandle_tool_call(lc.request, failing)
        annotated = await ahn_langchain.ahandle_tool_call(lc.request, returning)
        plain = await ahn_langchain.ahandle_tool_call(lc.request, fine)
        with pytest.raises(HTTPStatusError):
            await ahn_langchain.ahandle_tool_call(lc.request, failing, convert_exceptions=False)
        return converted, annotated, plain

    converted, annotated, plain = asyncio.run(run())
    assert converted.status == "error" and "AHN example.com:" in converted.content
    assert annotated.content.startswith("Error: 503 Service Unavailable\nAHN example.com:")
    assert plain.content == "ok"


def test_langchain_request_without_a_tool_call_does_not_break(transport):
    transport()
    odd = SimpleNamespace(tool_call=None)

    def handler(request):
        raise forbidden()

    # No id or name to build a message from is fine for the fake class; the point is that nothing raises but the tool's error.
    with pytest.raises(Exception):
        ahn_langchain.handle_tool_call(odd, handler, convert_exceptions=False)


def test_langchain_middleware_in_a_real_agent(transport):
    pytest.importorskip("langchain", minversion="1.0")
    from langchain.agents import create_agent
    from langchain.tools import tool
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    calls = transport()

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
        raise forbidden(url)

    agent = create_agent(FakeModel(), tools=[fetch_page], middleware=[ahn_langchain.ahn_tool_failure_middleware()])
    for run in (lambda: agent.invoke({"messages": [("user", "go")]}),
                lambda: asyncio.run(agent.ainvoke({"messages": [("user", "go")]}))):
        ahn.clear_cache()
        calls.clear()
        out = run()
        tool_message = next(m for m in out["messages"] if m.type == "tool")
        assert tool_message.status == "error"
        assert "403 Forbidden" in tool_message.content and "AHN example.com: recommendation=use_official_path" in tool_message.content
        assert sent(calls) == [("example.com", "site_access", None, f"ahn-client/{ahn.__version__} (+langchain; +https://agenthelpnetwork.com)")]
        assert "token" not in calls[0][0].full_url


# --- OpenAI Agents SDK -----------------------------------------------------------------------------------

class Ctx:
    def __init__(self, arguments=None):
        self.tool_arguments = arguments


def test_openai_adds_the_line_after_the_sdk_message(transport):
    calls = transport()
    fn = ahn_openai.with_ahn_note(lambda context, error: "base message")
    out = fn(Ctx(json.dumps({"url": "https://example.com/a?b=1"})), HTTPStatusError("429 Too Many Requests"))
    assert out.startswith("base message\nAHN example.com: recommendation=use_official_path")
    assert sent(calls)[0][3].endswith("(+openai-agents; +https://agenthelpnetwork.com)")
    assert sent(calls)[0][:3] == ("example.com", "service_error", None)


def test_openai_default_message_is_the_sdk_default_and_never_includes_the_exception(transport):
    transport()
    out = ahn_openai.ahn_failure_error_function(Ctx(json.dumps({"url": "https://example.com/a"})), forbidden("https://example.com/a?secret=1"))
    first, _, rest = out.partition("\n")
    assert first == ahn_openai.FALLBACK_MESSAGE or first.startswith("An error occurred")  # installed SDK: its own default text
    assert "secret" not in first and rest.startswith("AHN example.com:")


def test_openai_message_is_unchanged_when_there_is_nothing_to_add(transport):
    calls = transport()
    fn = ahn_openai.with_ahn_note(lambda context, error: "base")
    assert fn(Ctx(json.dumps({"url": "https://example.com/a"})), not_found()) == "base"
    assert fn(Ctx(None), forbidden("http://localhost/x")) == "base"
    assert fn(object(), RuntimeError("x")) == "base"
    assert calls == []
    transport(error=urllib.error.URLError("down"))
    assert fn(Ctx(json.dumps({"url": "https://example.org/a"})), forbidden("https://example.org/a")) == "base"


def test_openai_non_string_base_is_passed_through(transport):
    transport()
    sentinel = object()
    assert ahn_openai.with_ahn_note(lambda context, error: sentinel)(Ctx(), forbidden()) is sentinel


def test_openai_async_form(transport):
    transport()

    async def base(context, error):
        return "async base"

    fn = ahn_openai.with_ahn_note(base, asynchronous=True)
    assert asyncio.iscoroutinefunction(fn)
    out = asyncio.run(fn(Ctx(json.dumps({"url": "https://example.com/a"})), HTTPStatusError("503 Service Unavailable")))
    assert out.startswith("async base\nAHN example.com:")
    plain = ahn_openai.with_ahn_note(lambda context, error: "sync base", asynchronous=True)
    assert asyncio.run(plain(Ctx(None), not_found())) == "sync base"


def test_openai_documented_signature():
    import inspect
    assert list(inspect.signature(ahn_openai.ahn_failure_error_function).parameters) == ["context", "error"]
    assert not asyncio.iscoroutinefunction(ahn_openai.ahn_failure_error_function)  # the documented form is plain


def test_openai_failure_error_function_in_a_real_function_tool(transport):
    pytest.importorskip("agents")
    from agents import function_tool
    from agents.tool_context import ToolContext

    calls = transport()

    @function_tool(failure_error_function=ahn_openai.ahn_failure_error_function)
    def fetch_page(url: str) -> str:
        """Fetch a page."""
        raise forbidden(url)

    @function_tool(failure_error_function=ahn_openai.with_ahn_note(asynchronous=True))
    async def fetch_page_async(url: str) -> str:
        """Fetch a page."""
        raise HTTPStatusError("Server error '503 Service Unavailable'")

    async def run(tool, url):
        arguments = json.dumps({"url": url})
        context = ToolContext(context=None, tool_name=tool.name, tool_call_id="call_1", tool_arguments=arguments)
        return await tool.on_invoke_tool(context, arguments)

    out = asyncio.run(run(fetch_page, "https://example.com/a?b=1"))
    assert out.startswith("An error occurred while running the tool. Please try again.\nAHN example.com:")
    ahn.clear_cache()
    out = asyncio.run(run(fetch_page_async, "https://example.com/z"))
    assert out.startswith("An error occurred while running the tool. Please try again.\nAHN example.com:")
    assert [row[:3] for row in sent(calls)] == [("example.com", "site_access", None), ("example.com", "service_error", None)]


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
    ahn.clear_cache()
    assert ahn_crewai.note_for_event(tool_failure_event("403 Forbidden for url https://example.net/a", tool_args={}))
    assert sent(calls)[-1][0] == "example.net"


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
    for line in ('langchain = ["langchain>=1.0"]', 'crewai = ["crewai>=1.15.9"]', 'openai-agents = ["openai-agents"]',
                 'claude-agent-sdk = ["claude-agent-sdk"]'):
        assert line in extras


def test_package_version_and_exports():
    assert ahn.__version__ == "0.3.0"
    assert set(ahn.__all__) == {"check", "classify", "clear_cache", "Result", "__version__"}
    assert (ROOT / "clients" / "python" / "src" / "ahn_client" / "__init__.py").read_text(encoding="utf-8").count('__version__ = "0.3.0"') == 1


def test_examples_parse_and_name_the_browser_use_adapter():
    import ast
    examples = ROOT / "clients" / "python" / "examples"
    for path in sorted(examples.glob("*.py")):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    text = (examples / "browser_use_actions_example.py").read_text(encoding="utf-8")
    assert 'ADAPTER = "browser-use"' in text and "adapter=ADAPTER" in text
    assert "@tools.action(" in text and "on_step_end" in text
