from __future__ import annotations

import logging
import os
from datetime import date
from ipaddress import ip_address
from dataclasses import dataclass, field


def report_retention_days_from_env() -> int:
    """Days to keep reports; unset or blank means no automatic deletion."""
    raw = os.environ.get("AHN_REPORT_RETENTION_DAYS", "").strip()
    if not raw:
        return 0
    try:
        days = int(raw)
    except ValueError:
        raise RuntimeError("AHN_REPORT_RETENTION_DAYS must be a whole number of days") from None
    if not 30 <= days <= 3650:
        raise RuntimeError("AHN_REPORT_RETENTION_DAYS must be 30-3650")
    return days


LOG = logging.getLogger(__name__)


def task_routes_env_enabled() -> bool:
    """Whether the task-routes switch is on for this process: AHN_TASK_ROUTES is "1" (spaces ignored) and AHN_TASK_ROUTES_SINCE is a
    real YYYY-MM-DD date. The MCP adapter uses this, so it agrees with Settings.from_env and create_app; it never raises."""
    if os.getenv("AHN_TASK_ROUTES", "").strip() != "1":
        return False
    try:
        date.fromisoformat(os.getenv("AHN_TASK_ROUTES_SINCE", "").strip())
    except ValueError:
        return False
    return True
_EDGE_WARNED = False
_KIND_WARNED = False
_D95_WARNED = False
_D96_WARNED = False


def parse_failure_kind_switch(raw: str) -> bool:
    """AHN_FAILURE_KIND_LINES (D87): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an answer is the
    risky side), and logs one warning per process: the code only, never the value."""
    global _KIND_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _KIND_WARNED:
        _KIND_WARNED = True
        LOG.warning("failure_kind_switch_unknown_value")
    return False


def parse_d95_switch(raw: str) -> bool:
    """AHN_D95_NO_DEV_PATH_ON_FAILURE (D95): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D95_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D95_WARNED:
        _D95_WARNED = True
        LOG.warning("d95_switch_unknown_value")
    return False


def parse_d96_switch(raw: str) -> bool:
    """AHN_D96_STATUS_CHECK_ONE_ACTION (D96): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D96_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D96_WARNED:
        _D96_WARNED = True
        LOG.warning("d96_switch_unknown_value")
    return False


_D98_WARNED = False


def parse_d98_switch(raw: str) -> bool:
    """AHN_D98_ROBOTS_REFUSAL_HANDOFF (D98): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D98_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D98_WARNED:
        _D98_WARNED = True
        LOG.warning("d98_switch_unknown_value")
    return False


_D101_WARNED = False


def parse_d101_switch(raw: str) -> bool:
    """AHN_D101_NO_PATH_HANDOFF (D101): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D101_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D101_WARNED:
        _D101_WARNED = True
        LOG.warning("d101_switch_unknown_value")
    return False


_D99_WARNED = False


def parse_d99_switch(raw: str) -> bool:
    """AHN_D99_UNREADABLE_NOT_PENDING (D99): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D99_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D99_WARNED:
        _D99_WARNED = True
        LOG.warning("d99_switch_unknown_value")
    return False


_D100_WARNED = False


def parse_d100_switch(raw: str) -> bool:
    """AHN_D100_INCIDENTIO_INCIDENTS (D100): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D100_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D100_WARNED:
        _D100_WARNED = True
        LOG.warning("d100_switch_unknown_value")
    return False


_D104_WARNED = False


