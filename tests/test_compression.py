"""Response compression (ahn/compression.py): what is compressed, what is never touched. No database, no network."""

from __future__ import annotations

import asyncio
import gzip
import os

import pytest
from fastapi.testclient import TestClient
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from starlette.routing import Route

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn.compression import BUFFER_LIMIT, TextGZipMiddleware, accepts_gzip  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.settings import Settings  # noqa: E402

BIG_TEXT = ("Agent Help Network answers one question for a public hostname. " * 40).strip()  # about 2.4 KB
SMALL_TEXT = "tiny"
GZIP_BYTES = gzip.compress(BIG_TEXT.encode() * 3)  # already compressed, and larger than the minimum


def toy_app(**options) -> TextGZipMiddleware:
    async def stream(_request):
        async def chunks():
            for index in range(6):
                yield (f"chunk {index}: " + BIG_TEXT[:600] + "\n").encode()
        return StreamingResponse(chunks(), media_type="text/plain")

    async def events(_request):
        async def chunks():
            for index in range(3):
                yield f"data: {index} {BIG_TEXT[:700]}\n\n".encode()
        return StreamingResponse(chunks(), media_type="text/event-stream")

    async def chunked_known_length(_request):
        # What a function middleware does to a page: the declared length stays, the body arrives in pieces.
        async def chunks():
            for index in range(0, len(BIG_TEXT), 700):
                yield BIG_TEXT[index:index + 700].encode()
        return StreamingResponse(chunks(), media_type="text/html", headers={"Content-Length": str(len(BIG_TEXT))})

    async def chunked_known_small(_request):
        async def chunks():
            yield b"ti"
            yield b"ny"
        return StreamingResponse(chunks(), media_type="text/html", headers={"Content-Length": "4"})

    routes = [
        Route("/chunked-known", chunked_known_length),
        Route("/chunked-small", chunked_known_small),
        Route("/html", lambda _r: HTMLResponse(BIG_TEXT)),
        Route("/json", lambda _r: JSONResponse({"text": BIG_TEXT})),
        Route("/text", lambda _r: PlainTextResponse(BIG_TEXT)),
        Route("/small", lambda _r: PlainTextResponse(SMALL_TEXT)),
        Route("/gzip-file", lambda _r: Response(GZIP_BYTES, media_type="application/gzip")),
        Route("/image", lambda _r: Response(b"\x89PNG" + b"0" * 3000, media_type="image/png")),
        Route("/v1/bundle/latest.jsonl.gz.sha256", lambda _r: PlainTextResponse(BIG_TEXT)),  # text, but on an excluded path
        Route("/mcp", lambda _r: JSONResponse({"jsonrpc": "2.0", "result": BIG_TEXT}), methods=["GET", "POST"]),
        Route("/mcp/", lambda _r: JSONResponse({"jsonrpc": "2.0", "result": BIG_TEXT}), methods=["GET", "POST"]),
        Route("/mcpx", lambda _r: PlainTextResponse(BIG_TEXT)),  # only /mcp and below are excluded
        Route("/stream", stream),
        Route("/events", events),
        Route("/redirect", lambda _r: RedirectResponse("/html", status_code=302)),
        Route("/encoded", lambda _r: Response(BIG_TEXT, media_type="text/plain", headers={"Content-Encoding": "br"})),
        Route("/no-transform", lambda _r: PlainTextResponse(BIG_TEXT, headers={"Cache-Control": "no-transform"})),
        Route("/etag", lambda _r: PlainTextResponse(BIG_TEXT, headers={"ETag": '"abc"'})),
        Route("/not-modified", lambda _r: Response(status_code=304, headers={"ETag": '"abc"'})),
        Route("/empty", lambda _r: Response(status_code=204)),
        Route("/partial", lambda _r: Response(BIG_TEXT, status_code=206, media_type="text/plain")),
    ]
    return TextGZipMiddleware(Starlette(routes=routes), **options)


