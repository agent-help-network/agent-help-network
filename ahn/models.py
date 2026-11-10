from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID, uuid4

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator


HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
STEP = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
# Special-use and private-network suffixes (RFC 6761/6762/8375 and common LAN
# conventions). ".test" stays allowed because the local test mode depends on it.
RESERVED_TLDS = frozenset({"local", "localhost", "internal", "invalid", "example", "arpa", "onion", "lan",
                           "corp", "home", "intranet", "private", "localdomain", "alt"})


def normalize_host_input(value: str | None) -> str:
    """What people and agents type, made canonical: surrounding spaces, a pasted https:// scheme or
    one trailing slash, a trailing dot and letter case (phones capitalise the first letter).
    Anything else that is not a hostname is left for valid_host to reject."""
    value = (value or "").strip()
    for scheme in ("https://", "http://"):
        if value.lower().startswith(scheme):
            value = value[len(scheme):]
            break
    if value.endswith("/") and "/" not in value[:-1]:
        value = value[:-1]
    return value.rstrip(".").lower()


def valid_host(value: str) -> str:
    if not isinstance(value, str) or len(value) > 253 or value != value.lower():
        raise ValueError("invalid public hostname")
    labels = value.split(".")
    if len(labels) < 2 or any(not HOST_LABEL.fullmatch(label) for label in labels):
        raise ValueError("invalid public hostname")
    # Public TLDs are alphabetic or IDN (xn--); an all-digit last label is an
    # inet_aton-style address such as 127.1 or 0x7f.1.
    if labels[-1] in RESERVED_TLDS or labels[-1].isdigit() or not (labels[-1].isalpha() or labels[-1].startswith("xn--")):
        raise ValueError("invalid public hostname")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        pass
    else:
        raise ValueError("IP addresses are not allowed")
    return value


class ErrorClass(str, Enum):
    site_access = "site_access"
    login_auth = "login_auth"
    human_verification = "human_verification"
    service_error = "service_error"


class FailureStage(str, Enum):
    before_target_response = "before_target_response"
    response_observed = "response_observed"
    access_denial_observed = "access_denial_observed"
    unknown = "unknown"


class Challenge(str, Enum):
    """Whether the site's response carried a bot-challenge header (never a guess from the page body)."""
    header = "header"
    none = "none"


class RetryAfter(str, Enum):
    """Range of a Retry-After value in seconds; an HTTP-date form is sent as none."""
    none = "none"
    lt_60 = "lt_60"
    s60_3600 = "60_3600"
    gt_3600 = "gt_3600"


class Via(str, Enum):
    """What made the failed request: a plain HTTP fetch or a browser."""
    http_fetch = "http_fetch"
    browser = "browser"
    unknown = "unknown"


class AgentFamily(str, Enum):
    dots = "dots"
    muse = "muse"
    other = "other"


class AgentVendor(str, Enum):
    """D48 lookup parameter: the vendor whose documented robots.txt token the caller's own web requests send
    (access_profile.AGENT_VENDORS, plus other). Not the model vendor, and unrelated to the report field agent_family."""
    openai = "openai"
    anthropic = "anthropic"
    google = "google"
    perplexity = "perplexity"
    meta = "meta"
    mistral = "mistral"
    amazon = "amazon"
    microsoft = "microsoft"
    apple = "apple"
    duckduckgo = "duckduckgo"
    diffbot = "diffbot"
    exa = "exa"
    other = "other"


class AttemptActor(str, Enum):
    agent = "agent"
    human = "human"


class AttemptEnv(str, Enum):
    agent_cloud_browser = "agent_cloud_browser"
    human_in_agent_browser = "human_in_agent_browser"
    human_own_browser = "human_own_browser"
    unknown = "unknown"


class Outcome(str, Enum):
    passed = "passed"
    blocked = "blocked"
    error = "error"
    abandoned = "abandoned"


class ChallengeVendor(str, Enum):
    cloudflare = "cloudflare"
    recaptcha = "recaptcha"
    hcaptcha = "hcaptcha"
    unknown = "unknown"
    none = "none"


class ChallengeKind(str, Enum):
    interactive_check = "interactive_check"
    loop = "loop"
    block_page = "block_page"
    unknown = "unknown"
    none = "none"


class ObservationCause(str, Enum):
    """D109: the cause the reporting agent says it established, by the layer it points at. There is no "unknown" value: a
    report that did not establish a cause leaves the field out."""
    site_said_no_agents = "site_said_no_agents"
    site_bot_check = "site_bot_check"
    site_unavailable = "site_unavailable"
    platform_refused = "platform_refused"
    platform_needs_person = "platform_needs_person"
    verification_on_person_device = "verification_on_person_device"
    verification_not_received = "verification_not_received"
    signed_out = "signed_out"
    agent_browser_differs = "agent_browser_differs"


class CauseBasis(str, Enum):
    """D109: what the reporting agent established the cause from."""
    own_platform_message = "own_platform_message"
    site_message = "site_message"
    person_confirmed = "person_confirmed"
    compared_with_own_browser = "compared_with_own_browser"