def parse_d104_switch(raw: str) -> bool:
    """AHN_D104_BETTERSTACK_UPDATED_AT (D104): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _D104_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _D104_WARNED:
        _D104_WARNED = True
        LOG.warning("d104_switch_unknown_value")
    return False


def parse_edge_refusal_switch(raw: str) -> bool:
    """AHN_EDGE_REFUSAL_HANDOFF (D85): empty and "1" are on, "0" is off. Any other value is off, not on (the change of an
    answer is the risky side), and logs one warning per process: the code only, never the value."""
    global _EDGE_WARNED
    value = raw.strip()
    if value in ("", "1"):
        return True
    if value != "0" and not _EDGE_WARNED:
        _EDGE_WARNED = True
        LOG.warning("edge_refusal_switch_unknown_value")
    return False


def parse_proceed_mode(raw: str) -> tuple[str, str]:
    """AHN_PROCEED_WITHOUT_FAILURE as (switch, mode): "0" is off (before D63); "d65" (or "d66") is the blocked-by-default mode; every
    other value, empty and "1" included, is the D63 answers, as before."""
    value = raw.strip().lower()
    if value == "0":
        return "off", "d63"
    if value in ("d65", "d66"):
        return "on", "d65"
    if value not in ("", "1", "d63"):
        LOG.warning("proceed_mode_unknown_value")  # the code only, never the value
    return "on", "d63"


@dataclass(frozen=True)
class Settings:
    database_url: str = field(repr=False)
    key_pepper: str = field(repr=False)
    test_mode: bool = False
    collect_lookup_metrics: bool = False
    max_reports_per_hour: int = 12
    collect_request_metrics: bool = False
    public_mode: bool = False
    reporting_enabled: bool = False
    owner_password: str = field(default="", repr=False)
    owner_allowed_ips: tuple[str, ...] = field(default=(), repr=False)
    # Publisher shown on the privacy, terms and support pages. Kept out of the
    # repository; it must match the verified developer identity of the listing.
    operator_name: str = ""
    # 0 keeps reports without an automatic deletion date (the policy page says so).
    report_retention_days: int = 0
    # AHN_PUBLIC_BUNDLE=1 serves the offline data bundle (ahn/bundle.py); off, its paths do not exist.
    public_bundle: bool = False
    # AHN_PUBLIC_BUNDLE_SINCE (YYYY-MM-DD), required with the switch: the privacy policy's "Last updated" date.
    public_bundle_since: date | None = None
    # AHN_RESPONSE_GZIP=0 turns off gzip for text responses (ahn/compression.py); on by default.
    response_gzip: bool = True
    # AHN_REQUEST_LOG=0 turns off the one-line-per-request log (ahn/request_log.py); on by default.
    request_log: bool = True
    # AHN_VENDOR_AWARE_BLOCK=0 turns off the vendor-aware robots rule (D48): agent_vendor is accepted and ignored, and
    # the rule before D48 applies. On by default.
    vendor_aware_block: bool = True
    # AHN_RESPONSE_GUIDE=0 leaves summary_line, next_action and links out of a lookup answer (D51), so the answer is the
    # one before D51, and the MCP result is one JSON block again. On by default.
    response_guide: bool = True
    # AHN_PUBLIC_BOARD=1 serves the public "Site problems right now" board (ahn/public_board.py, D53): /v1/now and
    # /{lang}/now. Off by default: the paths then do not exist and the policy text is the one before D53.
    public_board: bool = False
    # AHN_PUBLIC_BOARD_SINCE (YYYY-MM-DD), required with the switch: the privacy policy's "Last updated" date.
    public_board_since: date | None = None
    # AHN_PUBLIC_BOARD_AHN_CHECK=1 adds the AHN-check (robots.txt read) column to that board. Off by default, and
    # ignored while the board itself is off.
    public_board_ahn_check: bool = False
    # AHN_LOOKUP_FEEDBACK=0 leaves the one-time feedback token out of blocked-lookup answers (D58). Tokens already issued
    # can still be sent back until they expire. On by default.
    lookup_feedback: bool = True
    # AHN_PROCEED_WITHOUT_FAILURE=0 restores the answers from before D63: the recommendation, the next_checks codes and texts, the summary line, next_action.url and customer_url. On by default.
    proceed_without_failure: bool = True
    # AHN_PROCEED_WITHOUT_FAILURE=d65 (D66): a lookup that reports no failure is answered like one that reported site_access, unless
    # its task_step is status_check or preflight. "1", empty and "d63" keep the D63 answers; only "d65" changes them.
    proceed_mode: str = "d63"
    # AHN_STATUS_CHECK_GUIDE=0 (D69) gives a task_step=status_check lookup the same summary_line as any other lookup and leaves
    # next_action.status_url out. On by default.
    status_check_guide: bool = True
    # AHN_CAUSE_HINT=0 (D73) leaves the optional `cause` object out of a failure lookup's answer and starts its summary_line as
    # it did before. `recommendation` and `next_action.do` are the same either way. On by default.
    cause_hint: bool = True
    # AHN_STATUS_CHECK_COMPACT=0 keeps access_profile.robots.rules in task_step=status_check answers (D71). On by default: the
    # answer then leaves the per-token rules out and gives robots.rules_omitted (their count).
    status_check_compact: bool = True
    # AHN_ROBOTS_RULES_FOLD=0 (D94) keeps all per-token robots.rules in an answer where every token has the same values. On by default;
    # the answer then states the shared values once as robots.rules_uniform and gives rules_omitted.
    robots_rules_fold: bool = True
    # AHN_EDGE_REFUSAL_HANDOFF=0 (D85) keeps a proceed answer for a site_access lookup even where AHN's own home-page check was refused
    # too. On by default; an unknown value is read as off (with one warning), never as on.
    edge_refusal_handoff: bool = True
    # AHN_FAILURE_KIND_LINES=0 (D87) keeps the summary_line of a lookup that reports a failure as it was before D87. On by default; an
    # unknown value is read as off (with one warning), never as on. Only a sentence changes.
    failure_kind_lines: bool = True
    # AHN_D95_NO_DEV_PATH_ON_FAILURE=0 (D95) gives a service_error lookup (a 5xx or no response) on a host with only a developer path
    # the answer from before D95 (use_official_path). On by default; an unknown value is read as off (with one warning), never as on.
    d95_no_dev_path_on_failure: bool = True
    # AHN_D96_STATUS_CHECK_ONE_ACTION=0 (D96) gives a task_step=status_check lookup the answer from before D96: no status answer when a
    # failure signal is sent, and a status page as next_action.url only on a use_official_path row. On by default; an unknown value is
    # read as off (with one warning), never as on.
    d96_status_check_one_action: bool = True
    # AHN_D98_ROBOTS_REFUSAL_HANDOFF=0 (D98) keeps the answer from before D98 (unknown, robots_fetch_refused) for a site_access lookup on a
    # host that refused AHN's plain robots.txt read and lists no developer path. On by default; an unknown value is read as off (with one
    # warning), never as on.
    d98_robots_refusal_handoff: bool = True
    # AHN_D101_NO_PATH_HANDOFF=0 (D101) keeps the answer from before D101 (unknown / use_official_path, next_action.do no_clear_action) for a
    # service_error lookup (a 5xx or no response) whose first line sends the caller to no path outside AHN, and the earlier H3 sentence when
    # AHN's home-page check is over a day old. On by default; an unknown value is read as off (with one warning), never as on.
    d101_no_path_handoff: bool = True
    # AHN_D99_UNREADABLE_NOT_PENDING=0 (D99) shows ahn_check as pending (with retry_after_seconds) again for a host whose robots.txt AHN
    # could not read and that has no read queued. On by default; an unknown value is read as off (with one warning), never as on.
    d99_unreadable_not_pending: bool = True
    # AHN_D100_INCIDENTIO_INCIDENTS=0 (D100) stops reading the summary.json of an incident.io-shaped status page (its unresolved
    # incidents) and keeps the sentences from before D100. On by default; an unknown value is read as off (with one warning), never as on.
    d100_incidentio_incidents: bool = True
    # AHN_D104_BETTERSTACK_UPDATED_AT=0 (D104) stores a Better Stack feed's page-level updated_at as its update time again (and names no
    # affected resources). On by default; an unknown value is read as off (with one warning), never as on.
    d104_betterstack_updated_at: bool = True
    # AHN_TASK_ROUTES=1 serves the task-routes read API and MCP tool (ahn/task_routes_api.py, ahn/mcp_task_routes.py): GET /v1/task-routes
    # and find_task_route. Off by default: the path then does not exist (404), the tool is not listed and the policy text is unchanged.
    task_routes: bool = False
    # AHN_TASK_ROUTES_SINCE (YYYY-MM-DD), required with the switch: the privacy policy's "Last updated" date once task routes are served.
    # Without a valid date the switch does not turn anything on (create_app and the MCP adapter), and Settings.from_env refuses to start.
    task_routes_since: date | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        database_url = os.environ.get("AHN_DATABASE_URL", "").strip()
        key_pepper = os.environ.get("AHN_KEY_PEPPER", "")
        if not database_url:
            raise RuntimeError("AHN_DATABASE_URL is required")
        if len(key_pepper) < 32:
            raise RuntimeError("AHN_KEY_PEPPER must contain at least 32 characters")
        limit = int(os.environ.get("AHN_MAX_REPORTS_PER_HOUR", "12"))
        if not 1 <= limit <= 1000:
            raise RuntimeError("AHN_MAX_REPORTS_PER_HOUR must be 1-1000")
        owner_password = os.environ.get("AHN_OWNER_PASSWORD", "")
        if owner_password and len(owner_password) < 32:
            raise RuntimeError("AHN_OWNER_PASSWORD must contain at least 32 characters")
        try:
            owner_allowed_ips = tuple(
                ip_address(value.strip()).compressed
                for value in os.environ.get("AHN_OWNER_ALLOWED_IPS", "").split(",")
                if value.strip()
            )
        except ValueError as exc:
            raise RuntimeError("AHN_OWNER_ALLOWED_IPS must contain only individual IP addresses") from exc
        operator_name = " ".join(os.environ.get("AHN_OPERATOR_NAME", "").split())
        if len(operator_name) > 80 or not operator_name.isprintable():
            raise RuntimeError("AHN_OPERATOR_NAME must be at most 80 printable characters")
        public_bundle = os.environ.get("AHN_PUBLIC_BUNDLE", "").strip() == "1"
        public_bundle_since = None
        if public_bundle:
            try:
                public_bundle_since = date.fromisoformat(os.environ.get("AHN_PUBLIC_BUNDLE_SINCE", "").strip())
            except ValueError:
                raise RuntimeError("AHN_PUBLIC_BUNDLE=1 needs AHN_PUBLIC_BUNDLE_SINCE=YYYY-MM-DD, "
                                   "the date the privacy policy shows as last updated") from None
        public_board = os.environ.get("AHN_PUBLIC_BOARD", "").strip() == "1"
        public_board_since = None
        if public_board:
            try:
                public_board_since = date.fromisoformat(os.environ.get("AHN_PUBLIC_BOARD_SINCE", "").strip())
            except ValueError:
                raise RuntimeError("AHN_PUBLIC_BOARD=1 needs AHN_PUBLIC_BOARD_SINCE=YYYY-MM-DD, "
                                   "the date the privacy policy shows as last updated") from None
        public_board_ahn_check = public_board and os.environ.get("AHN_PUBLIC_BOARD_AHN_CHECK", "").strip() == "1"
        task_routes = os.environ.get("AHN_TASK_ROUTES", "").strip() == "1"
        task_routes_since = None
        if task_routes:
            try:
                task_routes_since = date.fromisoformat(os.environ.get("AHN_TASK_ROUTES_SINCE", "").strip())
            except ValueError:
                raise RuntimeError("AHN_TASK_ROUTES=1 needs AHN_TASK_ROUTES_SINCE=YYYY-MM-DD, "
                                   "the date the privacy policy shows as last updated") from None
        proceed = parse_proceed_mode(os.environ.get("AHN_PROCEED_WITHOUT_FAILURE", ""))  # once: an unknown value warns once
        return cls(database_url, key_pepper, os.environ.get("AHN_TEST_MODE") == "1", os.environ.get("AHN_COLLECT_LOOKUP_METRICS") == "1", limit, os.environ.get("AHN_COLLECT_REQUEST_METRICS") == "1", os.environ.get("AHN_PUBLIC_MODE") == "1", os.environ.get("AHN_REPORTING_ENABLED") == "1", owner_password, owner_allowed_ips, operator_name, report_retention_days_from_env(), public_bundle, public_bundle_since,
                   os.environ.get("AHN_RESPONSE_GZIP", "").strip() != "0",
                   request_log=os.environ.get("AHN_REQUEST_LOG", "").strip() != "0",
                   vendor_aware_block=os.environ.get("AHN_VENDOR_AWARE_BLOCK", "").strip() != "0",
                   response_guide=os.environ.get("AHN_RESPONSE_GUIDE", "").strip() != "0",
                   public_board=public_board, public_board_since=public_board_since,
                   public_board_ahn_check=public_board_ahn_check,
                   lookup_feedback=os.environ.get("AHN_LOOKUP_FEEDBACK", "").strip() != "0",
                   proceed_without_failure=proceed[0] != "off", proceed_mode=proceed[1],
                   status_check_guide=os.environ.get("AHN_STATUS_CHECK_GUIDE", "").strip() != "0",
                   cause_hint=os.environ.get("AHN_CAUSE_HINT", "").strip() != "0",
                   status_check_compact=os.environ.get("AHN_STATUS_CHECK_COMPACT", "").strip() != "0",
                   robots_rules_fold=os.environ.get("AHN_ROBOTS_RULES_FOLD", "").strip() != "0",
                   edge_refusal_handoff=parse_edge_refusal_switch(os.environ.get("AHN_EDGE_REFUSAL_HANDOFF", "")),
                   failure_kind_lines=parse_failure_kind_switch(os.environ.get("AHN_FAILURE_KIND_LINES", "")),
                   d95_no_dev_path_on_failure=parse_d95_switch(os.environ.get("AHN_D95_NO_DEV_PATH_ON_FAILURE", "")),
                   d98_robots_refusal_handoff=parse_d98_switch(os.environ.get("AHN_D98_ROBOTS_REFUSAL_HANDOFF", "")),
                   d101_no_path_handoff=parse_d101_switch(os.environ.get("AHN_D101_NO_PATH_HANDOFF", "")),
                   d99_unreadable_not_pending=parse_d99_switch(os.environ.get("AHN_D99_UNREADABLE_NOT_PENDING", "")),
                   d100_incidentio_incidents=parse_d100_switch(os.environ.get("AHN_D100_INCIDENTIO_INCIDENTS", "")),
                   d104_betterstack_updated_at=parse_d104_switch(os.environ.get("AHN_D104_BETTERSTACK_UPDATED_AT", "")),
                   d96_status_check_one_action=parse_d96_switch(os.environ.get("AHN_D96_STATUS_CHECK_ONE_ACTION", "")),
                   task_routes=task_routes, task_routes_since=task_routes_since)