def drive(app, path: str, *, method: str = "GET", accept_encoding: str | None = "gzip", headers: dict | None = None):
    """Drive the ASGI app directly; returns (messages sent to the server, exception the app raised or None)."""
    raw_headers = [(b"host", b"testserver")]
    if accept_encoding is not None:
        raw_headers.append((b"accept-encoding", accept_encoding.encode()))
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": raw_headers, "scheme": "http", "server": ("testserver", 80), "client": ("1.2.3.4", 5000), "root_path": ""}
    sent: list[dict] = []

    delivered = []

    async def receive():
        if not delivered:
            delivered.append(True)
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # a connected client: no disconnect arrives while the response is sent

    async def send(message):
        sent.append(message)

    failure = None
    try:
        asyncio.run(app(scope, receive, send))
    except Exception as exc:  # noqa: BLE001 - the tests look at exactly what was sent before the failure
        failure = exc
    return sent, failure


def call(app, path: str, **kwargs):
    """Returns (status, headers dict, raw body bytes), exactly as sent on the wire; the response must end cleanly."""
    sent, failure = drive(app, path, **kwargs)
    if failure is not None:
        raise failure
    start = sent[0]
    assert start["type"] == "http.response.start"
    body = b"".join(m.get("body", b"") for m in sent[1:] if m["type"] == "http.response.body")
    assert sent[-1].get("more_body", False) is False, "the response must end"
    return start["status"], {k.decode().lower(): v.decode() for k, v in start["headers"]}, body


@pytest.mark.parametrize("path,original", [("/html", BIG_TEXT), ("/text", BIG_TEXT)])
def test_text_is_compressed_and_round_trips(path, original):
    status, headers, body = call(toy_app(), path)
    assert status == 200 and headers["content-encoding"] == "gzip"
    assert "accept-encoding" in headers["vary"].lower()
    assert headers["content-length"] == str(len(body))  # the compressed length, not the original
    assert len(body) < len(original.encode())
    assert gzip.decompress(body).decode() == original


def test_json_is_compressed_and_round_trips():
    status, headers, body = call(toy_app(), "/json")
    assert status == 200 and headers["content-encoding"] == "gzip" and headers["content-type"] == "application/json"
    assert gzip.decompress(body) == JSONResponse({"text": BIG_TEXT}).body


def test_without_gzip_in_accept_encoding_nothing_changes():
    for value in (None, "", "identity", "br", "deflate", "gzip;q=0", "gzip; q=0.0", "*"):
        status, headers, body = call(toy_app(), "/html", accept_encoding=value)
        assert status == 200 and "content-encoding" not in headers and body == BIG_TEXT.encode(), value
    for value in ("gzip", "GZIP", "gzip, deflate, br", "br;q=1.0, gzip;q=0.5", "x-gzip"):
        assert call(toy_app(), "/html", accept_encoding=value)[1].get("content-encoding") == "gzip", value


def test_accepts_gzip_parsing():
    assert accepts_gzip("gzip, deflate") and accepts_gzip("deflate, gzip;q=0.8") and accepts_gzip("GZip")
    assert not accepts_gzip("") and not accepts_gzip("gzip;q=0") and not accepts_gzip("gzipx") and not accepts_gzip("br")
    assert not accepts_gzip("gzip;q=abc")


def test_small_responses_are_left_alone():
    status, headers, body = call(toy_app(), "/small")
    assert status == 200 and "content-encoding" not in headers and body == SMALL_TEXT.encode()
    assert headers["content-length"] == str(len(SMALL_TEXT))
    # The threshold is a parameter: the same 2.4 KB body passes a 5000-byte minimum untouched.
    assert "content-encoding" not in call(toy_app(minimum_size=5000), "/html")[1]


def test_already_compressed_and_binary_types_are_not_recompressed():
    status, headers, body = call(toy_app(), "/gzip-file")
    assert "content-encoding" not in headers and body == GZIP_BYTES and headers["content-type"] == "application/gzip"
    status, headers, body = call(toy_app(), "/image")
    assert "content-encoding" not in headers and body.startswith(b"\x89PNG")
    status, headers, body = call(toy_app(), "/encoded")
    assert headers["content-encoding"] == "br" and body == BIG_TEXT.encode()  # someone else's encoding is never doubled


