"""Task routes (D117): the closed vocabulary, combination rules, models and pure helpers of a separate information layer.

For a site and a kind of customer task, a task route records the common obstacle and the routes that were tried or that the
site's own documents name. No database, no network, no settings. Nothing in the lookup verdict (triage, response_guide,
service.check) imports this module. The read API (ahn/task_routes_api.py) and the MCP tool (ahn/mcp_task_routes.py) call ``lookup``
behind the AHN_TASK_ROUTES switch; later steps build intake and pages on it.
The curated table lives in ahn/task_routes_data.py. The helpers fix the rules those steps share:

* ``official_url_problem``  which link a curated or operator entry may carry;
* ``is_sensitive``          when a record is shown as a case record rather than a general route (display only);
* ``period_for``            the server-set reporting period;
* ``is_current``            whether a curated entry is recent enough to answer with;
* ``route_host``            the apex a record is kept under, or None for a tenant subdomain;
* ``route_line``            one fixed English sentence built only from closed values;
* ``lookup``                the answer for a site: current curated records, newest period first, server-computed fields.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal, NamedTuple
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from .models import RESERVED_TLDS, StrictModel, _omit_none, normalize_host_input, valid_host

LOG = logging.getLogger(__name__)


# --- Vocabulary -------------------------------------------------------------------------------------------------------------
# Every field is a closed value of the lists below; there is no free text, no URL from a public report and no person-level
# detail. The vocabulary is listed again in scripts/README.md; tests/test_task_routes_vocab.py keeps the two in agreement.
TASK_ROUTE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class TaskKind(str, Enum):
    claim_unclaimed_property = "claim_unclaimed_property"
    warranty_claim = "warranty_claim"
    warranty_dispute = "warranty_dispute"
    refund_request = "refund_request"
    return_or_exchange = "return_or_exchange"
    cancel_subscription = "cancel_subscription"
    account_closure = "account_closure"
    account_recovery = "account_recovery"
    portal_login = "portal_login"
    address_or_profile_change = "address_or_profile_change"
    appointment_booking = "appointment_booking"
    appointment_change_or_cancel = "appointment_change_or_cancel"
    records_request = "records_request"
    complaint_filing = "complaint_filing"
    billing_dispute = "billing_dispute"
    fee_waiver_or_adjustment = "fee_waiver_or_adjustment"
    insurance_claim = "insurance_claim"
    government_benefit_application = "government_benefit_application"
    tax_or_government_filing = "tax_or_government_filing"
    id_or_license_renewal = "id_or_license_renewal"
    accommodation_request = "accommodation_request"
    prescription_refill = "prescription_refill"
    document_submission = "document_submission"
    other = "other"


class TaskObstacle(str, Enum):
    document_missing = "document_missing"
    document_rejected = "document_rejected"
    substitute_document_needed = "substitute_document_needed"
    form_unclear = "form_unclear"
    wrong_entry_point = "wrong_entry_point"
    wrong_channel = "wrong_channel"
    wrong_department_or_jurisdiction = "wrong_department_or_jurisdiction"
    eligibility_unclear = "eligibility_unclear"
    # Curated entries only: the site's own page states who qualifies or what is covered, quoted in condition_text.
    eligibility_conditions_stated = "eligibility_conditions_stated"
    verification_step = "verification_step"
    code_expired = "code_expired"
    deadline_or_window = "deadline_or_window"
    fee_required = "fee_required"
    waiting_reply = "waiting_reply"
    no_reply = "no_reply"
    request_denied = "request_denied"
    site_requires_info_from_person = "site_requires_info_from_person"
    # A technical stop (403, CAPTCHA, outage) is what check_incident answers; this value only marks that the record is one.
    technical_block = "technical_block"
    none = "none"


class VerificationKind(str, Enum):
    sms_code = "sms_code"
    email_code = "email_code"
    app_approval = "app_approval"
    new_device = "new_device"
    security_question = "security_question"
    account_locked = "account_locked"
    consent_screen = "consent_screen"
    id_document_upload = "id_document_upload"


class SiteRequires(str, Enum):
    """What the site asks for that only the person can supply. Named for the site's requirement, never the person's situation."""
    referral_or_prescription = "referral_or_prescription"
    prior_approval_record = "prior_approval_record"
    account_holder_step = "account_holder_step"
    original_document = "original_document"
    preference_only = "preference_only"


class DocumentKind(str, Enum):
    proof_of_identity = "proof_of_identity"
    proof_of_address = "proof_of_address"
    proof_of_ownership = "proof_of_ownership"
    proof_of_purchase = "proof_of_purchase"
    proof_of_authority = "proof_of_authority"
    business_registration_or_closure = "business_registration_or_closure"
    sworn_statement = "sworn_statement"
    approval_or_authorization_letter = "approval_or_authorization_letter"
    professional_letter = "professional_letter"
    prior_correspondence = "prior_correspondence"
    account_statement = "account_statement"
    official_form = "official_form"
    other = "other"


class EntryPointKind(str, Enum):
    role_specific_portal = "role_specific_portal"
    regional_site = "regional_site"
    separate_subdomain = "separate_subdomain"
    mobile_app_only = "mobile_app_only"
    partner_or_third_party_portal = "partner_or_third_party_portal"
    seller_or_provider = "seller_or_provider"  # curated entries only


class RouteKind(str, Enum):
    """A kind of channel the site or its regulator offers. A route that would describe the person's own situation (hiring
    counsel, for one) is deliberately not a value; adding one is a one-line change here plus scripts/README.md."""
    support_chat = "support_chat"
    phone = "phone"
    email = "email"
    web_form = "web_form"
    office_visit = "office_visit"
    mail_or_fax = "mail_or_fax"
    consumer_agency = "consumer_agency"
    regulator = "regulator"
    designated_dispute_program = "designated_dispute_program"
    official_app = "official_app"
    different_entry_point = "different_entry_point"
    escalation_within_site = "escalation_within_site"
    other = "other"


class RouteBasis(str, Enum):
    official_doc = "official_doc"  # curated entries only
    agency_reply = "agency_reply"
    outcome_observed = "outcome_observed"
    own_attempt = "own_attempt"


class RouteOutcome(str, Enum):
    resolved = "resolved"
    progressed = "progressed"
    no_progress = "no_progress"
    gave_up = "gave_up"
    not_applicable = "not_applicable"  # curated entries only: a document names a route, nobody's result is recorded


class RouteDuration(str, Enum):
    under_1w = "under_1w"
    from_1w_to_4w = "from_1w_to_4w"
    over_4w = "over_4w"


class RouteSourceKind(str, Enum):
    operator_panel = "operator_panel"
    external_agent = "external_agent"
    curated_official = "curated_official"
    curated_community = "curated_community"  # a value only: no intake path writes it yet


TASK_ROUTE_ENUMS: dict[str, type[Enum]] = {
    "task_kind": TaskKind, "obstacle": TaskObstacle, "verification_kind": VerificationKind, "site_requires": SiteRequires,
    "document_kind": DocumentKind, "entry_point_kind": EntryPointKind, "routes_tried": RouteKind, "basis": RouteBasis,
    "outcome": RouteOutcome, "duration": RouteDuration, "source_kind": RouteSourceKind,
}
ROUTE_WORKED_NONE = "none"
TASK_ROUTE_MAX_TRIED = 6
# The finer fields, each allowed only under the obstacle it explains (TASK_ROUTE_RULES).
TASK_ROUTE_DETAIL_FIELDS = ("verification_kind", "site_requires", "document_kind", "entry_point_kind")
TASK_ROUTE_RULES: dict[TaskObstacle, frozenset[str]] = {obstacle: frozenset() for obstacle in TaskObstacle}
TASK_ROUTE_RULES.update({
    TaskObstacle.verification_step: frozenset({"verification_kind"}),
    TaskObstacle.site_requires_info_from_person: frozenset({"site_requires"}),
    TaskObstacle.document_missing: frozenset({"document_kind"}),
    TaskObstacle.document_rejected: frozenset({"document_kind"}),
    TaskObstacle.substitute_document_needed: frozenset({"document_kind"}),
    TaskObstacle.wrong_entry_point: frozenset({"entry_point_kind"}),
})
# With no obstacle there is nothing to record unless the task got somewhere.
TASK_ROUTE_NO_OBSTACLE_OUTCOMES = frozenset({RouteOutcome.resolved, RouteOutcome.progressed})
# Values only a curated entry from the site's own documents carries; a curated entry carries all three.
TASK_ROUTE_CURATED_ONLY = (RouteSourceKind.curated_official, RouteBasis.official_doc, RouteOutcome.not_applicable)
# A value no report may send: the database check does not list it, so only the curated table in code can hold it.
TASK_ROUTE_CURATED_ONLY_ENTRY_POINT = EntryPointKind.seller_or_provider
# An obstacle no report may send, for the same reason (D127). A record with it always quotes the condition in condition_text:
# the value says that the page states who qualifies or what is covered, and the quotation is what the agent checks.
TASK_ROUTE_CURATED_ONLY_OBSTACLE = TaskObstacle.eligibility_conditions_stated
# Which sources may carry a link. A public report never does (a path can name a person or a case); curation and operators may.
TASK_ROUTE_URL_SOURCES = frozenset({RouteSourceKind.curated_official, RouteSourceKind.operator_panel})
# The one free-text value of a record: a short quotation of the condition a site's own page states, kept for curated entries only
# (a report has no such field). Never classified or interpreted here; whether it applies to a task is for the agent to judge.
TASK_ROUTE_CONDITION_TEXT_MAX = 200
# Reporting period: "quarter" (YYYY-Qn) or "month" (YYYY-MM). The server sets it; a coarser period makes a rare combination
# harder to tie to one person. Changing the unit is this one constant.
TASK_ROUTE_PERIOD_UNIT: Literal["quarter", "month"] = "quarter"
TASK_ROUTE_PERIOD_PATTERNS = {"quarter": r"^\d{4}-Q[1-4]$", "month": r"^\d{4}-(?:0[1-9]|1[0-2])$"}


def task_route_combination_problem(fields: dict) -> str | None:
    """The first rule of TASK_ROUTE_RULES and the outcome rules that a set of task-route fields breaks, or None.

    ``fields`` holds enum members (or their values) under the model's field names; absent or None means not given.
    Shared by the output and input models so both read one rule table."""
    def value(name):
        item = fields.get(name)
        return item.value if isinstance(item, Enum) else item

    obstacle = TaskObstacle(value("obstacle"))
    allowed = TASK_ROUTE_RULES[obstacle]
    for name in TASK_ROUTE_DETAIL_FIELDS:
        if value(name) is not None and name not in allowed:
            return f"invalid_task_route: {name} does not go with obstacle {obstacle.value}"
    outcome = RouteOutcome(value("outcome"))
    if obstacle == TaskObstacle.none and outcome not in TASK_ROUTE_NO_OBSTACLE_OUTCOMES:
        return "invalid_task_route: obstacle none goes with outcome resolved or progressed"
    tried = [item.value if isinstance(item, Enum) else item for item in (fields.get("routes_tried") or [])]
    if len(tried) > TASK_ROUTE_MAX_TRIED:
        return f"invalid_task_route: at most {TASK_ROUTE_MAX_TRIED} routes_tried"
    if len(set(tried)) != len(tried):
        return "invalid_task_route: routes_tried lists a route twice"
    worked = value("route_worked")
    if worked is not None and worked != ROUTE_WORKED_NONE:
        if worked not in tried:
            return "invalid_task_route: route_worked must be one of routes_tried or none"
        if outcome == RouteOutcome.gave_up:
            return "invalid_task_route: a route that worked does not go with outcome gave_up"
    return None


class _TaskRouteFields(StrictModel):
    site_host: str
    task_kind: TaskKind
    obstacle: TaskObstacle
    verification_kind: VerificationKind | None = Field(default=None, exclude_if=_omit_none)
    site_requires: SiteRequires | None = Field(default=None, exclude_if=_omit_none)
    document_kind: DocumentKind | None = Field(default=None, exclude_if=_omit_none)
    entry_point_kind: EntryPointKind | None = Field(default=None, exclude_if=_omit_none)
    routes_tried: list[RouteKind] = Field(
        default_factory=list, max_length=TASK_ROUTE_MAX_TRIED,
        description="The routes tried (a reported case) or, on a document record, the channels this record copied from the document, "
                    "which may not be every channel the document names. An empty list means this record lists none, not that the "
                    "document or the site has none.")
    route_worked: RouteKind | Literal["none"] | None = Field(default=None, exclude_if=_omit_none)
    basis: RouteBasis
    outcome: RouteOutcome = Field(
        description="How the case ended. not_applicable marks a record taken from a document: it names a route or a condition and "
                    "nobody's result is recorded. It does not mean the task does not apply.")
    duration: RouteDuration | None = Field(default=None, exclude_if=_omit_none)

    @field_validator("site_host")
    @classmethod
    def host_only(cls, value):
        return valid_host(value)

    @model_validator(mode="after")
    def combination(self):
        problem = task_route_combination_problem(dict(self))
        if problem:
            raise ValueError(problem)
        return self


class TaskRoute(_TaskRouteFields):
    """One task-route record as an answer carries it. ``sensitive`` and ``period`` are set by the server."""
    period: str = Field(description=f"The {TASK_ROUTE_PERIOD_UNIT} in which the record was read, reported or recorded. It is not a deadline and not a validity window.")
    source_kind: RouteSourceKind
    sensitive: bool
    official_url: str | None = Field(default=None, max_length=200, pattern=r"^https://[^\s?#]+$", exclude_if=_omit_none)
    source_url: str | None = Field(default=None, max_length=200, pattern=r"^https://[^\s?#]+$", exclude_if=_omit_none)
    verified_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", exclude_if=_omit_none)
    condition_text: str | None = Field(
        default=None, min_length=1, max_length=TASK_ROUTE_CONDITION_TEXT_MAX, exclude_if=_omit_none,
        description="A condition or caveat quoted as the site's own page words it (at most 200 characters). Not our judgement and "
                    "not classified: whether it applies to your task is for you to decide, with the page at source_url. It may state a "
                    "condition other than the obstacle.")

    @field_validator("condition_text")
    @classmethod
    def condition_text_shape(cls, value):
        problem = condition_text_problem(value) if value is not None else None
        if problem:
            raise ValueError(f"invalid_task_route: condition_text {problem}")
        return value

    @field_validator("verified_at")
    @classmethod
    def real_date(cls, value):
        if value is not None:
            try:
                date.fromisoformat(value)
            except ValueError:
                raise ValueError("invalid_task_route: verified_at is not a real date") from None
        return value

    @field_validator("period")
    @classmethod
    def period_shape(cls, value):
        if not re.fullmatch(TASK_ROUTE_PERIOD_PATTERNS[TASK_ROUTE_PERIOD_UNIT], value):
            raise ValueError("invalid_task_route: period is set by the server")
        return value

    @model_validator(mode="after")
    def source_rules(self):
        curated = self.source_kind == RouteSourceKind.curated_official
        if curated != (self.basis == RouteBasis.official_doc) or curated != (self.outcome == RouteOutcome.not_applicable):
            raise ValueError("invalid_task_route: basis official_doc and outcome not_applicable belong to curated_official only")
        if not curated and self.entry_point_kind == TASK_ROUTE_CURATED_ONLY_ENTRY_POINT:
            raise ValueError("invalid_task_route: this entry_point_kind belongs to curated_official only")
        if self.obstacle == TASK_ROUTE_CURATED_ONLY_OBSTACLE:
            if not curated:
                raise ValueError("invalid_task_route: this obstacle belongs to curated_official only")
            if self.condition_text is None:
                raise ValueError("invalid_task_route: this obstacle needs the condition_text that states the condition")
        if self.condition_text is not None and not curated:
            raise ValueError("invalid_task_route: condition_text belongs to curated_official only")
        if self.official_url is not None:
            if self.source_kind not in TASK_ROUTE_URL_SOURCES:
                raise ValueError("invalid_task_route: this source does not carry a link")
            problem = official_url_problem(self.site_host, self.official_url, curated=curated)
            if problem:
                raise ValueError(f"invalid_task_route: official_url {problem}")
        if self.source_url is not None:
            problem = official_url_problem(self.site_host, self.source_url, curated=curated)
            if problem:
                raise ValueError(f"invalid_task_route: source_url {problem}")
        if curated != (self.source_url is not None) or curated != (self.verified_at is not None):
            raise ValueError("invalid_task_route: source_url and verified_at belong to curated_official, which needs both")
        return self


class TaskRouteCounts(StrictModel):
    records: int = Field(ge=0)
    by_source_kind: dict[RouteSourceKind, int] = Field(default_factory=dict)


FIELD_NOTE_OUTCOME = ("not_applicable marks a record taken from a document: it names a route or a condition and nobody's result is "
                      "recorded. It does not mean the task does not apply.")
FIELD_NOTE_PERIOD = f"The {TASK_ROUTE_PERIOD_UNIT} in which the record was read, reported or recorded. It is not a deadline and not a validity window."
FIELD_NOTE_ROUTES_TRIED = ("For a document record, the channels this record copied from the document; they may not be every channel the "
                           "document names. An empty list means this record lists none, not that the document or the site has none.")


def call_timing_note() -> str:
    """The one sentence on when to call this lookup: ahn.mcp_task_routes.CALL_TIMING, the wording the tool description and the server
    instructions carry, so a caller that only reads the REST answer sees it too (D128). Imported here, not at the top: that module
    imports this one."""
    from .mcp_task_routes import CALL_TIMING

    return CALL_TIMING


def field_notes(shown: list[TaskRoute]) -> dict[str, str]:
    """The fixed sentences that say what outcome, period and routes_tried mean for the records shown, and, always, when to call
    (``call_timing``, D128). A meaning key appears only when a shown record makes it matter: outcome for a document record
    (not_applicable), routes_tried for a document record, period for any record. With no record only ``call_timing`` is left.
    The words are fixed and say nothing about a site."""
    notes: dict[str, str] = {}
    if not shown:
        return {"call_timing": call_timing_note()}
    if any(record.outcome == RouteOutcome.not_applicable for record in shown):
        notes["outcome"] = FIELD_NOTE_OUTCOME
    notes["period"] = FIELD_NOTE_PERIOD
    if any(record.source_kind == RouteSourceKind.curated_official for record in shown):
        notes["routes_tried"] = FIELD_NOTE_ROUTES_TRIED
    notes["call_timing"] = call_timing_note()
    return notes


class TaskRoutesResponse(StrictModel):
    field_notes: dict[str, str] | None = Field(
        default=None, exclude_if=_omit_none,
        description="Fixed sentences on what outcome, period and routes_tried mean in these records (those keys are left out when there is no record), "
                    "and call_timing, when to call this lookup (always present).")
    site_host: str
    summary_line: str = Field(min_length=1, max_length=200, pattern=r"^[ -~]+$")
    routes: list[TaskRoute] = Field(default_factory=list, max_length=20)
    counts: TaskRouteCounts
    notice: Literal["case_record_not_advice"] | None = Field(default=None, exclude_if=_omit_none)
    official_help_url: str | None = Field(
        default=None, exclude_if=_omit_none, pattern=r"^https://[^\s?#]+$", max_length=200,
        description="Only when routes is empty and the site's own help or customer service page is curated: that page, as a place to "
                    "look yourself. A link to read, not a route and not a statement that the site has none.")


class TaskRouteIn(_TaskRouteFields):
    """What a public report may send. There is no official_url, source_url, verified_at, period, source_kind or sensitive
    field: the server sets the last three, and a public report carries no link. Sending any of them is rejected (extra=forbid)."""
    idempotency_key: UUID = Field(default_factory=uuid4)

    @field_validator("site_host", mode="before")
    @classmethod
    def typed_host(cls, value):
        # The reading parse_query gives a lookup (case, a pasted scheme, a trailing slash or dot), before the hostname check.
        return normalize_host_input(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def public_values(self):
        if self.basis == RouteBasis.official_doc or self.outcome == RouteOutcome.not_applicable:
            raise ValueError("invalid_task_route: basis official_doc and outcome not_applicable belong to curated entries")
        if self.entry_point_kind == TASK_ROUTE_CURATED_ONLY_ENTRY_POINT:
            raise ValueError("invalid_task_route: this entry_point_kind belongs to curated entries")
        if self.obstacle == TASK_ROUTE_CURATED_ONLY_OBSTACLE:
            raise ValueError("invalid_task_route: this obstacle belongs to curated entries")
        return self


# --- Pure helpers ---------------------------------------------------------------------------------------------------------
TASK_ROUTE_URL_MAX = 200
# Plain https URL with a path of unreserved characters and percent escapes only: no query, fragment, userinfo or port.
_URL_SHAPE = re.compile(r"^https://[a-z0-9.-]+(?:/[A-Za-z0-9._~/%+=-]*)?$")
_LONG_NUMBER = re.compile(r"\d{6,}")  # an account, case or claim number
_UUID_LIKE = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.IGNORECASE)

# Display rule (never a verdict): a record is marked "case record, not advice" when one of these holds. Each list is the one
# place to change it.
SENSITIVE_TASK_KINDS = frozenset({
    TaskKind.accommodation_request, TaskKind.prescription_refill, TaskKind.insurance_claim,
    TaskKind.government_benefit_application, TaskKind.warranty_dispute, TaskKind.billing_dispute, TaskKind.complaint_filing,
    TaskKind.records_request,
})
SENSITIVE_SITE_REQUIRES = frozenset({SiteRequires.referral_or_prescription})
SENSITIVE_DOCUMENT_KINDS = frozenset({DocumentKind.professional_letter, DocumentKind.sworn_statement})
# A sign-in to a role-specific portal (a school or employer portal, for one) may be about a minor or an employee.
SENSITIVE_COMBINATIONS = frozenset({(TaskKind.portal_login, EntryPointKind.role_specific_portal)})

ROUTE_LINE_MAX = 240
ROUTE_LINE_NOTICE = "Case record, not advice."


# Shared hosting suffixes: a.github.io and b.github.io are different owners. scripts/verify_official_paths.py uses this
# same set and registrable(), so curation checks and these rules agree on who owns a domain.
PRIVATE_SUFFIXES = frozenset({
    "github.io", "gitbook.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev",
    "gitlab.io", "herokuapp.com", "blogspot.com", "web.app", "firebaseapp.com", "workers.dev", "fly.dev", "onrender.com",
    "cloudfront.net", "azurewebsites.net", "azurestaticapps.net", "appspot.com", "glitch.me", "ngrok-free.app",
    "trycloudflare.com",
    "myshopify.com", "wixsite.com", "run.app", "cloudfunctions.net", "azureedge.net", "cloudapp.net", "r2.dev", "deno.dev",
    "replit.app", "webflow.io", "notion.site", "ngrok.io", "ngrok.app", "surge.sh", "ddns.net",
})

# Two-label public suffixes that ahn.service.PUBLIC_SUFFIXES_2 does not list (that set serves the lookup verdict and stays as it
# is). Here they only decide who owns a name: a report under one of them is refused (route_host), and no stored row under one is
# read into a site's answer (registrable, key_candidates). A fixed list in code: no public suffix list is downloaded or bundled.
_US_STATES = ("ak al ar az ca co ct dc de fl ga hi ia id il in ks ky la ma md me mi mn mo ms mt nc nd ne nh nj nm nv ny oh ok or pa "
              "ri sc sd tn tx ut va vt wa wi wv wy as gu mp pr vi").split()
_CANADIAN_PROVINCES = "ab bc mb nb nl ns nt nu on pe qc sk yk".split()
EXTRA_PUBLIC_SUFFIXES_2 = frozenset({
    # United Kingdom (co, org, ac, gov, me and police are in PUBLIC_SUFFIXES_2)
    "sch.uk", "ltd.uk", "plc.uk", "net.uk",
    # United States (<state>.us) and Canada (<province>.ca)
    *(f"{label}.us" for label in _US_STATES), *(f"{label}.ca" for label in _CANADIAN_PROVINCES),
    # Singapore, Germany, France and other countries with a common second level
    "gov.sg", "edu.sg", "net.sg", "org.sg", "per.sg", "com.de", "com.fr", "asso.fr", "gouv.fr", "nom.fr",
    "asn.au", "id.au", "ac.nz", "govt.nz", "net.nz", "school.nz", "geek.nz",
    "ad.jp", "ed.jp", "gr.jp", "lg.jp", "es.kr", "hs.kr", "ms.kr", "sc.kr", "re.kr", "pe.kr", "kg.kr",
    "edu.cn", "ac.cn", "org.tw", "net.tw", "edu.tw", "gov.tw", "idv.tw", "org.hk", "net.hk", "edu.hk", "gov.hk", "idv.hk",
    "ac.in", "edu.in", "res.in", "firm.in", "gen.in", "ind.in", "nic.in", "net.br", "edu.br", "mil.br",
    "org.mx", "gob.mx", "edu.mx", "net.mx", "gob.ar", "org.ar", "net.ar", "edu.ar", "gov.ar", "org.za", "gov.za", "ac.za", "net.za",
    "org.il", "ac.il", "gov.il", "net.il", "muni.il", "k12.il", "org.tr", "net.tr", "gov.tr", "edu.tr", "k12.tr",
    "or.id", "go.id", "ac.id", "web.id", "sch.id", "org.my", "gov.my", "edu.my", "net.my", "or.th", "go.th", "ac.th", "in.th",
    "org.vn", "gov.vn", "edu.vn", "net.vn", "org.ph", "gov.ph", "edu.ph", "net.ph", "org.pl", "net.pl", "gov.pl", "edu.pl",
    "org.es", "nom.es", "gob.es", "edu.es", "org.pt", "gov.pt", "edu.pt", "com.ru", "org.ru", "net.ru",
    "com.ua", "org.ua", "gov.ua", "edu.ua", "net.ua", "com.eg", "com.sa", "com.ng", "com.pk", "com.bd", "com.co", "com.pe", "com.uy",
    "com.cy", "co.ke", "co.ug", "co.tz",
    # Names under which many unrelated parties register (CentralNic and similar)
    "co.com", "uk.com", "us.com", "eu.com", "br.com", "cn.com", "de.com", "ru.com", "sa.com", "za.com", "jpn.com", "gb.net", "uk.net",
    "eu.org", "us.org",
    # Austria, Italy, Colombia and Canada's federal level
    "co.at", "or.at", "gv.at", "gov.it", "gov.co", "org.co", "edu.co", "gc.ca",
})


def in_suffixes(name: str) -> bool:
    """True when ``name`` is a two-label public suffix (co.uk, sch.uk, ca.us) or a shared hosting suffix (github.io, herokuapp.com)."""
    from .service import PUBLIC_SUFFIXES_2

    return name in PUBLIC_SUFFIXES_2 or name in EXTRA_PUBLIC_SUFFIXES_2 or name in PRIVATE_SUFFIXES


def registrable(host: str) -> str:
    """The registrable domain of a host: the last two labels, or three under a two-label public suffix (co.uk) or a shared
    hosting suffix (github.io)."""
    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and in_suffixes(".".join(labels[-2:])):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def route_host(site_host: str) -> str | None:
    """The host a task route is kept under: the apex that policy_host() answers for (under a government zone, the exact host name),
    or None for an account or tenant subdomain (which can name a district, a school or a small landlord) that aggregate_eligible_host() keeps out."""
    from .service import aggregate_eligible_host, policy_host

    host = policy_host(valid_host(site_host))
    listed = government_site(host)
    if listed is not None:  # a government site kept under its own full name, never under a parent or a sibling
        return listed
    if suffix_name(host):  # co.uk, github.io or www.co.uk names no one site
        return None
    return host if aggregate_eligible_host(host) else None


# Government zones: suffixes under which only government bodies may register a name. Under one, the exact host name is the site
# key: irs.gov, dmv.ca.gov and my.uscis.gov are three sites, nothing is kept under or answered from a parent (nj.gov) or a
# sibling, and only one leading "www." is the same site. A fixed list in code, no public suffix list is read. Left out on purpose:
# school districts (k12.<state>.us) and the names under ci. and co.<place>.<state>.us, whose registrants are not all governments.
GOVERNMENT_ZONES = frozenset({"gov", "mil", "fed.us", "gov.uk", "go.kr", "gc.ca", "gov.au", "govt.nz"})
# state.<state>.us is a zone whose own name is a site too (state.nj.us), unlike the suffixes above.
GOVERNMENT_STATE_ZONES = frozenset(f"state.{label}.us" for label in _US_STATES)
GOVERNMENT_MAX_BELOW = 3  # labels allowed below the registered name (below irs.gov, or below state.nj.us itself)

# Government sites that no suffix identifies, and exceptions to the zone rule. A listed name is kept under exactly that name, as if
# it were an apex, with its "www." alias as the same site (except for a name that itself starts with "www."). www.gov.uk is the
# one name that starts with "www." and is a site: the United Kingdom's national site, while gov.uk itself is only a suffix.
GOVERNMENT_SITE_HOSTS = frozenset({"unclaimedproperty.nj.gov", "www.gov.uk"})


def _zone_below(name: str) -> int | None:
    """Labels below the registered name of ``name`` inside a government zone (0 for irs.gov or state.nj.us), or None when ``name`` is
    not in a zone or is a bare zone suffix (gov.uk, fed.us). A name that is deeper than allowed is still in the zone."""
    labels = name.split(".")
    for size in (1, 2, 3):
        if len(labels) < size:
            break
        suffix = ".".join(labels[-size:])
        if suffix in GOVERNMENT_STATE_ZONES:
            return len(labels) - size
        if suffix in GOVERNMENT_ZONES:
            return len(labels) - size - 1 if len(labels) > size else None
    return None


def government_zone(host: str) -> bool:
    """True when ``host`` (with or without "www.") is inside a government zone, whatever its depth."""
    name = host[4:] if host.startswith("www.") else host
    return _zone_below(name) is not None


def government_site(host: str) -> str | None:
    """The government site name that ``host`` is: a name in a government zone (GOVERNMENT_ZONES, GOVERNMENT_STATE_ZONES) at most
    GOVERNMENT_MAX_BELOW labels below its registered name and without a punycode label, or a name in GOVERNMENT_SITE_HOSTS, in
    either case with one leading ``www.`` removed (except for a listed name that starts with www.); else None. Only that one alias:
    no parent, no sibling and no other subdomain is the site."""
    if host in GOVERNMENT_SITE_HOSTS:
        return host
    name = host[4:] if host.startswith("www.") else host
    if name != host and name.startswith("www."):  # "www.www." is never the alias of a site
        return None
    if name in GOVERNMENT_SITE_HOSTS:
        return name
    below = _zone_below(name)
    if below is None or below > GOVERNMENT_MAX_BELOW or any(label.startswith("xn--") for label in name.split(".")):
        return None
    return name


def suffix_name(host: str) -> bool:
    """True for a two-label public suffix (co.uk, gov.uk, sch.uk), a shared hosting suffix (github.io) or "www." in front of one
    (www.co.uk). Such a name is not one site: nothing is kept under it (route_host) and stored rows under it are never read."""
    labels = host.split(".")
    return in_suffixes(host) or (labels[0] == "www" and in_suffixes(".".join(labels[1:])))


# The one exception to the link rule (D120): a curated entry may carry a reviewed page whose path has a long identifier (a retail
# help article or category, for one). Only the exact strings in ahn.task_routes_data.CURATED_URL_EXCEPTIONS, and only for a
# curated_official entry. A query is never excepted. The exception lifts the six-digit limit only for a path of the shape below.
_EXCEPTION_MAX_DIGITS = 15  # digits in the whole path
_LONG_RUN = re.compile(r"\d{6,}")
_EXCEPTION_CASE_WORDS = ("case", "claim", "order", "ticket", "invoice", "account")


def _exception_path_problem(path: str) -> str | None:
    """The shape a listed path must have; ``path`` is already percent-decoded."""
    if _UUID_LIKE.search(path):
        return "path has a UUID-shaped segment"
    if len(re.findall(r"\d", path)) > _EXCEPTION_MAX_DIGITS:
        return f"path has more than {_EXCEPTION_MAX_DIGITS} digits"
    runs = _LONG_RUN.findall(path)
    if len(runs) > 1:
        return "path has more than one run of six or more digits"
    # Compatibility forms (full-width letters, ligatures) read as the plain word they look like, so "ｏｒｄｅｒ" is "order".
    folded = unicodedata.normalize("NFKC", path).lower()
    if runs and any(word in folded for word in _EXCEPTION_CASE_WORDS):
        return "path has a case, claim, order, ticket, invoice or account word next to a long number"
    return None


# Listed (site key, URL host) pairs: a curated record keyed by the first host may carry a page on the second host although it is
# on another registrable domain. The URL host must match exactly (lowercase); the key is the record's own site key. Only curated
# records use it (official_url_problem curated=True); operator and public entries never do. One line per pair, with the reason.
CURATED_CROSS_DOMAIN: frozenset[tuple[str, str]] = frozenset({
    # Thirty Madison, Inc. operates Keeps and Nurx (the terms header reads "Thirty Madison, Inc. d/b/a Keeps, Cove, and Nurx"),
    # and the keeps.com and nurx.com home pages link its terms at this host, which answers /legals/terms-and-conditions.
    ("keeps.com", "patient.thirtymadison.com"),
    ("nurx.com", "patient.thirtymadison.com"),
})


def _listed_exceptions():
    from .task_routes_data import CURATED_URL_EXCEPTIONS

    return CURATED_URL_EXCEPTIONS


def condition_text_problem(value) -> str | None:
    """Why ``value`` may not be a record's condition_text, or None: a string of 1 to 200 characters with no space at either end
    and no line break or other control character inside (a quotation, not a block of text)."""
    if not isinstance(value, str):
        return "must be a string"
    if not value or value != value.strip():
        return "must be 1 to %d characters with no space at either end" % TASK_ROUTE_CONDITION_TEXT_MAX
    if len(value) > TASK_ROUTE_CONDITION_TEXT_MAX:
        return "is longer than %d characters" % TASK_ROUTE_CONDITION_TEXT_MAX
    if any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in value):
        return "must be one line without control characters"
    return None


def official_url_problem(site_host: str, url, *, curated: bool = False) -> str | None:
    """Why ``url`` may not be carried as a task route's official_url for ``site_host``, or None when it may.

    Only curated and operator entries carry a link (a public report never does). The link must be a plain https page on
    the site's own registrable domain, at most 200 characters, with no query, fragment, userinfo or port, and with no path
    segment that looks like it names a person or a case: six or more digits in a row, an e-mail address or a UUID.

    ``curated=True`` is for a curated_official entry only: a URL that equals, character for character, a key of
    CURATED_URL_EXCEPTIONS may have a longer number in its path (D120), if the decoded path has at most 15 digits in all, at
    most one run of six or more digits, no UUID-shaped segment, and no case, claim, order, ticket, invoice or account word
    together with a run of six or more digits. Every other rule still applies, and so does every rule to any other URL.

    ``curated=True`` also lets a (site key, URL host) pair listed in CURATED_CROSS_DOMAIN point at the operator's document on
    another registrable domain. The pair is exact, and every other rule still applies to that URL."""
    return _url_problem(site_host, url, _listed_exceptions() if curated else None, curated)


def _key_of(site_host) -> str | None:
    try:
        return route_host(site_host)
    except (ValueError, TypeError, AttributeError):
        return None


def _url_problem(site_host: str, url, exceptions, cross_domain: bool = False) -> str | None:
    if not isinstance(url, str):
        return "not a string"
    if len(url) > TASK_ROUTE_URL_MAX:
        return f"longer than {TASK_ROUTE_URL_MAX} characters"
    excepted = exceptions is not None and url in exceptions
    parts = urlsplit(url)
    if parts.scheme != "https" or not url.startswith("https://"):
        return "not an https URL"
    if parts.query or "?" in url:
        return "has a query"
    if parts.fragment or "#" in url:
        return "has a fragment"
    if "@" in parts.netloc or parts.username or parts.password:
        return "has userinfo"
    try:
        port = parts.port
    except ValueError:
        return "has an invalid port"
    if port is not None or ":" in parts.netloc:
        return "has a port"
    path = parts.path
    for _ in range(3):  # decode until stable, so %40 and %2540 are caught too
        path = unquote(path)
    segments = path.replace("\\", "/").split("/")
    if any(segment in (".", "..") for segment in segments):
        return "path has a . or .. segment"
    if "@" in path:
        return "path looks like it carries an e-mail address"
    if excepted:
        problem = _exception_path_problem(path)
        if problem:
            return problem
    else:
        if _UUID_LIKE.search(path):
            return "path has a UUID-shaped segment"
        if _LONG_NUMBER.search(path):
            return "path has six or more digits in a row"
    if not _URL_SHAPE.fullmatch(url):
        return "not a plain https URL"
    try:
        if valid_host(parts.hostname or "") != parts.netloc:
            return "not a public hostname"
    except ValueError:
        return "not a public hostname"
    if registrable(parts.hostname) != registrable(site_host):
        if cross_domain and (_key_of(site_host), parts.hostname) in CURATED_CROSS_DOMAIN:
            return None
        return "not on the site's own registrable domain"
    return None


def curated_exception_problems(exceptions) -> list[str]:
    """Rule violations in a CURATED_URL_EXCEPTIONS table (D120): each key must pass the link rule once the exception is
    applied to it, and each value, the public index page that links the key, must pass the plain link rule with no
    exception, on the same registrable domain, and must be a different page."""
    problems: list[str] = []
    if not isinstance(exceptions, dict):
        return ["CURATED_URL_EXCEPTIONS must be a dict of exact URL -> index page URL"]
    for url, index in exceptions.items():
        if not isinstance(url, str) or not isinstance(index, str):
            problems.append(f"{url!r}: the key and its index page must both be strings")
            continue
        host = (urlsplit(url).hostname or "").lower()
        problem = _url_problem(host, url, {url: index})
        if problem:
            problems.append(f"{url}: {problem}")
        problem = _url_problem(host, index, None)
        if problem:
            problems.append(f"{url}: index page {index}: {problem}")
        elif index == url:
            problems.append(f"{url}: the index page must be a different page from the URL it links")
    return problems


def _value(item):
    return item.value if isinstance(item, Enum) else item


def is_sensitive(task_kind, *, site_requires=None, document_kind=None, entry_point_kind=None,
                 government_host: bool = False) -> bool:
    """True when the record should be shown as a case record, not a general route. Display only.

    ``government_host`` is whether host_facts (a government domain list or a government-only suffix) applies to the site;
    the caller reads it, so this stays free of database access."""
    task = TaskKind(_value(task_kind))
    if government_host or task in SENSITIVE_TASK_KINDS:
        return True
    if site_requires is not None and SiteRequires(_value(site_requires)) in SENSITIVE_SITE_REQUIRES:
        return True
    if document_kind is not None and DocumentKind(_value(document_kind)) in SENSITIVE_DOCUMENT_KINDS:
        return True
    if entry_point_kind is not None and (task, EntryPointKind(_value(entry_point_kind))) in SENSITIVE_COMBINATIONS:
        return True
    return False


def period_for(day: date | datetime, unit: str | None = None) -> str:
    """The reporting period of a day: YYYY-Qn by default (TASK_ROUTE_PERIOD_UNIT), or YYYY-MM."""
    unit = unit or TASK_ROUTE_PERIOD_UNIT
    if unit == "quarter":
        return f"{day.year:04d}-Q{(day.month - 1) // 3 + 1}"
    if unit == "month":
        return f"{day.year:04d}-{day.month:02d}"
    raise ValueError(f"unknown period unit {unit!r}")


def is_current(route: dict, today: date | None = None) -> bool:
    """A curated entry (one with verified_at) answers while verified_at is at most TASK_ROUTE_MAX_AGE_DAYS old and not in
    the future. A record without verified_at follows the storage retention rules instead and is current here."""
    from .task_routes_data import TASK_ROUTE_MAX_AGE_DAYS

    verified = route.get("verified_at")
    if verified is None:
        return True
    try:
        verified_on = date.fromisoformat(verified)
    except (TypeError, ValueError):
        return False  # not a real date: never answered with (TaskRoute and the curation check refuse it too)
    today = today or datetime.now(timezone.utc).date()
    age = (today - verified_on).days
    return 0 <= age <= TASK_ROUTE_MAX_AGE_DAYS


def _words(item) -> str:
    return str(_value(item)).replace("_", " ")


def route_line(route) -> str:
    """One fixed English sentence for a record, built only from its closed values and the period: no host, URL or text.

    Example: "cancel subscription: wrong channel; tried 2 routes, worked: phone; outcome not applicable; curated official,
    2026-Q4." A sensitive record ends with "Case record, not advice." The sentence states what was recorded and gives no
    instruction."""
    get = route.get if isinstance(route, dict) else (lambda name: getattr(route, name, None))
    obstacle = _words(get("obstacle"))
    details = [_words(get(name)) for name in ("verification_kind", "site_requires", "document_kind", "entry_point_kind")
               if get(name) is not None]
    if details and TaskObstacle(_value(get("obstacle"))) != TaskObstacle.none:
        obstacle = f"{obstacle} ({details[0]})"
    tried = len(get("routes_tried") or [])
    parts = [f"{_words(get('task_kind'))}: {obstacle}"]
    if tried:
        trail = f"tried {tried} route" + ("s" if tried != 1 else "")
        if get("route_worked") is not None:
            trail += f", worked: {_words(get('route_worked'))}"
        parts.append(trail)
    parts.append(f"outcome {_words(get('outcome'))}")
    parts.append(f"{_words(get('source_kind') or RouteSourceKind.external_agent)}, {get('period')}")
    line = "; ".join(parts) + "."
    if get("sensitive"):
        line += " " + ROUTE_LINE_NOTICE
    return line


# --- Read: the answer for a site (PR-B) -------------------------------------------------------------------------------------
TASK_ROUTES_MAX = 20  # records in one answer, newest period first
SOURCE_LABELS = {
    RouteSourceKind.curated_official: "from the site's own documents",
    RouteSourceKind.operator_panel: "operator-entered",
    RouteSourceKind.external_agent: "reported by agents",
    RouteSourceKind.curated_community: "from community sources",
}
SUMMARY_NO_RECORDS = "No task route records for this site. A missing record does not mean there is no route."
SUMMARY_NO_RECORDS_FOR_KIND = "No task route records for this site and task kind. A missing record does not mean there is no route."
NEXT_STEP_NO_RECORDS = "Check the site's official help or customer service page directly."
SUMMARY_MAX = 200


def government_suffix(host: str) -> bool:
    """True when the host is in a government zone or listed as a government site, or its suffix is reserved for government bodies by
    the registry's rules (a reading of the code tables, no database). Display only."""
    from .gov_registry import _suffix_rule_for

    return government_site(host) is not None or _suffix_rule_for(host) is not None


