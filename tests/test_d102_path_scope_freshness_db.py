"""D102 over REST against the dedicated ahn_test database: provider_status.note is on every answer that carries a provider_status
(the status_check short answer of D71 included) and absent when there is none; no status_url_covers on a feed-backed host.

Stub feeds only (MockTransport); no real status page is read."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_d100_incidentio_db import OPENAI, ULID, Feeds, ask, lookup, read_feed, status_body, status_check, summary_body, unpaced, use  # noqa: E402,F401

from ahn.models import CheckResponse  # noqa: E402
from ahn.service import PROVIDER_STATUS_NOTE  # noqa: E402


def quiet_feed():
    return Feeds(**{OPENAI: {"/api/v2/status.json": status_body(ULID), "/api/v2/summary.json": summary_body(ULID, [])}})


def test_the_note_is_on_the_plain_and_the_status_check_answer(lookup, monkeypatch):
    settings, client = lookup
    with use(monkeypatch, quiet_feed()):
        read_feed(settings, client, "openai.com")
        plain = ask(client, "openai.com")
        short = status_check(client, "openai.com")
    for body in (plain, short):
        CheckResponse(**body)
        status = body["provider_status"]
        assert status["note"] == PROVIDER_STATUS_NOTE and status["freshness"] == "fresh"
        assert "status_url_covers" not in body["next_action"]


def test_an_answer_with_no_provider_status_has_no_note(lookup):
    _settings, client = lookup
    body = ask(client, "no-feed-example.org")
    assert not body.get("provider_status")
    assert "status_url_covers" not in body["next_action"]
