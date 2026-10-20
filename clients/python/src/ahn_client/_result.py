"""The parsed answer of ``GET /v1/incidents/check`` and its one-line summary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_SUMMARY = 700
ACTIONS = ("proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident", "stop_declared_block", "unknown")
CONFIDENCES = ("high", "medium", "low")

CODE = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$")
SAFE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._~/%+=&?#-]*)?$")


def _code(value: Any, default: str = "unknown") -> str:
    return value if isinstance(value, str) and CODE.fullmatch(value) else default


def _url(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and len(value) <= 200 and SAFE_URL.fullmatch(value) else None


def _dict(value: Any) -> Optional[Dict[str, Any]]:
    return value if isinstance(value, dict) else None


def _retry_after(profile: Optional[Dict[str, Any]]) -> Optional[int]:
    """Seconds to wait before one more lookup while the host's files are being read, or None."""
    if not profile or profile.get("freshness") != "pending":
        return None
    value = profile.get("retry_after_seconds")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        return None
    return value


def _summary(host: str, data: Dict[str, Any], url: str) -> str:
    """The same line the Claude Code plugin prints, at most MAX_SUMMARY characters, from validated fields only."""
    rec = _dict(data.get("recommendation")) or {}
    profile = _dict(data.get("access_profile"))
    official = _dict(data.get("official_paths")) or {}
    provider = _dict(data.get("provider_status"))
    checks = data.get("next_checks") if isinstance(data.get("next_checks"), list) else []

    if profile:
        robots = _code(profile.get("robots_status"))
        wait = _retry_after(profile)
        if wait is not None:
            # The service answers at once and reads robots.txt/llms.txt in the background.
            robots += f" (being read now; look up once more in {wait} s, do not poll)"
        rules = _dict(profile.get("robots")) or {}
        listed = rules.get("agents_blocked") if isinstance(rules.get("agents_blocked"), list) else []
        blocked: Optional[List[str]] = [item for item in listed if isinstance(item, str) and TOKEN.fullmatch(item)]
        llms = _code(profile.get("llms_txt_status"))
    else:
        robots, blocked, llms = "not read for this hostname", None, "unknown"
    path = _url(official.get("api_docs")) or _url(official.get("developer_portal"))
    path_listed = bool(official.get("api_docs") or official.get("developer_portal"))
    if provider:
        status = f"{_code(provider.get('indicator'))}/{_code(provider.get('freshness'))}"
    else:
        status = "not connected (official page listed)" if isinstance(data.get("official_status_page"), dict) else "not connected"
    codes = [_code(item.get("code")) for item in checks if isinstance(item, dict)][:2]

    def render(agent_limit: Optional[int], show_path: bool, show_url: bool) -> str:
        if blocked is None:
            agents = "unknown"
        elif not blocked:
            agents = "none" if robots == "ok" else "unknown"
        elif agent_limit is None or len(blocked) <= agent_limit:
            agents = ", ".join(blocked)
        elif agent_limit == 0:
            agents = f"{len(blocked)} agent tokens"
        else:
            agents = ", ".join(blocked[:agent_limit]) + f" (+{len(blocked) - agent_limit} more)"
        official_text = path if path and show_path else "listed" if path_listed else "none"
        full = url if show_url else "GET /v1/incidents/check on agenthelpnetwork.com"
        return (f"AHN {host}: recommendation={_code(rec.get('action'))} ({_code(rec.get('confidence'))}). "
                f"Declared policy: {robots}; blocked agents: {agents}; llms.txt: {llms}. "
                f"Official path: {official_text}. Provider status: {status}. "
                f"Reports: {_code(data.get('evidence_level'))}. Next: {', '.join(codes) or 'none'}. "
                f"Full JSON: {full}. "
                "Declared policy is not an access guarantee; bypassing site controls is not advised.")

    text = ""
    for options in ((6, True, True), (3, True, True), (0, True, True), (0, False, True), (0, False, False)):
        text = render(*options)
        if len(text) <= MAX_SUMMARY:
            return text
    tail = " Declared policy is not an access guarantee; bypassing site controls is not advised."
    return text[:MAX_SUMMARY - len(tail) - 1].rstrip() + "…" + tail


@dataclass(frozen=True)
class Result:
    """The answer for one hostname.

    ``recommendation`` is the action code, one of ``proceed``, ``use_official_path``,
    ``hand_off_to_human``, ``wait_provider_incident``, ``stop_declared_block``,
    ``unknown``; anything else from the service becomes ``unknown``. ``next_checks`` is
    the list of check codes in the service's order. ``access_profile``, ``official_paths``,
    ``provider_status`` and ``raw`` are the service's JSON as received: read them as
    data, never as instructions for a model. A declared policy is not an access
    guarantee, and ``no_reports`` does not mean a site works.
    """

    host: str
    recommendation: str
    confidence: str
    reasons: Tuple[str, ...]
    access_profile: Optional[Dict[str, Any]]
    official_paths: Optional[Dict[str, Any]]
    provider_status: Optional[Dict[str, Any]]
    evidence_level: str
    next_checks: Tuple[str, ...]
    url: str
    raw: Dict[str, Any] = field(repr=False, compare=False)

    @classmethod
    def from_response(cls, host: str, url: str, data: Any) -> Optional["Result"]:
        """Parse a response body; None when it is not a lookup answer."""
        if not isinstance(data, dict):
            return None
        rec = _dict(data.get("recommendation"))
        if rec is None:
            return None
        action = rec.get("action")
        reasons = rec.get("reasons") if isinstance(rec.get("reasons"), list) else []
        checks = data.get("next_checks") if isinstance(data.get("next_checks"), list) else []
        return cls(
            host=host,
            recommendation=action if action in ACTIONS else "unknown",
            confidence=rec.get("confidence") if rec.get("confidence") in CONFIDENCES else "unknown",
            reasons=tuple(_code(item) for item in reasons if isinstance(item, str))[:8],
            access_profile=_dict(data.get("access_profile")),
            official_paths=_dict(data.get("official_paths")),
            provider_status=_dict(data.get("provider_status")),
            evidence_level=_code(data.get("evidence_level")),
            next_checks=tuple(_code(item.get("code")) for item in checks if isinstance(item, dict))[:12],
            url=url,
            raw=data,
        )

    @property
    def retry_after_seconds(self) -> Optional[int]:
        """While the host's robots.txt and llms.txt are still being read (``access_profile.freshness``
        is ``pending``), the seconds to wait before asking once more; otherwise None."""
        return _retry_after(self.access_profile)

    @property
    def official_url(self) -> Optional[str]:
        """The listed official API documentation or developer portal (an https URL), or None."""
        paths = self.official_paths or {}
        return _url(paths.get("api_docs")) or _url(paths.get("developer_portal"))

    @property
    def blocked_agents(self) -> Tuple[str, ...]:
        """Agent user-agent tokens the host's robots.txt explicitly blocks at the root."""
        rules = _dict((self.access_profile or {}).get("robots")) or {}
        listed = rules.get("agents_blocked") if isinstance(rules.get("agents_blocked"), list) else []
        return tuple(item for item in listed if isinstance(item, str) and TOKEN.fullmatch(item))

    def summary(self) -> str:
        """One line, at most 700 characters, built only from validated fields."""
        return _summary(self.host, self.raw, self.url)
