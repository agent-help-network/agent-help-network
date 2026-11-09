"""D108: say, for a blocked step, which layer to look at first, whether a person has to act, and what other agents saw.

``build()`` is a pure function: it takes the answer of ``service.check()`` (already serialized as ``GET /v1/incidents/check``
serializes it), the public platform table, and two lists of stored changes, and returns the body of ``GET /v1/why``.
``explain()`` is the only function that reads anything: one ``check()`` call and one short read of two tables.

Rules this module keeps (tests/test_explain_*.py fix them):

* It never changes the lookup answer. ``server`` is copied from ``check()``; nothing here decides ``recommendation``.
* Every sentence is a fixed template (ahn/explain_text.py) whose blanks come from AHN's own data, the curated platform table
  or a fixed phrase table. Nothing the caller or a site wrote is put into a sentence.
* "Look first" is the first layer, in a fixed order, that has evidence or that the caller's own answer points to. It is
  never called the cause. A person is "needed" only by the fixed rules P-1..P-7; otherwise it is "not indicated", never "no".
* Nothing the caller chose (platform, symptom, environment) is stored; only the lookup itself is counted, as for ``check``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from . import explain_text as X
from .models import CauseBasis
from .response_guide import unresolved_incidents
from .why_models import CHECK_TEXT_MAX, HANDOFF_TEXT_MAX, LAYER_TEXT_MAX, SCHEMA, SHORT_TEXT_MAX

LOG = logging.getLogger(__name__)

PLATFORMS = ("claude_desktop_browser", "claude_in_chrome", "claude_code", "chatgpt_agent", "meta_muse", "gemini",
             "perplexity_comet", "other")
AGENT_ENVS = ("cloud_browser", "on_device_browser", "not_sure")
EXTERNAL_BROWSER = ("yes_same_account", "no", "not_tried")
STOP_POINTS = ("before_visit", "page_blank_or_blocked", "human_verification", "company_login", "site_login",
               "after_login_action_fails", "tools_off_page_visible", "timeout_or_5xx", "not_sure", "extra_verification")
SCREEN_MESSAGES = ("claude_org_policy_banner", "claude_asks_each_action", "claude_cannot_read_site", "device_not_compliant",
                   "access_denied", "human_check", "sign_in_required", "too_many_requests", "page_not_loading", "none_other",
                   "approval_on_other_device")
SINCE_KINDS = ("today_first", "worked_before", "not_sure")
TASK_CONTEXTS = ("my_account_consumer", "my_account_work", "public_read", "other")
ERROR_CLASSES = ("site_access", "login_auth", "human_verification", "service_error")
FAILURE_STAGES = ("before_target_response", "response_observed", "access_denial_observed", "unknown")
LANGS = ("en", "ko")
SINCE_MAX_DAYS = 90
WINDOW_DAYS = 7
CLAUDE_BROWSERS = ("claude_desktop_browser", "claude_in_chrome")
# A screen message that only one family of products shows; a match needs the caller's platform to be one of them.
MESSAGE_PLATFORMS = {
    "claude_org_policy_banner": CLAUDE_BROWSERS,
    "claude_asks_each_action": CLAUDE_BROWSERS,
    "claude_cannot_read_site": ("claude_desktop_browser",),
}
PLATFORM_NAMES = {"claude_code": "Claude Code"}  # a product the public table has no row for
SIGN_IN_STOPS = ("site_login", "company_login", "extra_verification")
SIGN_IN_MESSAGES = ("sign_in_required", "device_not_compliant", "approval_on_other_device")
ACCOUNT_STOPS = ("site_login", "company_login", "after_login_action_fails", "extra_verification")
# D110: a stop the caller chose is that layer's own answer, so it is the first place to look unless the site layer has direct
# evidence (a stored cause, a robots.txt block, a provider incident). AHN's home-page check or a recorded change alone never
# puts the site layer ahead of it.
PLATFORM_STOPS = ("before_visit", "tools_off_page_visible")
DIRECT_SITE_LAYERS = ("L1", "L1a", "L1d")
# D110: without a stop_point, the handoff names the stop from the codes the caller measured (error_class first, then failure_stage).
STOP_FROM_ERROR = {"site_access": "page_blank_or_blocked", "human_verification": "human_verification", "service_error": "timeout_or_5xx",
                   "login_auth": "site_login"}
STOP_FROM_STAGE = {"before_target_response": "timeout_or_5xx"}
# D110: the stored changes that are AHN's own home-page check (what AHN's request got), not a file the site declared.
HOME_FIELDS = ("home_outcome", "bot_vendor")

# Symptom -> the codes the lookup already understands (rows 1-10 of the stop-point table; the 11th row is below).
STOP_MAP: dict[str, tuple[str | None, str | None, str | None]] = {
    "before_visit": (None, "before_target_response", "site_open"),
    "page_blank_or_blocked": ("site_access", None, "site_open"),            # failure_stage only with the message access_denied
    "human_verification": ("human_verification", None, "human_verification"),
    "company_login": ("login_auth", None, "login"),
    "site_login": ("login_auth", None, "login"),
    "after_login_action_fails": (None, "response_observed", "service_use"),
    "tools_off_page_visible": (None, None, "other"),
    "timeout_or_5xx": ("service_error", None, "other"),
    "not_sure": (None, None, "other"),                                       # corrected by the message, see NOT_SURE_MAP
    "extra_verification": ("login_auth", None, "login"),
}
NOT_SURE_MAP: dict[str, tuple[str | None, str | None, str | None]] = {
    "human_check": ("human_verification", None, "other"),
    "sign_in_required": ("login_auth", None, "other"),
    "access_denied": ("site_access", "access_denial_observed", "other"),
    "page_not_loading": ("service_error", None, "other"),
    "approval_on_other_device": ("login_auth", None, "login"),             # the 11th row
}

OUTCOME_ORDER = {"passed": 0, "blocked": 1, "error": 2, "abandoned": 3}
SITE_EVIDENCE_HINTS = frozenset({"home_now_gated", "persistent_refusal", "provider_incident", "site_errors", "rate_limited",
                                 "robots_now_disallows", "terms_signal_changed", "new_bot_defense", "others_failing_same_hour"})
QUOTE_TAG_ORDER = ("banner_text", "blocked_category", "admin_policy", "confirm_required")
BASE_ORDER_R1 = ("L2", "L3a", "L3b", "L1b", "L1c", "L4", "L1", "L1d", "L1e", "L1a")
BASE_ORDER_SITE = ("L1", "L1d", "L1a", "L1e", "L1b", "L1c", "L4", "L2", "L3a", "L3b")
LAYER_GROUP = {"L1": "site", "L1a": "site", "L1b": "site", "L1c": "site", "L1d": "site", "L1e": "site", "L2": "platform",
               "L3a": "account", "L3b": "agent_env", "L4": "task"}
GROUPS = ("site", "platform", "account", "agent_env", "task")
HOME_NORMAL = ("reachable", "redirected_same_host")
ROW_LIMIT = 10
CHECK_LIMIT = 6
OTHERS_LIMIT = 5
CAUSE_BASES = frozenset(item.value for item in CauseBasis)


@dataclass(frozen=True)
class Inputs:
    """The accepted request. Only codes: a value outside its list was already dropped (and is absent from the echo)."""
    site_host: str
    lang: str = "en"
    host_normalized: str | None = None
    agent_platform: str | None = None
    agent_env: str | None = None
    external_browser: str | None = None
    stop_point: str | None = None
    screen_message: str | None = None
    since_kind: str | None = None
    since_date: date | None = None
    since_clamped: bool = False
    task_context: str | None = None
    error_class: str | None = None
    failure_stage: str | None = None
    agent_vendor: str | None = None
    # D110: the other lookup codes, kept only to echo them and to rebuild links.self. They reach the lookup through its signal.
    task_step: str | None = None
    http_status: int | None = None
    challenge: str | None = None
    retry_after: str | None = None
    via: str | None = None
    ahn_test: bool = False
    # D111: names (never values) of the query parameters that were sent but are not among the route's own, sorted. See clean_ignored_params.
    ignored_params: tuple[str, ...] = ()


IGNORED_PARAM_NAME = re.compile(r"[A-Za-z0-9_.-]{1,40}")
IGNORED_PARAMS_MAX = 10


def clean_ignored_params(names: Any) -> tuple[str, ...]:
    """D111: the parameter names that were received but not accepted, as a sorted, de-duplicated tuple of at most IGNORED_PARAMS_MAX
    names. A name that is not a short plain token (letters, digits, underscore, dot, hyphen; 1 to 40 characters) is left out, so
    nothing but a plain name can come back in the answer. Values are never passed in."""
    if not isinstance(names, (list, tuple, set, frozenset)):
        return ()
    found = {name for name in names if isinstance(name, str) and IGNORED_PARAM_NAME.fullmatch(name)}
    return tuple(sorted(found))[:IGNORED_PARAMS_MAX]


def clean_host(raw: str | None) -> tuple[str, bool]:
    """The hostname inside whatever was pasted, and whether anything else (path, query, user, port) was cut off.

    Raises ValueError when no valid hostname is left. The cut part is never kept, logged or echoed."""
    from .models import normalize_host_input, valid_host

    text = (raw or "").strip()
    cut = False
    low = text.lower()
    for scheme in ("https://", "http://"):
        if low.startswith(scheme):
            text = text[len(scheme):]
            break
    for sep in "/?#":
        index = text.find(sep)
        if index >= 0:
            rest = text[index:]
            text = text[:index]
            cut = cut or rest != "/"
    if "@" in text:
        text = text.rsplit("@", 1)[1]
        cut = True
    head, _, port = text.rpartition(":")
    if head and port.isascii() and port.isdigit():
        text = head
        cut = True
    host = normalize_host_input(text)
    valid_host(host)
    return host, cut


def _pick(value: Any, allowed: tuple[str, ...]) -> str | None:
    return value if isinstance(value, str) and value in allowed else None


def parse_inputs(site_host: str, *, today: date, lang: str | None = "en", agent_platform: Any = None, agent_env: Any = None,
                 external_browser: Any = None, stop_point: Any = None, screen_message: Any = None, since_kind: Any = None,
                 since_date: Any = None, task_context: Any = None, error_class: Any = None, failure_stage: Any = None,
                 agent_vendor: str | None = None, task_step: Any = None, http_status: Any = None, challenge: Any = None,
                 retry_after: Any = None, via: Any = None, ahn_test: bool = False, ignored_params: Any = ()) -> Inputs:
    """Make the request into ``Inputs``. A host that is not valid raises ValueError; every other value that is not on its
    list is ignored (the lookup still answers), exactly as the optional signal codes of the lookup are."""
    from .lookup_signals import lenient_codes
    from .models import STEP, Via
    from .response_guide import apex_host
    from .service import policy_host

    host, cut = clean_host(site_host)
    normalized = "path_dropped" if cut else None
    apex = apex_host(host)
    if apex and apex != policy_host(host):  # a "www." alias is answered from its apex by the lookup itself, nothing to say
        host, normalized = apex, "apex"
    kind = _pick(since_kind, SINCE_KINDS)
    since: date | None = None
    clamped = False
    if kind == "worked_before" and isinstance(since_date, (str, date)):
        try:
            since = since_date if isinstance(since_date, date) else date.fromisoformat(since_date.strip())
        except ValueError:
            since = None
        if since is not None and since > today:
            since = None
        elif since is not None and since < today - timedelta(days=SINCE_MAX_DAYS):
            since, clamped = today - timedelta(days=SINCE_MAX_DAYS), True
    codes = lenient_codes(http_status, challenge, retry_after, via)
    return Inputs(
        task_step=task_step if isinstance(task_step, str) and STEP.fullmatch(task_step) else None,
        http_status=codes["http_status"], challenge=codes["challenge"], retry_after=codes["retry_after"],
        via=via if isinstance(via, str) and via in Via._value2member_map_ else None, ahn_test=ahn_test is True,
        ignored_params=clean_ignored_params(ignored_params), site_host=host, lang=lang if lang in LANGS else "en", host_normalized=normalized,
        agent_platform=_pick(agent_platform, PLATFORMS), agent_env=_pick(agent_env, AGENT_ENVS),
        external_browser=_pick(external_browser, EXTERNAL_BROWSER), stop_point=_pick(stop_point, STOP_POINTS),
        screen_message=_pick(screen_message, SCREEN_MESSAGES), since_kind=kind, since_date=since, since_clamped=clamped,
        task_context=_pick(task_context, TASK_CONTEXTS), error_class=_pick(error_class, ERROR_CLASSES),
        failure_stage=_pick(failure_stage, FAILURE_STAGES), agent_vendor=agent_vendor)


def map_symptoms(inputs: Inputs) -> tuple[str | None, str | None, str | None]:
    """(error_class, failure_stage, task_step) for ``service.check``. A code the caller measured and sent wins over the
    mapping of what a person chose."""
    stop, message = inputs.stop_point, inputs.screen_message
    if stop is None and message is not None:
        stop = "not_sure"
    error_class = failure_stage = task_step = None
    if stop is not None:
        error_class, failure_stage, task_step = STOP_MAP[stop]
        if stop == "page_blank_or_blocked" and message == "access_denied":
            failure_stage = "access_denial_observed"
        if stop == "not_sure" and message in NOT_SURE_MAP:
            error_class, failure_stage, task_step = NOT_SURE_MAP[message]
        if stop != "not_sure" and message == "approval_on_other_device" and error_class is None:
            error_class, task_step = "login_auth", "login"
    return inputs.error_class or error_class, inputs.failure_stage or failure_stage, inputs.task_step or task_step


# --- small helpers ------------------------------------------------------------------------------------------------------
def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _day(value: Any) -> str | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    parsed = _dt(value)
    if parsed is not None:
        return parsed.date().isoformat()
    return value[:10] if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", value) else None


def _time(value: Any) -> str | None:
    parsed = _dt(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%MZ") if parsed else None


def _url(value: Any, limit: int = 200) -> str | None:
    return value if isinstance(value, str) and value.startswith("https://") and len(value) <= limit and " " not in value else None


def _get(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _safe_name(value: Any) -> str | None:
    """A curated source name (a provider's name) as a blank value, only when it is short printable text."""
    return value if isinstance(value, str) and 1 <= len(value) <= 60 and re.fullmatch(r"[ -~]+", value) else None


def _fill(template_id: str, lang: str, cap: int, **values: Any) -> str | None:
    """The template with its blanks filled, or None when a blank has no value or the text would not fit (never cut)."""
    template = X.TEMPLATES[template_id][lang]
    for name in re.findall(r"{(\w+)}", template):
        if values.get(name) is None:
            return None
    text = template.format(**values)
    if len(text) > cap:
        LOG.warning("why_item_dropped id=%s reason=too_long", template_id)
        return None
    return text


def _words(table: dict[str, dict[str, str]], key: Any, lang: str) -> str | None:
    entry = table.get(key) if isinstance(key, str) else None
    return entry[lang] if entry else None


# --- the context every part reads --------------------------------------------------------------------------------------
class _Ctx:
    def __init__(self, inputs: Inputs, check: dict, view: dict | None, changes: list, doc_events: list, today: date) -> None:
        self.i, self.check, self.today, self.lang = inputs, check, today, inputs.lang
        self.platform = inputs.agent_platform
        self.entry = None
        if view and self.platform:
            for item in view.get("platforms", []):
                if item.get("platform") == self.platform:
                    self.entry = item
        self.platform_known = self.platform not in (None, "other")
        self.docs = list(self.entry["docs"]) if self.entry else []
        self.window_from = inputs.since_date or (today - timedelta(days=WINDOW_DAYS))
        self.changes = [c for c in changes if self._in_window(c.get("at"))]
        urls = {d.get("url") for d in self.docs}
        self.doc_events = [e for e in doc_events if e.get("url") in urls and self._in_window(e.get("changed_at"))]
        self.next_action = check.get("next_action") or {}
        self.do = self.next_action.get("do")
        self.action = _get(check, "recommendation", "action")
        stop, message = inputs.stop_point, inputs.screen_message
        self.stop, self.message = stop, message
        self.message_matches = bool(message in MESSAGE_PLATFORMS and self.platform in MESSAGE_PLATFORMS[message])
        self.message_other = bool(message in MESSAGE_PLATFORMS and self.platform_known and not self.message_matches)
        self.base = {"yes_same_account": "R1", "no": "R4"}.get(inputs.external_browser or "", "R5")
        self.r7 = stop in ("before_visit", "tools_off_page_visible") or self.message_matches
        self.r2 = stop == "company_login" or message == "device_not_compliant"
        self.r3 = inputs.task_context in ("my_account_consumer", "my_account_work") or stop in ACCOUNT_STOPS
        self.r6 = inputs.since_kind == "worked_before"
        self.sign_in_step = stop in SIGN_IN_STOPS or message in SIGN_IN_MESSAGES
        self.verify = stop == "extra_verification" or message == "approval_on_other_device"
        self.position = "after_checks" if (self.r3 and self.do in ("use_official_path", "proceed") and not self.sign_in_step) else "first"
        # D110: the stop the handoff names. Without a chosen stop it comes from the codes the caller measured; only the handoff reads it.
        self.stop_named = stop or STOP_FROM_ERROR.get(inputs.error_class or "") or STOP_FROM_STAGE.get(inputs.failure_stage or "")
        self.status_check = inputs.task_step == "status_check"
        # D110: the layer the caller's own stop points to (None when the stop names no layer).
        self.stop_group = "platform" if stop in PLATFORM_STOPS else "account" if stop in ACCOUNT_STOPS else None
        self.open_self = False  # set by _person: a public read handed to the person only because AHN's reads did not reach it

    def _in_window(self, value: Any) -> bool:
        when = _dt(value)
        return when is not None and self.window_from <= when.date() <= self.today

    # platform words and quotes
    @property
    def pname(self) -> str:
        if self.entry:
            return self.entry["display_name"]
        if self.platform in PLATFORM_NAMES:
            return PLATFORM_NAMES[self.platform]
        return X.DEFAULT_PLATFORM[self.lang][0]

    @property
    def pname_cap(self) -> str:
        if self.entry or self.platform in PLATFORM_NAMES:
            return self.pname
        return X.DEFAULT_PLATFORM[self.lang][1]

    @property
    def platform_relevant(self) -> bool:
        """The product's own read pages say something about sites it limits, asks about, or does not act on."""
        return bool(self.quotes("blocked_category", "prohibited_action", "admin_policy", "site_side", "confirm_required", "banner_text"))

    def quotes(self, *tags: str) -> list[tuple[dict, dict]]:
        """(doc, quote) pairs of read pages, in table order, for the tags."""
        return [(d, q) for d in self.docs if d.get("status") == "read" for q in d.get("quotes", []) if q.get("tag") in tags]

    def first_quote(self, *tags: str) -> tuple[dict, dict] | None:
        found = self.quotes(*tags)
        return found[0] if found else None


def _source(kind: str, url: Any = None, checked: Any = None, admin: bool = False) -> dict:
    source: dict[str, Any] = {"kind": kind}
    if _url(url):
        source["url"] = url
    if _day(checked):
        source["checked_at"] = _day(checked)
    if admin:
        source["admin_deployment_doc"] = True
    return source


def _row(ctx: _Ctx, layer: str, state: str, template_id: str, text: str | None, source: dict | None = None) -> dict | None:
    if text is None:
        return None
    row: dict[str, Any] = {"layer": layer, "state": state, "template_id": template_id, "text": text}
    if source and len(source) > 1:
        row["source"] = source
    if ctx.base == "R5":
        row["undetermined"] = True
    return row


# --- layer rows ---------------------------------------------------------------------------------------------------------
def _site_rows(ctx: _Ctx) -> list[dict]:
    lang, rows = ctx.lang, []
    check = ctx.check
    host = ctx.i.site_host

    cause = check.get("cause")
    if cause:
        state = "evidence" if cause.get("hint") in SITE_EVIDENCE_HINTS else "background"
        rows.append(_row(ctx, "L1", state, "S-L1-CAUSE", _fill("S-L1-CAUSE", lang, LAYER_TEXT_MAX, host=host, cause_line=cause.get("line")),
                         _source("ahn_read")))

    # L1a: what the site's robots.txt declares, read by AHN
    profile = check.get("access_profile") or {}
    status, fetched = profile.get("robots_status"), profile.get("fetched_at")
    robots = profile.get("robots") or {}
    date_text = _day(fetched)
    declared_url = _get(profile, "source_urls", "robots")
    background_only = ctx.base == "R1" or ctx.r3
    if status == "ok" and date_text:
        if robots.get("wildcard_root_allowed") is False:
            tid, state = "S-L1A-BLOCK", "evidence"
        elif robots.get("agents_blocked"):
            tid, state = "S-L1A-SOME", "background"
        else:
            tid, state = "S-L1A-ALLOW", "background"
        text = _fill(tid, lang, LAYER_TEXT_MAX, host=host, date=date_text, token_scope=X.TOKEN_SCOPE[lang])
        stale = _fill("S-L1A-STALE", lang, 200, date=date_text) if profile.get("freshness") == "stale" else None
        if text and stale and len(text) + 1 + len(stale) <= LAYER_TEXT_MAX:
            text = f"{text} {stale}"
        rows.append(_row(ctx, "L1a", "background" if background_only else state, tid, text, _source("site_declaration", declared_url, fetched)))
    elif status == "absent" and date_text:
        rows.append(_row(ctx, "L1a", "background", "S-L1A-ABSENT", _fill("S-L1A-ABSENT", lang, LAYER_TEXT_MAX, host=host, date=date_text),
                         _source("site_declaration", declared_url, fetched)))
    elif not background_only:  # when the answer rests on the account or on the usual browser, an unread file needs no row of its own
        reading = _words(X.ROBOTS_STATE, status if status in X.ROBOTS_STATE else "none", lang)
        rows.append(_row(ctx, "L1a", "not_seen", "S-L1A-UNREAD", _fill("S-L1A-UNREAD", lang, LAYER_TEXT_MAX, state=reading), _source("ahn_read")))
    if ctx.base == "R1":
        rows.append(_row(ctx, "L1a", "background", "S-L1A-BACKGROUND-BROWSER", X.TEMPLATES["S-L1A-BACKGROUND-BROWSER"][lang]))
    if ctx.r3:
        rows.append(_row(ctx, "L1a", "background", "S-L1A-BACKGROUND-ACCOUNT", X.TEMPLATES["S-L1A-BACKGROUND-ACCOUNT"][lang]))

    # L1b: AHN's own home-page check (D67), as codes
    home = check.get("ahn_check") or {}
    if home.get("status") == "measured" and home.get("outcome") and _day(home.get("measured_at")):
        vendor = _get(home, "bot_defense", "vendor")
        vendor_name = X.BOT_VENDOR.get(vendor) if vendor else None
        vendor_part = X.VENDOR_PART[lang].format(vendor=vendor_name) if vendor_name else ""
        # A normal home page with only a bot-defense marker is background. On a sign-in step, a sign-in page or an
        # off-site redirect at the home page is what such a step looks like, so it is background there as well.
        sign_in_home = ctx.sign_in_step and home["outcome"] in ("login_wall", "redirected_offsite")
        state = "background" if home["outcome"] in HOME_NORMAL or sign_in_home else "evidence"
        rows.append(_row(ctx, "L1b", state, "S-L1B-AHN-HOME", _fill(
            "S-L1B-AHN-HOME", lang, LAYER_TEXT_MAX, host=host, date=_day(home["measured_at"]),
            outcome_words=_words(X.HOME_OUTCOME, home["outcome"], lang), vendor_part=vendor_part), _source("ahn_read", None, home["measured_at"])))
    elif ctx.stop == "human_verification":
        rows.append(_row(ctx, "L1b", "not_seen", "S-L1B-NOSEE", X.TEMPLATES["S-L1B-NOSEE"][lang]))

    # L1c: how the site's API signs in, only when a curated page says so; AHN never opens a sign-in page
    auth = _get(check, "official_paths", "access", "auth")
    if isinstance(auth, list) and auth and ctx.stop in SIGN_IN_STOPS:
        auth_words = _words(X.AUTH_WORDS, auth[0], lang)
        docs_url = _get(check, "official_paths", "api_docs")
        rows.append(_row(ctx, "L1c", "background", "S-L1C-API-AUTH", _fill("S-L1C-API-AUTH", lang, LAYER_TEXT_MAX, url=_url(docs_url), auth=auth_words),
                         _source("site_declaration", docs_url)))

    # L1d: the provider's status feed
    provider = check.get("provider_status") or {}
    name = _safe_name(provider.get("source_name"))
    indicator = provider.get("indicator")
    read_at = _time(provider.get("fetched_at"))
    if name and read_at and indicator in X.INDICATOR:
        rows.append(_row(ctx, "L1d", "evidence", "S-L1D-ACTIVE-NOTIME", _fill(
            "S-L1D-ACTIVE-NOTIME", lang, LAYER_TEXT_MAX, provider=name, indicator=_words(X.INDICATOR, indicator, lang), time2=read_at),
            _source("provider_status", provider.get("source_url"), provider.get("fetched_at"))))
    elif name and read_at and unresolved_incidents(provider):
        # D110 (D100 on this path): an operational indicator with an unresolved incident listed is never "no announced incident".
        rows.append(_row(ctx, "L1d", "evidence", "S-L1D-UNRESOLVED", _fill("S-L1D-UNRESOLVED", lang, LAYER_TEXT_MAX, provider=name, time=read_at),
                         _source("provider_status", provider.get("source_url"), provider.get("fetched_at"))))
    elif name and read_at and indicator == "none" and provider.get("freshness") == "fresh":
        rows.append(_row(ctx, "L1d", "background", "S-L1D-CLEAR", _fill("S-L1D-CLEAR", lang, LAYER_TEXT_MAX, provider=name, time=read_at),
                         _source("provider_status", provider.get("source_url"), provider.get("fetched_at"))))
    else:
        rows.append(_row(ctx, "L1d", "not_seen", "S-L1D-NONE", X.TEMPLATES["S-L1D-NONE"][lang]))

    # L1e: recorded changes of the declared policy or the home-page check (D75)
    shown = 0
    for change in sorted(ctx.changes, key=lambda c: _dt(c.get("at")) or datetime.min.replace(tzinfo=timezone.utc), reverse=True):
        sentence = _change_sentence(ctx, change)
        if sentence and shown < 2:
            # D110: a home-page check field that wavered in the window is background: AHN's own results moved, not the site.
            state = "background" if change.get("field") in HOME_FIELDS and _flapped(ctx, change.get("field")) else "evidence"
            rows.append(_row(ctx, "L1e", state, sentence[0], sentence[1], _source("ahn_read", None, change.get("at"))))
            shown += 1
    if not shown and ctx.r6:
        rows.append(_row(ctx, "L1e", "background", "S-L1E-UNCHANGED", _fill(
            "S-L1E-UNCHANGED", lang, LAYER_TEXT_MAX, host=host, date=ctx.window_from.isoformat(), date2=ctx.today.isoformat())))
    return [r for r in rows if r]


def _change_words(change: dict, lang: str) -> tuple[str, str, str] | None:
    """(field_words, from_words, to_words) for a stored change, or None when any code is not in the fixed tables."""
    from . import host_access_changes as hac

    field, before, after = change.get("field"), change.get("from_code"), change.get("to_code")
    if not (isinstance(field, str) and isinstance(before, str) and isinstance(after, str)):
        return None
    if field.startswith(hac.VERDICT_PREFIX):
        token = field[len(hac.VERDICT_PREFIX):]
        if token not in hac.verdict_tokens():
            return None
        token_words = X.OUR_TOKEN_WORDS[lang] if token == hac.OUR_TOKEN else token
        field_words = X.ROBOTS_VERDICT_FIELD[lang].format(token_words=token_words)
        table = X.CODE_WORDS["robots_verdict"]
    else:
        field_words = _words(X.FIELD_WORDS, field, lang)
        table = X.CODE_WORDS.get(field, {})
    from_words, to_words = _words(table, before, lang), _words(table, after, lang)
    return (field_words, from_words, to_words) if field_words and from_words and to_words else None


def _flapped(ctx: _Ctx, field: Any) -> bool:
    """D110: the values of one home-page check field went back to an earlier value within the window (for example a marker
    appeared, went and came back). AHN's own check then wavered, so no single change of that field shows the site changed."""
    seq = sorted((c for c in ctx.changes if c.get("field") == field), key=lambda c: _dt(c.get("at")) or datetime.min.replace(tzinfo=timezone.utc))
    if len(seq) < 2:
        return False
    values = [seq[0].get("from_code")] + [c.get("to_code") for c in seq]
    return len(set(values)) < len(values)


def _home_change_text(ctx: _Ctx, change: dict, day: str, cap: int) -> tuple[str, str | None] | None:
    """D110: a change of AHN's own home-page check, said as what AHN's request got, never as a setting of the site."""
    field, before, after = change.get("field"), change.get("from_code"), change.get("to_code")
    lang, host = ctx.lang, ctx.i.site_host
    if field == "bot_vendor":
        if not (isinstance(before, str) and isinstance(after, str)):
            return None
        old, new = X.BOT_VENDOR.get(before), X.BOT_VENDOR.get(after)
        if before == "none" and new:
            tid, text = "S-L1E-HOME-CHALLENGE-ON", _fill("S-L1E-HOME-CHALLENGE-ON", lang, cap, date=day, host=host, vendor=new)
        elif after == "none" and old:
            tid, text = "S-L1E-HOME-CHALLENGE-OFF", _fill("S-L1E-HOME-CHALLENGE-OFF", lang, cap, date=day, host=host, vendor=old)
        elif old and new:
            tid, text = "S-L1E-HOME-CHALLENGE-SWAP", _fill("S-L1E-HOME-CHALLENGE-SWAP", lang, cap, date=day, host=host, from_words=old, to_words=new)
        else:
            return None
    else:
        table = X.CODE_WORDS["home_outcome"]
        from_words, to_words = _words(table, before, lang), _words(table, after, lang)
        if not (from_words and to_words):
            return None
        tid, text = "S-L1E-HOME-OUTCOME", _fill("S-L1E-HOME-OUTCOME", lang, cap, date=day, host=host, from_words=from_words, to_words=to_words)
    return (tid, text) if text else None


def _change_sentence(ctx: _Ctx, change: dict, cap: int = LAYER_TEXT_MAX) -> tuple[str, str] | None:
    day = _day(change.get("at"))
    if not day:
        return None
    if change.get("field") in HOME_FIELDS:
        found = _home_change_text(ctx, change, day, cap)
        if not found:
            return None
        tid, text = found
        if _flapped(ctx, change.get("field")):
            flap = X.TEMPLATES["S-L1E-FLAP"][ctx.lang]
            if len(text) + 1 + len(flap) <= cap:
                text = f"{text} {flap}"
        return tid, text
    words = _change_words(change, ctx.lang)
    if not words:
        return None
    text = _fill("S-L1E-CHANGED", ctx.lang, cap, host=ctx.i.site_host, date=day, field_words=words[0], from_words=words[1], to_words=words[2])
    return ("S-L1E-CHANGED", text) if text else None


def _platform_rows(ctx: _Ctx) -> tuple[list[dict], str]:
    """(L2 rows, the platform layer's state)."""
    rows, state = _platform_rows_by_docs(ctx)
    # D110: a stop the caller placed at the agent product (it refused before the site, or the page shows and its tools do not
    # work) is that layer's own answer, whether or not the product was named or its pages could be read.
    if ctx.stop_group == "platform" and state not in ("evidence", "your_answer"):
        state = "your_answer"
    return rows, state


def _platform_rows_by_docs(ctx: _Ctx) -> tuple[list[dict], str]:
    lang, host = ctx.lang, ctx.i.site_host
    if not ctx.platform_known:
        return [], "not_reviewed"
    name = ctx.pname
    if ctx.entry is None:
        return [_row(ctx, "L2", "not_reviewed", "S-L2-NOT-REVIEWED", _fill("S-L2-NOT-REVIEWED", lang, LAYER_TEXT_MAX, platform=name))], "not_reviewed"
    entry = ctx.entry
    read_docs = [d for d in ctx.docs if d.get("status") == "read"]
    unread_docs = [d for d in ctx.docs if d.get("status") != "read"]
    vendor = X.VENDORS.get(entry.get("vendor"), entry.get("vendor"))
    rows: list[dict | None] = []
    centred = ctx.r7 or ctx.base == "R1"
    if not read_docs:
        state = "unread"
    elif entry.get("no_official_documentation_found") and not ctx.platform_relevant:
        state = "no_doc"
    elif not centred:
        state = "background"
    else:  # the stop is the caller's answer; it counts as evidence only where the product's own pages describe such a stop
        state = "evidence" if ctx.platform_relevant else "your_answer"

    if entry.get("no_official_documentation_found") and not unread_docs:
        rows.append(_row(ctx, "L2", state, "S-L2-NODOC", _fill("S-L2-NODOC", lang, LAYER_TEXT_MAX, vendor=vendor, platform=name, date=ctx.today.isoformat())))
    for doc in unread_docs[:2]:
        reason = _words(X.UNREAD_REASON, doc.get("unread_reason"), lang)
        rows.append(_row(ctx, "L2", "unread", "S-L2-UNREAD", _fill(
            "S-L2-UNREAD", lang, LAYER_TEXT_MAX, vendor=vendor, url=_url(doc.get("url")),
            date=_day(doc.get("last_attempt_at")) or ctx.today.isoformat(), unread_reason=reason), _source("vendor_doc", doc.get("url"), doc.get("last_attempt_at"))))

    if read_docs and centred:
        documented = False
        # Two pages of the product that describe the same thing differently are both shown; AHN does not choose.
        pair = _conflict_pair(ctx)
        if pair:
            (doc_a, quote_a), (_, quote_b) = pair
            text = _fill("S-L2-CONFLICT", lang, LAYER_TEXT_MAX, platform=name, quote=quote_a["text"], quote2=quote_b["text"])
            if text:
                rows.append(_row(ctx, "L2", "evidence", "S-L2-CONFLICT", text, _source("vendor_doc", doc_a.get("url"), doc_a.get("verified_at"))))
                documented = True
        if not documented:
            found = ctx.first_quote(*QUOTE_TAG_ORDER)
            if found:
                doc, quote = found
                text = _fill("S-L2-DOC", lang, LAYER_TEXT_MAX, platform=name, url=_url(doc.get("url")), date=_day(doc.get("verified_at")),
                             admin_mark=X.ADMIN_MARK[lang] if doc.get("admin_deployment_doc") else "", quote=quote["text"])
                if text:
                    rows.append(_row(ctx, "L2", "evidence", "S-L2-DOC", text, _source("vendor_doc", doc.get("url"), doc.get("verified_at"), bool(doc.get("admin_deployment_doc")))))
                    documented = True
        found = ctx.first_quote("site_side")
        if found:
            doc, quote = found
            text = _fill("S-L2-SITE-SIDE", lang, LAYER_TEXT_MAX, platform=name, url=_url(doc.get("url")), date=_day(doc.get("verified_at")), quote=quote["text"])
            if text:
                rows.append(_row(ctx, "L2", "evidence", "S-L2-SITE-SIDE", text, _source("vendor_doc", doc.get("url"), doc.get("verified_at"))))
                documented = True
        if ctx.stop in ("tools_off_page_visible", "after_login_action_fails"):
            found = ctx.first_quote("prohibited_action")
            if found:
                doc, quote = found
                text = _fill("S-L2-PROHIBITED", lang, LAYER_TEXT_MAX, platform=name, url=_url(doc.get("url")), quote=quote["text"])
                if text:
                    rows.append(_row(ctx, "L2", "background", "S-L2-PROHIBITED", text, _source("vendor_doc", doc.get("url"), doc.get("verified_at"))))
        if documented:
            rows.append(_row(ctx, "L2", "evidence", "S-L2-NOCLASS", X.TEMPLATES["S-L2-NOCLASS"][lang]))
            if ctx.quotes("list_incomplete"):
                rows.append(_row(ctx, "L2", "evidence", "S-L2-NOLIST", _fill("S-L2-NOLIST", lang, LAYER_TEXT_MAX, platform=name)))
        mechanism = _message_mechanism(ctx)
        if mechanism:
            rows.append(_row(ctx, "L2", "evidence", "S-L2-MSG-MATCH", _fill("S-L2-MSG-MATCH", lang, LAYER_TEXT_MAX, platform=name, mechanism=mechanism)))
    if ctx.message_other:
        other = _other_platform_name(ctx)
        if other:
            rows.append(_row(ctx, "L2", state, "S-L2-MSG-OTHER-PLATFORM", _fill("S-L2-MSG-OTHER-PLATFORM", lang, LAYER_TEXT_MAX, platform=name, platform2=other)))
    return [r for r in rows if r], state


def _conflict_pair(ctx: _Ctx) -> tuple[tuple[dict, dict], tuple[dict, dict]] | None:
    for doc in ctx.docs:
        if doc.get("status") != "read" or not doc.get("conflict") or not doc.get("conflict_peer_url"):
            continue
        peer = next((d for d in ctx.docs if d.get("url") == doc["conflict_peer_url"] and d.get("status") == "read"), None)
        if peer is None:
            continue
        pair_docs = (doc, peer)
        block = next(((d, q) for d in pair_docs for q in d["quotes"] if q.get("tag") == "blocked_category"), None)
        confirm = next(((d, q) for d in pair_docs for q in d["quotes"] if q.get("tag") == "confirm_required"), None)
        if block and confirm and block[1]["text"] != confirm[1]["text"]:
            return block, confirm
    return None


def _message_mechanism(ctx: _Ctx) -> str | None:
    """The fixed phrase for the mechanism the chosen message belongs to, only when the product's own pages describe it."""
    tag = {"claude_org_policy_banner": "banner_text", "claude_asks_each_action": "confirm_required"}.get(ctx.message or "")
    if tag and ctx.message_matches and ctx.quotes(tag):
        return _words(X.MECHANISM, tag, ctx.lang)
    return None


def _other_platform_name(ctx: _Ctx) -> str | None:
    return "Claude" if ctx.message in MESSAGE_PLATFORMS else None


def _account_rows(ctx: _Ctx) -> list[dict]:
    lang, rows = ctx.lang, []
    if ctx.verify:
        rows.append(_row(ctx, "L3a", "your_answer", "S-L3A-VERIFY", X.TEMPLATES["S-L3A-VERIFY"][lang]))
    elif ctx.stop in ("site_login", "company_login") or ctx.message == "sign_in_required":
        rows.append(_row(ctx, "L3a", "your_answer", "S-L3A-SIGNIN", X.TEMPLATES["S-L3A-SIGNIN"][lang]))
    elif ctx.stop == "after_login_action_fails":
        rows.append(_row(ctx, "L3a", "your_answer", "S-L3A-AFTER", X.TEMPLATES["S-L3A-AFTER"][lang]))
    if ctx.r2:
        rows.append(_row(ctx, "L3a", "your_answer", "S-L3-SSO-GENERAL", X.TEMPLATES["S-L3-SSO-GENERAL"][lang]))
    return [r for r in rows if r]


def _env_rows(ctx: _Ctx) -> list[dict]:
    lang, rows = ctx.lang, []
    if ctx.i.agent_env == "cloud_browser":
        rows.append(_row(ctx, "L3b", "your_answer", "S-L3B-CLOUD", X.TEMPLATES["S-L3B-CLOUD"][lang]))
    if ctx.i.agent_env and ctx.entry:
        for doc, quote in ctx.quotes("identity"):
            low = quote["text"].lower()
            if "device" in low or "machine" in low:
                text = _fill("S-L3B-DEVICE", lang, LAYER_TEXT_MAX, platform=ctx.pname, url=_url(doc.get("url")), quote=quote["text"])
                rows.append(_row(ctx, "L3b", "background", "S-L3B-DEVICE", text, _source("vendor_doc", doc.get("url"), doc.get("verified_at"))))
                break
    return [r for r in rows if r]


def _task_rows(ctx: _Ctx) -> list[dict]:
    lang, rows = ctx.lang, []
    if ctx.message == "claude_asks_each_action" and ctx.message_matches:
        found = ctx.first_quote("confirm_required")
        if found:
            doc, quote = found
            rows.append(_row(ctx, "L4", "your_answer", "S-L4-PLATFORM-CONFIRM", _fill(
                "S-L4-PLATFORM-CONFIRM", lang, LAYER_TEXT_MAX, platform=ctx.pname, quote=quote["text"]), _source("vendor_doc", doc.get("url"), doc.get("verified_at"))))
    terms = _get(ctx.check, "official_paths", "access", "terms")
    if terms and ctx.r3 and _url(terms.get("url")) and isinstance(terms.get("quote"), str):
        rows.append(_row(ctx, "L4", "background", "S-L4-TERMS", _fill("S-L4-TERMS", lang, LAYER_TEXT_MAX, url=terms["url"], quote=terms["quote"]),
                         _source("site_terms", terms["url"])))
    if ctx.r3 and _official_url(ctx):
        rows.append(_row(ctx, "L4", "background", "S-L4-OFFICIAL-PATH-ACCOUNT", X.TEMPLATES["S-L4-OFFICIAL-PATH-ACCOUNT"][lang]))
    return [r for r in rows if r]


def _official_url(ctx: _Ctx) -> str | None:
    na = ctx.next_action
    if na.get("do") == "use_official_path" and _url(na.get("url")):
        return na["url"]
    paths = ctx.check.get("official_paths") or {}
    return _url(paths.get("api_docs")) or _url(paths.get("developer_portal"))


# --- order of layers, states, and "look first" --------------------------------------------------------------------------
def _layer_order(ctx: _Ctx) -> list[str]:
    order = list(BASE_ORDER_R1 if ctx.base == "R1" else BASE_ORDER_SITE)
    if ctx.r7:
        order.remove("L2")
        order.insert(0, "L2")
    if ctx.r2:
        for token in ("L3b", "L3a"):
            order.remove(token)
            order.insert(0, token)
    if ctx.r6:
        moving = [t for t in order if (t == "L1e" and ctx.changes) or (t == "L1a" and ctx.changes) or (t == "L2" and ctx.doc_events)]
        for token in moving:
            index = order.index(token)
            if index > 0 and order[index - 1] not in ("L3a", "L3b"):
                order[index - 1], order[index] = order[index], order[index - 1]
    return order


def _group_states(rows: list[dict], platform_state: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for group in GROUPS:
        mine = [r["state"] for r in rows if LAYER_GROUP[r["layer"]] == group]
        if group == "platform":
            states[group] = platform_state
        elif "your_answer" in mine:
            states[group] = "your_answer"
        elif "evidence" in mine:
            states[group] = "evidence"
        elif "background" in mine:
            states[group] = "background"
        else:
            states[group] = "not_seen"
    return states


def _reason_key(ctx: _Ctx, group: str, rows: list[dict]) -> tuple[str, str]:
    if group == "site":
        by_layer = {r["layer"]: r for r in rows if r["state"] == "evidence"}
        if "L1" in by_layer:
            return "site", "cause"
        if "L1a" in by_layer:
            return "site", "robots_block"
        if "L1d" in by_layer:
            return "site", "provider_incident"
        if "L1b" in by_layer:
            return "site", "home_check"
        return "site", "change_recorded"
    if group == "platform":
        if ctx.stop == "before_visit":
            if not ctx.platform_known:
                return "platform", "stopped_unnamed"
            return "platform", "stopped_before_site" if ctx.platform_relevant else "stopped_plain"
        if ctx.stop == "tools_off_page_visible":
            if not ctx.platform_known:
                return "platform", "tools_off_unnamed"
            return "platform", "tools_off" if ctx.platform_relevant else "tools_off_plain"
        if ctx.message_matches:
            return "platform", "message_documented"
        return "platform", "differs_from_browser"
    if group == "account":
        if ctx.verify:
            return "account", "extra_verification"
        if ctx.r2:
            return "account", "company_login"
        if ctx.stop == "after_login_action_fails":
            return "account", "after_login"
        return "account", "sign_in"
    if group == "agent_env":
        return "agent_env", "cloud_browser"
    return "task", "asks_each_action"


def _where_to_look(ctx: _Ctx, order: list[str], rows: list[dict], states: dict[str, str]) -> dict:
    lang = ctx.lang
    sequence: list[str] = []
    for token in order:
        if LAYER_GROUP[token] not in sequence:
            sequence.append(LAYER_GROUP[token])
    # D110: with a stop that names a layer, the site layer goes ahead of it only on direct evidence (a stored cause, a robots.txt
    # block, a provider incident); AHN's home-page check or a recorded change alone does not.
    direct_site = any(r["layer"] in DIRECT_SITE_LAYERS and r["state"] == "evidence" for r in rows)
    held = ctx.stop_group is not None and states[ctx.stop_group] in ("evidence", "your_answer") and not direct_site
    first = next((g for g in sequence if states[g] in ("evidence", "your_answer") and not (held and g == "site")), None)
    text = None
    if first:
        key = _reason_key(ctx, first, rows)
        text = _fill("S-W-FIRST", lang, SHORT_TEXT_MAX, layer_words=_words(X.LAYER_WORDS, first, lang), reason_words=X.REASON_WORDS[key][lang])
    if text is None:
        first, text = "unknown", X.TEMPLATES["S-W-UNKNOWN"][lang]
    return {"first": first, "layers": states, "text": text}


# --- person -------------------------------------------------------------------------------------------------------------
def _person(ctx: _Ctx, rows: list[dict]) -> dict:
    lang = ctx.lang
    reasons: dict[str, None] = {}
    who: set[str] = set()
    if ctx.stop == "tools_off_page_visible":  # D110: the page shows, the agent's tools do not act; the person can open it
        reasons["page_visible_tools_off"] = None
        who.add("user")
    if ctx.stop in SIGN_IN_STOPS:
        reasons["sign_in_is_the_account_holders"] = None
        who.add("user")
        if ctx.stop == "company_login":
            who.add("it_admin")
    if ctx.verify:
        reasons["verification_on_person_device"] = None
        who.add("user")
    if ctx.message == "claude_asks_each_action" and ctx.message_matches:
        reasons["approval_is_the_persons_decision"] = None
        who.add("user")
    if ctx.message == "device_not_compliant" or ctx.r2:
        reasons["company_it_decides"] = None
        who.add("it_admin")
    admin = bool(ctx.quotes("admin_policy"))
    if ctx.message == "claude_org_policy_banner" and ctx.message_matches:
        reasons["platform_admin_decides"] = None
        who.add("platform_admin")
    if ctx.do == "hand_off_to_human":
        reasons["server_hand_off"] = None
        who.add("user")
        # D110: a public read that timed out or failed with a server error, where nothing but AHN's own reads failing is on
        # record (no stored cause, no provider incident): the reason says the person can open it, not that they must.
        direct = any(r["layer"] in ("L1", "L1d") and r["state"] == "evidence" for r in rows)
        ctx.open_self = ctx.i.task_context == "public_read" and ctx.stop_named == "timeout_or_5xx" and not direct
    if ctx.r7 and ctx.stop == "before_visit" and ctx.entry and ctx.quotes("blocked_category", "prohibited_action", "admin_policy"):
        reasons["platform_refused_step"] = None
        who.add("user")
        if admin:
            who.add("platform_admin")
    priority = ("verification_on_person_device", "platform_refused_step", "company_it_decides", "platform_admin_decides",
                "page_visible_tools_off", "approval_is_the_persons_decision", "sign_in_is_the_account_holders", "server_hand_off")
    codes = [c for c in priority if c in reasons][:3]
    if not codes:
        return {"needed": "not_indicated", "reasons": [], "text": X.TEMPLATES["S-P-NOT-INDICATED"][lang]}
    items = []
    for code in codes:
        template_id = "S-P-SERVER-HAND-OFF-OPEN" if code == "server_hand_off" and ctx.open_self else X.PERSON_REASON_ID[code]
        text = _fill(template_id, lang, SHORT_TEXT_MAX, platform=ctx.pname)
        if text:
            items.append({"code": code, "text": text})
    order = [w for w in ("user", "it_admin", "platform_admin", "site_support") if w in who][:3]
    return {"needed": "yes", "reasons": items, "who": order}


# --- things to do and what to tell a person ------------------------------------------------------------------------------
def _item(item_id: str, who: str, text: str | None, url: str | None = None) -> dict | None:
    if text is None:
        return None
    out: dict[str, Any] = {"id": item_id, "who": who, "text": text}
    if _url(url):
        out["url"] = url
    return out


def _checks(ctx: _Ctx, person: dict) -> list[dict]:
    lang, name, host = ctx.lang, ctx.pname, ctx.i.site_host
    out: list[dict | None] = []
    cap = CHECK_TEXT_MAX
    if ctx.r2:
        out.append(_item("CHK-IT-SSO", "it_admin", _fill("CHK-IT-SSO", lang, cap, platform=name)))
    if any(r["code"] == "verification_on_person_device" for r in person["reasons"]):
        out.append(_item("CHK-PERSON-VERIFY", "person", X.TEMPLATES["CHK-PERSON-VERIFY"][lang]))
    if ctx.base == "R5":
        out.append(_item("CHK-BROWSER-COMPARE", "person", X.TEMPLATES["CHK-BROWSER-COMPARE"][lang]))
    if (ctx.r7 or ctx.base == "R1") and ctx.entry:
        match = _msg_match_doc(ctx)
        if match:
            out.append(_item("CHK-MSG-MATCH", "person", _fill("CHK-MSG-MATCH", lang, cap, platform=name, url=_url(match.get("url"))), match.get("url")))
        if ctx.r7 and ctx.message == "claude_asks_each_action" and ctx.message_matches:
            confirm = next((d for d, _ in ctx.quotes("confirm_required")), None)
            if confirm:
                out.append(_item("CHK-SAFETY-CONFIRM", "person", _fill("CHK-SAFETY-CONFIRM", lang, cap, platform=name, url=_url(confirm.get("url"))), confirm.get("url")))
        admin = next((d for d, _ in ctx.quotes("admin_policy")), None)
        if admin:
            out.append(_item("CHK-ORG-ADMIN", "platform_admin", _fill("CHK-ORG-ADMIN", lang, cap, platform=name, host=host, url=_url(admin.get("url"))), admin.get("url")))
        remedy = ctx.first_quote("remedy")
        if remedy:
            doc, quote = remedy
            out.append(_item("CHK-PLATFORM-REMEDY", "person", _fill("CHK-PLATFORM-REMEDY", lang, cap, platform=name, quote=quote["text"], url=_url(doc.get("url"))), doc.get("url")))
    if ctx.r3 and ctx.stop in ("site_login", "company_login", "after_login_action_fails"):
        first = _fill("S-L3-C1", lang, cap, host=host, platform=name)
        second = X.TEMPLATES["S-L3-C2"][lang]
        both = f"{first} {second}" if first else None
        out.append(_item("CHK-SIGNED-IN", "person", both if both and len(both) <= cap else first))
    status_url = _url(_get(ctx.check, "provider_status", "source_url")) or _url(_get(ctx.check, "official_status_page", "source_url"))
    status_name = _safe_name(_get(ctx.check, "provider_status", "source_name")) or _safe_name(_get(ctx.check, "official_status_page", "source_name"))
    official = _official_url(ctx)
    if status_url and status_name:
        # D110: a public status page is one the agent can read itself; it is not a step only a person can take.
        out.append(_item("CHK-STATUS", "agent", _fill("CHK-STATUS", lang, cap, provider=status_name, url=status_url), status_url))
    public_read_site_first = ctx.base == "R4" and ctx.i.task_context == "public_read"
    if public_read_site_first and official:
        out.append(_item("CHK-OFFICIAL-PATH", "agent", _fill("CHK-OFFICIAL-PATH", lang, cap, url=official), official))
    # D110: "wait and look up once more" never sits next to a server action that hands the step over or stops it.
    if (ctx.stop == "timeout_or_5xx" or ctx.action == "wait_provider_incident" or ctx.do == "wait_for_provider") \
            and ctx.do not in ("hand_off_to_human", "stop_automation"):
        out.append(_item("CHK-WAIT", "agent", X.TEMPLATES["CHK-WAIT"][lang]))
    if ctx.doc_events:
        newest = max(ctx.doc_events, key=lambda e: _dt(e.get("changed_at")))
        out.append(_item("CHK-PLATFORM-CHANGES", "person", _fill("CHK-PLATFORM-CHANGES", lang, cap, platform=name, url=_url(newest.get("url")),
                                                                    date=_day(newest.get("changed_at"))), newest.get("url")))
    customer = _url(ctx.next_action.get("customer_url"))
    if customer and ctx.i.task_context in ("my_account_consumer", "my_account_work"):
        out.append(_item("CHK-CUSTOMER-PATH", "person", _fill("CHK-CUSTOMER-PATH", lang, cap, url=customer), customer))
    if ctx.next_action:
        out.append(_item("CHK-SERVER-ACTION", "agent", X.TEMPLATES["CHK-SERVER-ACTION"][lang]))
    if ctx.base == "R4":
        out.append(_item("CHK-SITE-HELP", "person", X.TEMPLATES["CHK-SITE-HELP"][lang]))
    if official and not public_read_site_first and (ctx.r3 or ctx.base != "R4"):
        text = _fill("CHK-OFFICIAL-PATH", lang, cap, url=official)
        if text and ctx.r3:
            warning = X.TEMPLATES["S-L4-OFFICIAL-PATH-ACCOUNT"][lang]
            text = f"{text} {warning}" if len(text) + 1 + len(warning) <= cap else text
        out.append(_item("CHK-OFFICIAL-PATH", "person" if ctx.r3 else "agent", text, official))
    items = [c for c in out if c]
    if ctx.status_check:
        # D110: a status check stays a status check: the status page first, else the server's own action.
        lead = next((c for c in items if c["id"] == "CHK-STATUS"), None) or next((c for c in items if c["id"] == "CHK-SERVER-ACTION"), None)
        if lead:
            items = [lead] + [c for c in items if c is not lead]
    if not items:
        items = [_item("CHK-BROWSER-COMPARE", "person", X.TEMPLATES["CHK-BROWSER-COMPARE"][lang])]
    while len(items) > CHECK_LIMIT:
        drop = next(i for i in range(len(items) - 1, -1, -1) if items[i]["id"] != "CHK-SERVER-ACTION")
        del items[drop]
    return items


def _msg_match_doc(ctx: _Ctx) -> dict | None:
    tag = {"claude_org_policy_banner": ("banner_text",), "claude_asks_each_action": ("confirm_required",)}.get(ctx.message or "")
    if tag and ctx.message_matches:
        found = ctx.first_quote(*tag)
        if found:
            return found[0]
    found = ctx.first_quote("banner_text", "blocked_category", "confirm_required")
    return found[0] if found else None


def _handoff(ctx: _Ctx, person: dict) -> list[dict]:
    lang, host = ctx.lang, ctx.i.site_host
    if ctx.i.since_kind == "today_first":
        since = X.SINCE_WORDS["today"][lang]
    elif ctx.i.since_date:
        since = ctx.i.since_date.isoformat()
    else:
        since = X.SINCE_WORDS["recent"][lang]
    stop = ctx.stop_named or "not_sure"  # D110: "an unclear step" only when neither a stop nor a measured code was sent
    stop_words = X.STOP_WORDS.get(stop, X.STOP_WORDS["not_sure"])[lang]
    common = {"host": host, "since": since, "stop_words": stop_words, "platform": ctx.pname}
    admin_quote = bool(ctx.quotes("admin_policy"))
    items: list[dict | None] = []
    if ctx.r2:
        items.append(_hand("it_admin", "H-IT", _fill("H-IT", lang, HANDOFF_TEXT_MAX, **common)))
    elif (ctx.message == "claude_org_policy_banner" and ctx.message_matches) or (ctx.r7 and ctx.platform_known and admin_quote and ctx.entry):
        items.append(_hand("platform_admin", "H-ORG-ADMIN", _fill("H-ORG-ADMIN", lang, HANDOFF_TEXT_MAX, **common)))
    elif (ctx.base == "R1" or ctx.r7) and ctx.platform_known:
        items.append(_hand("platform_vendor", "H-PLATFORM", _fill("H-PLATFORM", lang, HANDOFF_TEXT_MAX, **common)))
    elif ctx.base == "R4":
        items.append(_hand("site_support", "H-SITE", _fill("H-SITE", lang, HANDOFF_TEXT_MAX, **common)))
    first_code = person["reasons"][0]["code"] if person["needed"] == "yes" and person["reasons"] else None
    if first_code == "server_hand_off" and ctx.open_self:
        line = X.TEMPLATES["H-USER-OPEN"][lang]
    elif first_code == "check_needs_person":
        line = X.TEMPLATES["H-USER-CHECK"][lang]
    elif first_code:
        line = _fill("H-USER-YES", lang, 200, reason_short=X.REASON_SHORT[first_code][lang])
    else:
        line = X.TEMPLATES["H-USER-NO"][lang]
    # A very long host can push the sentence over the cap; the fallback needs no host, so it always fits.
    user_text = _fill("H-USER", lang, HANDOFF_TEXT_MAX, host=host, stop_words=stop_words, person_line=line, platform_cap=ctx.pname_cap)
    if user_text is not None:
        items.append(_hand("user", "H-USER", user_text))
    else:
        items.append(_hand("user", "H-USER-NOHOST", _fill("H-USER-NOHOST", lang, HANDOFF_TEXT_MAX, stop_words=stop_words,
                                                          person_line=line, platform_cap=ctx.pname_cap)))
    return [i for i in items if i][:2]


def _hand(to: str, template_id: str, text: str | None) -> dict | None:
    return {"to": to, "template_id": template_id, "text": text} if text else None


# --- what other agents saw, and the timeline ----------------------------------------------------------------------------
def _others(ctx: _Ctx) -> dict:
    lang, host = ctx.lang, ctx.i.site_host
    reports = []
    seen = [o for o in (ctx.check.get("observations") or []) if not o.get("synthetic")]
    for item in seen[:OTHERS_LIMIT]:
        day, outcome = _day(item.get("observed_at")), item.get("outcome")
        env = item.get("attempt_env")
        env_words = _words(X.ENV_WORDS, env, lang)
        # D109: the cause code a reporting agent sent, only when both codes are on their lists (a published row always is). It is
        # shown as the reporting agent's statement, never as AHN's, and nothing in the verdict above reads it.
        cause, basis = item.get("cause"), item.get("cause_basis")
        cause_words = _words(X.CAUSE_WORDS, cause, lang) if basis in CAUSE_BASES else None
        text = _fill("S-O-ROW", lang, SHORT_TEXT_MAX, date=day, outcome_words=_words(X.OUTCOME_WORDS, outcome, lang),
                     step_words=_words(X.STEP_WORDS, item.get("task_step"), lang) or X.STEP_WORDS["other"][lang],
                     env_words=f" ({env_words})" if env_words else "",
                     cause_part=X.CAUSE_LEAD[lang] + cause_words if cause_words else "")
        if text and item.get("error_class") and item.get("task_step") and env:
            report = {"observed_at": day, "outcome": outcome, "error_class": item["error_class"], "task_step": item["task_step"],
                      "attempt_env": env}
            if cause_words:
                report.update(cause=cause, cause_basis=basis)
            report.update(text=text, verified=False)
            reports.append(report)
    if reports:
        head = _fill("S-O-SOME", lang, SHORT_TEXT_MAX, n=len(reports))
    else:
        head = _fill("S-O-NONE", lang, SHORT_TEXT_MAX, host=host)
    tail = X.TEMPLATES["S-G-NO-SUCCESS"][lang]
    text = head if head else tail
    if head and len(head) + 1 + len(tail) <= SHORT_TEXT_MAX:
        text = f"{head} {tail}"
    out: dict[str, Any] = {"window_days": 7, "reports": reports, "text": text}
    band = _get(ctx.check, "lookup_activity", "volume_band")
    if band in ("1-4", "5-9", "10-49", "50+"):
        out["lookup_band"] = band
    return out


def _timeline(ctx: _Ctx) -> dict:
    lang, items = ctx.lang, []
    for change in ctx.changes:
        sentence = _change_sentence(ctx, change, 300)
        when = _dt(change.get("at"))
        if sentence and when:
            kind = "home_check_changed" if change.get("source") == "home_check" else "declared_policy_changed"
            items.append({"at": when, "layer": "L1e", "kind": kind, "template_id": sentence[0], "text": sentence[1]})
    for event in ctx.doc_events:
        when = _dt(event.get("changed_at"))
        text = _fill("TL-PLATFORM-DOC", lang, 300, date=_day(when), platform=ctx.pname, url=_url(event.get("url")))
        if text and when:
            item: dict[str, Any] = {"at": when, "layer": "L2", "kind": "platform_doc_changed", "template_id": "TL-PLATFORM-DOC", "text": text}
            if _url(event.get("url")):
                item["source_url"] = event["url"]
            items.append(item)
    items.sort(key=lambda i: i["at"])
    items = items[-20:]
    limits = X.TEMPLATES["S-G-WINDOW-LIMITS" if items else "S-G-WINDOW-EMPTY"][lang]
    return {"window_from": ctx.window_from, "window_to": ctx.today, "items": items, "limits_text": limits}


# --- the answer ---------------------------------------------------------------------------------------------------------
def _fit_rows(rows: list[dict]) -> list[dict]:
    """At most ROW_LIMIT rows: first the ones that say nothing was seen go, then background rows from the back."""
    rows = list(rows)
    for states in (("not_seen",), ("background",), ("not_reviewed", "no_doc", "unread")):
        while len(rows) > ROW_LIMIT:
            index = next((i for i in range(len(rows) - 1, -1, -1) if rows[i]["state"] in states), None)
            if index is None:
                break
            del rows[index]
    return rows[:ROW_LIMIT]


def _server(ctx: _Ctx) -> dict:
    check = ctx.check
    server: dict[str, Any] = {"recommendation": check.get("recommendation") or {"action": "unknown", "confidence": "low", "reasons": []},
                              "next_checks": check.get("next_checks") or [], "position": ctx.position}
    for key in ("summary_line", "next_action", "cause", "failure_stage"):
        if check.get(key) is not None:
            server[key] = check[key]
    return server


# D110: every accepted input, the lookup codes and the test mark included, in this order.
ECHO_CODES = ("error_class", "failure_stage", "task_step", "http_status", "challenge", "retry_after", "via", "agent_vendor")
ECHO_CHOICES = ("agent_platform", "agent_env", "external_browser", "stop_point", "screen_message", "since_kind", "task_context")


def _echo(inputs: Inputs) -> dict:
    echo: dict[str, Any] = {}
    for key in ECHO_CODES + ECHO_CHOICES:
        if getattr(inputs, key) is not None:
            echo[key] = getattr(inputs, key)
    if inputs.ahn_test:
        echo["ahn_test"] = True
    if inputs.ignored_params:
        echo["ignored_params"] = list(inputs.ignored_params)
    if inputs.since_date:
        echo["since_date"] = inputs.since_date
    if inputs.since_clamped:
        echo["since_clamped"] = True
    return echo


def _self_link(inputs: Inputs) -> str:
    pairs = [("site_host", inputs.site_host)]
    for key in ECHO_CODES + ECHO_CHOICES:
        if getattr(inputs, key) is not None:
            pairs.append((key, getattr(inputs, key)))
    if inputs.ahn_test:
        pairs.append(("ahn_test", "1"))
    if inputs.since_date:
        pairs.append(("since_date", inputs.since_date.isoformat()))
    pairs.append(("lang", inputs.lang))
    return "/v1/why?" + urlencode(pairs)


def _person_matches_checks(ctx: _Ctx, person: dict, checks: list[dict]) -> dict:
    """D110, the one place the invariant is kept: a check only a person can do (who=person) means a person is needed. When no
    rule above said so, the reason is that check, and it does not say the whole step is the person's."""
    if person["needed"] == "yes" or not any(c["who"] == "person" for c in checks):
        return person
    text = X.TEMPLATES["S-P-CHECK-PERSON"][ctx.lang]
    return {"needed": "yes", "reasons": [{"code": "check_needs_person", "text": text}], "who": ["user"]}


def build(inputs: Inputs, check: dict, view: dict | None, changes: list[dict], doc_events: list[dict], today: date) -> dict:
    """The body of GET /v1/why. Pure: the same arguments always give the same answer."""
    ctx = _Ctx(inputs, check, view, changes, doc_events, today)
    lang = ctx.lang
    platform_rows, platform_state = _platform_rows(ctx)
    rows = _site_rows(ctx) + platform_rows + _account_rows(ctx) + _env_rows(ctx) + _task_rows(ctx)
    order = _layer_order(ctx)
    rows.sort(key=lambda r: order.index(r["layer"]))
    rows = _fit_rows(rows)
    states = _group_states(rows, platform_state)
    person = _person(ctx, rows)
    checks = _checks(ctx, person)
    person = _person_matches_checks(ctx, person, checks)  # last word on person, before anything that reads it (the handoff)

    rules = [ctx.base] + [name for name, applies in (("R7", ctx.r7), ("R2", ctx.r2), ("R3", ctx.r3), ("R6", ctx.r6)) if applies]
    unknowns = [{"template_id": "S-L3-SCOPE", "text": X.TEMPLATES["S-L3-SCOPE"][lang]}]
    if ctx.r7:
        unknowns.append({"template_id": "S-L4-NOJUDGE", "text": X.TEMPLATES["S-L4-NOJUDGE"][lang]})
    if inputs.host_normalized == "apex":
        unknowns.append({"template_id": "S-G-APEX", "text": _fill("S-G-APEX", lang, SHORT_TEXT_MAX, host=inputs.site_host)})
    if inputs.host_normalized == "path_dropped":
        unknowns.append({"template_id": "S-G-PATH-DROPPED", "text": X.TEMPLATES["S-G-PATH-DROPPED"][lang]})
    unknowns = [u for u in unknowns if u["text"]][:6]

    render = ["where_to_look", "person", "what_happened", "do_now", "others_saw", "timeline", "handoff"]
    if ctx.r6:
        render = ["where_to_look", "person", "what_happened", "timeline", "do_now", "others_saw", "handoff"]

    generated = _dt(check.get("generated_at")) or datetime.now(timezone.utc)
    body: dict[str, Any] = {
        "schema_version": SCHEMA, "site_host": inputs.site_host, "generated_at": generated, "lang": lang,
        "input_echo": _echo(inputs), "where_to_look": _where_to_look(ctx, order, rows, states), "person": person,
        "others_saw": _others(ctx), "rules_applied": rules, "render_order": render, "server": _server(ctx),
        "what_happened": {"layers": rows, "unknowns": unknowns}, "do_now": {"checks": checks},
        "timeline": _timeline(ctx), "handoff": _handoff(ctx, person), "basis_text": X.TEMPLATES["S-G-BASIS"][lang],
        "links": {"self": _self_link(inputs)},
    }
    if lang == "ko" and body["server"].get("summary_line"):
        body["server_note"] = {"template_id": "S-G-SERVER-EN", "text": X.TEMPLATES["S-G-SERVER-EN"]["ko"]}
    if inputs.host_normalized:
        body["host_normalized"] = inputs.host_normalized
    return body


# --- the one function that reads ---------------------------------------------------------------------------------------
def _read_changes(conn, host: str, since: datetime) -> list[dict]:
    rows = conn.execute("SELECT at, field, from_code, to_code, source FROM host_access_changes WHERE site_host=%s AND at>=%s ORDER BY at DESC LIMIT 50",
                        (host, since)).fetchall()
    return [dict(r) for r in rows]


def _read_doc_events(conn, platform: str, since: datetime) -> list[dict]:
    rows = conn.execute("SELECT url, changed_at FROM platform_doc_events WHERE platform=%s AND changed_at>=%s AND prev_hash IS NOT NULL "
                        "ORDER BY changed_at DESC LIMIT 20", (platform, since)).fetchall()
    return [dict(r) for r in rows]


def explain(settings, inputs: Inputs, signal, *, today: date | None = None, platforms: list[dict] | None = None) -> dict:
    """Answer GET /v1/why: one lookup (counted like a lookup, no result token), then two short reads of stored records."""
    from . import platform_policies
    from .db import connect
    from .models import CheckResponse
    from .service import check, policy_host

    today = today or datetime.now(timezone.utc).date()
    error_class, failure_stage, task_step = map_symptoms(inputs)
    result = check(settings, inputs.site_host, error_class, task_step, 168, failure_stage, signal, agent_vendor=inputs.agent_vendor,
                   issue_feedback=False, plain_view=False, fold_rules=True, test_marked=inputs.ahn_test)
    # The same serialization as GET /v1/incidents/check, so `server` carries the lookup's own bytes.
    result = CheckResponse.model_validate(result).model_dump(mode="json")
    view = platform_policies.view(today, inputs.lang, platforms)
    since = datetime.combine(inputs.since_date or (today - timedelta(days=WINDOW_DAYS)), datetime.min.time(), tzinfo=timezone.utc)
    changes: list[dict] = []
    events: list[dict] = []
    try:
        with connect(settings) as conn:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '200ms'")
                try:
                    with conn.transaction():
                        changes = _read_changes(conn, policy_host(inputs.site_host), since)
                except Exception as exc:
                    LOG.warning("why_item_dropped id=host_access_changes error=%s", type(exc).__name__)
                if inputs.agent_platform and inputs.agent_platform != "other":
                    try:
                        with conn.transaction():
                            events = _read_doc_events(conn, inputs.agent_platform, since)
                    except Exception as exc:
                        LOG.warning("why_item_dropped id=platform_doc_events error=%s", type(exc).__name__)
    except Exception as exc:
        LOG.warning("why_item_dropped id=reads error=%s", type(exc).__name__)
    return build(inputs, result, view, changes, events, today)