def server_route(site_host: str, entry: dict) -> TaskRoute:
    """A table entry as an answer carries it, with every server-decided field computed here.

    ``sensitive``, ``source_kind``, ``basis``, ``outcome`` and ``period`` are never taken from the entry: the curated table is
    the only source today, so its records are curated_official / official_doc / not_applicable, the period follows
    ``verified_at``, and ``sensitive`` is computed by is_sensitive() whatever the entry says. Raises ValueError when the entry
    is not a valid record."""
    fields = {key: value for key, value in entry.items()
              if key not in ("sensitive", "source_kind", "basis", "outcome", "period", "site_host")}
    verified = fields.get("verified_at")
    try:
        period = period_for(date.fromisoformat(verified))
    except (TypeError, ValueError):
        raise ValueError("invalid_task_route: verified_at is not a real date") from None
    sensitive = is_sensitive(fields.get("task_kind"), site_requires=fields.get("site_requires"),
                             document_kind=fields.get("document_kind"), entry_point_kind=fields.get("entry_point_kind"),
                             government_host=government_suffix(site_host))
    return TaskRoute(site_host=site_host, source_kind=RouteSourceKind.curated_official, basis=RouteBasis.official_doc,
                     outcome=RouteOutcome.not_applicable, period=period, sensitive=sensitive, **fields)


