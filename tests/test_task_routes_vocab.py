"""D117: the closed vocabulary, combination rules and pure helpers of task routes. Pure: no database, no network.

Live checks of curated records (robots.txt first, each source page read): python scripts/verify_official_paths.py --task-routes-only
"""

from __future__ import annotations

import importlib.util
import itertools
import re
import typing
import uuid
from datetime import date, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ahn import task_routes
from ahn.task_routes import (ROUTE_WORKED_NONE, TASK_ROUTE_DETAIL_FIELDS, TASK_ROUTE_ENUMS, TASK_ROUTE_MAX_TRIED,
                        TASK_ROUTE_PERIOD_UNIT, TASK_ROUTE_RULES, DocumentKind, EntryPointKind, RouteBasis, RouteKind,
                        RouteOutcome, RouteSourceKind, SiteRequires, TaskKind, TaskObstacle, TaskRoute, TaskRouteIn,
                        TaskRoutesResponse, VerificationKind, task_route_combination_problem)
from ahn.task_routes import (ROUTE_LINE_MAX, SENSITIVE_TASK_KINDS, TASK_ROUTE_URL_MAX, is_current, is_sensitive,
                              official_url_problem, period_for, route_host, route_line)
from ahn.task_routes_data import TASK_ROUTE_MAX_AGE_DAYS, TASK_ROUTES_CURATED, _route

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 11, 13)
CODE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFIER = _verifier()

# Written out here, apart from TASK_ROUTE_RULES, so the table is checked against an independent reading of the rules.
DETAIL_FOR = {
    "verification_step": "verification_kind",
    "site_requires_info_from_person": "site_requires",
    "document_missing": "document_kind",
    "document_rejected": "document_kind",
    "substitute_document_needed": "document_kind",
    "wrong_entry_point": "entry_point_kind",
}
DETAIL_ENUMS = {"verification_kind": VerificationKind, "site_requires": SiteRequires, "document_kind": DocumentKind,
                "entry_point_kind": EntryPointKind}


def public(**fields) -> dict:
    base = {"site_host": "example.com", "task_kind": "cancel_subscription", "obstacle": "wrong_channel",
            "routes_tried": ["web_form", "phone"], "route_worked": "phone", "basis": "own_attempt", "outcome": "resolved"}
    base.update(fields)
    return {key: value for key, value in base.items() if value is not None}


def record(**fields) -> dict:
    base = public(period="2026-Q4", source_kind="external_agent", sensitive=False)
    base.pop("idempotency_key", None)
    base.update(fields)
    return {key: value for key, value in base.items() if value is not None}


CURATED = _route("cancel_subscription", obstacle="wrong_channel", routes=["web_form", "phone"], worked="phone",
                 official_url="https://www.example.com/help/cancel", source_url="https://www.example.com/help",
                 verified_at="2026-11-02")


# --- The vocabulary --------------------------------------------------------------------------------------------------------

def test_every_value_is_a_short_lowercase_code():
    for field, enum in TASK_ROUTE_ENUMS.items():
        values = [member.value for member in enum]
        assert values, field
        assert len(set(values)) == len(values), field
        for value in values:
            assert CODE.fullmatch(value), (field, value)
    assert CODE.fullmatch(ROUTE_WORKED_NONE)


def test_list_sizes_are_the_agreed_contract():
    assert len(TaskKind) == 24 and len(TaskObstacle) == 18 and len(VerificationKind) == 8 and len(SiteRequires) == 5
    assert len(DocumentKind) == 13 and len(EntryPointKind) == 5 and len(RouteKind) == 13 and len(RouteBasis) == 4
    assert len(RouteOutcome) == 5 and len(TASK_ROUTE_ENUMS["duration"]) == 3 and len(RouteSourceKind) == 4
    assert TASK_ROUTE_MAX_TRIED == 6
    assert "attorney" not in {kind.value for kind in RouteKind}  # a route that would describe the person's situation
    assert "in_person" not in {kind.value for kind in RouteKind} and "office_visit" in {kind.value for kind in RouteKind}