# D109: for each cause, the bases it may come with and the error classes it may be reported under (None = any).
CAUSE_RULES: dict[ObservationCause, tuple[frozenset[CauseBasis], frozenset[ErrorClass] | None]] = {
    ObservationCause.site_said_no_agents: (frozenset({CauseBasis.site_message}), None),
    ObservationCause.site_bot_check: (frozenset({CauseBasis.site_message}), frozenset({ErrorClass.human_verification})),
    ObservationCause.site_unavailable: (frozenset({CauseBasis.site_message, CauseBasis.person_confirmed}), frozenset({ErrorClass.service_error})),
    ObservationCause.platform_refused: (frozenset({CauseBasis.own_platform_message}), None),
    ObservationCause.platform_needs_person: (frozenset({CauseBasis.own_platform_message}), None),
    ObservationCause.verification_on_person_device: (frozenset({CauseBasis.site_message, CauseBasis.person_confirmed}), frozenset({ErrorClass.login_auth})),
    ObservationCause.verification_not_received: (frozenset({CauseBasis.person_confirmed, CauseBasis.compared_with_own_browser}), frozenset({ErrorClass.login_auth})),
    ObservationCause.signed_out: (frozenset({CauseBasis.site_message, CauseBasis.person_confirmed}), frozenset({ErrorClass.login_auth})),
    ObservationCause.agent_browser_differs: (frozenset({CauseBasis.compared_with_own_browser}), None),
}
# D109: the causes that point at a site's policy or at a particular product. A report carrying one is never published by the
# automatic review; an operator looks at it first.
CAUSE_NAMING_SITE_OR_PLATFORM = frozenset({
    ObservationCause.site_said_no_agents, ObservationCause.platform_refused, ObservationCause.platform_needs_person,
    ObservationCause.agent_browser_differs,
})


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class AttemptFields(StrictModel):
    schema_version: int = Field(default=1, description="Report format version. Always 1; leave it out.")
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="When the attempt happened, as an ISO 8601 time with a timezone offset, for example 2026-11-01T07:10:00Z. Leave it out for an attempt that just happened. Must be within the last 7 days and not in the future.")
    agent_family: AgentFamily = Field(default=AgentFamily.other, description="Which agent family you are: dots, muse, or other (the default; use it if neither fits).")
    attempt_actor: AttemptActor = Field(default=AttemptActor.agent, description="Who made the attempt: agent (you, the default) or human (the person did it themselves).")
    attempt_env: AttemptEnv = Field(default=AttemptEnv.unknown, description="Where the attempt ran: agent_cloud_browser (a browser run by an agent in the cloud; needs attempt_actor agent), human_in_agent_browser or human_own_browser (a person at a browser; needs attempt_actor human), or unknown (the default).")
    outcome: Outcome = Field(description="How the attempt ended: passed (it worked), blocked (the site or a check stopped it), error (it failed with an error), or abandoned (you or the person stopped before the end).")
    challenge_vendor: ChallengeVendor = Field(default=ChallengeVendor.none, description="Which bot-check provider showed a challenge: cloudflare, recaptcha, hcaptcha, unknown (a challenge from another or unclear provider), or none (the default; no challenge).")
    challenge_kind: ChallengeKind = Field(default=ChallengeKind.none, description="What the challenge looked like: interactive_check (a box or puzzle to complete), loop (the check kept repeating), block_page (a page that only says access is denied), unknown, or none (the default; no challenge).")
    synthetic: StrictBool = Field(default=False, description="true only for a made-up test report. Leave it out (false) for a real attempt.")
    idempotency_key: UUID = Field(default_factory=uuid4, description="A UUID that makes a resend safe: sending the same key with the same body again does not create a second record. Leave it out to get a new one.")
    # Legacy compatibility only. The submitting agent follows its own authority rules.
    consent_confirmed: StrictBool | None = Field(default=None, description="Legacy field kept for older clients. It is not used to decide anything; leave it out.")

    @field_validator("schema_version")
    @classmethod
    def version_one(cls, value):
        if value != 1:
            raise ValueError("unsupported schema version")
        return value

    @field_validator("observed_at")
    @classmethod
    def reasonable_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        now = datetime.now(timezone.utc)
        if value > now + timedelta(minutes=5) or value < now - timedelta(days=7):
            raise ValueError("observed_at outside local pilot window")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def actor_environment(self):
        if self.attempt_env == AttemptEnv.agent_cloud_browser and self.attempt_actor != AttemptActor.agent:
            raise ValueError("agent_cloud_browser requires agent actor")
        if self.attempt_env in {AttemptEnv.human_in_agent_browser, AttemptEnv.human_own_browser} and self.attempt_actor != AttemptActor.human:
            raise ValueError("human browser requires human actor")
        return self