def official_help_url(site_host: str, today: date | None = None) -> str | None:
    """The first curated customer help page of the site's own key (D128), or None: a tenant subdomain, a site with no current
    consumer entry, or one with no help page (an app or an entry link is not a help page). Read from the in-code table; no network."""
    from .official_paths import CONSUMER_PATHS, consumer_is_current

    try:
        key = route_host(site_host)
    except ValueError:
        return None
    consumer = CONSUMER_PATHS.get(key) if key is not None else None
    if consumer is None or not consumer_is_current(consumer, today):
        return None
    for item in consumer.get("paths") or []:
        url = item.get("url")
        if item.get("kind") == "customer_help" and isinstance(url, str) and _URL_SHAPE.fullmatch(url) and len(url) <= TASK_ROUTE_URL_MAX:
            return url
    return None


def kind_counts_text(by_kind: dict, room: int) -> str:
    """" By task: a 3, b 1." with the most records first (then the name), as many as fit in ``room`` characters; the rest is
    " +N more". Empty when not even the first fits. The words are task kind values, never a site's."""
    items = sorted(by_kind.items(), key=lambda item: (-item[1], item[0]))
    for shown in range(len(items), 0, -1):
        text = " By task: " + ", ".join(f"{name} {count}" for name, count in items[:shown])
        if shown < len(items):
            text += f", +{len(items) - shown} more"
        text += "."
        if len(text) <= room:
            return text
    return ""


