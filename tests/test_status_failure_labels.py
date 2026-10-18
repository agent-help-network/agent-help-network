"""Why a status-feed read failed must survive as a short label. No database or live network."""

from __future__ import annotations

import json
import socket
import ssl

import httpx
import pytest
import urllib3

from ahn import status_refresh
from ahn.status_refresh import FAILURE_LABELS, FeedFailure, Source, failure_label

FEED = Source("Example Status", "https://status.example.com/", "https://status.example.com/api/v2/status.json")


def _raise(exc):
    def handler(_request):
        raise exc
    return handler


def _label_from_read(handler) -> str:
    """Run the real feed reader against a mock transport and return the label refresh_one would store."""
    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        with pytest.raises(Exception) as caught:
            status_refresh._read_source(FEED, client)
    return failure_label(caught.value)


@pytest.mark.parametrize("handler,label", [
    (lambda _r: httpx.Response(502), "http_502"),
    (lambda _r: httpx.Response(403), "http_403"),
    (lambda _r: httpx.Response(429), "http_429"),
    (lambda _r: httpx.Response(301, headers={"Location": "https://elsewhere.example/"}), "http_301"),
    (lambda _r: httpx.Response(204), "http_204"),
    (lambda _r: httpx.Response(200, content=b"x" * 32_769), "too_large"),
    (lambda _r: httpx.Response(200, content=b"not json"), "invalid_body"),
    (lambda _r: httpx.Response(200, json={"status": {"indicator": "purple"}}), "invalid_body"),
    (lambda _r: httpx.Response(200, json=[]), "invalid_body"),
    (_raise(httpx.ConnectTimeout("timed out")), "timeout"),
    (_raise(httpx.ReadTimeout("timed out")), "timeout"),
    (_raise(httpx.ConnectError("[Errno 11001] getaddrinfo failed")), "dns_failed"),
    (_raise(httpx.ConnectError("[Errno -2] Name or service not known")), "dns_failed"),
    (_raise(httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")), "tls_error"),
    (_raise(httpx.ConnectError("[Errno 111] Connection refused")), "connect_error"),
    (_raise(httpx.RemoteProtocolError("peer closed connection")), "read_error"),
    (_raise(httpx.ReadError("read failed")), "read_error"),
])
def test_statuspage_feed_failures_get_a_label(handler, label):
    assert _label_from_read(handler) == label


def test_a_good_feed_still_parses_after_the_status_check_moved():
    body = {"page": {"updated_at": "2026-10-03T18:00:00Z"}, "status": {"indicator": "minor"}}
    with httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body))) as client:
        indicator, updated_at = status_refresh._read_source(FEED, client)
    assert indicator == "minor" and updated_at is not None and updated_at.tzinfo is not None


@pytest.mark.parametrize("exc,label", [
    (socket.gaierror(11001, "getaddrinfo failed"), "dns_failed"),
    (ssl.SSLError(1, "certificate verify failed"), "tls_error"),
    (TimeoutError("timed out"), "timeout"),
    (ConnectionRefusedError(111, "refused"), "connect_error"),
    (ConnectionResetError(104, "reset"), "connect_error"),
    (OSError(5, "io"), "connect_error"),
    (urllib3.exceptions.ReadTimeoutError(None, "/api/v2/status.json", "read timed out"), "timeout"),
    (urllib3.exceptions.ConnectTimeoutError("connect timed out"), "timeout"),
    (urllib3.exceptions.NewConnectionError(None, "Failed to establish a new connection: [Errno 111] refused"), "connect_error"),
    (urllib3.exceptions.NewConnectionError(None, "Failed to establish a new connection: [Errno 11001] getaddrinfo failed"), "dns_failed"),
    (urllib3.exceptions.NameResolutionError("status.example.com", None, socket.gaierror(8, "nodename nor servname provided")), "dns_failed"),
    (urllib3.exceptions.ProtocolError("connection broken"), "read_error"),
    (urllib3.exceptions.SSLError("bad handshake"), "tls_error"),
    (urllib3.exceptions.HTTPError("other"), "error"),
    (urllib3.exceptions.DecodeError("bad gzip"), "read_error"),
    (RecursionError("maximum recursion depth exceeded"), "invalid_body"),
    (httpx.DecodingError("bad gzip"), "read_error"),
    (json.JSONDecodeError("x", "doc", 0), "invalid_body"),
    (ValueError("invalid provider status response"), "invalid_body"),
    (KeyError("status"), "invalid_body"),
    (TypeError("bad"), "invalid_body"),
    (FeedFailure("id_mismatch"), "id_mismatch"),
    (RuntimeError("unexpected"), "error"),
])
def test_failure_label_covers_every_caught_exception_family(exc, label):
    assert failure_label(exc) == label
    assert label.startswith("http_") or label in FAILURE_LABELS
    assert len(label) <= 32  # status_cache.last_error_detail is varchar(32)


