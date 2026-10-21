"""Response compression for the web service, which pays for outbound bandwidth by the gigabyte.

Starlette's GZipMiddleware compresses every response that is not on a short deny-list. This one is
an allow-list instead, so a new route cannot start double-compressing something by accident:

* Only text types are compressed: HTML, JSON, CSS, JavaScript, XML and plain text (llms.txt,
  robots.txt, sitemap.xml). The offline bundle (application/gzip), images, video and server-sent
  events are never touched, and neither is anything that already carries a Content-Encoding.
* Paths in `skip_prefixes` (the MCP endpoint, the offline bundle) pass straight through, whatever
  their content type, so MCP clients and bundle checksums never see a transformed body.
* Small responses (under `minimum_size`) are sent as they are, redirects and 304s have no body to
  compress, and a request with a Range header or a HEAD request is left alone.
* A body whose Content-Length is declared (every page and JSON reply, also when a function middleware
  re-streams it in chunks) is gathered up to BUFFER_LIMIT bytes, compressed in one pass and sent with
  the compressed Content-Length. A body of unknown length is compressed as it streams (each chunk is
  flushed, so nothing is held back) and carries no Content-Length.
* A compressed response turns a strong ETag into a weak one and drops Accept-Ranges (its bytes differ
  from the identity form), and carries `Vary: Accept-Encoding` so a shared cache keeps the forms apart.

Order in the app: this middleware is added last, so it is the outermost one. The rate limiter and
the request metrics run inside it and see the identity response; the metrics count requests and
latency only, never bytes, so compression changes none of those numbers.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Callable, Iterable

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

COMPRESSIBLE_TYPES = frozenset({
    "text/html", "text/plain", "text/css", "text/xml", "text/javascript", "text/markdown",
    "application/json", "application/javascript", "application/xml",
    "application/openapi+json", "application/problem+json", "application/manifest+json",
})
# Roughly the break-even point: below it the gzip header and the lost Content-Length cost more than they save.
DEFAULT_MINIMUM_SIZE = 1000
# zlib's own default. Level 9 costs several times the CPU for a few percent fewer bytes on pages this size.
DEFAULT_LEVEL = 6
# Larger declared bodies are compressed as a stream instead of being held in memory.
BUFFER_LIMIT = 1 << 20

_GZIP_TOKEN = re.compile(r"^\s*(?:gzip|x-gzip|\*)\s*(?:;\s*q\s*=\s*([0-9.]+))?\s*$", re.IGNORECASE)


def accepts_gzip(accept_encoding: str) -> bool:
    """True when the client lists gzip (or x-gzip) with a non-zero quality value.

    A bare `*` is not treated as a request for gzip: browsers and CDNs that want it say so.
    """
    accepted = False
    for part in accept_encoding.split(","):
        name = part.split(";", 1)[0].strip().lower()
        if name not in {"gzip", "x-gzip"}:
            continue
        match = _GZIP_TOKEN.match(part)
        if match is None:
            continue
        quality = match.group(1)
        try:
            accepted = float(quality) > 0 if quality is not None else True
        except ValueError:
            accepted = False
    return accepted


def media_type(content_type: str) -> str:
    return content_type.partition(";")[0].strip().lower()


class TextGZipMiddleware:
    def __init__(self, app: ASGIApp, *, minimum_size: int = DEFAULT_MINIMUM_SIZE, compresslevel: int = DEFAULT_LEVEL,
                 skip_prefixes: Iterable[str] = (), skip_request: Callable[[Scope], bool] | None = None) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.skip_prefixes = tuple(skip_prefixes)
        self.skip_request = skip_request

    def _skipped(self, scope: Scope, headers: Headers) -> bool:
        if scope["method"] != "GET":  # HEAD must keep the length of the identity body; replies to writes are tiny
            return True
        path = scope["path"]
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in self.skip_prefixes):
            return True
        if "range" in headers or not accepts_gzip(headers.get("accept-encoding", "")):
            return True
        return bool(self.skip_request and self.skip_request(scope))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or self._skipped(scope, Headers(scope=scope)):
            await self.app(scope, receive, send)
            return
        await _Responder(send, self.minimum_size, self.compresslevel)._run(self.app, scope, receive)


class _Responder:
    def __init__(self, send: Send, minimum_size: int, level: int) -> None:
        self.send = send
        self.minimum_size = minimum_size
        self.level = level
        self.start: Message = {}
        self.passthrough = False
        self.buffer: bytearray | None = None  # set while a declared-length body is being gathered
        self.compressor: zlib._Compress | None = None  # set once a streamed body is being compressed

    async def _run(self, app: ASGIApp, scope: Scope, receive: Receive) -> None:
        await app(scope, receive, self.handle)

    def _eligible(self, message: Message) -> bool:
        headers = Headers(raw=message["headers"])
        if message["status"] < 200 or message["status"] in {204, 206, 304}:
            return False
        if "content-encoding" in headers or "no-transform" in headers.get("cache-control", "").lower():
            return False
        if media_type(headers.get("content-type", "")) not in COMPRESSIBLE_TYPES:
            return False
        length = headers.get("content-length", "")
        return not (length.isdigit() and int(length) < self.minimum_size)

    def _mark_compressed(self, length: int | None) -> None:
        headers = MutableHeaders(raw=self.start["headers"])
        headers.add_vary_header("Accept-Encoding")
        headers["Content-Encoding"] = "gzip"
        if length is None:
            if "content-length" in headers:
                del headers["content-length"]
        else:
            headers["Content-Length"] = str(length)
        if "accept-ranges" in headers:  # a byte range of the identity form is not a range of this one
            del headers["accept-ranges"]
        etag = headers.get("etag")
        if etag and not etag.startswith("W/"):
            headers["etag"] = "W/" + etag

    async def _release(self) -> None:
        """Send the held start message (and any gathered bytes) unchanged, then stop interfering."""
        self.passthrough = True
        await self.send(self.start)
        if self.buffer:
            await self.send({"type": "http.response.body", "body": bytes(self.buffer), "more_body": True})
        self.buffer = None

    async def handle(self, message: Message) -> None:
        kind = message["type"]
        if kind == "http.response.start":
            self.start = message
            self.passthrough = not self._eligible(message)
            if self.passthrough:
                await self.send(message)
                return
            length = Headers(raw=message["headers"]).get("content-length", "")
            if length.isdigit() and int(length) <= BUFFER_LIMIT:
                self.buffer = bytearray()
            return
        if kind != "http.response.body" or self.passthrough:
            # Early hints, debug info, pathsend, trailers and the like: not ours to transform. (A held start goes out first.)
            if self.start and not self.passthrough and self.compressor is None and kind != "http.response.debug":
                await self._release()
            await self.send(message)
            return
        body = message.get("body", b"")
        more = message.get("more_body", False)
        if self.buffer is not None:
            self.buffer.extend(body)
            if more:
                return
            data = bytes(self.buffer)
            self.buffer = None
            if len(data) < self.minimum_size:
                self.passthrough = True
                await self.send(self.start)
                await self.send({"type": "http.response.body", "body": data, "more_body": False})
                return
            packed = zlib.compress(data, self.level, wbits=16 + zlib.MAX_WBITS)
            self._mark_compressed(len(packed))
            await self.send(self.start)
            await self.send({"type": "http.response.body", "body": packed, "more_body": False})
            return
        if self.compressor is None:  # first piece of a body of unknown length
            if len(body) < self.minimum_size and not more:
                self.passthrough = True
                await self.send(self.start)
                await self.send(message)
                return
            self.compressor = zlib.compressobj(self.level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
            self._mark_compressed(None)
            await self.send(self.start)
        data = self.compressor.compress(body)
        data += self.compressor.flush(zlib.Z_SYNC_FLUSH) if more else self.compressor.flush()
        await self.send({"type": "http.response.body", "body": data, "more_body": more})