class ObservationIn(AttemptFields):
    site_host: str = Field(description="The public hostname the attempt was about, for example example.com. Only the host: no scheme, path, query, port or credentials.")
    task_step: str = Field(default="other", description="What you were trying to do, as a short lowercase code of letters, digits and underscores that starts with a letter, for example login, checkout or status_check. Default other.")
    error_class: ErrorClass = Field(description="What kind of stop it was: site_access (page or API refused or unreachable), login_auth (sign-in failed or an extra verification step appeared), human_verification (a CAPTCHA or a verify-you-are-human check), or service_error (the site returned an error or timed out).")
    # D109: both or neither. Only for a blocked or failed attempt, and only from the fixed combinations of CAUSE_RULES. A cause
    # is sent only when it was established from the stated basis, never as a guess; otherwise the fields are left out.
    cause: ObservationCause | None = Field(default=None, description="Optional, with cause_basis, for outcome blocked or error: the cause you established. Leave out if not established.")
    cause_basis: CauseBasis | None = Field(default=None, description="Required with cause: what you established it from.")

    @field_validator("site_host")
    @classmethod
    def host_only(cls, value):
        return valid_host(value)

    @field_validator("task_step")
    @classmethod
    def step_code(cls, value):
        if not STEP.fullmatch(value):
            raise ValueError("invalid task step")
        return value

    @model_validator(mode="after")
    def cause_combination(self):
        if self.cause is None:
            if self.cause_basis is not None:
                raise ValueError("invalid_cause: cause_basis needs cause")
            return self
        bases, classes = CAUSE_RULES[self.cause]
        if self.cause_basis is None or self.cause_basis not in bases:
            raise ValueError("invalid_cause: cause_basis is missing or not allowed for this cause")
        if classes is not None and self.error_class not in classes:
            raise ValueError("invalid_cause: error_class does not fit this cause")
        if self.outcome not in {Outcome.blocked, Outcome.error}:
            raise ValueError("invalid_cause: a cause goes with a blocked or error outcome")
        return self


class OutcomeIn(AttemptFields):
    pass


class CheckObservation(StrictModel):
    observed_at: datetime
    attempt_actor: AttemptActor
    attempt_env: AttemptEnv
    outcome: Outcome
    error_class: ErrorClass
    task_step: str
    synthetic: bool
    # D109: what the reporting agent said it established. Present only when the report carried it and passed review; left out
    # (never null) otherwise. Not verified by AHN, and it takes no part in the recommendation or any count.
    cause: ObservationCause | None = Field(default=None, exclude_if=lambda value: value is None)
    cause_basis: CauseBasis | None = Field(default=None, exclude_if=lambda value: value is None)


def _omit_none(value) -> bool:
    return value is None


VENDOR_CODES = ("cloudflare", "datadome", "perimeterx", "aws_waf", "vercel", "generic_captcha", "unknown")
VendorCode = Literal["cloudflare", "datadome", "perimeterx", "aws_waf", "vercel", "generic_captcha", "unknown"]


class AhnCheckVantage(StrictModel):
    kind: Literal["datacenter", "residential", "mobile", "unknown"]
    region: str | None = Field(default=None, exclude_if=_omit_none)


class AhnCheckBotDefense(StrictModel):
    vendor: VendorCode
    signal: Literal["header", "cookie", "body"]
    confidence: Literal["high", "medium"]


class AhnCheckRedirect(StrictModel):
    followed_www: bool
    to_relation: Literal["same_host", "www_alias", "same_site", "other_site"]
    path_class: Literal["login", "locale", "other"]


class AhnCheck(StrictModel):
    """D67: the outcome of AHN's own server's check of the host's home page, as codes. Information only; it is added after
    the recommendation is decided and changes none of it (except the D85 case, ``edge_refusal_confirmed``). Fields that do not apply are left out, never null."""
    status: Literal["measured", "pending", "not_measured"]
    basis: Literal["ahn_server"]
    vantage: AhnCheckVantage
    ua_token: Literal["AgentHelpNetwork"]
    path_kind: Literal["home"]
    measured_at: datetime | None = Field(default=None, exclude_if=_omit_none)
    age_seconds: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    freshness: Literal["fresh", "stale"] | None = Field(default=None, exclude_if=_omit_none)
    outcome: Literal["reachable", "redirected_same_host", "bot_challenge", "refused", "rate_limited", "login_wall",
                     "redirected_offsite", "server_error", "unreachable", "other_status"] | None = Field(default=None, exclude_if=_omit_none)
    http_status: int | None = Field(default=None, ge=100, le=599, exclude_if=_omit_none)
    bot_defense: AhnCheckBotDefense | None = Field(default=None, exclude_if=_omit_none)
    edge_vendors_seen: list[VendorCode] | None = Field(default=None, max_length=5, exclude_if=_omit_none)
    redirect: AhnCheckRedirect | None = Field(default=None, exclude_if=_omit_none)
    login_signal: Literal["http_auth", "redirect", "form"] | None = Field(default=None, exclude_if=_omit_none)
    response_ms: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    retry_after_seconds: int | None = Field(default=None, ge=0, le=3600, exclude_if=_omit_none)
    not_measured_reason: Literal["robots_disallowed", "robots_refused", "robots_unreadable", "robots_stale", "not_eligible",
                                 "budget", "backoff", "disabled"] | None = Field(default=None, exclude_if=_omit_none)
    fingerprint_version: str = Field(max_length=16)
    note: str


