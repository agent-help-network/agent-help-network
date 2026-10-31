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


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=False)


class AttemptFields(StrictModel):
    schema_version: int = 1
    observed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    agent_family: AgentFamily = AgentFamily.other
    attempt_actor: AttemptActor = AttemptActor.agent
    attempt_env: AttemptEnv = AttemptEnv.unknown
    outcome: Outcome
    challenge_vendor: ChallengeVendor = ChallengeVendor.none
    challenge_kind: ChallengeKind = ChallengeKind.none
    synthetic: StrictBool = False
    idempotency_key: UUID = Field(default_factory=uuid4)
    # Legacy compatibility only. The submitting agent follows its own authority rules.
    consent_confirmed: StrictBool | None = None

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
    site_host: str
    task_step: str = "other"
    error_class: ErrorClass

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


def _omit_none(value) -> bool:
    return value is None


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
    # "active_items" (D54): source_updated_at is the latest update of the incidents and maintenances the feed lists as
    # active (Instatus, Google Gmail), not a page-wide time. Left out for every other feed.
    source_time: Literal["active_items"] | None = Field(default=None, exclude_if=_omit_none)
    # Scope of an Atlassian Statuspage feed (ahn/status_scope.py, D43). Each is left out when it was not read.
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


class RobotsSummary(StrictModel):
    rules: list[AgentRule]
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
    code: Literal["locate_failure_stage", "human_handoff", "account_handoff", "before_target_response", "response_observed", "access_denial_observed", "provider_scope", "provider_components_only", "provider_incident_stale", "provider_scope_partial", "provider_update_time_unknown", "provider_maintenance_only", "provider_history_incident", "provider_clear", "provider_unknown", "reports_unknown", "reports_stale", "reports_unverified", "robots_blocks_agents", "robots_blocks_some_agents", "robots_names_vendor_alias", "robots_blocks_not_your_agent", "robots_allows_agents", "robots_wildcard_disallow", "robots_grouping_differs_by_parser", "robots_unknown", "robots_fetch_refused", "profile_not_read", "profile_pending", "llms_txt_present", "content_use_ai_input_declared_no", "official_path_available", "government_registry_listed", "government_suffix_restricted", "government_list_version_missing"]
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


class Links(StrictModel):
    feedback: str | None = Field(default=None, exclude_if=_omit_none)
    changelog: str | None = Field(default=None, exclude_if=_omit_none)


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


class CheckResponse(StrictModel):
    site_host: str
    generated_at: datetime
    # D51: present unless AHN_RESPONSE_GUIDE=0; left out (never null) otherwise.
    summary_line: str | None = Field(default=None, min_length=1, max_length=200, pattern=r"^[ -~]+$", exclude_if=_omit_none)
    next_action: NextAction | None = Field(default=None, exclude_if=_omit_none)
    links: Links | None = Field(default=None, exclude_if=_omit_none)
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
