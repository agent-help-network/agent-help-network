"""The parsed answer of ``GET /v1/incidents/check`` and its one-line summary."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

MAX_SUMMARY = 700
ACTIONS = ("proceed", "use_official_path", "hand_off_to_human", "wait_provider_incident", "stop_declared_block", "unknown")
CONFIDENCES = ("high", "medium", "low")

# Short sentences for the next_checks codes that came with the vendor-aware robots rule (D48), shown in summary().
# They restate the contract's text (docs/api-contract.en.md, next_checks table); they decide nothing.
NEXT_CHECK_NOTES = {
    "robots_blocks_some_agents": "Note: robots.txt blocks some agents and the answer does not know yours; if your fetcher sends a blocked token, stop; else it is not about you.",
    "robots_blocks_not_your_agent": "Note: robots.txt blocks other agents, not the ones this answer was decided on; keep requests modest, not an access guarantee.",
    "robots_names_vendor_alias": "Note: a robots.txt block names your vendor under an unlisted name; not a declared block, not permission: prefer an official path.",
}

# D51: the nine values of next_action.do.
DO_VALUES = ("wait_for_provider", "hand_off_to_human", "use_official_path", "stop_automation", "proceed", "retry_lookup_once",
             "stop_if_listed", "lookup_apex_host", "no_clear_action")
# Only for these may the site's own Retry-After be merged into a wait (the answer is "come back later"). After a stop, a
# hand-off or an official path, a wait would read as "retry in an hour", so there nothing is merged and nothing is carried.
WAIT_DO = frozenset({"wait_for_provider", "retry_lookup_once", "no_clear_action", "proceed", "lookup_apex_host"})
MAX_WAIT = 3600  # the largest wait this client reports; a longer one is a reason to hand off
# The floor of each Retry-After band an adapter already holds (lookup code `retry_after`): the site said at least this long.
RETRY_BAND_FLOOR = {"none": 0, "lt_60": 0, "60_3600": 60, "gt_3600": MAX_WAIT}
SERVER_LINE = re.compile(r"^[ -~]{1,200}$")
HOSTNAME = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")

# Without a usable next_action the same gate is read from the verdict: a stop, a hand-off or an official path is not
# "come back later". Unknown values never merge (conservative).
WAIT_ACTIONS = frozenset({"proceed", "wait_provider_incident", "unknown"})

SAFETY_TAIL = "Declared policy is not an access guarantee; bypassing site controls is not advised."

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


def _seconds(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= MAX_WAIT else None


def site_over(value: Any) -> bool:
    """True when the site asked for more than an hour (clipped to 3600 by ``site_wait``)."""
    return value == "gt_3600" or (isinstance(value, int) and not isinstance(value, bool) and value > MAX_WAIT)


def site_wait(value: Any) -> Optional[int]:
    """The site's own Retry-After as whole seconds (an int, or one of the band codes an adapter holds), or None."""
    if isinstance(value, str):
        value = RETRY_BAND_FLOOR.get(value)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return min(value, MAX_WAIT)