def test_excluded_paths_pass_through_even_when_the_type_is_text():
    app = toy_app(skip_prefixes=("/mcp/", "/v1/bundle/"))
    for path in ("/v1/bundle/latest.jsonl.gz.sha256", "/mcp", "/mcp/"):
        status, headers, body = call(app, path)
        assert status == 200 and "content-encoding" not in headers and "vary" not in headers, path
        assert len(body) > 1000
    for method in ("POST",):
        assert "content-encoding" not in call(app, "/mcp", method=method)[1]
    # Only the prefix is excluded: a sibling path with the same start is compressed as usual.
    assert call(app, "/mcpx")[1]["content-encoding"] == "gzip"


def test_a_request_predicate_can_skip_compression():
    app = toy_app(skip_request=lambda scope: dict(scope["headers"]).get(b"x-internal") == b"1")
    assert "content-encoding" not in call(app, "/html", headers={"X-Internal": "1"})[1]
    assert call(app, "/html")[1]["content-encoding"] == "gzip"


def test_event_streams_are_never_compressed():
    status, headers, body = call(toy_app(), "/events")
    assert status == 200 and "content-encoding" not in headers and headers["content-type"].startswith("text/event-stream")
    assert body.decode().count("data: ") == 3 and body.endswith(b"\n\n")


def test_streamed_text_is_compressed_and_complete():
    status, headers, body = call(toy_app(), "/stream")
    assert status == 200 and headers["content-encoding"] == "gzip" and "content-length" not in headers
    text = gzip.decompress(body).decode()
    assert text.count("chunk ") == 6 and text.startswith("chunk 0: ") and text.endswith("\n")
    assert text == "".join(f"chunk {i}: " + BIG_TEXT[:600] + "\n" for i in range(6))


def test_a_declared_length_body_sent_in_pieces_is_gathered_and_gets_the_new_exact_length():
    status, headers, body = call(toy_app(), "/chunked-known")
    assert status == 200 and headers["content-encoding"] == "gzip" and headers["content-length"] == str(len(body))
    assert gzip.decompress(body).decode() == BIG_TEXT
    status, headers, body = call(toy_app(), "/chunked-small")  # small and in pieces: delivered whole, as it was
    assert status == 200 and "content-encoding" not in headers and body == b"tiny" and headers["content-length"] == "4"


def raw_app(declared: int | None, pieces: list[bytes], content_type: bytes = b"text/html; charset=utf-8", **options):
    """An app that declares `declared` bytes but sends exactly `pieces`; the last piece ends the response (more_body False)."""
    async def app(scope, receive, send):
        headers = [(b"content-type", content_type)]
        if declared is not None:
            headers.append((b"content-length", str(declared).encode()))
        await send({"type": "http.response.start", "status": 200, "headers": headers})
        for index, piece in enumerate(pieces):
            await send({"type": "http.response.body", "body": piece, "more_body": index < len(pieces) - 1})
    return TextGZipMiddleware(app, **options)


def wire(sent):
    return (sent[0]["status"], {k.decode().lower(): v.decode() for k, v in sent[0]["headers"]},
            b"".join(m.get("body", b"") for m in sent[1:] if m["type"] == "http.response.body"), sent[-1].get("more_body", False))


def test_a_gathered_body_shorter_than_its_declared_length_is_not_turned_into_a_complete_response():
    # The failure BaseHTTPMiddleware hides: 6,040 of 12,080 declared bytes arrive and the response is closed normally.
    half = (BIG_TEXT.encode() * 5)[:6040]
    for pieces in ([half], [half[:3000], half[3000:]]):
        sent, failure = drive(raw_app(12080, pieces), "/page")
        status, headers, body, more = wire(sent)
        assert failure is None and status == 200 and more is False
        assert "content-encoding" not in headers and headers["content-length"] == "12080"  # the server sees 6040 != 12080
        assert body == half


def test_a_gathered_body_longer_than_its_declared_length_is_also_sent_unchanged():
    sent, failure = drive(raw_app(1500, [BIG_TEXT.encode()]), "/page")
    status, headers, body, _more = wire(sent)
    assert failure is None and "content-encoding" not in headers and headers["content-length"] == "1500" and body == BIG_TEXT.encode()