def _readme_lists() -> dict[str, list[str]]:
    text = (ROOT / "scripts" / "README.md").read_text(encoding="utf-8")
    section = text.split("## Curated task routes", 1)[1].split("\n## ", 1)[0]
    found = {}
    for line in section.splitlines():
        match = re.match(r"^- `([a-z_]+)`[^:]*: (.+)$", line.strip())
        if match:
            found[match.group(1)] = re.findall(r"`([a-z0-9_]+)`", match.group(2))
    return found


def test_values_agree_between_models_and_docs():
    docs = _readme_lists()
    assert set(docs) == set(TASK_ROUTE_ENUMS)
    for field, enum in TASK_ROUTE_ENUMS.items():
        values = [member.value for member in enum]
        listed = docs[field]
        if field in DETAIL_ENUMS:  # the line also names the obstacles the field goes with; its values come last
            listed = listed[-len(values):]
        if field == "routes_tried":  # the line also says route_worked may be none
            listed = [value for value in listed if value not in {"route_worked", "none"}]
        assert listed == values, field


def test_models_use_the_same_enums():
    hints = typing.get_type_hints(TaskRoute)
    for field in ("task_kind", "obstacle", "basis", "outcome", "source_kind"):
        assert hints[field] is TASK_ROUTE_ENUMS[field]
    for field in (*TASK_ROUTE_DETAIL_FIELDS, "duration"):
        assert TASK_ROUTE_ENUMS[field] in typing.get_args(hints[field])
    assert typing.get_args(hints["routes_tried"]) == (RouteKind,)


def test_rule_table_names_only_detail_fields():
    assert set(TASK_ROUTE_RULES) == set(TaskObstacle)
    assert {obstacle.value: next(iter(fields)) for obstacle, fields in TASK_ROUTE_RULES.items() if fields} == DETAIL_FOR
    assert all(len(fields) <= 1 for fields in TASK_ROUTE_RULES.values())


# --- Combination rules over the whole space ---------------------------------------------------------------------------------

def _expected(obstacle: str, detail: str | None, outcome: str, worked: str) -> bool:
    if detail is not None and DETAIL_FOR.get(obstacle) != detail:
        return False
    if obstacle == "none" and outcome not in {"resolved", "progressed"}:
        return False
    if worked == "not_tried":
        return False
    if worked == "tried" and outcome == "gave_up":
        return False
    return True


def _space():
    details = [(None, None)] + [(field, member.value) for field, enum in DETAIL_ENUMS.items() for member in enum]
    worked_states = {"absent": None, "none": ROUTE_WORKED_NONE, "tried": "phone", "not_tried": "email"}
    for obstacle, (field, value), outcome, state in itertools.product(TaskObstacle, details, RouteOutcome, worked_states):
        fields = {"obstacle": obstacle.value, "outcome": outcome.value, "routes_tried": ["web_form", "phone"],
                  "route_worked": worked_states[state]}
        if field:
            fields[field] = value
        yield obstacle.value, field, outcome.value, state, fields


def test_combination_rule_accepts_or_rejects_every_combination():
    count = 0
    for obstacle, field, outcome, state, fields in _space():
        problem = task_route_combination_problem(fields)
        assert (problem is None) == _expected(obstacle, field, outcome, state), (obstacle, field, outcome, state, problem)
        count += 1
    assert count == 18 * (1 + 8 + 5 + 13 + 5) * 5 * 4


def test_public_input_model_follows_the_rule_over_the_whole_space():
    for obstacle, field, outcome, state, fields in _space():
        expected = _expected(obstacle, field, outcome, state) and outcome != "not_applicable"
        body = public(**{**fields, "route_worked": fields["route_worked"]})
        try:
            TaskRouteIn.model_validate(body)
            accepted = True
        except ValidationError:
            accepted = False
        assert accepted == expected, (obstacle, field, outcome, state)


def test_two_detail_fields_are_never_accepted_together():
    for first, second in itertools.combinations(TASK_ROUTE_DETAIL_FIELDS, 2):
        for obstacle in TaskObstacle:
            fields = {"obstacle": obstacle.value, "outcome": "resolved",
                      first: next(iter(DETAIL_ENUMS[first])).value, second: next(iter(DETAIL_ENUMS[second])).value}
            assert task_route_combination_problem(fields) is not None