class ProviderStatus(StrictModel):
    source_name: str
    source_url: str
    indicator: Literal["none", "minor", "major", "critical", "maintenance", "unknown"] | None
    fetched_at: datetime | None
    # The feed's page.updated_at as the provider published it; not necessarily when the status changed (D43).
    source_updated_at: datetime | None
    freshness: Literal["pending", "fresh", "stale"]
    error_detail: str | None = None
    # "history_atom" (D52): the reading comes from the page's public Atom history, not its status API. indicator is then
    # always null, and no open incident listed means unknown, not healthy. window_blind (only ever true) says the feed
    # may be missing a recent or an older open incident.
    source_kind: Literal["history_atom"] | None = Field(default=None, exclude_if=_omit_none)
    window_blind: Literal[True] | None = Field(default=None, exclude_if=_omit_none)
    # D102: what freshness and source_updated_at measure (a fixed sentence, service.PROVIDER_STATUS_NOTE); freshness itself is D46's.
    note: str | None = Field(default=None, exclude_if=_omit_none)
    # D111: whole days since source_updated_at (the provider's own update time); left out when that time is missing or unusable.
    source_age_days: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    # D111: true only when source_age_days is over 30; left out otherwise. Information only: freshness and the answer do not read it.
    provider_page_stale: Literal[True] | None = Field(default=None, exclude_if=_omit_none)
    # What source_updated_at is. "active_items" (D54): the latest update of the incidents and maintenances the feed lists
    # as active (Instatus, Google Gmail). "page" (D80): the page-wide time the provider last changed its status page, which
    # need not move with incidents. Left out when there is no source_updated_at (for example the Atom history).
    source_time: Literal["active_items", "page"] | None = Field(default=None, exclude_if=_omit_none)
    # Scope of a Statuspage-shaped feed (ahn/status_scope.py, D43). Each is left out when it was not read.
    active_incidents: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    max_incident_impact: Literal["none", "minor", "major", "critical"] | None = Field(default=None, exclude_if=_omit_none)
    incident_updated_at: datetime | None = Field(default=None, exclude_if=_omit_none)
    active_maintenances: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    basis: Literal["incident", "maintenance", "components_only"] | None = Field(default=None, exclude_if=_omit_none)
    affected: list[Annotated[str, Field(min_length=1, max_length=80)]] | None = Field(
        default=None, min_length=1, max_length=3, exclude_if=_omit_none)


class OfficialStatusPage(StrictModel):
    source_name: str
    source_url: str
    # D72: true only when the listed page needs the user's sign-in; left out (never null) otherwise.
    requires_sign_in: Literal[True] | None = Field(default=None, exclude_if=_omit_none)
    # D80: "api" only when the page's own name says it covers the provider's API or developer services, not the whole site;
    # left out (never null) otherwise.
    covers: Literal["api"] | None = Field(default=None, exclude_if=_omit_none)


class LookupActivity(StrictModel):
    period_days: Literal[7]
    volume_band: Literal["1-4", "5-9", "10-49", "50+"]
    trend: Literal["increased"] | None = None
    unit: Literal["requests_not_agents"] = "requests_not_agents"


class AgentRule(StrictModel):
    agent: str
    kind: Literal["user_agent", "search_crawler", "training_crawler"]
    explicit_group: bool
    root_allowed: bool
    disallow_count: int = Field(ge=0)
    crawl_delay: float | None = None
    disallow_paths: list[str] = Field(default_factory=list)


class RulesUniform(StrictModel):
    """D94: the values every token in the (left-out) `rules` list shares."""
    explicit_group: bool
    root_allowed: bool
    disallow_count: int = Field(ge=0)
    crawl_delay: float | None = Field(default=None, exclude_if=_omit_none)
    disallow_paths: list[str] | None = Field(default=None, exclude_if=_omit_none)


class RobotsSummary(StrictModel):
    # D71: a task_step=status_check answer leaves `rules` out and gives `rules_omitted` (the count) instead.
    rules: list[AgentRule] | None = Field(default=None, exclude_if=_omit_none)
    rules_omitted: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    # D94: where every token has the same values, `rules` is left out and the shared values are given here once.
    rules_uniform: RulesUniform | None = Field(default=None, exclude_if=_omit_none)
    wildcard_root_allowed: bool
    sitemap: bool = False
    sitemaps: list[str] = Field(default_factory=list)
    wildcard_crawl_delay: float | None = None
    wildcard_disallow_paths: list[str] = Field(default_factory=list)
    agents_blocked: list[str] = Field(default_factory=list)
    agents_allowed: list[str] = Field(default_factory=list)
    # "*" and agent tokens whose root decision differs under Google-style grouping (strict RFC 9309); informational.
    grouping_differs_by_parser: list[str] = Field(default_factory=list)
    # D48: names from access_profile.VENDOR_ALIASES whose own groups disallow the root. Absent from profiles read before
    # D48; such a profile gets the previous robots rule until it is read again.
    vendor_aliases_blocked: list[str] | None = Field(default=None, exclude_if=_omit_none)


UseValue = Literal["yes", "no", "unset"]