def test_a_streamed_body_cut_short_fails_the_request_and_is_not_finished():
    declared = 2 * BUFFER_LIMIT  # past the gathering limit: the streaming path
    piece = (BIG_TEXT.encode() * 3)[:5000]
    sent, failure = drive(raw_app(declared, [piece, piece, piece]), "/page")
    assert isinstance(failure, RuntimeError) and "Content-Length" in str(failure)
    assert sent[0]["type"] == "http.response.start" and dict(sent[0]["headers"])[b"content-encoding"] == b"gzip"
    # No closing message: the last body message announced more_body, so the server never saw a complete response.
    assert all(m["type"] != "http.response.body" or m["more_body"] for m in sent[1:])


def test_a_streamed_body_of_the_declared_length_is_compressed_and_complete():
    piece = (BIG_TEXT.encode() * 600)[: BUFFER_LIMIT // 2 + 1]
    pieces = [piece, piece, piece]
    sent, failure = drive(raw_app(len(piece) * 3, pieces), "/page")
    status, headers, body, more = wire(sent)
    assert failure is None and more is False and headers["content-encoding"] == "gzip" and "content-length" not in headers
    assert gzip.decompress(body) == b"".join(pieces)


def test_a_streamed_body_of_unknown_length_that_just_ends_is_complete():
    sent, failure = drive(raw_app(None, [BIG_TEXT.encode()[:1200], BIG_TEXT.encode()[1200:]]), "/page")
    status, headers, body, more = wire(sent)
    assert failure is None and more is False and gzip.decompress(body) == BIG_TEXT.encode()


def test_vary_is_set_on_every_response_of_a_compressible_type_and_on_304():
    vary = lambda headers: "accept-encoding" in headers.get("vary", "").lower()  # noqa: E731
    for encoding in ("gzip", None, "identity"):
        assert vary(call(toy_app(), "/html", accept_encoding=encoding)[1]), encoding
        assert vary(call(toy_app(), "/small", accept_encoding=encoding)[1]), encoding  # small: left plain, still varies
        assert vary(call(toy_app(), "/no-transform", accept_encoding=encoding)[1]), encoding
    assert vary(call(toy_app(), "/html", method="HEAD")[1]) and vary(call(toy_app(), "/html", headers={"Range": "bytes=0-9"})[1])
    assert vary(call(toy_app(), "/not-modified")[1]) and vary(call(toy_app(), "/not-modified", accept_encoding=None)[1])
    # Not a compressible type, or no content type at all: no Vary added.
    for path in ("/gzip-file", "/image", "/events", "/redirect", "/empty"):
        assert not vary(call(toy_app(), path)[1]), path
    # An app that already says Vary: Accept-Encoding is not given a second copy.
    def own_vary(_request):
        return PlainTextResponse(BIG_TEXT, headers={"Vary": "Accept-Encoding"})
    app = TextGZipMiddleware(Starlette(routes=[Route("/v", own_vary)]))
    assert call(app, "/v")[1]["vary"].lower().count("accept-encoding") == 1
    assert call(app, "/v", accept_encoding=None)[1]["vary"].lower().count("accept-encoding") == 1


def test_redirects_empty_and_not_modified_and_partial_responses_are_untouched():
    status, headers, body = call(toy_app(), "/redirect")
    assert status == 302 and headers["location"] == "/html" and "content-encoding" not in headers
    status, headers, body = call(toy_app(), "/not-modified")
    assert status == 304 and body == b"" and "content-encoding" not in headers and headers["etag"] == '"abc"'
    status, headers, body = call(toy_app(), "/empty")
    assert status == 204 and body == b"" and "content-encoding" not in headers
    status, headers, body = call(toy_app(), "/partial")
    assert status == 206 and "content-encoding" not in headers and body == BIG_TEXT.encode()
    # A client that asks for a byte range is not given a compressed representation either.
    status, headers, body = call(toy_app(), "/html", headers={"Range": "bytes=0-9"})
    assert "content-encoding" not in headers and body == BIG_TEXT.encode()
    assert "content-encoding" not in call(toy_app(), "/no-transform")[1]


def test_head_keeps_the_identity_length():
    status, headers, body = call(toy_app(), "/html", method="HEAD")
    # (Starlette hands the body to the server, which drops it for HEAD; the headers must describe the identity form.)
    assert "content-encoding" not in headers and headers["content-length"] == str(len(BIG_TEXT.encode()))


def test_a_strong_etag_becomes_weak_when_the_body_is_compressed():
    status, headers, body = call(toy_app(), "/etag")
    assert headers["content-encoding"] == "gzip" and headers["etag"] == 'W/"abc"'
    assert call(toy_app(), "/etag", accept_encoding=None)[1]["etag"] == '"abc"'  # the identity form keeps it


# ---- the real app -----------------------------------------------------------------------------------------------

def web(**fields) -> TestClient:
    return TestClient(create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, **fields)))


