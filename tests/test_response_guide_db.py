"""D51 end to end over REST against the dedicated ahn_test database: the answer carries summary_line, next_action and links,
and AHN_RESPONSE_GUIDE=0 gives the answer before D51. Every read uses a stub; nothing here contacts a real host."""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

from fastapi.testclient import TestClient  # noqa: E402  (after the skip: ahn.main builds an app from the environment)

from ahn import access_profile, background_reads, service
from ahn.db import connect, migrate
from ahn.main import create_app
from ahn.models import CheckResponse
from ahn.response_guide import ROWS
from ahn.service import PUBLIC_REPORTER_ID
from ahn.settings import Settings

NEW_KEYS = ("summary_line", "next_action", "links")
ROBOTS = b"User-agent: Claude-User\nDisallow: /\n\nUser-agent: ChatGPT-User\nDisallow: /\n\nUser-agent: *\nAllow: /\n"
OFFICIAL = {"name": "Example", "api_docs": "https://developer.example.org/docs", "developer_portal": None, "note": None}


def prepare(monkeypatch, *, guide: bool):
    settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True,
                        collect_lookup_metrics=True, response_guide=guide)
    with connect(settings) as conn:
        if conn.info.dbname != "ahn_test":
            raise RuntimeError("Refusing to reset a database other than ahn_test")
    migrate(settings)
    with connect(settings) as conn:
        conn.execute("TRUNCATE audit_events, report_submissions, observations, incidents, reporters, lookup_metrics, request_metrics, "
                     "traffic_attribution_daily, traffic_client_daily, mcp_check_metrics, public_intake_daily, public_intake_host_daily, "
                     "status_cache, status_sources, queried_hosts, host_access_profile, background_reads RESTART IDENTITY CASCADE")
        conn.execute("INSERT INTO reporters (id,key_hash,label) VALUES (%s,repeat('0',64),'public-unverified')", (PUBLIC_REPORTER_ID,))
        conn.execute("UPDATE service_flags SET enabled=true WHERE name IN ('public_intake','public_publication')")
    monkeypatch.setattr(background_reads, "MIN_READ_INTERVAL", 0.0)
    monkeypatch.setattr(background_reads, "discover", lambda _host, _digest=None: None)
    monkeypatch.setattr(service, "cached_profile", access_profile.cached_profile)
    monkeypatch.setattr(service, "official_paths", lambda host: dict(OFFICIAL) if host == "fresh-example.org" else None)
    monkeypatch.setattr(background_reads, "read_profile_file",
                        lambda host, path, *, cap_bytes, **_: ("ok", ROBOTS, "http_200") if path == "/robots.txt" else ("absent", None, "http_404"))
    return settings


def stripped(body: dict) -> dict:
    return {key: value for key, value in body.items() if key not in NEW_KEYS and key != "generated_at"}


def test_first_lookup_then_second_lookup_over_rest(monkeypatch):
    settings = prepare(monkeypatch, guide=True)
    with TestClient(create_app(settings)) as client:
        first = client.get("/v1/incidents/check", params={"site_host": "fresh-example.org", "error_class": "site_access"})
        assert first.status_code == 200
        body = first.json()
        retry = body["access_profile"]["retry_after_seconds"]
        assert body["recommendation"]["action"] == "use_official_path" and body["recommendation"]["reasons"][0] == "profile_pending"
        assert body["next_action"] == {"do": "use_official_path", "url": OFFICIAL["api_docs"], "retry_after_seconds": retry}
        assert body["summary_line"] == ROWS["O1"][1].replace("{n}", str(retry))
        assert set(body["links"]) == {"feedback", "changelog"}
        CheckResponse(**body)
        # the keys sit at the top of the JSON, after the two that always came first
        assert list(body)[:5] == ["site_host", "generated_at", "summary_line", "next_action", "links"]

        background_reads.process_due(settings, limit=50)
        second = client.get("/v1/incidents/check", params={"site_host": "fresh-example.org", "error_class": "site_access"}).json()
        assert second["recommendation"]["reasons"] == ["robots_blocks_some_agents", "official_path_available"]
        assert second["next_action"]["do"] == "use_official_path" and second["summary_line"] == ROWS["O3"][1]
        assert set(second["next_action"]["stop_if_you_are"]) == {"Claude-User", "ChatGPT-User"}
        assert "retry_after_seconds" not in second["access_profile"] and second["next_action"]["retry_after_seconds"] is None

        sent = client.get("/v1/incidents/check", params={"site_host": "fresh-example.org", "agent_vendor": "anthropic"}).json()
        assert sent["recommendation"]["action"] == "use_official_path" and "stop_if_you_are" not in sent["next_action"]


def test_the_switch_off_answer_has_none_of_the_new_keys_and_the_rest_is_the_same(monkeypatch):
    on_settings = prepare(monkeypatch, guide=True)
    with TestClient(create_app(on_settings)) as client:
        on = client.get("/v1/incidents/check", params={"site_host": "plain-example.org"}).json()
    off_settings = Settings(TEST_URL, "test-pepper-must-be-at-least-thirty-two-chars", public_mode=True, reporting_enabled=True,
                            collect_lookup_metrics=True, response_guide=False)
    with TestClient(create_app(off_settings)) as client:
        off = client.get("/v1/incidents/check", params={"site_host": "plain-example.org"})
    assert off.status_code == 200 and not set(NEW_KEYS) & set(off.json())
    assert stripped(off.json()) == stripped(on)
    assert on["recommendation"] == off.json()["recommendation"] and on["next_checks"] == off.json()["next_checks"]
    assert datetime.fromisoformat(off.json()["generated_at"].replace("Z", "+00:00")).tzinfo is timezone.utc


def test_the_openapi_schema_lists_the_new_keys_as_optional(monkeypatch):
    settings = prepare(monkeypatch, guide=True)
    with TestClient(create_app(settings)) as client:
        schema = client.get("/openapi.json").json()["components"]["schemas"]["CheckResponse"]
    assert set(NEW_KEYS) <= set(schema["properties"]) and not set(NEW_KEYS) & set(schema.get("required", []))
