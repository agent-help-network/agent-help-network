"""D87 end to end over REST against the dedicated ahn_test database: the first line of a failed lookup follows the kind of failure, and
AHN_FAILURE_KIND_LINES=0 (Settings.failure_kind_lines) gives the sentences before D87 while every other key is the same. Every read uses a stub."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.response_guide import FAILED_U3, FAILED_U3_SERVICE_ERROR, ROWS, SERVICE_ERROR_ROWS  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from test_response_guide_db import ROBOTS, prepare  # noqa: E402

OPEN_ROBOTS = b"User-agent: *\nAllow: /\n"


def settings_with(kind: bool) -> Settings:
    return Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True,
                    collect_lookup_metrics=True, response_guide=True, failure_kind_lines=kind)


def read_stub(host, path, *, cap_bytes, **_):
    if path == "/robots.txt":
        return "ok", (OPEN_ROBOTS if host == "open-example.org" else ROBOTS), "http_200"
    return "absent", None, "http_404"


def look(client, host, **params):
    response = client.get("/v1/incidents/check", params={"site_host": host, **params})
    assert response.status_code == 200
    return response.json()


def test_failed_lookups_get_the_sentence_of_their_kind_and_the_switch_off_gives_the_old_ones(monkeypatch):
    prepare(monkeypatch, guide=True)
    monkeypatch.setattr(background_reads, "read_profile_file", read_stub)
    answers = {}
    for kind in (True, False):
        settings = settings_with(kind)
        with TestClient(create_app(settings)) as client:
            for host in ("open-example.org", "plain-example.org"):
                look(client, host, error_class="site_access")      # first lookup queues the read
            background_reads.process_due(settings, limit=50)
            answers[kind] = {
                "5xx": look(client, "open-example.org", error_class="service_error"),
                "denied": look(client, "open-example.org", error_class="site_access"),
                "u3_5xx": look(client, "plain-example.org", error_class="service_error"),
                "u3_denied": look(client, "plain-example.org", error_class="site_access"),
                "u3_none": look(client, "plain-example.org"),
            }
    on, off = answers[True], answers[False]
    assert on["5xx"]["next_action"]["do"] == "proceed" and on["5xx"]["summary_line"] == SERVICE_ERROR_ROWS["P1"]
    assert on["u3_5xx"]["summary_line"] == FAILED_U3_SERVICE_ERROR and on["u3_denied"]["summary_line"] == FAILED_U3
    assert on["denied"]["summary_line"] == off["denied"]["summary_line"] and on["u3_none"]["summary_line"] == off["u3_none"]["summary_line"]
    assert off["5xx"]["summary_line"] != on["5xx"]["summary_line"] and off["u3_denied"]["summary_line"] == ROWS["U3"][1]
    for name in on:
        assert {k: v for k, v in on[name].items() if k not in ("summary_line", "generated_at", "feedback_token", "lookup_activity")} == \
            {k: v for k, v in off[name].items() if k not in ("summary_line", "generated_at", "feedback_token", "lookup_activity")}, name
