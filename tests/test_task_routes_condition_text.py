"""condition_text: a quoted condition of a curated task route record. No database, no network.

The field is curated-only, never classified, omitted when absent, and shown with source_url. A report cannot send it.
(The write-path refusal against a real database is in tests/test_task_routes_write_db.py.)
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

os.environ.setdefault("AHN_DATABASE_URL", "postgresql://invalid/unused")
os.environ.setdefault("AHN_KEY_PEPPER", "x" * 32)

from ahn import mcp_task_routes, task_route_intake, task_routes, task_routes_data
from ahn.main import create_app
from ahn.settings import Settings
from ahn.task_routes import TASK_ROUTE_CONDITION_TEXT_MAX, TaskRoute, TaskRouteIn, condition_text_problem
from ahn.task_routes_data import TASK_ROUTES_CURATED, _route

ROOT = Path(__file__).resolve().parents[1]
HOST = "example-retailer.com"
SOURCE = "https://www.example-retailer.com/help"
TEXT = "Returns accepted within 30 days of delivery, unopened items only."
TODAY = datetime.now(timezone.utc).date()


def days_ago(days: int) -> str:
    return (TODAY - timedelta(days=days)).isoformat()


def curated(**more) -> dict:
    return _route("return_or_exchange", obstacle="deadline_or_window", routes=("web_form",), source_url=SOURCE,
                  verified_at=days_ago(3), **more)


def test_the_helper_leaves_the_key_out_when_there_is_no_text():
    assert "condition_text" not in curated() and "condition_text" not in curated(condition_text=None)
    assert curated(condition_text="  " + TEXT + " ")["condition_text"] == TEXT  # the helper trims


@pytest.mark.parametrize("bad", ["", "   ", "a\nb", "a\r\nb", "a\tb", "a b", "a\x00b", "a​b", "x" * (TASK_ROUTE_CONDITION_TEXT_MAX + 1)])
def test_the_helper_fails_at_once_for_a_bad_text(bad):
    with pytest.raises(ValueError, match="condition_text"):
        curated(condition_text=bad)


def test_the_length_limit_is_200_characters_and_counts_characters():
    assert condition_text_problem("x" * 200) is None and condition_text_problem("x") is None
    assert condition_text_problem("x" * 201) and condition_text_problem(" x") and condition_text_problem("x ")
    assert condition_text_problem("조건 문구 " * 5 + "끝") is None


def model(**more) -> dict:
    return {"site_host": HOST, "sensitive": False, **curated(**more)}


def test_a_curated_record_carries_and_dumps_the_text_and_omits_it_when_absent():
    assert TaskRoute.model_validate(model(condition_text=TEXT)).model_dump(mode="json")["condition_text"] == TEXT
    assert "condition_text" not in TaskRoute.model_validate(model()).model_dump(mode="json")


def test_the_model_refuses_a_bad_text_even_when_the_helper_is_bypassed():
    for bad in ("", " x", "x ", "a\nb", "x" * 201, 5):
        with pytest.raises(ValidationError):
            TaskRoute.model_validate({**model(), "condition_text": bad})


def test_only_a_curated_record_may_carry_it():
    base = {"site_host": "example.com", "task_kind": "refund_request", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
            "route_worked": "web_form", "period": "2026-Q4", "sensitive": False, "condition_text": TEXT}
    for kind in ("operator_panel", "external_agent", "curated_community"):
        with pytest.raises(ValidationError) as caught:
            TaskRoute.model_validate(dict(base, source_kind=kind, basis="own_attempt", outcome="resolved"))
        assert "condition_text belongs to curated_official only" in str(caught.value)


def test_a_public_report_cannot_send_it_and_its_schema_does_not_list_it():
    body = {"site_host": "example.com", "task_kind": "refund_request", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
            "route_worked": "web_form", "basis": "own_attempt", "outcome": "resolved"}
    TaskRouteIn.model_validate(body)
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate({**body, "condition_text": TEXT})
    assert "condition_text" not in TaskRouteIn.model_json_schema()["properties"]
    assert "condition_text" not in mcp_task_routes._report_schema()["properties"]


def test_the_stored_row_reader_the_columns_and_the_migrations_do_not_know_the_field():
    assert "condition_text" not in task_route_intake.STORED_COLUMNS
    row = {"site_host": "example.com", "task_kind": "refund_request", "obstacle": "wrong_channel", "routes_tried": ["web_form"],
           "route_worked": "web_form", "basis": "own_attempt", "outcome": "resolved", "period": "2026-Q4",
           "source_kind": "external_agent", "condition_text": TEXT}
    assert task_routes.stored_route(row).condition_text is None
    assert not any("condition_text" in path.read_text(encoding="utf-8") for path in (ROOT / "migrations").glob("*.sql"))


def test_every_curated_record_of_the_table_follows_the_rule():
    for records in TASK_ROUTES_CURATED.values():
        for record in records:
            if "condition_text" in record:
                assert condition_text_problem(record["condition_text"]) is None


def table(monkeypatch, data):
    monkeypatch.setattr(task_routes_data, "TASK_ROUTES_CURATED", data)


def client() -> TestClient:
    settings = Settings("postgresql://invalid/unused", "x" * 32, public_mode=True, reporting_enabled=True, task_routes=True,
                        task_routes_since=date(2026, 11, 20))
    return TestClient(create_app(settings))  # no `with`: the lifespan (database) never runs


def test_the_rest_answer_carries_the_text_next_to_source_url_and_omits_it_when_absent(monkeypatch):
    plain = _route("refund_request", obstacle="wrong_channel", routes=("phone",), worked="phone", source_url=SOURCE, verified_at=days_ago(4))
    table(monkeypatch, {HOST: [curated(condition_text=TEXT), plain]})
    answer = client().get("/v1/task-routes", params={"site_host": HOST}).json()
    by_task = {item["task_kind"]: item for item in answer["routes"]}
    assert by_task["return_or_exchange"]["condition_text"] == TEXT and by_task["return_or_exchange"]["source_url"] == SOURCE
    assert "condition_text" not in by_task["refund_request"]


def test_the_mcp_answer_carries_it_too_and_the_descriptions_say_it_is_not_our_judgement(monkeypatch):
    from mcp.server import MCPServer

    table(monkeypatch, {HOST: [curated(condition_text=TEXT)]})
    server = MCPServer(name="test")
    mcp_task_routes.register(server)
    result = asyncio.run(server.call_tool("find_task_route", {"site_host": HOST}))
    route = result.structured_content["routes"][0]
    assert route["condition_text"] == TEXT and route["source_url"] == SOURCE
    assert json.loads(result.content[1].text) == result.structured_content
    tool = {item.name: item for item in asyncio.run(server.list_tools())}["find_task_route"]
    described = tool.output_schema["$defs"]["TaskRoute"]["properties"]["condition_text"]["description"]
    assert "judgement" in described and "source_url" in described and "judgement" in tool.description


def test_the_page_shows_the_text_escaped_in_both_languages(monkeypatch):
    nasty = 'Refund <b>only</b> "within" 30 days & <script>alert(1)</script>'
    table(monkeypatch, {HOST: [curated(condition_text=nasty)]})
    for lang in ("en", "ko"):
        page = client().get(f"/{lang}/routes/{HOST}")
        assert page.status_code == 200 and "routes-condition" in page.text
        assert "<script>alert(1)</script>" not in page.text and "<b>only</b>" not in page.text
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page.text and "&amp;" in page.text


def test_a_page_without_any_text_has_no_condition_markup(monkeypatch):
    table(monkeypatch, {HOST: [curated()]})
    for lang in ("en", "ko"):
        assert "routes-condition" not in client().get(f"/{lang}/routes/{HOST}").text


def test_a_zero_width_character_is_refused_by_the_helper_and_the_model():
    assert condition_text_problem("Unopened​items only")
    with pytest.raises(ValueError, match="condition_text"):
        curated(condition_text="Unopened​items only")


def test_a_page_without_any_text_is_byte_identical_to_the_template_without_the_condition_markup(monkeypatch):
    """With no condition_text the condition markup adds nothing to /routes, not even a blank line."""
    import re
    from jinja2 import ChoiceLoader, DictLoader
    from ahn import main
    table(monkeypatch, {HOST: [curated(), _route("refund_request", obstacle="wrong_channel", routes=("phone",),
                                                  source_url=SOURCE, verified_at=days_ago(4))]})
    pages = {lang: client().get(f"/{lang}/routes/{HOST}").text for lang in ("en", "ko")}
    env = main.templates.env
    source = env.loader.get_source(env, "routes.html")[0]
    # The template as it was before the field: the condition markup removed, and with it the line it would sit on alone.
    block = r"\{% if row\.condition_text %\}.*?\{% endif %\}"
    stripped, alone = re.subn(r"\n[ \t]*" + block + r"(?=\n)", "", source, flags=re.S)
    if not alone:
        stripped = re.sub(block, "", source, flags=re.S)
    assert stripped != source
    monkeypatch.setattr(env, "loader", ChoiceLoader([DictLoader({"routes.html": stripped}), env.loader]))
    if env.cache is not None:
        env.cache.clear()
    try:
        for lang, page in pages.items():
            assert client().get(f"/{lang}/routes/{HOST}").text == page
    finally:
        if env.cache is not None:
            env.cache.clear()


def _verifier():
    import importlib.util
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIRST = "Except for cancelled events, you will not receive a refund for completed purchases."
SECOND = "A job loss claim must be made at least 72 hours prior to the event start date."


def two_conditions() -> list[dict]:
    """The same (task_kind, obstacle) under one host with two different quoted conditions, one record per condition."""
    return [curated(condition_text=FIRST), curated(condition_text=SECOND)]


def test_one_key_with_two_conditions_is_two_records_the_table_check_accepts():
    first, second = two_conditions()
    assert (first["task_kind"], first["obstacle"], first["source_url"]) == (second["task_kind"], second["obstacle"], second["source_url"])
    assert first != second
    assert _verifier().task_routes_table_problems({HOST: [first, second]}, today=TODAY) == []
    assert _verifier().task_routes_table_problems({HOST: [first, dict(first)]}, today=TODAY) == [
        f"{HOST}: task_routes: the same record is listed twice"]  # a true duplicate is still refused


def test_both_records_of_one_key_are_in_the_rest_and_the_mcp_answers(monkeypatch):
    from mcp.server import MCPServer

    table(monkeypatch, {HOST: two_conditions()})
    rest = client().get("/v1/task-routes", params={"site_host": HOST}).json()
    assert sorted(item["condition_text"] for item in rest["routes"]) == sorted([FIRST, SECOND])
    assert rest["counts"]["records"] == 2
    server = MCPServer(name="test")
    mcp_task_routes.register(server)
    result = asyncio.run(server.call_tool("find_task_route", {"site_host": HOST}))
    assert sorted(item["condition_text"] for item in result.structured_content["routes"]) == sorted([FIRST, SECOND])


def test_the_table_has_a_key_with_two_conditions_and_every_text_of_the_table_is_valid():
    pairs = {}
    for host, records in TASK_ROUTES_CURATED.items():
        for record in records:
            if "condition_text" in record:
                assert condition_text_problem(record["condition_text"]) is None
                task_routes.server_route(host, record)  # the answer model accepts every record
                pairs.setdefault((host, record["task_kind"], record["obstacle"], record["source_url"]), []).append(record["condition_text"])
    assert pairs and any(len(texts) == 2 and texts[0] != texts[1] for texts in pairs.values())
    assert _verifier().task_routes_table_problems(TASK_ROUTES_CURATED) == []