def has_condition_text(record) -> bool:
    """Whether a record quotes the site's condition (a non-empty ``condition_text``): the one test behind the sort order and the
    summary count (D130)."""
    return bool(record.condition_text)


def condition_text_count(records) -> int:
    return sum(1 for record in records if has_condition_text(record))


def summary_line(total: int, by_source: dict, task_kind: TaskKind | None, notice: bool, by_kind: dict | None = None,
                 with_condition: int = 0) -> str:
    """The one sentence of an answer, built only from fixed words, the counts and the task kind's name. ``by_kind`` (task kind value
    to count, over all matching records) adds a short count per task kind when the answer is not filtered to one kind (D128).
    ``with_condition`` (how many of all matching records quote a condition, ``condition_text_count``) adds "N with a quoted condition."
    after the source counts; with none the clause is left out and the sentence is what it was (D130)."""
    if total == 0:
        return (SUMMARY_NO_RECORDS_FOR_KIND if task_kind is not None else SUMMARY_NO_RECORDS) + " " + NEXT_STEP_NO_RECORDS
    parts = ", ".join(f"{count} {SOURCE_LABELS[kind]}" for kind, count in by_source.items() if count)
    line = f"{total} task route record{'s' if total != 1 else ''}" + (f" for {_words(task_kind)}" if task_kind is not None else "")
    line += f": {parts}."
    if with_condition:
        line += f" {with_condition} with a quoted condition."
    tail = ""
    if total > TASK_ROUTES_MAX:
        tail += f" Showing the {TASK_ROUTES_MAX} newest."
    tail += " Records, not guarantees."
    if notice:
        tail += " " + ROUTE_LINE_NOTICE
    if by_kind and task_kind is None and total > 1:
        line += kind_counts_text(by_kind, SUMMARY_MAX - len(line) - len(tail))
    line += tail
    if len(line) > SUMMARY_MAX:  # cannot happen with the fixed labels at realistic counts; the guard keeps the answer valid
        line = f"{total} task route records. Records, not guarantees." + (" " + ROUTE_LINE_NOTICE if notice else "")
    return line