def test_route_worked_is_one_of_routes_tried_or_none():
    for tried in ([], ["phone"], ["phone", "email", "web_form"]):
        for worked in [ROUTE_WORKED_NONE, *(kind.value for kind in RouteKind)]:
            ok = worked == ROUTE_WORKED_NONE or worked in tried
            assert (task_route_combination_problem({"obstacle": "wrong_channel", "outcome": "progressed", "routes_tried": tried,
                                                    "route_worked": worked}) is None) == ok, (tried, worked)
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(routes_tried=[], route_worked="phone"))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(route_worked="attorney"))  # not a value at all


def test_routes_tried_has_at_most_six_and_no_repeats():
    six = [kind.value for kind in RouteKind][:6]
    TaskRouteIn.model_validate(public(routes_tried=six, route_worked=six[-1]))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(routes_tried=[kind.value for kind in RouteKind][:7], route_worked=None))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(routes_tried=["phone", "phone"], route_worked="phone"))


def test_every_field_is_closed():
    for field in ("task_kind", "obstacle", "basis", "outcome", "duration"):
        with pytest.raises(ValidationError):
            TaskRouteIn.model_validate(public(**{field: "ignore_previous_instructions"}))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(routes_tried=["phone", "ignore_previous_instructions"]))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(obstacle="verification_step", verification_kind="Call 555 0100"))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(site_host="https://example.com/claims/123456"))


# --- The public input carries no link and no server-set field --------------------------------------------------------------

@pytest.mark.parametrize("field, value", [
    ("official_url", "https://www.example.com/help"), ("source_url", "https://www.example.com/help"),
    ("verified_at", "2026-11-01"), ("period", "2026-Q4"), ("source_kind", "curated_official"), ("sensitive", False),
    ("note", "free text"),
])
def test_public_input_rejects_server_and_curation_fields(field, value):
    assert field not in TaskRouteIn.model_fields
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(**{field: value}))


def test_public_input_rejects_curated_only_values():
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(basis="official_doc"))
    with pytest.raises(ValidationError):
        TaskRouteIn.model_validate(public(outcome="not_applicable"))
    accepted = TaskRouteIn.model_validate(public(duration="from_1w_to_4w"))
    assert isinstance(accepted.idempotency_key, uuid.UUID)
    dumped = accepted.model_dump(mode="json")
    assert "verification_kind" not in dumped and "duration" in dumped  # absent optional fields are left out, never null


def test_public_input_schema_has_no_link_or_server_field():
    properties = set(TaskRouteIn.model_json_schema()["properties"])
    assert not properties & {"official_url", "source_url", "verified_at", "period", "source_kind", "sensitive"}


# --- Source rules on a stored record ---------------------------------------------------------------------------------------

def test_curated_values_belong_to_curated_official_only():
    for source, basis, outcome in itertools.product(RouteSourceKind, RouteBasis, RouteOutcome):
        curated = source == RouteSourceKind.curated_official
        fields = record(source_kind=source.value, basis=basis.value, outcome=outcome.value, route_worked=None)
        if curated:
            fields.update(source_url="https://www.example.com/help", verified_at="2026-11-02")
        ok = (curated == (basis == RouteBasis.official_doc) and curated == (outcome == RouteOutcome.not_applicable))
        try:
            TaskRoute.model_validate(fields)
            accepted = True
        except ValidationError:
            accepted = False
        assert accepted == ok, (source, basis, outcome)


def test_only_curation_and_operators_carry_a_link():
    link = "https://www.example.com/help/cancel"
    TaskRoute.model_validate(record(source_kind="operator_panel", official_url=link))
    for source in ("external_agent", "curated_community"):
        with pytest.raises(ValidationError):
            TaskRoute.model_validate(record(source_kind=source, official_url=link))
    with pytest.raises(ValidationError):  # source_url and verified_at go with curated_official only, which needs both
        TaskRoute.model_validate(record(source_url="https://www.example.com/help"))
    with pytest.raises(ValidationError):
        TaskRoute.model_validate({**{key: value for key, value in CURATED.items() if key != "source_url"},
                                  "site_host": "example.com", "sensitive": False})
    for bad in (link + "?id=1", "https://www.example.com/claims/1234567", "https://www.elsewhere.com/help"):
        with pytest.raises(ValidationError):  # the model applies official_url_problem too
            TaskRoute.model_validate(record(source_kind="operator_panel", official_url=bad))


