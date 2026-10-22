"""Shared test guards: no test may read a real host's robots.txt, llms.txt or status feed."""

from __future__ import annotations

import pytest

from ahn import feed_robots


def _no_network(*_args, **_kwargs):
    raise RuntimeError("tests must not reach real hosts: replace this background read with a stub")


class AllowAllGate:
    """Stands in for feed_robots.Gate in the feed tests that read stub feeds under real mapping hosts: every path is
    allowed, no robots.txt is read and no database is touched. tests/test_feed_robots*.py use the real gate with a
    stub robots.txt reader."""

    def __init__(self, *_args, **_kwargs):
        pass

    def decide(self, host, paths):
        from datetime import datetime, timezone

        from ahn.feed_robots import Decision

        return Decision(host, "absent", "http_404", datetime.now(timezone.utc),
                        {path: {"allowed": True, "rule": None, "group": None} for path in paths})


_REAL_FEED_STATE = feed_robots.feed_state


@pytest.fixture(autouse=True)
def feed_gate_allows_all(monkeypatch):
    # The same for the lookup path, which asks the stored decision whether a feed's statement may show.
    monkeypatch.setattr("ahn.status_refresh.make_gate", lambda _settings=None: AllowAllGate())
    monkeypatch.setattr(feed_robots, "feed_state", lambda _conn, _api_url: "allowed")


@pytest.fixture
def allow_all_gate():
    """A gate for tests that call discover_source directly (without one it looks nothing up)."""
    return AllowAllGate()


@pytest.fixture
def real_feed_robots(monkeypatch):
    """The real gate and the real stored-decision check, for the tests of the robots.txt decision itself."""
    monkeypatch.setattr("ahn.status_refresh.make_gate", feed_robots.Gate)
    monkeypatch.setattr(feed_robots, "feed_state", _REAL_FEED_STATE)


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
