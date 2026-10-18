from __future__ import annotations

import os
from ipaddress import ip_address
from dataclasses import dataclass, field


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
        return cls(database_url, key_pepper, os.environ.get("AHN_TEST_MODE") == "1", os.environ.get("AHN_COLLECT_LOOKUP_METRICS") == "1", limit, os.environ.get("AHN_COLLECT_REQUEST_METRICS") == "1", os.environ.get("AHN_PUBLIC_MODE") == "1", os.environ.get("AHN_REPORTING_ENABLED") == "1", owner_password, owner_allowed_ips)