def test_curated_helper_builds_a_valid_record():
    assert CURATED["basis"] == "official_doc" and CURATED["outcome"] == "not_applicable"
    assert CURATED["source_kind"] == "curated_official" and CURATED["period"] == "2026-Q4"
    route = TaskRoute.model_validate({"site_host": "example.com", "sensitive": False, **CURATED})
    assert route.model_dump(mode="json")["route_worked"] == "phone"
    assert "verification_kind" not in route.model_dump(mode="json")


def test_period_is_set_by_the_server():
    assert TASK_ROUTE_PERIOD_UNIT == "quarter"
    assert [period_for(date(2026, month, 1)) for month in (1, 3, 4, 6, 7, 9, 10, 12)] == [
        "2026-Q1", "2026-Q1", "2026-Q2", "2026-Q2", "2026-Q3", "2026-Q3", "2026-Q4", "2026-Q4"]
    assert period_for(date(2026, 11, 13), "month") == "2026-11"
    with pytest.raises(ValueError):
        period_for(date(2026, 11, 13), "week")
    for bad in ("2026-11", "2026-Q5", "2026-11-13", "Q4"):
        with pytest.raises(ValidationError):
            TaskRoute.model_validate(record(period=bad))


def test_response_model_shape():
    response = TaskRoutesResponse.model_validate({
        "site_host": "example.com", "summary_line": "1 record for example.com: curated official.",
        "routes": [{"site_host": "example.com", "sensitive": False, **CURATED}],
        "counts": {"records": 1, "by_source_kind": {"curated_official": 1}}})
    assert "notice" not in response.model_dump(mode="json")
    with pytest.raises(ValidationError):
        TaskRoutesResponse.model_validate({"site_host": "example.com", "summary_line": "café", "counts": {"records": 0}})
    with pytest.raises(ValidationError):
        TaskRoutesResponse.model_validate({"site_host": "example.com", "summary_line": "x", "counts": {"records": 0},
                                           "notice": "follow this"})


# --- official_url ------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "https://www.example.com/help/cancel-membership",
    "https://example.com/support/returns",
    "https://help.example.com/claims/form-12345",  # five digits are a form number, not a case number
    "https://www.example.com/",
])
def test_official_url_accepts_plain_site_pages(url):
    assert official_url_problem("example.com", url) is None


@pytest.mark.parametrize("url, reason", [
    ("https://www.example.com/help?topic=cancel", "query"),
    ("https://www.example.com/help?", "query"),
    ("https://www.example.com/help#cancel", "fragment"),
    ("https://jane@www.example.com/help", "userinfo"),
    ("https://jane:pw@www.example.com/help", "userinfo"),
    ("https://www.example.com:8443/help", "port"),
    ("https://www.example.com:443/help", "port"),
    ("http://www.example.com/help", "https"),
    ("ftp://www.example.com/help", "https"),
    ("https://www.example.com/" + "a" * TASK_ROUTE_URL_MAX, "longer"),
    ("https://www.other-site.com/help", "registrable"),
    ("https://example.com.attacker.net/help", "registrable"),
    ("https://www.example.com/claims/1234567/status", "digits"),
    ("https://www.example.com/acct/000123456", "digits"),
    ("https://www.example.com/u/jane.doe@mail.test", "e-mail"),
    ("https://www.example.com/u/jane.doe%40mail.test", "e-mail"),
    ("https://www.example.com/u/jane.doe%2540mail.test", "e-mail"),
    ("https://www.example.com/case/123e4567-e89b-12d3-a456-426614174000", "UUID"),
    ("https://www.example.com/case/123E4567E89B12D3A456426614174000", "UUID"),
    ("https://www.example.com/a/../b", ". or .."),
    ("https://www.example.com/a/%2e%2e/b", ". or .."),
    ("https://10.0.0.1/help", "public hostname"),
    ("https://WWW.EXAMPLE.COM/help", "plain https"),
    ("https://www.example.com/help me", "plain https"),
    (None, "string"),
])
def test_official_url_refuses_links_that_can_name_a_person_or_leave_the_site(url, reason):
    problem = official_url_problem("example.com", url)
    assert problem is not None and reason in problem, (url, problem)


