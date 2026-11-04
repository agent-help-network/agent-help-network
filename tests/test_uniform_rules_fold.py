"""D94 (no database): where every token in access_profile.robots.rules has the same values, the final answer states them once."""

from __future__ import annotations

import json

from ahn import service
from ahn.settings import Settings


def rule(agent, **over):
    return {"agent": agent, "kind": "user_agent", "explicit_group": False, "root_allowed": True, "disallow_count": 0,
            "crawl_delay": None, "disallow_paths": [], **over}


def answer(rules, **robots):
    return {"access_profile": {"robots_status": "ok", "robots": {"rules": rules, "agents_blocked": [], **robots}}, "note": "n"}


def test_identical_tokens_fold_into_one_uniform_object():
    result = answer([rule("A"), rule("B", kind="training_crawler"), rule("C", kind="search_crawler")])
    out = service._fold_uniform_rules(result)
    robots = out["access_profile"]["robots"]
    assert "rules" not in robots and robots["rules_omitted"] == 3
    assert robots["rules_uniform"] == {"explicit_group": False, "root_allowed": True, "disallow_count": 0}
    assert robots["agents_blocked"] == [] and out["note"] == "n"
    assert len(result["access_profile"]["robots"]["rules"]) == 3  # the input is not changed


def test_shared_crawl_delay_and_paths_are_kept_once():
    rules = [rule(name, disallow_count=2, crawl_delay=5.0, disallow_paths=["/a", "/b"]) for name in "ABC"]
    uniform = service._fold_uniform_rules(answer(rules))["access_profile"]["robots"]["rules_uniform"]
    assert uniform == {"explicit_group": False, "root_allowed": True, "disallow_count": 2, "crawl_delay": 5.0,
                       "disallow_paths": ["/a", "/b"]}


def test_one_different_token_keeps_every_rule():
    for change in ({"root_allowed": False}, {"explicit_group": True}, {"disallow_count": 1}, {"crawl_delay": 1.0},
                   {"disallow_paths": ["/x"]}):
        result = answer([rule("A"), rule("B", **change), rule("C")])
        assert service._fold_uniform_rules(result) is result, change


def test_blocked_tokens_few_rules_and_odd_shapes_are_left_alone():
    blocked = answer([rule("A"), rule("B")], agents_blocked=["A"])
    assert service._fold_uniform_rules(blocked) is blocked
    for result in (answer([rule("A")]), answer([]), {"access_profile": None}, {"access_profile": {"robots_status": "absent"}},
                   {"access_profile": {"robots": {"rules": ["x", "y"]}}}):
        assert service._fold_uniform_rules(result) is result


def test_the_folded_robots_object_validates_against_the_answer_model():
    out = service._fold_uniform_rules(answer([rule("A"), rule("B")]))["access_profile"]["robots"]
    from ahn.models import RobotsSummary
    parsed = RobotsSummary(wildcard_root_allowed=True, **{k: v for k, v in out.items() if k in ("rules_omitted", "rules_uniform")})
    dumped = json.loads(parsed.model_dump_json())
    assert dumped["rules_uniform"]["root_allowed"] is True and "crawl_delay" not in dumped["rules_uniform"]


def test_the_switch_reads_the_environment(monkeypatch):
    monkeypatch.setenv("AHN_DATABASE_URL", "postgresql://u:p@127.0.0.1:1/x")
    monkeypatch.setenv("AHN_KEY_PEPPER", "test-pepper-must-be-at-least-thirty-two-chars")
    monkeypatch.delenv("AHN_ROBOTS_RULES_FOLD", raising=False)
    assert Settings.from_env().robots_rules_fold is True
    monkeypatch.setenv("AHN_ROBOTS_RULES_FOLD", "0")
    assert Settings.from_env().robots_rules_fold is False
