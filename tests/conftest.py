"""Shared test guards: no test may read a real host's robots.txt or llms.txt."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_live_access_profile(monkeypatch):
    # service.check reads declared policy from the network for eligible hosts.
    # Tests opt in explicitly with a stub reader; by default the layer is absent.
    try:
        monkeypatch.setattr("ahn.service.get_profile", lambda _settings, _host, **_kw: None)
    except Exception:
        pass
    yield