def get(client: TestClient, path: str, encoding: str):
    """Returns (response, bytes on the wire). TestClient decodes gzip itself, so the wire size is `num_bytes_downloaded`."""
    with client.stream("GET", path, headers={"Accept-Encoding": encoding}) as response:
        response.read()
    return response, response.num_bytes_downloaded


PAGES = ["/en/", "/ko/", "/en/human", "/en/connect", "/ko/connect", "/en/agents", "/en/search", "/en/policy", "/ko/policy", "/en/terms",
         "/openapi.json", "/llms.txt", "/sitemap.xml", "/static/site.css", "/static/report.js"]


@pytest.mark.parametrize("path", PAGES)
def test_public_pages_are_compressed_and_decode_to_the_same_bytes(path):
    client = web()
    plain, plain_bytes = get(client, path, "identity")
    packed, packed_bytes = get(client, path, "gzip")
    assert plain.status_code == packed.status_code == 200
    assert "content-encoding" not in plain.headers
    assert packed.headers["content-encoding"] == "gzip" and "accept-encoding" in packed.headers["vary"].lower()
    assert packed.content == plain.content and packed.headers["content-type"] == plain.headers["content-type"]
    assert packed_bytes < plain_bytes * 0.6  # text this size shrinks to roughly 20-45 percent
    if "content-length" in packed.headers:
        assert int(packed.headers["content-length"]) == packed_bytes


def test_small_public_responses_stay_plain():
    client = web()
    for path in ("/robots.txt", "/static/prompt.js", "/.well-known/mcp-registry-auth"):
        response, wire = get(client, path, "gzip")
        assert response.status_code == 200 and "content-encoding" not in response.headers, path
        assert wire == len(response.content)


def test_rate_limited_response_is_not_compressed_and_still_counted_as_429():
    client = web()
    responses = [get(client, "/en/", "gzip")[0] for _ in range(241)]
    limited = next(response for response in responses if response.status_code == 429)
    assert limited.json() == {"error": "rate_limited"} and "content-encoding" not in limited.headers


def test_static_file_revalidation_still_answers_304_after_the_etag_turned_weak():
    client = web()
    first, _ = get(client, "/static/site.css", "gzip")
    etag = first.headers["etag"]
    assert first.headers["content-encoding"] == "gzip" and etag.startswith('W/"')
    again = client.get("/static/site.css", headers={"Accept-Encoding": "gzip", "If-None-Match": etag})
    assert again.status_code == 304 and again.content == b""
    identity, _ = get(client, "/static/site.css", "identity")
    assert not identity.headers["etag"].startswith("W/")  # the identity form keeps its strong tag
    assert client.get("/static/site.css", headers={"Accept-Encoding": "identity", "If-None-Match": etag}).status_code == 304


def test_the_static_redirect_to_the_cdn_is_untouched():
    client = web()
    response = client.get("/static/agent-help-network-review-demo-v3.webm", headers={"Accept-Encoding": "gzip"}, follow_redirects=False)
    assert response.status_code == 302 and response.headers["location"].startswith("https://cdn.jsdelivr.net/")
    assert "content-encoding" not in response.headers


