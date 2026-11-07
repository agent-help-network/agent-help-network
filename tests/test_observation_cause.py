"""D109: the optional cause and cause_basis of a report. No database: the combination rules, the 422 code, the
idempotency hash, the MCP tool text and the fixed words."""

from __future__ import annotations

import asyncio
import json
import re

import pytest
from pydantic import ValidationError

from ahn import explain_text, mcp_adapter
from ahn.lookup_signals import why_url
from ahn.models import (CAUSE_NAMING_SITE_OR_PLATFORM, CAUSE_RULES, CauseBasis, CheckObservation, ErrorClass, Links, ObservationCause,
                        ObservationIn, Outcome)
from ahn.service import _request_hash


def report(**changes):
    data = {"site_host": "example.org", "error_class": "site_access", "outcome": "blocked"}
    data.update(changes)
    return data


def accepted(cause, basis, error_class, outcome="blocked"):
    return report(cause=cause, cause_basis=basis, error_class=error_class, outcome=outcome)


def test_the_rule_table_covers_exactly_the_nine_causes_and_four_bases():
    assert len(ObservationCause) == 9 and len(CauseBasis) == 4
    assert set(CAUSE_RULES) == set(ObservationCause)
    assert CAUSE_NAMING_SITE_OR_PLATFORM == {ObservationCause.site_said_no_agents, ObservationCause.platform_refused,
                                             ObservationCause.platform_needs_person, ObservationCause.agent_browser_differs}
    assert "unknown" not in {item.value for item in ObservationCause} | {item.value for item in CauseBasis}


@pytest.mark.parametrize("cause", list(ObservationCause))
@pytest.mark.parametrize("basis", list(CauseBasis))
def test_every_cause_basis_pair_is_accepted_exactly_where_the_table_says(cause, basis):
    bases, classes = CAUSE_RULES[cause]
    error_class = next(iter(classes)).value if classes else "site_access"
    body = accepted(cause.value, basis.value, error_class)
    if basis in bases:
        parsed = ObservationIn.model_validate(body)
        assert parsed.cause == cause and parsed.cause_basis == basis
    else:
        with pytest.raises(ValidationError, match="invalid_cause"):
            ObservationIn.model_validate(body)


@pytest.mark.parametrize("cause", list(ObservationCause))
def test_a_cause_with_an_error_class_outside_its_list_is_refused(cause):
    bases, classes = CAUSE_RULES[cause]
    if classes is None:
        pytest.skip("any error class fits")
    other = next(item for item in ErrorClass if item not in classes)
    with pytest.raises(ValidationError, match="invalid_cause"):
        ObservationIn.model_validate(accepted(cause.value, next(iter(bases)).value, other.value))


def test_a_report_without_either_field_is_unchanged():
    parsed = ObservationIn.model_validate(report())
    assert parsed.cause is None and parsed.cause_basis is None
    assert "cause" not in parsed.model_dump(exclude_unset=True)


@pytest.mark.parametrize("body", [
    report(cause_basis="site_message"),                                              # a basis alone
    report(cause="site_said_no_agents"),                                             # a cause alone
    report(cause="unknown", cause_basis="site_message"),                            # no "unknown" value
    report(cause="site_said_no_agents", cause_basis="a guess"),                     # outside the list
    report(cause="Anthropic blocked it", cause_basis="site_message"),               # free text
    accepted("site_said_no_agents", "site_message", "site_access", outcome="passed"),
    accepted("site_said_no_agents", "site_message", "site_access", outcome="abandoned"),
])
def test_a_malformed_or_misplaced_cause_is_refused(body):
    with pytest.raises(ValidationError):
        ObservationIn.model_validate(body)


def test_an_error_outcome_may_carry_a_cause_too():
    parsed = ObservationIn.model_validate(accepted("site_unavailable", "person_confirmed", "service_error", outcome="error"))
    assert parsed.outcome == Outcome.error


def test_the_idempotency_hash_covers_the_cause_and_the_basis():
    key = "00000000-0000-4000-8000-000000000001"
    plain = ObservationIn.model_validate(report(idempotency_key=key))
    with_cause = ObservationIn.model_validate(accepted("site_said_no_agents", "site_message", "site_access") | {"idempotency_key": key})
    other_basis = ObservationIn.model_validate(accepted("verification_not_received", "person_confirmed", "login_auth") | {"idempotency_key": key})
    other_basis_2 = ObservationIn.model_validate(accepted("verification_not_received", "compared_with_own_browser", "login_auth") | {"idempotency_key": key})
    hashes = {_request_hash('observation', None, item) for item in (plain, with_cause, other_basis, other_basis_2)}
    assert len(hashes) == 4


