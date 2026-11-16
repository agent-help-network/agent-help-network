"""field_notes (D126): fixed sentences at the top of a task route answer that say what outcome, period and routes_tried mean.

No database and no network. The words are fixed, the key set depends only on the records shown, an answer with no record carries only
the call_timing note (D128), and no verdict module reads task routes.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

import pytest
from fastapi.testclient import TestClient

from ahn import task_routes
from ahn.main import create_app
from ahn.mcp_task_routes import CALL_TIMING, DESCRIPTION, task_route_result
from ahn.settings import Settings
from ahn.task_routes import (FIELD_NOTE_OUTCOME, FIELD_NOTE_PERIOD, FIELD_NOTE_ROUTES_TRIED, RouteOutcome, RouteSourceKind,
                             TaskRoutesResponse, field_notes, lookup)

ROOT = Path(__file__).resolve().parents[1]
SINCE = date(2026, 11, 20)


def curated_host() -> str:
    """A site the curated table holds a current record for."""
    from ahn import task_routes_data

    for host in sorted(task_routes_data.TASK_ROUTES_CURATED):
        if lookup(host).routes:
            return host
    raise AssertionError("the curated table has no current record")


def client() -> TestClient:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=True,
                        task_routes_since=SINCE)
    return TestClient(create_app(settings))


def test_a_curated_answer_carries_the_three_notes_first():
    answer = lookup(curated_host())
    assert answer.routes and all(r.outcome == RouteOutcome.not_applicable for r in answer.routes)
    assert answer.field_notes == {"outcome": FIELD_NOTE_OUTCOME, "period": FIELD_NOTE_PERIOD, "routes_tried": FIELD_NOTE_ROUTES_TRIED,
                                  "call_timing": CALL_TIMING}
    assert next(iter(answer.model_dump(mode="json"))) == "field_notes"
    assert FIELD_NOTE_OUTCOME == ("not_applicable marks a record taken from a document: it names a route or a condition and nobody's "
                                  "result is recorded. It does not mean the task does not apply.")
    assert FIELD_NOTE_PERIOD == "The quarter in which the record was read, reported or recorded. It is not a deadline and not a validity window."
    from ahn.task_routes import TASK_ROUTE_PERIOD_UNIT, TaskRoute

    assert TASK_ROUTE_PERIOD_UNIT in FIELD_NOTE_PERIOD and TASK_ROUTE_PERIOD_UNIT in TaskRoute.model_fields["period"].description
    assert FIELD_NOTE_ROUTES_TRIED == ("For a document record, the channels this record copied from the document; they may not be every "
                                       "channel the document names. An empty list means this record lists none, not that the document "
                                       "or the site has none.")
    for sentence in (FIELD_NOTE_OUTCOME, FIELD_NOTE_PERIOD, FIELD_NOTE_ROUTES_TRIED):
        assert re.fullmatch(r"[ -~]+", sentence)


def test_the_rest_data_and_the_mcp_structured_content_carry_the_same_notes():
    host = curated_host()
    body = client().get("/v1/task-routes", params={"site_host": host}).json()
    assert list(body)[0] == "field_notes" and body["field_notes"] == lookup(host).field_notes
    result = task_route_result(lookup(host))
    assert result.structured_content["field_notes"] == body["field_notes"]
    assert json.loads(result.content[1].text)["field_notes"] == body["field_notes"]


def test_an_answer_without_a_record_carries_only_the_call_timing_note():
    for host in ("no-such-site-for-routes.example.com", "example.org"):
        answer = lookup(host)
        assert not answer.routes and answer.field_notes == {"call_timing": CALL_TIMING}
        body = client().get("/v1/task-routes", params={"site_host": host}).json()
        assert list(body)[0] == "field_notes" and body["field_notes"] == {"call_timing": CALL_TIMING}
        result = task_route_result(answer)
        assert result.structured_content["field_notes"] == {"call_timing": CALL_TIMING}
        assert json.loads(result.content[1].text)["field_notes"] == {"call_timing": CALL_TIMING}
        assert result.content[0].text == answer.summary_line and "call_timing" not in answer.summary_line
    assert field_notes([]) == {"call_timing": CALL_TIMING}


def test_call_timing_is_the_one_sentence_the_tool_description_carries_and_is_the_same_for_every_answer():
    assert CALL_TIMING in DESCRIPTION and re.fullmatch(r"[ -~]+", CALL_TIMING)
    assert task_routes.call_timing_note() is CALL_TIMING
    for host in (curated_host(), "no-such-site-for-routes.example.com"):
        body = client().get("/v1/task-routes", params={"site_host": host}).json()
        assert body["field_notes"]["call_timing"] == CALL_TIMING
        assert body["field_notes"].get("call_timing") and list(body["field_notes"])[-1] == "call_timing"


def _record(outcome, source_kind):
    base = lookup(curated_host()).routes[0]
    return base.model_copy(update={"outcome": outcome, "source_kind": source_kind})


def test_reported_records_alone_get_only_the_period_note():
    reported = _record(RouteOutcome.resolved, RouteSourceKind.external_agent)
    assert field_notes([reported]) == {"period": FIELD_NOTE_PERIOD, "call_timing": CALL_TIMING}
    mixed = field_notes([reported, _record(RouteOutcome.not_applicable, RouteSourceKind.curated_official)])
    assert list(mixed) == ["outcome", "period", "routes_tried", "call_timing"]


def test_summary_line_and_notice_keep_their_words():
    answer = lookup(curated_host())
    assert "Records, not guarantees." in answer.summary_line
    assert answer.model_dump(mode="json")["summary_line"] == answer.summary_line
    assert "field_notes" not in answer.summary_line


def test_the_output_schema_and_the_tool_description_name_the_notes():
    schema = TaskRoutesResponse.model_json_schema()
    assert "field_notes" in schema["properties"]
    defs = schema["$defs"]["TaskRoute"]["properties"]
    assert "does not mean the task does not apply" in defs["outcome"]["description"]
    assert "not a deadline" in defs["period"]["description"]
    assert "may not be every channel the document names" in defs["routes_tried"]["description"]
    assert "lists none, not that the document or the site has none" in defs["routes_tried"]["description"]
    assert "It may state a condition other than the obstacle." in defs["condition_text"]["description"]
    assert DESCRIPTION.endswith("field_notes explains outcome, period and routes_tried.")


def test_the_verdict_modules_do_not_import_task_routes():
    for name in ("triage", "response_guide", "service"):
        text = (ROOT / "ahn" / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"^\s*(from|import)\s+\.?(ahn\.)?task_routes", text, re.M), name


def test_the_contract_and_the_decision_say_it():
    en = (ROOT / "docs" / "api-contract.en.md").read_text(encoding="utf-8")
    ko = (ROOT / "docs" / "api-contract.md").read_text(encoding="utf-8")
    decisions = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert "`field_notes` (first in the answer, always present since D128)" in en and "`field_notes`(답의 맨 위, D128 부터 항상 있음)" in ko
    assert "`call_timing`" in en and "`call_timing`" in ko and "`official_help_url`" in en and "`official_help_url`" in ko
    assert len(re.findall(r"^\| D128 ", decisions, re.M)) == 1
    assert "does not mean the task does not apply" in en and "D126" in decisions
    assert len(re.findall(r"^\| D126 ", decisions, re.M)) == 1


def test_the_advertised_output_schema_leaves_room_for_new_notes_and_fields():
    """A client session keeps the output schema it got from list_tools and checks every later structuredContent against it, so a
    key a deployed schema does not allow would break it (D128). The answer's top level is open, and field_notes is a map of strings
    with no fixed keys: call_timing and official_help_url validate against the schema a client cached before they existed."""
    schema = json.loads((ROOT / "tests" / "fixtures" / "mcp" / "tool_schemas_public_task_routes.json").read_text(encoding="utf-8"))
    output = schema["find_task_route"]["output_schema"]
    assert output["additionalProperties"] is True
    notes = next(item for item in output["properties"]["field_notes"]["anyOf"] if item.get("type") == "object")
    assert notes == {"additionalProperties": {"type": "string"}, "type": "object"}  # no "properties", no closed key set