class TaskRouteInputError(ValueError):
    """A query that cannot be answered. ``code`` is the whole message: it never carries the input."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_query(site_host, task_kind=None) -> tuple[str, TaskKind | None]:
    """The one reading of a query for REST and MCP: the host is normalized (case, a pasted scheme, a trailing slash or dot) and must
    be a public hostname; task_kind is None (no filter) or exactly one TaskKind value, so an empty string is refused."""
    host = normalize_host_input(site_host if isinstance(site_host, str) else "")
    try:
        valid_host(host)
    except ValueError:
        raise TaskRouteInputError("invalid_site_host") from None
    if task_kind is None:
        return host, None
    value = _value(task_kind)
    if not isinstance(value, str) or value not in TaskKind._value2member_map_:
        raise TaskRouteInputError("invalid_task_kind")
    return host, TaskKind(value)


SITE_INPUT_MAX = 2000
_SITE_PORT = re.compile(r":\d{1,5}$")
_SITE_LABEL = re.compile(r"^[a-z0-9-]{1,63}$")


class SiteInput(NamedTuple):
    """What the search box on the human pages made of what was typed. ``kind`` is "empty" (no site condition), "host" (a public
    hostname, in ``value``), "label" (one name without a dot, in ``value``) or "invalid" (``value`` is empty: the input is never
    carried on)."""

    kind: str
    value: str = ""


def site_from_input(raw) -> SiteInput:
    """The reading of the site box on the task route pages (a screen-only rule: parse_query(), and so REST and MCP, is unchanged).
    A pasted address is cut down to its hostname: an http(s) scheme, everything from the first "/", "?" or "#", and a port are
    dropped, and what was dropped is not used anywhere. Another scheme, an account part ("@"), a space or a control character, a
    name that is not a public hostname and more than SITE_INPUT_MAX characters are "invalid"."""
    if not isinstance(raw, str):
        return SiteInput("empty")
    if len(raw) > SITE_INPUT_MAX:
        return SiteInput("invalid")
    text = raw.strip()
    if not text:
        return SiteInput("empty")
    lowered = text.lower()
    for scheme in ("https://", "http://"):
        if lowered.startswith(scheme):
            text = text[len(scheme):]
            break
    else:
        if "://" in text:  # another scheme (ftp://, file://)
            return SiteInput("invalid")
    for mark in ("/", "?", "#"):
        text = text.split(mark, 1)[0]
    if "@" in text or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text):
        return SiteInput("invalid")
    text = _SITE_PORT.sub("", text).rstrip(".").lower()
    if not text:
        return SiteInput("invalid")
    try:
        return SiteInput("host", valid_host(text))
    except ValueError:
        pass
    if _SITE_LABEL.fullmatch(text) and text not in RESERVED_TLDS and not text.isdigit() and not text.startswith("-")             and not text.endswith("-"):
        return SiteInput("label", text)
    return SiteInput("invalid")


# Labels that name a service of the same site, not a tenant: support.example.com answers from example.com. Any other subdomain of a
# name that aggregate_eligible_host() keeps out (springfield.classlink.com) can name a district or a small business: empty answer.
GENERIC_SUBDOMAIN_LABELS = frozenset({"www", "support", "help", "care", "shop", "store", "m", "mobile", "en", "us"})


def tenant_host(host: str) -> bool:
    """True when ``host`` is an account or tenant subdomain, by the rule behind route_host() (aggregate_eligible_host, and
    government_site for a government zone, where each exact name is a site)."""
    from .service import aggregate_eligible_host, policy_host

    host = policy_host(host)
    if aggregate_eligible_host(host) or government_site(host) is not None:
        return False
    if government_zone(host):  # in a government zone but too deep or punycode: not a site, and it never climbs to a parent
        return True
    labels = host.split(".")
    apex = registrable(host)
    below = labels[:len(labels) - len(apex.split("."))]
    # A generic label climbs to its apex only when that apex is itself a site (so www.x and x answer alike: neither when x is not).
    return not (below and all(label in GENERIC_SUBDOMAIN_LABELS for label in below) and aggregate_eligible_host(apex))


def key_candidates(host: str) -> list[str]:
    """The names that may answer for ``host``, nearest first: the host itself, then each parent down to its registrable domain
    (registrable), never above it: bbc.co.uk never climbs to co.uk, a.github.io never to github.io. A government site (a name in a
    government zone, or listed in GOVERNMENT_SITE_HOSTS, with its www. alias) answers for its own name alone."""
    listed = government_site(host)
    if listed is not None:
        return [listed]
    labels = host.split(".")
    floor = len(registrable(host).split("."))
    return [".".join(labels[index:]) for index in range(max(1, len(labels) - floor + 1))]


def table_key(host: str, stored_keys=frozenset()) -> str | None:
    """The key that answers for ``host`` (a normalized public hostname): the host itself or its nearest parent that has a curated
    table entry or, with ``stored_keys``, stored records (D118), never a bare top-level label, and None for a tenant subdomain or a
    site with neither. lookup(), GET /v1/task-routes, find_task_route and the human pages all use it, so they agree on the site."""
    from . import task_routes_data

    if tenant_host(host):  # a tenant subdomain gets the empty answer
        return None
    for candidate in key_candidates(host):  # exact host first, then each parent, as official_paths() does
        if candidate in task_routes_data.TASK_ROUTES_CURATED or candidate in stored_keys:
            return candidate
    return None


def stored_route(row: dict) -> TaskRoute:
    """A stored record (a row of task_routes, D118) as an answer carries it. ``sensitive`` is computed again here by is_sensitive(), so a
    change of the rule applies to stored rows too; the other server-decided values (period, source_kind) are the ones the server
    stored. Raises ValueError when the row is not a valid record."""
    fields = {name: row.get(name) for name in ("task_kind", "obstacle", "verification_kind", "site_requires", "document_kind",
                                               "entry_point_kind", "route_worked", "basis", "outcome", "duration", "period",
                                               "source_kind", "official_url")}
    fields = {name: value for name, value in fields.items() if value is not None}
    sensitive = is_sensitive(row.get("task_kind"), site_requires=row.get("site_requires"), document_kind=row.get("document_kind"),
                             entry_point_kind=row.get("entry_point_kind"), government_host=government_suffix(row["site_host"]))
    return TaskRoute(site_host=row["site_host"], routes_tried=list(row.get("routes_tried") or []), sensitive=sensitive, **fields)


def site_records(host: str, settings=None) -> tuple[str | None, list[dict]]:
    """The key that answers for ``host`` and the stored rows under it. Stored rows are read only while the write switch is served
    (settings.task_route_reports_served); otherwise, with no settings, or when the database cannot be read (a warning is logged and
    the answer is the curated table alone), there are none."""
    if tenant_host(host):
        return None, []
    rows: list[dict] = []
    if settings is not None:
        from .settings import task_route_reports_served

        if task_route_reports_served(settings):
            from .task_route_intake import published_rows

            try:  # a row under a suffix name (co.uk) is never read, even if one was stored
                rows = published_rows(settings, [name for name in key_candidates(host) if not suffix_name(name)])
            except Exception:  # the database is down or not migrated yet: answer from the curated table alone
                LOG.warning("task_route_db_unavailable")
                rows = []
    key = table_key(host, frozenset(row["site_host"] for row in rows))
    return key, [row for row in rows if row["site_host"] == key]


def lookup(site_host: str, task_kind=None, today: date | None = None, *, settings=None) -> TaskRoutesResponse:
    """The task routes recorded for a site: the apex table entry for the host or its nearest parent (www.example.com and
    shop.example.com both find example.com), current entries only (verified within TASK_ROUTE_MAX_AGE_DAYS), optionally one
    task kind, newest period first (within a period, records that quote a condition first), at most TASK_ROUTES_MAX records. Reads the in-code table and, with ``settings`` while the write
    switch is served (D118), the published, not withdrawn stored records of the same site; no network.

    Raises TaskRouteInputError (a ValueError) for a host that is not a public hostname or a task_kind outside TaskKind."""
    from . import task_routes_data

    host, kind = parse_query(site_host, task_kind)
    key, rows = site_records(host, settings)
    entries = task_routes_data.TASK_ROUTES_CURATED.get(key, []) if key is not None else []
    records: list[TaskRoute] = []
    for entry in entries:
        if not is_current(entry, today):
            continue
        try:
            record = server_route(key, entry)
        except ValueError:  # one malformed table entry must not take the answer down; the curation check reports it
            LOG.warning("task_route_entry_invalid")
            continue
        if kind is None or record.task_kind == kind:
            records.append(record)
    for row in rows:
        try:
            record = stored_route(row)
        except ValueError:  # a row the current rules refuse is left out, never shown half-checked
            LOG.warning("task_route_row_invalid")
            continue
        if kind is None or record.task_kind == kind:
            records.append(record)
    records.sort(key=lambda r: (r.period, has_condition_text(r), r.verified_at or "", r.task_kind.value, r.obstacle.value), reverse=True)
    by_source: dict[RouteSourceKind, int] = {}
    for record in records:
        by_source[record.source_kind] = by_source.get(record.source_kind, 0) + 1
    by_kind: dict[str, int] = {}
    for record in records:
        by_kind[record.task_kind.value] = by_kind.get(record.task_kind.value, 0) + 1
    shown = records[:TASK_ROUTES_MAX]
    notice = any(record.sensitive for record in shown)
    return TaskRoutesResponse(
        field_notes=field_notes(shown), site_host=host,
        summary_line=summary_line(len(records), by_source, kind, notice, by_kind, condition_text_count(records)), routes=shown,
        counts=TaskRouteCounts(records=len(records), by_source_kind=by_source),
        notice="case_record_not_advice" if notice else None,
        official_help_url=official_help_url(host, today) if not records else None)
