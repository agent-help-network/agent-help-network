"""D108: the answer of GET /v1/why. A separate model from CheckResponse (ahn/models.py is not touched).

The ``server`` object reuses the models of the lookup answer, so its fields serialize exactly as ``GET /v1/incidents/check``
serializes them. Fields that do not apply are left out of the JSON, never null (the convention of CheckResponse, D51).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field

from .models import AttemptEnv, Cause, ErrorClass, FailureStage, NextAction, NextCheck, Recommendation, StrictModel, _omit_none

SCHEMA = "ahn-why/1"

# Upper bounds of the fixed templates. A text that would not fit is dropped by explain.build() (log code why_item_dropped),
# never cut.
LAYER_TEXT_MAX = 520        # a quotation (200) + its source address (up to 200) + the fixed words
CHECK_TEXT_MAX = 520
SHORT_TEXT_MAX = 300
HANDOFF_TEXT_MAX = 400

LayerState = Literal["evidence", "your_answer", "background", "not_seen", "not_reviewed", "no_doc", "unread"]
PersonReasonCode = Literal["sign_in_is_the_account_holders", "verification_on_person_device", "approval_is_the_persons_decision",
                           "company_it_decides", "platform_admin_decides", "platform_refused_step", "server_hand_off"]
Who = Literal["user", "it_admin", "platform_admin", "site_support"]


class WhyInputEcho(StrictModel):
    """The accepted codes only; a value outside its list is left out (never an echo of what was sent)."""
    agent_platform: str | None = Field(default=None, exclude_if=_omit_none)
    agent_env: str | None = Field(default=None, exclude_if=_omit_none)
    external_browser: str | None = Field(default=None, exclude_if=_omit_none)
    stop_point: str | None = Field(default=None, exclude_if=_omit_none)
    screen_message: str | None = Field(default=None, exclude_if=_omit_none)
    since_kind: str | None = Field(default=None, exclude_if=_omit_none)
    since_date: date | None = Field(default=None, exclude_if=_omit_none)
    since_clamped: Literal[True] | None = Field(default=None, exclude_if=_omit_none)
    task_context: str | None = Field(default=None, exclude_if=_omit_none)


class LayerStates(StrictModel):
    site: LayerState
    platform: LayerState
    account: LayerState
    agent_env: LayerState
    task: LayerState


class WhereToLook(StrictModel):
    first: Literal["site", "platform", "account", "agent_env", "task", "unknown"]
    layers: LayerStates
    text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)


class PersonReason(StrictModel):
    code: PersonReasonCode
    text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)


class Person(StrictModel):
    needed: Literal["yes", "not_indicated"]
    reasons: list[PersonReason] = Field(default_factory=list, max_length=3)
    who: list[Who] | None = Field(default=None, max_length=3, exclude_if=_omit_none)
    # The fixed sentence of needed=not_indicated; left out when a person is needed.
    text: str | None = Field(default=None, max_length=SHORT_TEXT_MAX, exclude_if=_omit_none)


class OtherReport(StrictModel):
    observed_at: date
    outcome: Literal["passed", "blocked", "error", "abandoned"]
    error_class: ErrorClass
    task_step: str
    attempt_env: AttemptEnv
    text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)
    verified: Literal[False] = False


class OthersSaw(StrictModel):
    window_days: Literal[7] = 7
    reports: list[OtherReport] = Field(default_factory=list, max_length=5)
    lookup_band: Literal["1-4", "5-9", "10-49", "50+"] | None = Field(default=None, exclude_if=_omit_none)
    text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)


class WhyServer(StrictModel):
    """check()'s own words and decision, copied. Nothing here is computed by explain."""
    summary_line: str | None = Field(default=None, min_length=1, max_length=401, pattern=r"^[ -~]+$", exclude_if=_omit_none)
    recommendation: Recommendation
    next_action: NextAction | None = Field(default=None, exclude_if=_omit_none)
    cause: Cause | None = Field(default=None, exclude_if=_omit_none)
    failure_stage: FailureStage | None = Field(default=None, exclude_if=_omit_none)
    next_checks: list[NextCheck] = Field(default_factory=list)
    position: Literal["first", "after_checks"]


class LayerSource(StrictModel):
    kind: Literal["site_declaration", "vendor_doc", "ahn_read", "provider_status", "site_terms"]
    url: str | None = Field(default=None, max_length=200, exclude_if=_omit_none)
    checked_at: date | None = Field(default=None, exclude_if=_omit_none)
    admin_deployment_doc: Literal[True] | None = Field(default=None, exclude_if=_omit_none)


class LayerRow(StrictModel):
    layer: Literal["L1", "L1a", "L1b", "L1c", "L1d", "L1e", "L2", "L3a", "L3b", "L4"]
    state: LayerState
    undetermined: Literal[True] | None = Field(default=None, exclude_if=_omit_none)
    template_id: str
    text: str = Field(min_length=1, max_length=LAYER_TEXT_MAX)
    source: LayerSource | None = Field(default=None, exclude_if=_omit_none)


class TextItem(StrictModel):
    template_id: str
    text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)


class WhatHappened(StrictModel):
    layers: list[LayerRow] = Field(max_length=10)
    unknowns: list[TextItem] = Field(min_length=1, max_length=6)


class CheckItem(StrictModel):
    id: str
    who: Literal["person", "agent", "it_admin", "platform_admin"]
    text: str = Field(min_length=1, max_length=CHECK_TEXT_MAX)
    url: str | None = Field(default=None, max_length=200, exclude_if=_omit_none)


class DoNow(StrictModel):
    checks: list[CheckItem] = Field(min_length=1, max_length=6)


class TimelineItem(StrictModel):
    at: datetime
    layer: Literal["L1d", "L1e", "L2"]
    kind: Literal["declared_policy_changed", "home_check_changed", "platform_doc_changed"]
    template_id: str
    text: str = Field(min_length=1, max_length=300)
    source_url: str | None = Field(default=None, max_length=200, exclude_if=_omit_none)


class Timeline(StrictModel):
    window_from: date
    window_to: date
    items: list[TimelineItem] = Field(default_factory=list, max_length=20)
    limits_text: str = Field(min_length=1, max_length=SHORT_TEXT_MAX)


class HandoffItem(StrictModel):
    to: Literal["it_admin", "platform_admin", "platform_vendor", "site_support", "user"]
    template_id: str
    text: str = Field(min_length=1, max_length=HANDOFF_TEXT_MAX)


class WhyLinks(StrictModel):
    # The request again with the accepted codes only (never the pasted address, never the codes that were refused).
    self_link: str = Field(alias="self", max_length=1000)


class WhyResponse(StrictModel):
    schema_version: Literal["ahn-why/1"] = SCHEMA
    site_host: str = Field(max_length=253)
    generated_at: datetime
    lang: Literal["en", "ko"]
    host_normalized: Literal["apex", "path_dropped"] | None = Field(default=None, exclude_if=_omit_none)
    input_echo: WhyInputEcho
    where_to_look: WhereToLook
    person: Person
    others_saw: OthersSaw
    rules_applied: list[str] = Field(max_length=7)
    render_order: list[str] = Field(max_length=8)
    server: WhyServer
    what_happened: WhatHappened
    do_now: DoNow
    timeline: Timeline
    handoff: list[HandoffItem] = Field(min_length=1, max_length=2)
    basis_text: str = Field(min_length=1, max_length=200)
    links: WhyLinks