class ContentSignals(StrictModel):
    """Cloudflare Content Signals, normalized; `use` is Cloudflare's experimental fourth signal."""
    search: UseValue
    ai_input: UseValue
    ai_train: UseValue
    use: Literal["immediate", "reference", "full"] | None = None


class ContentUsage(StrictModel):
    """IETF AIPREF preferences (draft-ietf-aipref-vocab-08 categories), y/n normalized to yes/no."""
    train_ai: UseValue
    ai_use: UseValue
    search: UseValue


class ContentUseGroup(StrictModel):
    agents: list[str]  # only tokens from the service's agent-token table
    other_agents: int = Field(default=0, ge=0)  # names outside that table (or over the cap), counted, never kept
    signals: ContentSignals | None = None
    usage: ContentUsage | None = None
    path_specific_samples: list[str] = Field(default_factory=list)


class ContentUseDeclared(StrictModel):
    status: Literal["declared"]
    reason: None = None
    formats: list[Literal["cloudflare_content_signal", "ietf_aipref"]]
    signals: ContentSignals | None = None
    usage: ContentUsage | None = None
    scope: Literal["all_paths", "path_specific"] | None = None
    path_specific_samples: list[str] = Field(default_factory=list)
    named_groups: list[ContentUseGroup] = Field(default_factory=list)
    named_groups_total: int = Field(default=0, ge=0)
    vendor_default_hint: bool = False
    ai_input_declared_no: bool = False
    ai_input_no_agents: list[str] = Field(default_factory=list)
    ignored_keys: list[str] = Field(default_factory=list)
    vocab: str
    source_url: str
    fetched_at: datetime | None = None
    freshness: Literal["fresh", "stale", "pending"]
    note: str


class ContentUseCompact(StrictModel):
    """Nothing declared, or the file could not be read: the short shape, without the note and vocabulary strings."""
    status: Literal["none_declared", "not_read"]
    reason: str | None = None
    source_url: str
    fetched_at: datetime | None = None
    freshness: Literal["fresh", "stale", "pending"]


ContentUse = Annotated[ContentUseDeclared | ContentUseCompact, Field(discriminator="status")]


DeclaredStatus = Literal["declared", "none_declared", "not_read"]


class CatalogApi(StrictModel):
    """One API of an RFC 9727 catalog: its anchor and RFC 8631 links as published (at most 3 per relation)."""
    anchor: str | None = None
    service_desc: list[str] | None = Field(default=None, exclude_if=_omit_none)
    service_doc: list[str] | None = Field(default=None, exclude_if=_omit_none)
    service_meta: list[str] | None = Field(default=None, exclude_if=_omit_none)
    status: list[str] | None = Field(default=None, exclude_if=_omit_none)


class ApiCatalog(StrictModel):
    """/.well-known/api-catalog (RFC 9727). Fields other than status and source_url appear only when declared."""
    status: DeclaredStatus
    reason: str | None = Field(default=None, exclude_if=_omit_none)
    source_url: str
    apis: list[CatalogApi] | None = Field(default=None, exclude_if=_omit_none)
    apis_total: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    items: list[str] | None = Field(default=None, exclude_if=_omit_none)
    items_total: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    nested_catalogs: list[str] | None = Field(default=None, exclude_if=_omit_none)


class TdmRule(StrictModel):
    location: str
    tdm_reservation: Literal[0, 1] | None = None  # null: a value other than 0 or 1 (TDMRep reads it as unset)
    tdm_policy: str | None = Field(default=None, exclude_if=_omit_none)


class TdmHeader(StrictModel):
    """tdm-reservation / tdm-policy response headers seen on the response to one of the files read."""
    path: str
    tdm_reservation: Literal[0, 1] | None = None
    tdm_policy: str | None = Field(default=None, exclude_if=_omit_none)


class TdmReservation(StrictModel):
    """/.well-known/tdmrep.json and TDM response headers (W3C TDMRep). Quoted as declared, never a legal reading."""
    status: DeclaredStatus
    reason: str | None = Field(default=None, exclude_if=_omit_none)
    source_url: str
    rules: list[TdmRule] | None = Field(default=None, exclude_if=_omit_none)
    rules_total: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    root_reservation: Literal[0, 1] | None = Field(default=None, exclude_if=_omit_none)  # left out: unset for "/"
    any_reserved: bool | None = Field(default=None, exclude_if=_omit_none)
    headers: list[TdmHeader] | None = Field(default=None, exclude_if=_omit_none)
    # Only when a header made status 'declared' although tdmrep.json itself was not declared: that file read's result.
    file_status: Literal["none_declared", "not_read"] | None = Field(default=None, exclude_if=_omit_none)
    file_reason: str | None = Field(default=None, exclude_if=_omit_none)


class McpCatalogServer(StrictModel):
    identifier: str | None = None
    card_url: str | None = Field(default=None, exclude_if=_omit_none)
    inline_card: Literal[True] | None = Field(default=None, exclude_if=_omit_none)
    name: str | None = Field(default=None, exclude_if=_omit_none)
    remote_urls: list[str] | None = Field(default=None, exclude_if=_omit_none)