def test_failure_label_survives_an_exception_whose_str_raises():
    class Loud(ValueError):
        def __str__(self):
            raise RuntimeError("no message for you")
    assert failure_label(Loud()) == "invalid_body"


def test_resolver_failures_are_dns_failed_not_no_cname(monkeypatch):
    import dns.exception
    import dns.resolver

    class TimingOut:
        def __init__(self):
            self.timeout = self.lifetime = None

        def resolve(self, host, kind):
            raise dns.exception.Timeout()

    class NoServers(TimingOut):
        def resolve(self, host, kind):
            raise dns.resolver.NoNameservers()

    class Missing(TimingOut):
        def resolve(self, host, kind):
            raise dns.resolver.NXDOMAIN()

    for resolver in (TimingOut, NoServers):
        monkeypatch.setattr(status_refresh.dns.resolver, "Resolver", resolver)
        with pytest.raises(FeedFailure) as caught:
            status_refresh._read_discovered_statuspage("example.org")
        assert caught.value.label == "dns_failed"
        assert status_refresh.discover_source("example.org") is None  # discovery still swallows it
    monkeypatch.setattr(status_refresh.dns.resolver, "Resolver", Missing)
    assert status_refresh._statuspage_target("example.org") is None
    with pytest.raises(FeedFailure) as caught:
        status_refresh._read_discovered_statuspage("example.org")
    assert caught.value.label == "no_cname"


def test_deeply_nested_feed_body_is_invalid_body_everywhere(monkeypatch):
    nested = b"[" * 20_000
    assert _label_from_read(lambda _r: httpx.Response(200, content=nested)) == "invalid_body"
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh.socket, "getaddrinfo", lambda *_a, **_k: [(None, None, None, None, ("93.184.216.34", 443))])

    class Response:
        status = 200

        def read(self, _limit):
            return nested

        def release_conn(self):
            pass

    class Pool:
        def __init__(self, *_a, **_k):
            pass

        def request(self, *_a, **_k):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    assert status_refresh.discover_source("example.org") is None


def test_discovered_statuspage_failures_get_a_label(monkeypatch):
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: None)
    with pytest.raises(FeedFailure) as caught:
        status_refresh._read_discovered_statuspage("example.org")
    assert caught.value.label == "no_cname"

    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh.socket, "getaddrinfo", lambda *_a, **_k: [(None, None, None, None, ("10.0.0.5", 443))])
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", lambda *_a, **_k: pytest.fail("private address was contacted"))
    with pytest.raises(FeedFailure) as caught:
        status_refresh._read_discovered_statuspage("example.org")
    assert caught.value.label == "not_public"

    monkeypatch.setattr(status_refresh.socket, "getaddrinfo", lambda *_a, **_k: [(None, None, None, None, ("93.184.216.34", 443))])
    answers = iter([
        (503, b""),
        (200, b"x" * 32_769),
        (200, b'{"page":{"id":"anotherpage","name":"Wrong source"},"status":{"indicator":"none"}}'),
        (200, b'{"page":{"id":"abcdefghijkl","name":""},"status":{"indicator":"none"}}'),
        (200, b"not json at all"),
    ])

    class Response:
        def __init__(self, status, body):
            self.status, self._body = status, body

        def read(self, _limit):
            return self._body

        def release_conn(self):
            pass

    class Pool:
        def __init__(self, *_a, **_k):
            pass

        def request(self, *_a, **_k):
            return Response(*next(answers))

        def close(self):
            pass

    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    labels = []
    for _ in range(5):
        with pytest.raises((FeedFailure, json.JSONDecodeError)) as caught:
            status_refresh._read_discovered_statuspage("example.org")
        labels.append(failure_label(caught.value))
    assert labels == ["http_503", "too_large", "id_mismatch", "invalid_body", "invalid_body"]
    # Discovery stays best-effort: every one of those is swallowed into "no source found".
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: None)
    assert status_refresh.discover_source("example.org") is None
