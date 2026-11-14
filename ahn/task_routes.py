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
from datetime import date, datetime, timezone
from enum import Enum
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

from pydantic import Field, field_validator, model_validator

from .models import StrictModel, _omit_none, valid_host

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
# Which sources may carry a link. A public report never does (a path can name a person or a case); curation and operators may.
TASK_ROUTE_URL_SOURCES = frozenset({RouteSourceKind.curated_official, RouteSourceKind.operator_panel})
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
    routes_tried: list[RouteKind] = Field(default_factory=list, max_length=TASK_ROUTE_MAX_TRIED)
    route_worked: RouteKind | Literal["none"] | None = Field(default=None, exclude_if=_omit_none)
    basis: RouteBasis
    outcome: RouteOutcome
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
    period: str
    source_kind: RouteSourceKind
    sensitive: bool
    official_url: str | None = Field(default=None, max_length=200, pattern=r"^https://[^\s?#]+$", exclude_if=_omit_none)
    source_url: str | None = Field(default=None, max_length=200, pattern=r"^https://[^\s?#]+$", exclude_if=_omit_none)
    verified_at: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$", exclude_if=_omit_none)

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
        if self.official_url is not None:
            if self.source_kind not in TASK_ROUTE_URL_SOURCES:
                raise ValueError("invalid_task_route: this source does not carry a link")
            problem = official_url_problem(self.site_host, self.official_url)
            if problem:
                raise ValueError(f"invalid_task_route: official_url {problem}")
        if self.source_url is not None:
            problem = official_url_problem(self.site_host, self.source_url)
            if problem:
                raise ValueError(f"invalid_task_route: source_url {problem}")
        if curated != (self.source_url is not None) or curated != (self.verified_at is not None):
            raise ValueError("invalid_task_route: source_url and verified_at belong to curated_official, which needs both")
        return self


class TaskRouteCounts(StrictModel):
    records: int = Field(ge=0)
    by_source_kind: dict[RouteSourceKind, int] = Field(default_factory=dict)


class TaskRoutesResponse(StrictModel):
    site_host: str
    summary_line: str = Field(min_length=1, max_length=200, pattern=r"^[ -~]+$")
    routes: list[TaskRoute] = Field(default_factory=list, max_length=20)
    counts: TaskRouteCounts
    notice: Literal["case_record_not_advice"] | None = Field(default=None, exclude_if=_omit_none)


class TaskRouteIn(_TaskRouteFields):
    """What a public report may send. There is no official_url, source_url, verified_at, period, source_kind or sensitive
    field: the server sets the last three, and a public report carries no link. Sending any of them is rejected (extra=forbid)."""
    idempotency_key: UUID = Field(default_factory=uuid4)

    @model_validator(mode="after")
    def public_values(self):
        if self.basis == RouteBasis.official_doc or self.outcome == RouteOutcome.not_applicable:
            raise ValueError("invalid_task_route: basis official_doc and outcome not_applicable belong to curated entries")
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
PRIVATE_SUFFIXES = frozenset({"github.io", "gitbook.io", "readthedocs.io", "netlify.app", "vercel.app", "pages.dev"})


def registrable(host: str) -> str:
    """The registrable domain of a host: the last two labels, or three under a two-label public suffix (co.uk) or a shared
    hosting suffix (github.io)."""
    from .service import PUBLIC_SUFFIXES_2

    labels = host.lower().rstrip(".").split(".")
    if len(labels) >= 3 and (".".join(labels[-2:]) in PUBLIC_SUFFIXES_2 or ".".join(labels[-2:]) in PRIVATE_SUFFIXES):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def route_host(site_host: str) -> str | None:
    """The host a task route is kept under: the apex that policy_host() answers for, or None for an account or tenant
    subdomain (which can name a district, a school or a small landlord) that aggregate_eligible_host() keeps out."""
    from .service import aggregate_eligible_host, policy_host

    host = policy_host(valid_host(site_host))
    return host if aggregate_eligible_host(host) else None


def official_url_problem(site_host: str, url) -> str | None:
    """Why ``url`` may not be carried as a task route's official_url for ``site_host``, or None when it may.

    Only curated and operator entries carry a link (a public report never does). The link must be a plain https page on
    the site's own registrable domain, at most 200 characters, with no query, fragment, userinfo or port, and with no path
    segment that looks like it names a person or a case: six or more digits in a row, an e-mail address or a UUID."""
    if not isinstance(url, str):
        return "not a string"
    if len(url) > TASK_ROUTE_URL_MAX:
        return f"longer than {TASK_ROUTE_URL_MAX} characters"
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
        return "not on the site's own registrable domain"
    return None


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
SUMMARY_MAX = 200


def government_suffix(host: str) -> bool:
    """True when the host's suffix is reserved for government bodies (a reading of the code table, no database). Display only."""
    from .gov_registry import _suffix_rule_for

    return _suffix_rule_for(host) is not None


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


def summary_line(total: int, by_source: dict, task_kind: TaskKind | None, notice: bool) -> str:
    """The one sentence of an answer, built only from fixed words, the counts and the task kind's name."""
    if total == 0:
        return SUMMARY_NO_RECORDS_FOR_KIND if task_kind is not None else SUMMARY_NO_RECORDS
    parts = ", ".join(f"{count} {SOURCE_LABELS[kind]}" for kind, count in by_source.items() if count)
    line = f"{total} task route record{'s' if total != 1 else ''}" + (f" for {_words(task_kind)}" if task_kind is not None else "")
    line += f": {parts}."
    if total > TASK_ROUTES_MAX:
        line += f" Showing the {TASK_ROUTES_MAX} newest."
    line += " Records, not guarantees."
    if notice:
        line += " " + ROUTE_LINE_NOTICE
    if len(line) > SUMMARY_MAX:  # cannot happen with the fixed labels at realistic counts; the guard keeps the answer valid
        line = f"{total} task route records. Records, not guarantees." + (" " + ROUTE_LINE_NOTICE if notice else "")
    return line


def lookup(site_host: str, task_kind=None, today: date | None = None) -> TaskRoutesResponse:
    """The task routes recorded for a site: the apex table entry for the host or its nearest parent (www.example.com and
    shop.example.com both find example.com), current entries only (verified within TASK_ROUTE_MAX_AGE_DAYS), optionally one
    task kind, newest period first, at most TASK_ROUTES_MAX records. Reads the in-code table only: no database, no network.

    Raises ValueError for a host that is not a public hostname or a task_kind outside TaskKind."""
    from . import task_routes_data

    host = valid_host(site_host)
    kind = TaskKind(_value(task_kind)) if task_kind is not None else None
    labels = host.split(".")
    key, entries = None, []
    for index in range(len(labels) - 1):  # exact host first, then each parent, as official_paths() does
        candidate = ".".join(labels[index:])
        if candidate in task_routes_data.TASK_ROUTES_CURATED:
            key, entries = candidate, task_routes_data.TASK_ROUTES_CURATED[candidate]
            break
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
    records.sort(key=lambda r: (r.period, r.verified_at or "", r.task_kind.value, r.obstacle.value), reverse=True)
    by_source: dict[RouteSourceKind, int] = {}
    for record in records:
        by_source[record.source_kind] = by_source.get(record.source_kind, 0) + 1
    shown = records[:TASK_ROUTES_MAX]
    notice = any(record.sensitive for record in shown)
    return TaskRoutesResponse(
        site_host=host, summary_line=summary_line(len(records), by_source, kind, notice), routes=shown,
        counts=TaskRouteCounts(records=len(records), by_source_kind=by_source),
        notice="case_record_not_advice" if notice else None)