def test_the_published_observation_leaves_the_fields_out_rather_than_null():
    bare = CheckObservation.model_validate({"observed_at": "2026-11-07T00:00:00Z", "attempt_actor": "agent", "attempt_env": "unknown", "outcome": "error",
                                            "error_class": "site_access", "task_step": "site_open", "synthetic": False})
    assert "cause" not in bare.model_dump(mode="json") and "cause_basis" not in bare.model_dump(mode="json")
    full = bare.model_copy(update={"cause": ObservationCause.signed_out, "cause_basis": CauseBasis.person_confirmed})
    assert full.model_dump(mode="json")["cause"] == "signed_out"


def test_links_why_is_left_out_when_unset_and_kept_within_its_bound():
    assert Links().model_dump(mode="json") == {}
    assert Links(why="https://agenthelpnetwork.com/v1/why?site_host=example.org").model_dump(mode="json") == {
        "why": "https://agenthelpnetwork.com/v1/why?site_host=example.org"}
    with pytest.raises(ValidationError):
        Links(why="x" * 601)


def test_the_why_address_exists_only_for_a_lookup_that_sent_a_failure_code():
    assert why_url({"site_host": "example.org"}) is None
    assert why_url({"site_host": "example.org", "task_step": "status_check"}) is None
    assert why_url({"site_host": "example.org", "error_class": "none"}) is None
    url = why_url({"site_host": "example.org", "error_class": "login_auth", "via": "browser"})
    assert url == "https://agenthelpnetwork.com/v1/why?site_host=example.org&error_class=login_auth&via=browser"
    assert mcp_adapter.why_link({"site_host": "example.org", "error_class": "login_auth", "via": "browser"}) == url


def test_the_report_tool_description_stays_within_the_budget_and_says_when_to_send_a_cause():
    tools = {tool.name: tool for tool in asyncio.run(mcp_adapter.mcp.list_tools())}
    description = tools["report_observation"].description
    assert len(description) <= 2048
    assert "cause_basis" in description and "never as a guess" in description
    schema = json.dumps(tools["report_observation"].input_schema)
    assert "cause_basis" in schema and "site_said_no_agents" in schema and "compared_with_own_browser" in schema
    output = json.dumps(tools["check_incident"].output_schema)
    assert '"why"' in output and "cause_basis" in output


def test_the_fixed_cause_words_exist_in_both_languages_and_pass_the_lint():
    assert set(explain_text.CAUSE_WORDS) == {item.value for item in ObservationCause}
    for words in explain_text.CAUSE_WORDS.values():
        assert words["en"].strip() and words["ko"].strip()
        assert not re.search(r"\b(allowed|permitted|safe|trusted|guarantee|verified)\b", words["en"], re.I)
    assert explain_text.CAUSE_LEAD["en"] and explain_text.CAUSE_LEAD["ko"]


def test_the_mcp_line_carries_the_test_mark_when_only_the_outer_header_set_it(monkeypatch):
    """D109: REST builds links.why from the header too, so the MCP line must carry ahn_test=1 for a call marked by the header."""
    from urllib.parse import parse_qs, urlparse

    import test_d63_decision_order as d63

    payload = {**d63.envelope(d63.answer(d63.WITH_CONSUMER, d63.ALLOWS)), **d63.guide(d63.answer(d63.WITH_CONSUMER, d63.ALLOWS))}

    async def fake(method, path, *, params=None, extra_headers=None, **_):
        return payload
    monkeypatch.setattr(mcp_adapter, "call_api", fake)
    monkeypatch.setattr(mcp_adapter, "outer_request_headers", lambda ctx: {"x-ahn-test": "1"})
    marked = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "login-fixture-host.com", "error_class": "login_auth"}))
    line = marked.content[0].text.splitlines()[-1]
    assert parse_qs(urlparse(line.rsplit(" ", 1)[1]).query)["ahn_test"] == ["1"]
    monkeypatch.setattr(mcp_adapter, "outer_request_headers", lambda ctx: {})
    plain = asyncio.run(mcp_adapter.mcp.call_tool("check_incident", {"site_host": "login-fixture-host.com", "error_class": "login_auth"}))
    assert "ahn_test" not in plain.content[0].text


def test_the_web_report_form_offers_the_cause_fields_in_both_languages_and_sends_them_only_as_a_pair():
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    template = (root / "ahn" / "templates" / "report.html").read_text(encoding="utf-8")
    script = (root / "ahn" / "static" / "report.js").read_text(encoding="utf-8")
    for name, enum in (("cause", ObservationCause), ("cause_basis", CauseBasis)):
        select = re.search(r'name="%s">(.*?)</select>' % name, template, re.S).group(1)
        assert set(re.findall(r'<option value="([a-z_]+)"', select)) == {item.value for item in enum}
    assert "cause_basis: causeBasis" in script and "causePair" in script and "invalid_cause" in script
