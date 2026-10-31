from __future__ import annotations

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
    # AHN_PROCEED_WITHOUT_FAILURE=0 restores the recommendation and the next_checks code list from before D63. On by default.
    proceed_without_failure: bool = True

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
        return cls(database_url, key_pepper, os.environ.get("AHN_TEST_MODE") == "1", os.environ.get("AHN_COLLECT_LOOKUP_METRICS") == "1", limit, os.environ.get("AHN_COLLECT_REQUEST_METRICS") == "1", os.environ.get("AHN_PUBLIC_MODE") == "1", os.environ.get("AHN_REPORTING_ENABLED") == "1", owner_password, owner_allowed_ips, operator_name, report_retention_days_from_env(), public_bundle, public_bundle_since,
                   os.environ.get("AHN_RESPONSE_GZIP", "").strip() != "0",
                   vendor_aware_block=os.environ.get("AHN_VENDOR_AWARE_BLOCK", "").strip() != "0",
                   response_guide=os.environ.get("AHN_RESPONSE_GUIDE", "").strip() != "0",
                   public_board=public_board, public_board_since=public_board_since,
                   public_board_ahn_check=public_board_ahn_check,
                   lookup_feedback=os.environ.get("AHN_LOOKUP_FEEDBACK", "").strip() != "0",
                   proceed_without_failure=os.environ.get("AHN_PROCEED_WITHOUT_FAILURE", "").strip() != "0")