class McpDiscovery(StrictModel):
    """Experimental: MCP Server Card entries of the host's /.well-known/ai-catalog.json (MCP SEP-2127 discovery)."""
    experimental: Literal[True] = True
    status: DeclaredStatus
    reason: str | None = Field(default=None, exclude_if=_omit_none)
    source_url: str
    servers: list[McpCatalogServer] | None = Field(default=None, exclude_if=_omit_none)
    servers_total: int | None = Field(default=None, ge=0, exclude_if=_omit_none)
    spec_version: str | None = Field(default=None, exclude_if=_omit_none)


class SiteDeclared(StrictModel):
    """What the host publishes for agents at standard locations (D59); information only, never in the recommendation."""
    api_catalog: ApiCatalog
    tdm: TdmReservation
    mcp_discovery: McpDiscovery | None = Field(default=None, exclude_if=_omit_none)  # absent with AHN_MCP_DISCOVERY=0
    fetched_at: datetime | None = None
    freshness: Literal["fresh", "stale", "pending"]
    note: str | None = Field(default=None, exclude_if=_omit_none)  # present when at least one file is declared


class AccessProfile(StrictModel):
    robots_status: Literal["ok", "absent", "redirected", "refused", "invalid", "error", "too_large", "not_public", "pending"]
    robots: RobotsSummary | None = None
    robots_detail: str | None = None
    llms_txt_status: Literal["present", "absent", "redirected", "refused", "error", "not_public", "pending", "not_read"]
    llms_detail: str | None = None
    read_host: str | None = None
    source_urls: dict[str, str]
    fetched_at: datetime | None = None
    freshness: Literal["fresh", "stale", "pending"]
    # Present only while a read is queued and no definitive answer exists: ask again once after this many
    # seconds. Left out of the JSON otherwise (never serialized as null).
    retry_after_seconds: int | None = Field(default=None, ge=1, le=3600, exclude_if=lambda value: value is None)
    note: str
    # Content-use preferences declared in the same robots.txt; information only, never part of the recommendation.
    content_use: ContentUse | None = None
    # D59: standard-location files the host publishes for agents; left out with AHN_SITE_DECLARED=0 (never null).
    site_declared: SiteDeclared | None = Field(default=None, exclude_if=_omit_none)


class OfficialPaths(StrictModel):
    name: str
    api_docs: str | None = None
    developer_portal: str | None = None
    note: str | None = None
    # Curated "how to use it" bundle for some hosts; left out of the JSON when there is none (never null).
    access: OfficialAccess | None = Field(default=None, exclude_if=lambda value: value is None)
    # D61: the site's own customer path (help centre, official app, entry link) for a task in a person's own account;
    # curated for some hosts, left out of the JSON otherwise. Information only: it never changes `recommendation`.
    consumer: ConsumerPaths | None = Field(default=None, exclude_if=lambda value: value is None)


class ConsumerPath(StrictModel):
    kind: Literal["customer_help", "official_app", "entry_link"]
    url: str = Field(pattern=r"^https://[^\s]+$", max_length=200)
    source_url: str = Field(pattern=r"^https://[^\s]+$", max_length=200)
    # D102: what the page is for, when the table says; left out (never null) otherwise. customer_url does not use it.
    topic: Literal["returns", "orders", "contact", "help", "account", "shipping"] | None = Field(default=None, exclude_if=_omit_none)


class ConsumerPaths(StrictModel):
    paths: list[ConsumerPath] = Field(min_length=1, max_length=6)
    verified_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


# Every optional field of the access bundle is left out of the JSON when unknown, to keep answers small.
_ABSENT = {"default": None, "exclude_if": lambda value: value is None}
HTTPS_URL = r"^https://[^\s]+$"


class AccessAuth(StrictModel):
    method: Literal["none", "api_key", "oauth1", "oauth2", "device_code", "pat"]
    source_url: str = Field(pattern=HTTPS_URL)


class AccessQuote(StrictModel):
    quote: str = Field(min_length=1, max_length=200)
    source_url: str = Field(pattern=HTTPS_URL)


class AccessTerms(StrictModel):
    url: str = Field(pattern=HTTPS_URL)
    quote: str = Field(min_length=1, max_length=200)


class AccessMcp(StrictModel):
    endpoint: str = Field(pattern=HTTPS_URL)
    doc_url: str = Field(pattern=HTTPS_URL)


class OfficialAccess(StrictModel):
    auth: list[AccessAuth] | None = Field(**_ABSENT, min_length=1)
    key_signup_url: str | None = Field(**_ABSENT, pattern=HTTPS_URL)
    free_tier: AccessQuote | None = Field(**_ABSENT)
    rate_limits_url: str | None = Field(**_ABSENT, pattern=HTTPS_URL)
    sandbox_url: str | None = Field(**_ABSENT, pattern=HTTPS_URL)
    openapi_url: str | None = Field(**_ABSENT, pattern=HTTPS_URL)
    official_mcp: list[AccessMcp] | None = Field(**_ABSENT, min_length=1)
    # Official alternative paths to the same information (D60). Curated links only: the service never reads these URLs,
    # and they never change `recommendation` or `next_checks`.
    feeds: list[Annotated[str, Field(pattern=HTTPS_URL, max_length=300)]] | None = Field(**_ABSENT, min_length=1, max_length=3)
    llms_full_url: str | None = Field(**_ABSENT, pattern=HTTPS_URL, max_length=300)
    docs_repo: str | None = Field(**_ABSENT, pattern=HTTPS_URL, max_length=300)
    data_export_url: str | None = Field(**_ABSENT, pattern=HTTPS_URL, max_length=300)
    terms: AccessTerms | None = Field(**_ABSENT)
    verified_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")


