"""Public first-lookup discovery must be bounded to publisher-owned Statuspage."""

from __future__ import annotations

import pytest

from ahn import status_refresh


class _Answer:
    def __init__(self, target: str):
        self.target = target


def test_discovery_requires_statuspage_delegation(monkeypatch):
    class Resolver:
        def resolve(self, host, kind):
            assert host == "status.example.org" and kind == "CNAME"
            return [_Answer("api.attacker.example.")]
    monkeypatch.setattr(status_refresh.dns.resolver, "Resolver", Resolver)
    assert status_refresh._statuspage_target("example.org") is None


def test_discovery_never_connects_to_private_ip(monkeypatch, allow_all_gate):
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh.socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))])
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", lambda *_args, **_kwargs: pytest.fail("private address was contacted"))
    assert status_refresh.discover_source("example.org", gate=allow_all_gate) is None


def test_discovery_rejects_mismatched_page_identity(monkeypatch, allow_all_gate):
    monkeypatch.setattr(status_refresh, "_statuspage_target", lambda _host: ("abcdefghijkl.stspg-customer.com", "abcdefghijkl"))
    monkeypatch.setattr(status_refresh.socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 443))])
    class Response:
        status = 200
        def read(self, _limit):
            return b'{"page":{"id":"anotherpage","name":"Wrong source"},"status":{"indicator":"none"}}'
        def release_conn(self):
            pass
    class Pool:
        def __init__(self, *_args, **_kwargs):
            pass
        def request(self, *_args, **_kwargs):
            return Response()
        def close(self):
            pass
    monkeypatch.setattr(status_refresh.urllib3, "HTTPSConnectionPool", Pool)
    assert status_refresh.discover_source("example.org", gate=allow_all_gate) is None
