"""Shared test guards: no test may read a real host's robots.txt, llms.txt or status feed."""

from __future__ import annotations

import pytest


def _no_network(*_args, **_kwargs):
    raise RuntimeError("tests must not reach real hosts: replace this background read with a stub")


@pytest.fixture(autouse=True)
def no_live_access_profile(monkeypatch):
    # The lookup path reads only the database. By default the declared-policy layer is absent
    # (no cached row, nothing due), as before the reads moved off the lookup path; tests that
    # exercise it opt in with monkeypatch.setattr("ahn.service.cached_profile", access_profile.cached_profile).
    try:
        monkeypatch.setattr("ahn.service.cached_profile", lambda _conn, _host, **_kw: (None, False))
    except Exception:
        pass
    # The background reader is the only code that reaches third-party hosts. Its three external
    # calls fail loudly unless a test replaces them with a stub.
    for name in ("read_profile_file", "refresh_feed", "discover"):
        try:
            monkeypatch.setattr(f"ahn.background_reads.{name}", _no_network)
        except Exception:
            pass
    # The government-list download is the only other external read, and it only ever goes to the lists' publishers.
    try:
        monkeypatch.setattr("ahn.gov_registry.http_get", _no_network)
    except Exception:
        pass
    yield