@dataclass(frozen=True)
class NextAction:
    """The service's ``next_action`` (D51), validated field by field. ``do`` is one of ``DO_VALUES``. ``url`` is an https URL
    from the service's curated official paths or None. ``retry_after_seconds`` is when to repeat the same lookup once (not a
    polling interval) or None. ``stop_if_you_are`` lists agent tokens that, if your requests send one, mean a declared block
    (empty tuple when the service did not send it). ``lookup_host`` is the site's main domain whose policy covers this host."""

    do: str
    url: Optional[str] = None
    retry_after_seconds: Optional[int] = None
    stop_if_you_are: Tuple[str, ...] = ()
    lookup_host: Optional[str] = None

    @classmethod
    def parse(cls, value: Any) -> Optional["NextAction"]:
        data = _dict(value)
        if data is None or data.get("do") not in DO_VALUES:
            return None
        tokens = data.get("stop_if_you_are") if isinstance(data.get("stop_if_you_are"), list) else []
        lookup = data.get("lookup_host")
        return cls(
            do=data["do"],
            url=_url(data.get("url")),
            retry_after_seconds=_seconds(data.get("retry_after_seconds")),
            stop_if_you_are=tuple(item for item in tokens if isinstance(item, str) and TOKEN.fullmatch(item))[:16],
            lookup_host=lookup if isinstance(lookup, str) and HOSTNAME.fullmatch(lookup) else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        """The non-empty fields, for a compact JSON."""
        data: Dict[str, Any] = {"do": self.do}
        if self.url:
            data["url"] = self.url
        if self.retry_after_seconds is not None and self.do in WAIT_DO:  # not after a stop, a hand-off or an official path
            data["retry_after_seconds"] = self.retry_after_seconds
        if self.stop_if_you_are:
            data["stop_if_you_are"] = list(self.stop_if_you_are)
        if self.lookup_host:
            data["lookup_host"] = self.lookup_host
        return data


def _head(host: str, line: str, action: Optional[NextAction], site_retry_after: Any, merge: bool) -> str:
    """The service's sentence first, then the few next_action values it points at, as fixed text."""
    parts = [f"AHN {host}: {line}"]
    if action and action.url:
        parts.append(f"URL: {action.url}")
    if action and action.stop_if_you_are:
        parts.append("Stop if you are: " + ", ".join(action.stop_if_you_are[:6]) + ".")
    if action and action.lookup_host:
        parts.append(f"Look up: {action.lookup_host}.")
    site = site_wait(site_retry_after)
    if site and merge and site > ((action.retry_after_seconds or 0) if action else 0):
        parts.append("The site's own Retry-After is longer: wait more than an hour." if site_over(site_retry_after)
                     else f"The site's own Retry-After is longer: wait at least {site} s.")
    return " ".join(parts)


def _summary(host: str, data: Dict[str, Any], url: str, action_code: Optional[str] = None, limit: int = MAX_SUMMARY,
             compact: bool = False) -> str:
    """A line like the Claude Code plugin's, at most MAX_SUMMARY characters, from validated fields only. It differs from the
    plugin's in one way: for the three D48 robots codes (NEXT_CHECK_NOTES) a short sentence follows the link."""
    rec = _dict(data.get("recommendation")) or {}
    action_code = action_code or _code(rec.get("action"))
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
    note = next((NEXT_CHECK_NOTES[code] for code in codes if code in NEXT_CHECK_NOTES), "")

    def render(agent_limit: Optional[int], show_path: bool, show_url: bool, show_note: bool = False) -> str:
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
        official_text = path if path and show_path and not compact else "listed" if path_listed else "none"
        full = url if show_url else "GET /v1/incidents/check on agenthelpnetwork.com"
        return (f"{'' if compact else f'AHN {host}: '}recommendation={action_code} ({_code(rec.get('confidence'))}). "
                f"Declared policy: {robots}; blocked agents: {agents}; llms.txt: {llms}. "
                f"Official path: {official_text}. Provider status: {status}. "
                f"Reports: {_code(data.get('evidence_level'))}. Next: {', '.join(codes) or 'none'}. "
                f"Full JSON: {full}. "
                f"{note + ' ' if note and show_note else ''}"
                "Declared policy is not an access guarantee; bypassing site controls is not advised.")

    text = ""
    # Order of sacrifice. Without a note: agent names, then path, then link (as the plugin does). With a note, the note
    # is dropped before anything the reader needs to apply it: at least 3 blocked-agent names and the link stay, and the
    # path goes before the names shrink below 3. Only when that is not enough are the names given up, then the link.
    if note:
        order = ((6, True, True, True), (3, True, True, True), (6, True, True, False), (3, True, True, False),
                 (6, False, True, False), (3, False, True, False), (0, True, True, False), (0, False, True, False), (0, False, False, False))
    else:
        order = ((6, True, True, False), (3, True, True, False), (0, True, True, False), (0, False, True, False), (0, False, False, False))
    for options in order:
        text = render(*options)
        if len(text) <= limit:
            return text
    tail = " " + SAFETY_TAIL
    return text[:limit - len(tail) - 1].rstrip() + "…" + tail


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

    From 0.6.0 the service's own guidance is kept (D51): ``summary_line`` (its one-sentence advice, or None),
    ``next_action`` (a ``NextAction``, or None) and ``links`` (``feedback`` and ``changelog`` URLs, or None). A service
    that does not send them (an older one, or ``AHN_RESPONSE_GUIDE=0``) gives None and everything works as before.
    ``recommendation`` is always the service's verdict; ``next_action`` is kept beside it as sent.
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
    summary_line: Optional[str] = None
    next_action: Optional[NextAction] = None
    links: Optional[Dict[str, str]] = None

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
        line = data.get("summary_line")
        next_action = NextAction.parse(data.get("next_action"))
        links = _dict(data.get("links")) or {}
        links = {name: link for name in ("feedback", "changelog") if (link := _url(links.get(name)))}
        recommendation = action if action in ACTIONS else "unknown"
        return cls(
            host=host,
            recommendation=recommendation,
            confidence=rec.get("confidence") if rec.get("confidence") in CONFIDENCES else "unknown",
            reasons=tuple(_code(item) for item in reasons if isinstance(item, str))[:8],
            access_profile=_dict(data.get("access_profile")),
            official_paths=_dict(data.get("official_paths")),
            provider_status=_dict(data.get("provider_status")),
            evidence_level=_code(data.get("evidence_level")),
            next_checks=tuple(_code(item.get("code")) for item in checks if isinstance(item, dict))[:12],
            url=url,
            raw=data,
            summary_line=line if isinstance(line, str) and SERVER_LINE.fullmatch(line) else None,
            next_action=next_action,
            links=links or None,
        )

    @property
    def _merges_site(self) -> bool:
        """The one gate for every use of the site's Retry-After: True only when the answer means "come back later".
        With a ``next_action`` that is ``do`` in ``WAIT_DO``; with a ``next_action`` key this client cannot read (a ``do`` it does
        not know) never; with an older service (no key) the verdict as the service sent it decides: only ``proceed``,
        ``wait_provider_incident`` or an explicit ``unknown`` (``WAIT_ACTIONS``). A verdict this version does not know is
        normalised to ``unknown`` in ``recommendation`` but is read here from ``raw``, so it never merges."""
        if self.next_action is not None:
            return self.next_action.do in WAIT_DO
        if self.raw.get("next_action") is not None:
            return False
        sent = (_dict(self.raw.get("recommendation")) or {}).get("action")
        return isinstance(sent, str) and sent in WAIT_ACTIONS

    @property
    def retry_after_seconds(self) -> Optional[int]:
        """Seconds to wait before repeating the same lookup once, or None. With a ``next_action``: its
        ``retry_after_seconds`` (a provider incident: 900; a profile being read), only when ``do`` means "come back later"
        (``wait_for_provider``, ``retry_lookup_once``, ``no_clear_action``, ``proceed``, ``lookup_apex_host``); after a stop,
        a hand-off or an official path it is None. With an older service (no ``next_action``): the pending profile's value.
        ``lookup_again_seconds`` is the profile's value whatever the answer says."""
        if self.next_action is not None:
            return self.next_action.retry_after_seconds if self.next_action.do in WAIT_DO else None
        return _retry_after(self.access_profile)

    @property
    def lookup_again_seconds(self) -> Optional[int]:
        """While the host's robots.txt and llms.txt are being read (``access_profile.freshness`` is ``pending``), the seconds
        before the one repeat lookup the service asks for; otherwise None. It is the profile's own value and is not combined
        with the site's Retry-After; it stays set after a hand-off or an official path (0.5.0 gave it as
        ``retry_after_seconds`` there too)."""
        return _retry_after(self.access_profile)

    def wait_seconds(self, site_retry_after: Any = None) -> Optional[int]:
        """``retry_after_seconds`` combined with the site's own Retry-After: the larger of the two, at most 3600. Pass the
        site's value as whole seconds, or as the band code (``60_3600``, ``gt_3600``) an adapter holds. The merge happens
        here only; the site's value is never sent to the service and never changes its answer. After a stop, a hand-off or
        an official path (by ``next_action.do``, or by ``recommendation`` for an older service) there is nothing to wait for
        and the result is None."""
        if not self._merges_site:
            return None
        values = [value for value in (self.retry_after_seconds, site_wait(site_retry_after)) if value]
        return max(values) if values else None

    @property
    def remember(self) -> Optional[str]:
        """A fixed one-line note for the next task on this host, made from ``next_action`` alone (no new judgement), or None
        when there is nothing worth remembering: no ``next_action``, or a state that passes (``wait_for_provider``,
        ``hand_off_to_human``, ``retry_lookup_once``, ``no_clear_action``)."""
        action = self.next_action
        if action is None:
            return None
        host = self.host
        if action.do == "use_official_path":
            return f"For {host}, start with the official path: {action.url}" if action.url else f"For {host}, start with the official path the service lists."
        if action.do == "stop_automation":
            return f"For {host}, the site declares agents blocked: do not automate its pages."
        if action.do == "proceed":
            return f"For {host}, no declared block was found for the agents checked: continue with modest requests (not an access guarantee)."
        if action.do == "stop_if_listed":
            names = ", ".join(action.stop_if_you_are[:6])
            return f"For {host}, if your requests send {names}, do not automate its pages." if names else f"For {host}, if your agent is among those robots.txt blocks, do not automate its pages."
        if action.do == "lookup_apex_host" and action.lookup_host:
            return f"For {host}, look up {action.lookup_host} for the site-wide policy."
        return None

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

    @property
    def crawl_delay_seconds(self) -> Optional[float]:
        """The ``Crawl-delay`` of the host's ``*`` group in robots.txt (``access_profile.robots.wildcard_crawl_delay``), in
        seconds between requests, rounded to one decimal; None when absent, not a number, below 0.1 or above 3600. Information about the site's
        request, not a permission and not part of the verdict."""
        rules = _dict((self.access_profile or {}).get("robots")) or {}
        value = rules.get("wildcard_crawl_delay")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 3600:
            return None
        value = round(float(value), 1)  # one decimal: no exponent notation, no 1e-05
        return value if value >= 0.1 else None

    def summary(self, site_retry_after: Any = None) -> str:
        """One line, at most 700 characters, built only from validated fields. With a service ``summary_line`` it comes
        first (then the ``next_action`` values it points at and the detail line, as far as it fits); without, the line is the
        one earlier versions gave. ``site_retry_after`` (seconds or a band code) only adds a note when the site asks for a
        longer wait than the service. When robots.txt asks for a ``Crawl-delay`` the line ends with "robots.txt asks N s
        between requests"."""
        delay = self.crawl_delay_seconds
        if delay is None:
            return self._summary_line(site_retry_after, MAX_SUMMARY)
        note = f"robots.txt asks {delay:g} s between requests."
        return self._summary_line(site_retry_after, MAX_SUMMARY - len(note) - 1) + " " + note

    def _summary_line(self, site_retry_after: Any, limit: int) -> str:
        action_code = self.recommendation if self.recommendation in ACTIONS else None
        if self.summary_line is None:
            return _summary(self.host, self.raw, self.url, action_code, limit)
        head = _head(self.host, self.summary_line, self.next_action, site_retry_after, self._merges_site)
        room = limit - len(head) - 1
        if room >= 200:
            return head + " " + _summary(self.host, self.raw, self.url, action_code, room, compact=True)
        # No room for the detail line: keep the closing caution, cut the head.
        tail = " " + SAFETY_TAIL
        if len(head) + len(tail) <= limit:
            return head + tail
        return head[:limit - len(tail) - 1].rstrip() + "…" + tail
