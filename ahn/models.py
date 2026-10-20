from __future__ import annotations

import ipaddress
import re
from datetime import datetime, timedelta, timezone
from enum import Enum
from uuid import UUID, uuid4

from typing import Literal

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


class AgentFamily(str, Enum):
    dots = "dots"
    muse = "muse"
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


class ProviderStatus(StrictModel):
    source_name: str
    source_url: str
    indicator: Literal["none", "minor", "major", "critical", "maintenance", "unknown"] | None
    fetched_at: datetime | None
    source_updated_at: datetime | None
    freshness: Literal["pending", "fresh", "stale"]
    error_detail: str | None = None


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


class AccessProfile(StrictModel):
    robots_status: Literal["ok", "absent", "redirected", "refused", "invalid", "error", "too_large", "not_public", "pending"]
    robots: RobotsSummary | None = None
    robots_detail: str | None = None
    llms_txt_status: Literal["present", "absent", "redirected", "refused", "error", "not_public", "pending"]
    llms_detail: str | None = None
    read_host: str | None = None
    source_urls: dict[str, str]
    fetched_at: datetime | None = None
    freshness: Literal["fresh", "stale", "pending"]
    # Present only while a read is queued and no definitive answer exists: ask again once after this many
    # seconds. Left out of the JSON otherwise (never serialized as null).
    retry_after_seconds: int | None = Field(default=None, ge=1, le=3600, exclude_if=lambda value: value is None)
    note: str


class OfficialPaths(StrictModel):
    name: str
    api_docs: str | None = None
    developer_portal: str | None = None
    note: str | None = None


class Recommendation(StrictModel):
    action: Literal["proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident", "stop_declared_block", "unknown"]
    confidence: Literal["low", "medium", "high"]
    reasons: list[str] = Field(default_factory=list)


class NextCheck(StrictModel):
    code: Literal["locate_failure_stage", "human_handoff", "account_handoff", "before_target_response", "response_observed", "access_denial_observed", "provider_scope", "provider_clear", "provider_unknown", "reports_unknown", "reports_stale", "reports_unverified", "robots_blocks_agents", "robots_allows_agents", "robots_wildcard_disallow", "robots_unknown", "robots_fetch_refused", "profile_not_read", "profile_pending", "llms_txt_present", "official_path_available"]
    text: str


class CheckResponse(StrictModel):
    site_host: str
    generated_at: datetime
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
    recommendation: Recommendation | None = None
    failure_stage: FailureStage | None = None
    next_checks: list[NextCheck] = Field(default_factory=list)


class Receipt(StrictModel):
    observation_id: UUID
    incident_id: UUID
    review_state: str
    synthetic: bool
    withdrawal_token: str | None = None