OfficialPaths.model_rebuild()


class GovernmentRegistry(StrictModel):
    """Which public government domain list names this domain, as of which version. A fact about the domain, not a judgement."""
    status: Literal["listed", "not_in_list_version"]
    registry: Literal["us_dotgov", "uk_govuk"]
    registry_name: str
    domain_type: str | None = Field(default=None, exclude_if=_omit_none)
    organization: str | None = Field(default=None, exclude_if=_omit_none)
    suborganization: str | None = Field(default=None, exclude_if=_omit_none)
    state: str | None = Field(default=None, exclude_if=_omit_none)
    list_version: str
    source_url: str
    license: str
    attribution: str | None = Field(default=None, exclude_if=_omit_none)
    fetched_at: datetime


class SuffixRule(StrictModel):
    suffix: str
    rule: Literal["registration_limited_to_government"]
    text: str
    source_url: str


class HostFacts(StrictModel):
    government_registry: GovernmentRegistry | None = Field(default=None, exclude_if=_omit_none)
    suffix_rule: SuffixRule | None = Field(default=None, exclude_if=_omit_none)
    note: str


class Recommendation(StrictModel):
    action: Literal["proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident", "stop_declared_block", "unknown"]
    confidence: Literal["low", "medium", "high"]
    reasons: list[str] = Field(default_factory=list)
    # D48: the agent_vendor the robots part of this answer was decided for; present only when one was accepted.
    agent_vendor: AgentVendor | None = Field(default=None, exclude_if=_omit_none)


class NextCheck(StrictModel):
    code: Literal["locate_failure_stage", "human_handoff", "account_handoff", "before_target_response", "response_observed", "access_denial_observed", "edge_refusal_confirmed", "edge_refusal_robots", "no_path_after_failure", "provider_scope", "provider_components_only", "provider_incident_stale", "provider_scope_partial", "provider_update_time_unknown", "provider_maintenance_only", "provider_history_incident", "provider_clear", "provider_unknown", "reports_unknown", "reports_stale", "reports_unverified", "robots_blocks_agents", "robots_blocks_some_agents", "robots_names_vendor_alias", "robots_blocks_not_your_agent", "robots_allows_agents", "robots_wildcard_disallow", "robots_grouping_differs_by_parser", "robots_unknown", "robots_fetch_refused", "profile_not_read", "profile_pending", "llms_txt_present", "content_use_ai_input_declared_no", "official_path_available", "government_registry_listed", "government_suffix_restricted", "government_list_version_missing"]
    text: str


class NextAction(StrictModel):
    """D51: what to do now, computed from the recommendation (never a second verdict)."""
    do: Literal["wait_for_provider", "hand_off_to_human", "use_official_path", "stop_automation", "proceed", "retry_lookup_once",
                "stop_if_listed", "lookup_apex_host", "no_clear_action"]
    url: str | None = Field(default=None, max_length=200)
    # When to repeat the same lookup once; not a polling interval.
    retry_after_seconds: int | None = Field(default=None, ge=0, le=3600)
    stop_if_you_are: list[Annotated[str, Field(min_length=1, max_length=64)]] | None = Field(
        default=None, min_length=1, max_length=16, exclude_if=_omit_none)
    lookup_host: str | None = Field(default=None, exclude_if=_omit_none)
    # D61: with do=use_official_path or, since D63, proceed, the site's own customer page for a task in the user's own
    # account, which a developer API may not reach. Present only when official_paths.consumer is curated.
    customer_url: str | None = Field(default=None, max_length=200, exclude_if=_omit_none)
    # D69: the provider's or the official status page, for a task_step=status_check lookup. Present only when one is known.
    status_url: str | None = Field(default=None, max_length=200, exclude_if=_omit_none)
    # D102: "api" when status_url is a status page that covers only the provider's API (official_status_page.covers); left out otherwise.
    status_url_covers: Literal["api"] | None = Field(default=None, exclude_if=_omit_none)


class Links(StrictModel):
    feedback: str | None = Field(default=None, exclude_if=_omit_none)
    changelog: str | None = Field(default=None, exclude_if=_omit_none)
    # D109: GET /v1/why for the same failed lookup (same hostname and codes). Present only on a lookup that sent a failure code.
    why: str | None = Field(default=None, max_length=600, exclude_if=_omit_none)


class FeedbackOutcome(str, Enum):
    """D58: what happened on the caller's next attempt after a blocked lookup. "Not tried" is never sent: no answer is that."""
    succeeded = "succeeded"
    still_blocked = "still_blocked"
    handed_off = "handed_off"
    gave_up = "gave_up"


