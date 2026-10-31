"""D62: the recommended task_step vocabulary is advice. Contract, tool text and code must agree,
and the server must keep validating the step by pattern only."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from ahn import auto_review, mcp_adapter, service
from ahn.main import create_app
from ahn.models import (PUBLIC_TASK_STEP_CODES, STEP, TASK_STEP_FILTER_HINT, TASK_STEP_HINT, TASK_STEPS,
                        ObservationIn)
from ahn.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
ROW = re.compile(r"^\| `([a-z][a-z0-9_]*)` \| .+ \| (.+) \|$", re.M)


def contract_rows(name: str, accepted_word: str) -> dict[str, bool]:
    text = (ROOT / "docs" / name).read_text(encoding="utf-8")
    start = text.index("## Task step vocabulary" if name.endswith(".en.md") else "## 작업 단계 어휘")
    section = text[start:text.index("\n## ", start + 4)]
    return {code: cell.strip() == accepted_word for code, cell in ROW.findall(section)}


def test_vocabulary_shape():
    assert 8 <= len(TASK_STEPS) <= 14
    assert all(STEP.fullmatch(code) for code in TASK_STEPS)
    assert all(text.strip().endswith(".") and len(text) < 120 for text in TASK_STEPS.values())
    assert "other" in TASK_STEPS and ObservationIn.model_fields["task_step"].default == "other"


def test_public_sets_are_one_subset_of_the_vocabulary():
    assert service.PUBLIC_TASK_STEPS == auto_review.PUBLIC_STEPS == set(PUBLIC_TASK_STEP_CODES)
    assert PUBLIC_TASK_STEP_CODES <= set(TASK_STEPS)


@pytest.mark.parametrize("name,word", [("api-contract.en.md", "accepted"), ("api-contract.md", "받음")])
def test_contract_table_matches_code(name, word):
    rows = contract_rows(name, word)
    assert list(rows) == list(TASK_STEPS)
    assert {code for code, ok in rows.items() if ok} == set(PUBLIC_TASK_STEP_CODES)


def test_contract_says_codes_outside_the_list_are_accepted_by_pattern():
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    assert "a code outside the list is also accepted" in en and "the pattern is the only check" in en
    assert "목록 밖 코드도 받는다" in ko and "형식뿐" in ko


def test_server_still_checks_the_pattern_only():
    base = {"site_host": "example.org", "error_class": "site_access", "outcome": "blocked"}
    for code in ("checkout", "my_custom_step", "a" * 64):
        assert ObservationIn(**base, task_step=code).task_step == code
    for bad in ("Checkout", "1step", "two words", "step-1", "a" * 65, ""):
        with pytest.raises(ValidationError):
            ObservationIn(**base, task_step=bad)


def test_tool_and_openapi_descriptions_list_the_vocabulary():
    tools = {t.name: t for t in asyncio.run(mcp_adapter.mcp.list_tools())}
    check = tools["check_incident"].input_schema["properties"]["task_step"]
    assert check["description"] == TASK_STEP_FILTER_HINT
    schema = create_app(Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True)).openapi()
    params = {p["name"]: p for p in schema["paths"]["/v1/incidents/check"]["get"]["parameters"]}
    assert params["task_step"]["description"] == TASK_STEP_FILTER_HINT
    body = schema["components"]["schemas"]["ObservationIn"]["properties"]["task_step"]
    assert body["description"] == TASK_STEP_HINT
    for code in TASK_STEPS:
        assert code in TASK_STEP_HINT and code in TASK_STEP_FILTER_HINT
    assert len(TASK_STEP_HINT) < 600
