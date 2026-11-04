"""D94 over REST against the dedicated ahn_test database: where every robots token has the same values the answer states them
once; the verdict is the same with the fold on and off. Every read uses a stub."""

from __future__ import annotations

import json
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

from ahn import background_reads  # noqa: E402
from ahn.main import create_app  # noqa: E402
from ahn.models import CheckResponse  # noqa: E402

HOST = "fresh-example.org"
VERDICT = ("recommendation", "next_checks", "summary_line", "next_action", "cause", "links")
FAILURES = ({}, {"error_class": "site_access"}, {"error_class": "service_error"}, {"error_class": "human_verification"},
            {"task_step": "status_check", "error_class": "service_error"}, {"task_step": "site_open", "failure_stage": "unknown"},
            {"task_step": "status_check"})
ROBOTS_FILES = {
    "star_only_open": "User-agent: *\nAllow: /\n",
    "star_disallow_paths": "User-agent: *\nCrawl-delay: 5\nDisallow: /private\nDisallow: /tmp\n",
    "star_blocks_root": "User-agent: *\nDisallow: /\n",
    "named_block": "User-agent: ClaudeBot\nDisallow: /\n\nUser-agent: GPTBot\nDisallow: /\n",
    "named_allow": "User-agent: GPTBot\nAllow: /\n",
}


def serve(monkeypatch, settings, robots):
    monkeypatch.setattr(background_reads, "read_profile_file",
                        lambda host, path, *, cap_bytes, **_: ("ok", robots, "http_200") if path == "/robots.txt" and robots is not None
                        else ("absent", None, "http_404"))
    with TestClient(create_app(settings)) as client:
        client.get("/v1/incidents/check", params={"site_host": HOST})
        background_reads.process_due(settings, limit=50)


def lookup(client, **params):
    response = client.get("/v1/incidents/check", params={"site_host": HOST, **params})
    assert response.status_code == 200
    body = response.json()
    CheckResponse(**body)
    return body


def robots_of(body):
    return body["access_profile"].get("robots") or {}


@pytest.mark.parametrize("name", sorted(ROBOTS_FILES))
def test_verdict_is_the_same_with_the_fold_on_and_off_and_only_the_rules_representation_changes(monkeypatch, name):
    settings = prepare(monkeypatch, guide=True)
    serve(monkeypatch, settings, ROBOTS_FILES[name])
    off = replace(settings, robots_rules_fold=False)
    folded_any = 0
    for params in FAILURES:
        with TestClient(create_app(settings)) as client:
            on = lookup(client, **params)
        with TestClient(create_app(off)) as client:
            full = lookup(client, **params)
        for key in VERDICT:
            assert on.get(key) == full.get(key), (name, params, key)
        rest = lambda body: {k: v for k, v in body.items() if k not in ("access_profile", "generated_at", "feedback_token", "lookup_activity")}  # noqa: E731
        assert rest(on) == rest(full)
        if "rules_uniform" in robots_of(on):
            folded_any += 1
            uniform = robots_of(on)["rules_uniform"]
            assert "rules" not in robots_of(on) and robots_of(on)["rules_omitted"] == len(robots_of(full)["rules"]) > 1
            assert all(rule[k] == v for rule in robots_of(full)["rules"] for k, v in uniform.items() if k != "disallow_paths")
            assert len(json.dumps(on)) < len(json.dumps(full))
            assert not robots_of(full)["agents_blocked"]
        elif params.get("task_step") != "status_check" or params.get("error_class") or params.get("failure_stage"):
            assert robots_of(on)["rules"] == robots_of(full)["rules"], (name, params)
        drop = ("rules", "rules_omitted", "rules_uniform")
        assert {k: v for k, v in robots_of(on).items() if k not in drop} == {k: v for k, v in robots_of(full).items() if k not in drop}
    expect_fold = name in ("star_only_open", "star_disallow_paths", "star_blocks_root")
    assert (folded_any > 0) is expect_fold, (name, folded_any)


def test_the_uniform_values_of_a_star_group_with_paths_and_a_delay(monkeypatch):
    settings = prepare(monkeypatch, guide=True)
    serve(monkeypatch, settings, ROBOTS_FILES["star_disallow_paths"])
    with TestClient(create_app(settings)) as client:
        body = lookup(client, error_class="service_error")
    assert robots_of(body)["rules_uniform"] == {"explicit_group": False, "root_allowed": True, "disallow_count": 2,
                                                "crawl_delay": 5.0, "disallow_paths": ["/private", "/tmp"]}


def test_blocked_tokens_keep_the_rules_for_every_task_step(monkeypatch):
    settings = prepare(monkeypatch, guide=True)
    serve(monkeypatch, settings, ROBOTS_FILES["named_block"])
    with TestClient(create_app(settings)) as client:
        for params in FAILURES:
            body = lookup(client, **params)
            assert robots_of(body)["agents_blocked"] and "rules_uniform" not in robots_of(body)


def test_status_check_without_a_failure_keeps_the_d71_shape(monkeypatch):
    settings = prepare(monkeypatch, guide=True)
    serve(monkeypatch, settings, ROBOTS_FILES["star_only_open"])
    with TestClient(create_app(settings)) as client:
        body = lookup(client, task_step="status_check")
    assert "rules" not in robots_of(body) and robots_of(body)["rules_omitted"] > 1 and "rules_uniform" not in robots_of(body)
