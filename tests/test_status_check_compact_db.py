"""D71 over REST against the dedicated ahn_test database: a status_check lookup without a failure signal leaves
access_profile.robots.rules out and gives rules_omitted; nothing else in the answer changes. Every read uses a stub."""

from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

TEST_URL = os.environ.get("AHN_TEST_DATABASE_URL", "")
if not TEST_URL:
    pytest.skip("AHN_TEST_DATABASE_URL is required", allow_module_level=True)

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_response_guide_db import prepare  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from ahn import background_reads, service  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.models import CheckResponse  # noqa: E402
from ahn.settings import Settings  # noqa: E402

HOST = "fresh-example.org"


def lookup(client, **params):
    response = client.get("/v1/incidents/check", params={"site_host": HOST, **params})
    assert response.status_code == 200
    body = response.json()
    CheckResponse(**body)
    return body


def warm(monkeypatch, settings):
    with TestClient(create_app(settings)) as client:
        lookup(client)  # queues the read
        background_reads.process_due(settings, limit=50)
    return settings


def robots_of(body):
    return body["access_profile"]["robots"]


def test_status_check_without_failure_omits_rules_and_counts_them(monkeypatch):
    settings = warm(monkeypatch, prepare(monkeypatch, guide=True))
    off = replace(settings, status_check_compact=False)
    with TestClient(create_app(settings)) as client:
        full = lookup(client)
        compact = lookup(client, task_step="status_check")
    with TestClient(create_app(off)) as client:
        switch_off = lookup(client, task_step="status_check")
    count = len(robots_of(full)["rules"])
    assert count > 0 and "rules_omitted" not in robots_of(full)
    assert "rules" not in robots_of(compact) and robots_of(compact)["rules_omitted"] == count
    assert {k: v for k, v in robots_of(compact).items() if k != "rules_omitted"} == {k: v for k, v in robots_of(full).items() if k != "rules"}
    assert sorted(robots_of(compact)["agents_blocked"]) == ["ChatGPT-User", "Claude-User"]
    # the verdict and the guide were computed from the full profile
    assert robots_of(switch_off)["rules"] == robots_of(full)["rules"] and "rules_omitted" not in robots_of(switch_off)
    for key in ("recommendation", "next_checks", "summary_line", "next_action", "links"):
        assert compact[key] == switch_off[key], key
    rest = lambda body: {k: v for k, v in body.items() if k not in ("access_profile", "generated_at")}  # noqa: E731
    assert rest(compact) == rest(switch_off)
    assert len(str(compact)) < len(str(switch_off))


def test_rules_stay_with_a_failure_signal_another_step_or_the_switch_off(monkeypatch):
    settings = warm(monkeypatch, prepare(monkeypatch, guide=True))
    with TestClient(create_app(settings)) as client:
        for params in ({"task_step": "status_check", "error_class": "site_access"},
                       {"task_step": "status_check", "failure_stage": "unknown"},
                       {"task_step": "site_open"}, {"task_step": "statuscheck"}, {}):
            body = lookup(client, **params)
            assert robots_of(body)["rules"] and "rules_omitted" not in robots_of(body), params
    with TestClient(create_app(replace(settings, status_check_compact=False))) as client:
        body = lookup(client, task_step="status_check")
        assert robots_of(body)["rules"] and "rules_omitted" not in robots_of(body)


def test_the_stored_profile_is_not_changed_by_a_compact_answer(monkeypatch):
    settings = warm(monkeypatch, prepare(monkeypatch, guide=True))
    with TestClient(create_app(settings)) as client:
        before = lookup(client)
        lookup(client, task_step="status_check")
        lookup(client, task_step="status_check")
        after = lookup(client)
    assert robots_of(after)["rules"] == robots_of(before)["rules"]
    direct = service.check(settings, HOST, task_step="status_check")
    assert "rules" not in direct["access_profile"]["robots"]
    assert service.check(settings, HOST)["access_profile"]["robots"]["rules"] == robots_of(before)["rules"]


def test_compact_helper_keeps_answers_without_rules_unchanged():
    assert service._compact_status_check({"access_profile": None}) == {"access_profile": None}
    no_robots = {"access_profile": {"robots_status": "absent"}}
    assert service._compact_status_check(no_robots) is no_robots
    empty = {"access_profile": {"robots": {"rules": [], "wildcard_root_allowed": True}}}
    assert service._compact_status_check(empty) is empty  # zero rules: no rules_omitted
    one = {"access_profile": {"robots": {"rules": [{"agent": "x"}], "agents_blocked": []}, "note": "n"}}
    out = service._compact_status_check(one)
    assert out["access_profile"] == {"robots": {"agents_blocked": [], "rules_omitted": 1}, "note": "n"}
    assert one["access_profile"]["robots"]["rules"] == [{"agent": "x"}]


def test_the_switch_reads_the_environment(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")
    monkeypatch.delenv("AHN_STATUS_CHECK_COMPACT", raising=False)
    assert Settings.from_env().status_check_compact is True
    monkeypatch.setenv("AHN_STATUS_CHECK_COMPACT", "0")
    assert Settings.from_env().status_check_compact is False