class FeedbackPath(str, Enum):
    """D58: which way the attempt that the outcome is about went."""
    official_path = "official_path"
    same_site = "same_site"
    unknown = "unknown"


# Token text as issued by ahn/feedback.py: base64url payload, a dot, base64url MAC. The bound only keeps junk short.
FEEDBACK_TOKEN = re.compile(r"^[A-Za-z0-9_-]{16,480}\.[A-Za-z0-9_-]{43}$")


class Feedback(StrictModel):
    """D58: a one-time token for saying, later, whether the next attempt worked (POST /v1/feedback, MCP rate_last_lookup)."""
    token: str = Field(max_length=530)
    expires_in_seconds: int = Field(ge=1, le=3600)
    outcomes: list[FeedbackOutcome]
    paths: list[FeedbackPath]


class FeedbackIn(StrictModel):
    """D58: body of POST /v1/feedback. No hostname: the token says which lookup it answers."""
    token: str = Field(max_length=530)
    outcome: FeedbackOutcome
    path: FeedbackPath = FeedbackPath.unknown


class FeedbackReceipt(StrictModel):
    accepted: Literal[True]


class CauseWindow(StrictModel):
    """D73: the period of the measurement the cause rests on (UTC). ``to`` is left out while it is still going on."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False, populate_by_name=True)
    from_: datetime = Field(alias="from")
    to: datetime | None = Field(default=None, exclude_if=_omit_none)


class Cause(StrictModel):
    """D73: why the step failed, from AHN's own stored host-level measurements. Information only: it is added after the
    recommendation is final and changes none of it. ``line`` is a fixed English sentence with times and no site text."""
    branch: Literal["policy_closed", "transient", "undetermined"]
    hint: Literal["home_now_gated", "persistent_refusal", "provider_incident", "site_errors", "recovered", "rate_limited",
                  "others_failing_same_hour", "your_side_likely", "no_measurement", "home_gate_unconfirmed",
                  "robots_now_disallows", "terms_signal_changed", "new_bot_defense"]
    confidence: Literal["high", "medium", "low"]
    basis: list[Literal["provider_status_events", "outage_episodes", "site_probe", "site_probe_daily",
                        "lookup_signals_hourly", "host_access_changes"]] = Field(max_length=6)
    window: CauseWindow | None = Field(default=None, exclude_if=_omit_none)
    line: str = Field(min_length=1, max_length=200, pattern=r"^[ -~]+$")
    note: str = Field(default="Information only; follow next_action.", min_length=1, max_length=80, pattern=r"^[ -~]+$")
    recheck_after: datetime | None = Field(default=None, exclude_if=_omit_none)


class CheckResponse(StrictModel):
    site_host: str
    generated_at: datetime
    # D51: present unless AHN_RESPONSE_GUIDE=0; left out (never null) otherwise.
    summary_line: str | None = Field(default=None, min_length=1, max_length=401, pattern=r"^[ -~]+$", exclude_if=_omit_none)
    next_action: NextAction | None = Field(default=None, exclude_if=_omit_none)
    links: Links | None = Field(default=None, exclude_if=_omit_none)
    # D73: present only on a failure lookup while AHN_CAUSE_HINT is not 0 and a cause can be said; left out (never null) otherwise.
    cause: Cause | None = Field(default=None, exclude_if=_omit_none)
    synthetic: bool
    window_hours: int = Field(ge=1, le=168)
    data_horizon_hours: Literal[168]
    current_site_status: Literal["unknown"]
    evidence_level: Literal["no_reports", "single_observation", "multiple_observations", "later_success_observed", "stale_reports"]
    summary_code: str
    summary: str
    failures: int = Field(ge=0)
    successes: int = Field(ge=0)
    reporting_sources: int = Field(ge=0)
    observations: list[CheckObservation]
    provider_status: ProviderStatus | None = None
    provider_source_state: Literal["feed_connected", "official_page_only", "not_verified_by_current_methods"]
    provider_discovery_attempted_at: datetime | None = None
    official_status_page: OfficialStatusPage | None = None
    # D67: present only while AHN_SITE_PROBE=1 and a check exists or is pending for the host; left out (never null) otherwise.
    ahn_check: AhnCheck | None = Field(default=None, exclude_if=_omit_none)
    lookup_activity: LookupActivity | None = None
    access_profile: AccessProfile | None = None
    official_paths: OfficialPaths | None = None
    # Present only when a government domain list or a government-only suffix rule applies to the host.
    host_facts: HostFacts | None = Field(default=None, exclude_if=_omit_none)
    recommendation: Recommendation | None = None
    failure_stage: FailureStage | None = None
    next_checks: list[NextCheck] = Field(default_factory=list)
    # D58: present only on a blocked lookup (error_class sent) that the service counts; left out (never null) otherwise.
    feedback_token: Feedback | None = Field(default=None, exclude_if=_omit_none)


class Receipt(StrictModel):
    observation_id: UUID
    incident_id: UUID
    review_state: str
    synthetic: bool
    withdrawal_token: str | None = None