def test_official_url_uses_the_registrable_domain_under_two_label_suffixes():
    assert official_url_problem("example.co.uk", "https://help.example.co.uk/returns") is None
    assert official_url_problem("example.co.uk", "https://other.co.uk/returns") is not None


# --- Where a route is kept ---------------------------------------------------------------------------------------------------

def test_route_host_is_the_apex_and_refuses_tenant_subdomains():
    assert route_host("example.com") == "example.com"
    assert route_host("www.example.com") == "example.com"
    assert route_host("example.co.uk") == "example.co.uk"
    assert route_host("district12.portal.example.com") is None
    assert route_host("help.example.com") is None
    with pytest.raises(ValueError):
        route_host("https://example.com/a")


# --- sensitive (display only) --------------------------------------------------------------------------------------------------

def test_sensitive_rules():
    assert {kind.value for kind in SENSITIVE_TASK_KINDS} == {
        "accommodation_request", "prescription_refill", "insurance_claim", "government_benefit_application",
        "warranty_dispute", "billing_dispute", "complaint_filing", "records_request"}
    for kind in TaskKind:
        assert is_sensitive(kind) == (kind in SENSITIVE_TASK_KINDS)
        assert is_sensitive(kind, government_host=True)
    assert is_sensitive("cancel_subscription", site_requires="referral_or_prescription")
    assert not is_sensitive("cancel_subscription", site_requires="original_document")
    assert is_sensitive("document_submission", document_kind="professional_letter")
    assert is_sensitive("document_submission", document_kind="sworn_statement")
    assert not is_sensitive("document_submission", document_kind="proof_of_purchase")
    assert is_sensitive("portal_login", entry_point_kind="role_specific_portal")
    assert not is_sensitive("portal_login", entry_point_kind="regional_site")
    assert not is_sensitive("account_recovery", entry_point_kind="role_specific_portal")
    assert is_sensitive(TaskKind.portal_login, entry_point_kind=EntryPointKind.role_specific_portal)


# --- Freshness of curated entries ------------------------------------------------------------------------------------------------

def test_is_current_follows_the_ninety_day_rule():
    assert TASK_ROUTE_MAX_AGE_DAYS == 90
    verified = date.fromisoformat(CURATED["verified_at"])
    assert is_current(CURATED, verified) and is_current(CURATED, verified + timedelta(days=90))
    assert not is_current(CURATED, verified + timedelta(days=91))
    assert not is_current(CURATED, verified - timedelta(days=1))  # a future verified_at is not current
    assert is_current(record())  # a stored record follows the storage retention rules instead


# --- route_line ---------------------------------------------------------------------------------------------------------------

BANNED = re.compile(r"\b(you|your|should|must|need to|recommend\w*|please|ignore|instruction\w*)\b", re.IGNORECASE)


def test_route_line_example():
    line = route_line({"site_host": "example.com", "sensitive": False, **CURATED})
    assert line == ("cancel subscription: wrong channel; tried 2 routes, worked: phone; outcome not applicable; "
                    "curated official, 2026-Q4.")
    one = route_line(TaskRoute.model_validate(record(routes_tried=["phone"], route_worked="phone", task_kind="billing_dispute",
                                                     sensitive=True)))
    assert one == ("billing dispute: wrong channel; tried 1 route, worked: phone; outcome resolved; external agent, 2026-Q4. "
                   "Case record, not advice.")
    bare = route_line(record(obstacle="none", routes_tried=[], route_worked=None, outcome="progressed"))
    assert bare == "cancel subscription: none; outcome progressed; external agent, 2026-Q4."


