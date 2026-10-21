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
  from the identity form).
* Every GET/HEAD response of a compressible type (and every 304) outside the excluded paths carries
  `Vary: Accept-Encoding`, compressed or not, so a shared cache keeps the forms apart consistently.
* A response that ends shorter or longer than its declared Content-Length is never made to look
  complete: a gathered body whose length differs from the declaration is sent unchanged, so the server
  notices the mismatch and drops the connection, and a streamed one is cut off without its final flush
  and the request fails, so the client sees a truncated transfer instead of a valid short gzip file.

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
# Larger declared bodies are compressed as a stream instead of being held in memory. Compressing a full
# 1 MiB buffer in one call blocks the event loop for about 19 ms; real responses are 1 to 25 KB (about 1 ms).
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


def _add_vary(headers: MutableHeaders) -> None:
    existing = headers.get("vary", "")
    tokens = {token.strip().lower() for token in existing.split(",")}
    if "accept-encoding" not in tokens and "*" not in tokens:
        headers["vary"] = f"{existing}, Accept-Encoding" if existing else "Accept-Encoding"


def _with_vary(message: Message) -> Message:
    message = dict(message)
    message["headers"] = list(message["headers"])
    _add_vary(MutableHeaders(raw=message["headers"]))
    return message


class TextGZipMiddleware:
    def __init__(self, app: ASGIApp, *, minimum_size: int = DEFAULT_MINIMUM_SIZE, compresslevel: int = DEFAULT_LEVEL,
                 skip_prefixes: Iterable[str] = (), skip_request: Callable[[Scope], bool] | None = None) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.skip_prefixes = tuple(skip_prefixes)
        self.skip_request = skip_request

    def _excluded(self, scope: Scope) -> bool:
        path = scope["path"]
        if any(path == prefix.rstrip("/") or path.startswith(prefix) for prefix in self.skip_prefixes):
            return True
        return bool(self.skip_request and self.skip_request(scope))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in {"GET", "HEAD"} or self._excluded(scope):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        # HEAD must keep the length of the identity body, and a byte range is a range of the identity form.
        compress = scope["method"] == "GET" and "range" not in headers and accepts_gzip(headers.get("accept-encoding", ""))
        responder = _Responder(send, self.minimum_size, self.compresslevel, compress)
        await self.app(scope, receive, responder.handle)
        if responder.truncated:
            raise RuntimeError("response body length differs from its Content-Length; transfer aborted")


class _Responder:
    def __init__(self, send: Send, minimum_size: int, level: int, compress: bool) -> None:
        self.send = send
        self.minimum_size = minimum_size
        self.level = level
        self.compress = compress
        self.start: Message = {}
        self.passthrough = False
        self.declared: int | None = None  # Content-Length the app declared, when it did
        self.seen = 0  # body bytes received so far on the streaming path
        self.truncated = False
        self.buffer: bytearray | None = None  # set while a declared-length body is being gathered
        self.compressor: zlib._Compress | None = None  # set once a streamed body is being compressed

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
        _add_vary(headers)
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
        await self.send(_with_vary(self.start))
        if self.buffer:
            await self.send({"type": "http.response.body", "body": bytes(self.buffer), "more_body": True})
        self.buffer = None

    async def _send_unchanged(self, message: Message) -> None:
        self.passthrough = True
        await self.send(_with_vary(self.start))
        await self.send(message)

    async def handle(self, message: Message) -> None:
        kind = message["type"]
        if kind == "http.response.start":
            headers = Headers(raw=message["headers"])
            if not (self.compress and self._eligible(message)):
                self.passthrough = True
                if media_type(headers.get("content-type", "")) in COMPRESSIBLE_TYPES or message["status"] == 304:
                    message = _with_vary(message)
                await self.send(message)
                return
            self.start = message
            length = headers.get("content-length", "")
            self.declared = int(length) if length.isdigit() else None
            if self.declared is not None and self.declared <= BUFFER_LIMIT:
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
            # A body that is not the declared length (the app failed part-way) or is too small is sent exactly
            # as it came, so the server sees the same mismatch it would have seen without compression.
            if len(data) != self.declared or len(data) < self.minimum_size:
                await self._send_unchanged({"type": "http.response.body", "body": data, "more_body": False})
                return
            packed = zlib.compress(data, self.level, wbits=16 + zlib.MAX_WBITS)
            self._mark_compressed(len(packed))
            await self.send(self.start)
            await self.send({"type": "http.response.body", "body": packed, "more_body": False})
            return
        if self.compressor is None:  # first piece of a body of unknown (or very large) length
            if not more and (len(body) < self.minimum_size or (self.declared is not None and len(body) != self.declared)):
                await self._send_unchanged(message)
                return
            self.compressor = zlib.compressobj(self.level, zlib.DEFLATED, 16 + zlib.MAX_WBITS)
            self._mark_compressed(None)
            await self.send(self.start)
        self.seen += len(body)
        if not more and self.declared is not None and self.seen != self.declared:
            # Cut short (or long): no final flush, and the request fails once the app returns, so the
            # connection is dropped mid-transfer instead of ending with a complete-looking gzip stream.
            self.truncated = True
            return
        data = self.compressor.compress(body)
        data += self.compressor.flush(zlib.Z_SYNC_FLUSH) if more else self.compressor.flush()
        await self.send({"type": "http.response.body", "body": data, "more_body": more})