def test_compression_is_the_outermost_layer_so_the_limiter_and_metrics_see_the_plain_response():
    stack = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True)).user_middleware
    assert stack[0].cls is TextGZipMiddleware and len(stack) == 2  # [compression, the rate-limit/metrics function middleware]
    off = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, response_gzip=False)).user_middleware
    assert all(item.cls is not TextGZipMiddleware for item in off)


def test_the_switch_turns_compression_off():
    client = web(response_gzip=False)
    response, wire = get(client, "/en/policy", "gzip")
    assert response.status_code == 200 and "content-encoding" not in response.headers and wire == len(response.content)


def test_the_switch_reads_the_environment(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    monkeypatch.delenv("AHN_RESPONSE_GZIP", raising=False)
    assert Settings.from_env().response_gzip is True
    for value, expected in (("0", False), ("1", True), ("", True), ("off", True)):
        monkeypatch.setenv("AHN_RESPONSE_GZIP", value)
        assert Settings.from_env().response_gzip is expected, value


def test_mcp_endpoint_is_never_compressed(monkeypatch):
    """The real one-port app: a client that accepts gzip still gets the MCP replies byte for byte, as plain JSON."""
    import importlib

    monkeypatch.setenv("AHN_BACKGROUND_READS", "0")
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://invalid/unused")
    monkeypatch.setenv("AHN_KEY_PEPPER", "x" * 32)
    for name in ("AHN_PUBLIC_MODE", "AHN_API_BASE", "PORT", "AHN_MCP_PUBLIC_HOSTS", "RENDER_EXTERNAL_HOSTNAME"):
        monkeypatch.delenv(name, raising=False)
    combined = importlib.import_module("ahn.combined")  # sets AHN_API_BASE; monkeypatch undoes it below
    monkeypatch.delenv("AHN_API_BASE", raising=False)
    headers = {"Host": "127.0.0.1:10000", "Accept": "application/json, text/event-stream", "Accept-Encoding": "gzip, deflate"}
    with TestClient(combined.app, base_url="http://127.0.0.1:10000") as client:
        initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                      "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
        for payload, name in ((initialize, "initialize"), ({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, "tools")):
            with client.stream("POST", "/mcp", json=payload, headers=headers) as response:
                response.read()
            assert response.status_code == 200 and "content-encoding" not in response.headers, name
            assert response.headers["content-type"].startswith("application/json")
            assert response.num_bytes_downloaded == len(response.content) > 1000, name  # big enough that gzip would have applied
        assert "check_incident" in {tool["name"] for tool in response.json()["result"]["tools"]}
        # The same client does get the website compressed from that same process.
        page = client.get("/en/policy", headers={"Accept-Encoding": "gzip"})
        assert page.status_code == 200 and page.headers["content-encoding"] == "gzip"
        # GET /mcp is the SSE side of the endpoint. It cannot be read through TestClient (the stream never ends), so
        # drive the app inside the client's own loop and look at the response headers only.
        start = client.portal.call(first_response_start, combined.app, "/mcp",
                                   {"Host": "127.0.0.1:10000", "Accept": "text/event-stream", "Accept-Encoding": "gzip, deflate"})
        sent = {k.decode().lower(): v.decode() for k, v in start["headers"]}
        assert start["status"] == 200 and sent["content-type"].startswith("text/event-stream"), sent
        assert "content-encoding" not in sent and "vary" not in sent, sent


async def first_response_start(app, path: str, headers: dict):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "path": path, "raw_path": path.encode(),
             "query_string": b"", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()], "scheme": "http",
             "server": ("127.0.0.1", 10000), "client": ("127.0.0.1", 5000), "root_path": ""}
    started, disconnect, box = asyncio.Event(), asyncio.Event(), {}

    async def receive():
        if "request" not in box:
            box["request"] = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            box["start"] = message
            started.set()

    task = asyncio.ensure_future(app(scope, receive, send))
    try:
        await asyncio.wait_for(started.wait(), 10)
    finally:
        disconnect.set()
        try:
            await asyncio.wait_for(task, 5)
        except Exception:  # noqa: BLE001 - a timeout or the app's own cancellation: only the headers matter
            task.cancel()
    return box["start"]