def test_route_line_is_short_ascii_and_states_no_instruction_over_the_space():
    longest_detail = {field: max((member.value for member in enum), key=len) for field, enum in DETAIL_ENUMS.items()}
    longest_route = max((kind.value for kind in RouteKind), key=len)
    tried = [longest_route] + [kind.value for kind in RouteKind if kind.value != longest_route][:5]
    longest = 0
    for task, obstacle, outcome, source, sensitive in itertools.product(TaskKind, TaskObstacle, RouteOutcome, RouteSourceKind,
                                                                        (False, True)):
        route = {"task_kind": task.value, "obstacle": obstacle.value, "routes_tried": tried, "route_worked": longest_route,
                 "outcome": outcome.value, "source_kind": source.value, "period": "2026-Q4", "sensitive": sensitive}
        detail = DETAIL_FOR.get(obstacle.value)
        if detail:
            route[detail] = longest_detail[detail]
        line = route_line(route)
        assert re.fullmatch(r"[ -~]+", line), line
        assert len(line) <= ROUTE_LINE_MAX, line
        assert not BANNED.search(line), line
        assert "http" not in line and "example" not in line
        longest = max(longest, len(line))
    assert longest > 150  # the bound above was reached for, not trivially met


# --- The curated table and its checker -----------------------------------------------------------------------------------------

def test_every_curated_record_passes_the_rules():
    assert VERIFIER.task_routes_table_problems(TASK_ROUTES_CURATED) == []
    for host, routes in TASK_ROUTES_CURATED.items():
        for route in routes:
            TaskRoute.model_validate({"site_host": host, "sensitive": False, **route})


def test_checker_accepts_a_good_record():
    assert VERIFIER.task_route_problems("example.com", CURATED, TODAY) == []
    assert VERIFIER.task_routes_table_problems({"example.com": [CURATED]}, TODAY) == []


@pytest.mark.parametrize("host, change, needle", [
    ("www.example.com", {}, "apex"),
    ("help.example.com", {}, "apex"),
    ("example.com", {"verified_at": "2026-12-01", "period": "2026-Q4"}, "future"),
    ("example.com", {"period": "2026-Q3"}, "period"),
    ("example.com", {"official_url": "https://www.example.com/help/cancel?ref=1"}, "official_url"),
    ("example.com", {"official_url": "https://www.example.com/claims/1234567"}, "official_url"),
    ("example.com", {"official_url": "https://www.example.com/sign-in"}, "sign-in"),
    ("example.com", {"official_url": "https://www.elsewhere.com/help"}, "official_url"),
    ("example.com", {"source_url": "https://www.elsewhere.com/help"}, "source_url"),
    ("example.com", {"source_url": "https://www.example.com/login"}, "sign-in"),
    ("example.com", {"source_kind": "external_agent"}, "invalid_task_route"),
    ("example.com", {"outcome": "resolved"}, "invalid_task_route"),
    ("example.com", {"verification_kind": "sms_code"}, "invalid_task_route"),
    ("example.com", {"note": "free text"}, "note"),
])
def test_checker_reports_rule_problems(host, change, needle):
    problems = VERIFIER.task_route_problems(host, {**CURATED, **change}, TODAY)
    assert problems and any(needle in line for line in problems), problems


def test_checker_reports_duplicates_and_empty_lists():
    assert any("twice" in line for line in VERIFIER.task_routes_table_problems({"example.com": [CURATED, dict(CURATED)]}, TODAY))
    assert VERIFIER.task_routes_table_problems({"example.com": []}, TODAY)


def test_task_routes_only_flag_is_offered():
    source = (ROOT / "scripts" / "verify_official_paths.py").read_text(encoding="utf-8")
    assert "--task-routes-only" in source and "check_task_routes(hosts)" in source


# --- The verdict does not read this layer --------------------------------------------------------------------------------------

@pytest.mark.parametrize("module", ["triage.py", "response_guide.py", "cause.py"])
def test_verdict_modules_do_not_read_task_routes(module):
    source = (ROOT / "ahn" / module).read_text(encoding="utf-8")
    assert "task_route" not in source.lower() and "TaskRoute" not in source


def test_task_routes_module_reads_no_database_or_network():
    source = (ROOT / "ahn" / "task_routes.py").read_text(encoding="utf-8")
    for name in ("psycopg", "httpx", "connect(", "get_settings", "requests"):
        assert name not in source
    assert task_routes.__doc__
