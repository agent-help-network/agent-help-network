"""D95 end to end over REST against the dedicated ahn_test database: a service_error lookup on a host that lists only a developer path is not sent
to it, and AHN_D95_NO_DEV_PATH_ON_FAILURE=0 (Settings.d95_no_dev_path_on_failure) gives the answer before D95. Every read uses a stub."""

from __future__ import annotations

import os

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, service  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.response_guide import NO_RESPONSE_ROWS  # noqa: E402
from ahn.settings import Settings  # noqa: E402
from test_response_guide_db import prepare  # noqa: E402

PORTAL = {"name": "Example", "api_docs": None, "developer_portal": "https://developer.example.org/", "note": None}
HELP = "https://www.example.org/help"
CURATED = {**PORTAL, "consumer": {"paths": [{"kind": "customer_help", "url": HELP, "source_url": "https://www.example.org/"}],
                                  "verified_at": "2026-10-31"}}
OPEN_ROBOTS = b"User-agent: *\nAllow: /\n"
BLOCK_ROBOTS = b"User-agent: *\nDisallow: /\n"
HOSTS = {"open-example.org": OPEN_ROBOTS, "closed-example.org": BLOCK_ROBOTS, "curated-example.org": OPEN_ROBOTS}


def settings_with(on: bool) -> Settings:
    return Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True,
                    collect_lookup_metrics=True, response_guide=True, d95_no_dev_path_on_failure=on)


def read_stub(host, path, *, cap_bytes, **_):
    if path == "/robots.txt":
        return "ok", HOSTS[host], "http_200"
    return "absent", None, "http_404"


def look(client, host, **params):
    response = client.get("/v1/incidents/check", params={"site_host": host, **params})
    assert response.status_code == 200
    return response.json()


def test_failed_lookups_are_not_sent_to_a_developer_path_and_the_switch_off_gives_the_old_answer(monkeypatch):
    prepare(monkeypatch, guide=True)
    monkeypatch.setattr(service, "official_paths", lambda host: dict(CURATED if host == "curated-example.org" else PORTAL))
    monkeypatch.setattr(background_reads, "read_profile_file", read_stub)
    answers = {}
    for on in (True, False):
        settings = settings_with(on)
        with TestClient(create_app(settings)) as client:
            for host in HOSTS:
                look(client, host, error_class="service_error")      # the first lookup queues the read
            background_reads.process_due(settings, limit=50)
            answers[on] = {host: look(client, host, error_class="service_error", failure_stage="before_target_response") for host in HOSTS}
            answers[on]["open-503"] = look(client, "open-example.org", error_class="service_error", failure_stage="response_observed")
    new, old = answers[True], answers[False]
    assert new["open-example.org"]["recommendation"]["action"] == "proceed"
    assert new["open-example.org"]["summary_line"] == NO_RESPONSE_ROWS["P1"]
    assert new["open-example.org"]["next_action"]["url"] is None and old["open-example.org"]["next_action"]["do"] == "use_official_path"
    assert old["open-example.org"]["next_action"]["url"] == PORTAL["developer_portal"]
    assert new["open-example.org"]["official_paths"] == old["open-example.org"]["official_paths"]  # the reference stays in the answer
    # a robots block and a curated customer path are as before
    for host in ("closed-example.org", "curated-example.org"):
        assert new[host]["recommendation"] == old[host]["recommendation"] and new[host]["next_action"] == old[host]["next_action"], host
    assert new["closed-example.org"]["next_action"]["url"] == PORTAL["developer_portal"]
    assert new["curated-example.org"]["next_action"]["customer_url"] == HELP
    # with the switch off every answer differs from the switched-on one in exactly the D95 fields
    for name in new:
        assert {k: v for k, v in new[name].items() if k not in ("recommendation", "summary_line", "next_action", "next_checks", "generated_at",
                                                              "feedback_token", "lookup_activity")} == \
            {k: v for k, v in old[name].items() if k not in ("recommendation", "summary_line", "next_action", "next_checks", "generated_at",
                                                             "feedback_token", "lookup_activity")}, name
