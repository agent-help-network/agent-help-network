"""Official platform policy table: what each agent product's own vendor documents say about the sites it limits,
what it asks a person to approve, and how it identifies itself. Information only (D107).

Everything here is a short quotation from a page on the vendor's own domain, with the date AHN last read that page.
AHN does not judge whether a given site falls under any quoted description, adds no quotation from the press or from
third parties, and shows no row for what a vendor has not published ("no official documentation found" is an answer).
A page AHN could not read is listed as unread, with the reason; it has no quotation and no ``verified_at``.
Nothing here changes a lookup answer (``recommendation``, ``next_checks`` and the like are untouched).

The table is checked by ``problems()`` (tests/test_platform_policies.py) and re-read by ``scripts/check_platform_docs.py``.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel

SCHEMA = "ahn-platforms/1"
QUOTE_MAX = 200
STALE_DAYS = 90  # a page not re-read for this long is listed without its quotations (same rule as the access bundles)
TAGS = ("blocked_category", "confirm_required", "admin_policy", "banner_text", "remedy", "identity", "prohibited_action", "change_note")
UNREAD_REASONS = ("http_403", "challenge_page", "script_rendered", "not_read_this_round")

# Vendor-owned hosts only. A page on any other host is refused by problems().
VENDOR_HOSTS: dict[str, frozenset[str]] = {
    "anthropic": frozenset({"support.claude.com", "claude.com"}),
    "openai": frozenset({"help.openai.com", "developers.openai.com"}),
    "google": frozenset({"support.google.com"}),
    "meta": frozenset({"platform.muse.ai", "www.meta.com"}),
    "perplexity": frozenset({"www.perplexity.ai"}),
}

NOTICE = {
    "en": "These are the vendors' own words, read from their public help pages on the date shown. AHN does not judge whether a site falls under any of these descriptions, does not know a vendor's unpublished lists, and has not tested any of these products.",
    "ko": "공급사가 자기 공개 도움말에 적은 문장을 표시한 날짜에 읽어 옮긴 거예요. 어떤 사이트가 이 설명에 드는지는 AHN 이 판단하지 않고, 공급사가 공개하지 않은 목록은 알지 못하며, 이 제품들을 직접 시험하지도 않았어요.",
}

NOTES = {
    "conflict_between_pages": {
        "en": "Another official page describes this differently (see conflict_peer_url). AHN shows both and does not choose.",
        "ko": "다른 공식 문서가 이 부분을 다르게 적어요(conflict_peer_url 참고). AHN 은 둘 다 보여 주고 고르지 않아요."},
    "managed_deployment_doc": {
        "en": "Written for administrators of managed deployments. AHN does not say the same settings apply to an individual user's screen.",
        "ko": "관리 배포 담당자를 위한 문서예요. 개인 사용자 화면에 같은 설정이 그대로 적용된다고 AHN 이 말하지 않아요."},
    "does_not_say_which_surface": {
        "en": "The page does not say which of the vendor's agent surfaces send the user agents it describes, and AHN does not say so either.",
        "ko": "이 문서는 공급사의 어느 에이전트 화면이 여기 적힌 사용자 에이전트를 보내는지 적지 않았고, AHN 도 말하지 않아요."},
    "retired_product": {
        "en": "The vendor says this product is retired.",
        "ko": "공급사가 이 제품이 종료됐다고 적었어요."},
    "no_self_identification_statement": {
        "en": "The page contains no statement on how this product identifies itself to sites.",
        "ko": "이 문서에는 이 제품이 사이트에 자기를 어떻게 밝히는지에 대한 말이 없어요."},
    "connector_guidelines_only": {
        "en": "Connector submission guidelines for developers; the page says nothing about site restrictions, sign-in handling or self-identification.",
        "ko": "개발자용 커넥터 제출 안내예요. 사이트 제한·로그인 처리·자기 식별에 대한 말은 없어요."},
}

NOT_FOUND_TOPICS = {
    "site_restrictions": {"en": "which sites it limits", "ko": "어떤 사이트를 제한하는지"},
    "self_identification": {"en": "how it identifies itself to sites", "ko": "사이트에 자기를 어떻게 밝히는지"},
    "blocked_site_list": {"en": "a list of blocked sites", "ko": "차단 사이트 목록"},
}


def _q(tag: str, text: str) -> dict[str, str]:
    return {"tag": tag, "text": text}


def _read(surface: str, url: str, title: str, quotes: list[dict[str, str]], verified_at: str, **extra: Any) -> dict[str, Any]:
    return {"surface": surface, "url": url, "title": title, "status": "read", "verified_at": verified_at, "quotes": quotes, **extra}


def _unread(surface: str, url: str, title: str, reason: str, last_attempt_at: str) -> dict[str, Any]:
    return {"surface": surface, "url": url, "title": title, "status": "unread", "unread_reason": reason,
            "last_attempt_at": last_attempt_at, "quotes": []}


_D = "2026-11-06"
_C = "https://support.claude.com/en/articles/"
_O = "https://help.openai.com/en/articles/"
_G = "https://support.google.com/"
_P = "https://www.perplexity.ai/help-center/"
_M = "https://www.meta.com/"

PLATFORMS: list[dict[str, Any]] = [
    {"platform": "claude_in_chrome", "display_name": "Claude in Chrome", "vendor": "anthropic", "not_found": [], "docs": [
        _read("chrome_extension", _C + "12902428-use-claude-in-chrome-safely", "Use Claude in Chrome safely", [
            _q("blocked_category", "For your safety, Claude cannot access sensitive, high-risk sites such as:"),
            _q("blocked_category", "Adult content websites"),
            _q("blocked_category", "Known pirated content sites"),
            _q("confirm_required", "Claude asks for permission before accessing financial sites."),
            _q("remedy", "It’s unlikely that we’ve captured all sites in these categories"),
            _q("confirm_required", "Action confirmations for certain high-risk actions such as downloading a file or entering sensitive information."),
            _q("admin_policy", "Allowlists and blocklists to restrict which sites Claude can access"),
            _q("admin_policy", "If you have questions about which sites are permitted in your organization, contact your admin."),
        ], _D, conflict=True, conflict_peer_url=_C + "12902405-claude-in-chrome-troubleshooting", note_code="conflict_between_pages"),
        _read("chrome_extension", _C + "12902405-claude-in-chrome-troubleshooting", "Claude in Chrome troubleshooting", [
            _q("blocked_category", "The site may be in Claude's default blocked categories (financial services, banking, investment platforms, cryptocurrency exchanges, adult content, pirated content)."),
            _q("admin_policy", "If you're on a Team or Enterprise plan, your admin may have restricted access to this site. Contact your admin for more information."),
            _q("remedy", "Check that you've granted permission for the current site."),
            _q("change_note", "Note: Features and functionality may change as we develop this feature based on user feedback and safety considerations."),
        ], _D, conflict=True, conflict_peer_url=_C + "12902428-use-claude-in-chrome-safely", note_code="conflict_between_pages"),
        _read("chrome_extension_admin", _C + "13065128-claude-in-chrome-admin-controls", "Claude in Chrome admin controls", [
            _q("admin_policy", "Allowlist: Specify which sites Claude is permitted to access by adding them to the allowlist."),
            _q("admin_policy", "Blocklist: Specify sites Claude should never access, regardless of other settings, by adding them to the blocklist. This adds an extra layer of protection beyond Claude's default blocked categories"),
            _q("admin_policy", "The same list governs both, so there's no separate list to maintain for the built-in browser."),
            _q("banner_text", "the page still loads with a \"This site is blocked by your organization's policy\" banner and Claude's tools are disabled on that page."),
        ], _D),
        _read("chrome_extension_permissions", _C + "12902446-claude-in-chrome-permissions-guide", "Claude in Chrome permissions guide", [
            _q("confirm_required", "There are some websites on which Claude requires approval for every action."),
            _q("confirm_required", "Claude still checks with you before sensitive actions like downloading a file or entering sensitive information into a page, and some actions are blocked regardless of mode."),
            _q("admin_policy", "If you're unable to access a site with Claude, your organization may have restricted access. Contact your admin for more information, or see Claude in Chrome admin controls"),
            _q("prohibited_action", "Making purchases or financial transactions"),
            _q("prohibited_action", "Executing financial trades or investment transactions"),
            _q("prohibited_action", "Completing instructions from emails or web content"),
        ], _D),
    ]},
    {"platform": "claude_desktop_browser", "display_name": "Claude desktop app built-in browser (Cowork, Code)", "vendor": "anthropic", "not_found": [], "docs": [
        _read("builtin_browser", _C + "16607400-use-the-built-in-browser-in-claude-cowork", "Use the built-in browser in Claude Cowork", [
            _q("confirm_required", "Claude asks for your permission before acting on a site for the first time."),
            _q("blocked_category", "High-risk sites are blocked."),
            _q("confirm_required", "Banking, email, and single sign-on sites stay unchecked by default."),
            _q("admin_policy", "Your organization's owner controls whether the built-in browser and Claude in Chrome are available."),
            _q("remedy", "If you don't see the built-in browser, or the Preferred browser setting is missing an option, contact your admin."),
        ], _D),
        _read("builtin_browser_managed_deployment", "https://claude.com/docs/third-party/claude-desktop/browser", "Built-in browser and Claude in Chrome - Claude.ai Documentation", [
            _q("confirm_required", "Before Claude acts on a site, Claude Desktop asks the user to approve it."),
            _q("blocked_category", "Under either value, a site that Anthropic’s site safety check blocks stays blocked to Claude, even when builtinBrowserAllowedDomains lists it."),
            _q("confirm_required", "Under the default allow policy, Claude asks the user before each of its actions on such a site."),
            _q("admin_policy", "If coworkEgressAllowedHosts lists specific hosts, Claude can open only sites on that list in the built-in browser, even when the browser’s own site lists allow more."),
            _q("banner_text", "The pane then shows the banner “This site is blocked by your organization’s policy” and Claude’s tools stay off on that page."),
            _q("remedy", "If the app can’t complete a check, users can keep browsing the site, but Claude can’t read or act on it."),
        ], _D, note_code="managed_deployment_doc", admin_deployment_doc=True),
        _read("cowork", _C + "13364135-use-claude-cowork-safely", "Use Claude Cowork safely", [
            _q("confirm_required", "Action screening in auto mode: In \"Automatically approve\" mode, Claude reviews each action for safety before it runs and blocks anything it determines to be unsafe."),
            _q("confirm_required", "Both run the same safeguards, and the same guidance applies to both: we strongly advise against using either to manage or take actions involving sensitive information."),
            _q("admin_policy", "Network egress permissions don't apply to the web fetch or web search tools or MCPs, including Claude in Chrome."),
        ], _D),
        _read("cowork_admin", _C + "13455879-use-claude-cowork-on-team-and-enterprise-plans", "Use Claude Cowork on Team and Enterprise plans", [
            _q("admin_policy", "Built-in browser: Controlled from Organization settings > Cowork"),
            _q("blocked_category", "Both run the same safety layers: a blocklist for high-risk sites and safety checks on every action."),
            _q("identity", "Browser traffic comes from the user's machine, where the desktop app runs."),
            _q("identity", "To site operators it looks like traffic from that device, even when the session is steered from web or mobile."),
        ], _D),
        _read("cowork_browser_admin", _C + "16635803-set-up-browser-use-in-claude-cowork-for-team-and-enterprise-plans", "Set up browser use in Claude Cowork for Team and Enterprise plans", [
            _q("confirm_required", "Both run the same safety layers: per-site permission prompts before Claude acts on a new site, a blocklist for high-risk sites, and safety checks on every action."),
            _q("admin_policy", "Site allowlists and blocklists you configure there apply to both the extension and the built-in browser."),
        ], _D),
    ]},
    {"platform": "chatgpt_agent", "display_name": "ChatGPT agent and browser", "vendor": "openai", "not_found": ["blocked_site_list"], "docs": [
        _read("cloud_browser", _O + "11845367-chatgpt-works-cloud-browser-allowlisting", "ChatGPT Work's Cloud browser allowlisting", [
            _q("identity", "ChatGPT Work's Cloud browser uses Web Bot Auth to sign outbound HTTP requests, allowing website operators to verify that requests genuinely originate from ChatGPT."),
            _q("identity", "Bot tag: chatgpt-agent"),
            _q("change_note", "at launch, Cloud browser cannot sign in to websites or complete payments."),
        ], _D),
        _read("cloud_browser", _O + "20001280-using-cloud-browser-in-chatgpt", "Using cloud browser in ChatGPT", [
            _q("confirm_required", "By default, ChatGPT will ask for your permission before accessing a new website in the cloud browser."),
            _q("confirm_required", "ChatGPT is designed to request confirmation before actions that could be hard to reverse or create a financial, legal, account, or other real-world commitment"),
            _q("blocked_category", "A website may block ChatGPT from accessing the site or completing your task, even if the same website works normally in your own browser."),
            _q("blocked_category", "These restrictions are set by the website, not by ChatGPT."),
            _q("remedy", "If a website is blocked, try another website or open the page directly in your own browser."),
        ], _D),
        _unread("desktop_builtin_browser", _O + "20001277-using-the-built-in-browser-in-the-chatgpt-desktop-app", "Using the built-in browser in the ChatGPT desktop app", "http_403", _D),
        _unread("agent", _O + "11752874-chatgpt-agent", "ChatGPT agent", "not_read_this_round", _D),
        _read("atlas_browser_retired", _O + "20001371-chatgpt-atlas-retirement", "ChatGPT Atlas retirement", [
            _q("change_note", "ChatGPT Atlas has been retired and no longer receives updates."),
        ], _D, note_code="retired_product"),
        _read("search_and_user_agents", "https://developers.openai.com/api/docs/bots", "Overview of OpenAI Crawlers", [
            _q("identity", "OAI-SearchBot is used to surface websites in search results in ChatGPT’s search features."),
            _q("identity", "ChatGPT-User is not used for crawling the web in an automatic fashion. Because these actions are initiated by a user, robots.txt rules may not apply."),
        ], _D, note_code="does_not_say_which_surface"),
    ]},
    {"platform": "gemini", "display_name": "Gemini (Chrome auto browse, agent tasks)", "vendor": "google", "not_found": ["self_identification"], "docs": [
        _read("chrome_auto_browse", _G + "chrome/answer/16821166?hl=en", "Ask Gemini in Chrome to complete tasks for you with auto browse", [
            _q("confirm_required", "Gemini in Chrome aims to ask for your review and confirmation before certain actions, like:"),
            _q("confirm_required", "Accessing sites with highly sensitive financial or health data"),
            _q("confirm_required", "Finalizing financial transactions"),
            _q("confirm_required", "Site & action restrictions: Gemini in Chrome restricts its activity to the websites and actions relevant to your task."),
            _q("admin_policy", "If you use Gemini in Chrome with a work or school Google Account, access must be enabled by your administrator."),
        ], _D, note_code="no_self_identification_statement"),
        _read("gemini_agent_tasks", _G + "gemini/answer/17094507?hl=en", "Manage your tasks & workflows in Gemini Apps", [
            _q("confirm_required", "After that, for every task that involves web browsing, Gemini will ask for confirmation first."),
            _q("confirm_required", "For certain steps, like signing in to a website, the task pauses until you take over and complete that step."),
            _q("remedy", "Do not enter sign-in info, payment details, or info you may find sensitive directly into the task thread."),
            _q("admin_policy", "For now, this feature isn’t available if you sign in with a work or school Google Account."),
        ], _D, note_code="no_self_identification_statement"),
    ]},
    {"platform": "meta_muse", "display_name": "Meta Muse", "vendor": "meta", "not_found": ["site_restrictions", "self_identification"], "docs": [
        _read("connector_platform", "https://platform.muse.ai/docs", "Muse Connector Platform", [
            _q("change_note", "Muse can already browse websites."),
        ], _D, note_code="connector_guidelines_only"),
        _unread("muse_agent_browser", _M + "help/superintelligence/2124746764949121/", "How your Muse agent browses the web", "script_rendered", "2026-10-31"),
        _unread("muse_agent_browser", _M + "en-gb/help/superintelligence/1385290430137537/", "How Muse works with your guidance and approval", "script_rendered", "2026-10-31"),
        _unread("muse_agent_browser", _M + "help/superintelligence/1303670544995562/", "Muse Help Center", "script_rendered", "2026-10-31"),
    ]},
    {"platform": "perplexity_comet", "display_name": "Perplexity Comet", "vendor": "perplexity", "not_found": [], "docs": [
        _unread("comet_browser", _P + "comet/en/articles/12658082-control-what-comet-assistant-can-use", "Control what Comet Assistant can use", "challenge_page", "2026-10-31"),
        _unread("comet_browser", _P + "en/articles/13531931-my-organization-has-restricted-comet", "My organization has restricted Comet", "challenge_page", "2026-10-31"),
        _unread("comet_browser", _P + "en/articles/13529668-comet-policies-and-controls", "Comet Policies and Controls", "challenge_page", "2026-10-31"),
        _unread("comet_browser", _P + "en/articles/13531023-managing-comet-assistant-permissions", "Managing Comet Assistant permissions", "challenge_page", "2026-10-31"),
    ]},
]

# Words that would turn a fixed sentence of ours into advice on getting around a platform or a site (checked on our own text only).
FORBIDDEN = ("bypass", "workaround", "get around", "circumvent", "spoof", "vpn", "proxy", "incognito", "user-agent string",
             "different user agent", "probably", "likely", "violat", "safe to", "우회", "피해 가", "시크릿", "아마", "가능성이 높", "위반")


def _date(value: Any) -> date | None:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def problems(platforms: list[dict[str, Any]] | None = None, today: date | None = None) -> list[str]:
    """Every rule the table must keep; empty when the table is sound."""
    out: list[str] = []
    today = today or date.today()
    seen: set[str] = set()
    for p in PLATFORMS if platforms is None else platforms:
        name = p.get("platform", "?")
        if name in seen:
            out.append(f"{name}: duplicate platform")
        seen.add(name)
        allowed = VENDOR_HOSTS.get(p.get("vendor", ""))
        if not allowed:
            out.append(f"{name}: unknown vendor")
            continue
        for topic in p.get("not_found", []):
            if topic not in NOT_FOUND_TOPICS:
                out.append(f"{name}: unknown not_found topic {topic}")
        for doc in p.get("docs", []):
            url = doc.get("url", "")
            parsed = urlparse(url)
            tag = f"{name} {url}"
            if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password or parsed.port:
                out.append(f"{tag}: not an https page on the vendor's own host")
            if doc.get("status") == "read":
                when = _date(doc.get("verified_at"))
                if when is None:
                    out.append(f"{tag}: verified_at is not YYYY-MM-DD")
                elif when > today:
                    out.append(f"{tag}: verified_at is in the future")
                if not doc.get("quotes"):
                    out.append(f"{tag}: read page without a quotation")
                if doc.get("unread_reason"):
                    out.append(f"{tag}: read page with unread_reason")
            elif doc.get("status") == "unread":
                if doc.get("unread_reason") not in UNREAD_REASONS:
                    out.append(f"{tag}: unread without a known reason")
                if doc.get("quotes") or doc.get("verified_at"):
                    out.append(f"{tag}: unread page with a quotation or verified_at")
                if _date(doc.get("last_attempt_at")) is None:
                    out.append(f"{tag}: unread page without last_attempt_at")
            else:
                out.append(f"{tag}: status must be read or unread")
            for q in doc.get("quotes", []):
                text = q.get("text", "")
                if q.get("tag") not in TAGS:
                    out.append(f"{tag}: unknown quotation tag {q.get('tag')}")
                if not text or len(text) > QUOTE_MAX:
                    out.append(f"{tag}: quotation empty or longer than {QUOTE_MAX}")
                if "@" in text or "[email" in text:
                    out.append(f"{tag}: quotation holds an address")
            if doc.get("conflict") and not doc.get("conflict_peer_url"):
                out.append(f"{tag}: conflict without conflict_peer_url")
            if doc.get("note_code") and doc["note_code"] not in NOTES:
                out.append(f"{tag}: unknown note_code")
    fixed = list(NOTICE.values()) + [v for item in NOTES.values() for v in item.values()] + [v for item in NOT_FOUND_TOPICS.values() for v in item.values()]
    for text in fixed:
        if any(word in text.lower() for word in FORBIDDEN):
            out.append(f"fixed text uses a forbidden word: {text[:40]}")
    return out


def view(today: date | None = None, lang: str = "en") -> dict[str, Any]:
    """The public table. A page not read for more than STALE_DAYS keeps its link but loses its quotations."""
    today = today or date.today()
    lang = lang if lang in NOTICE else "en"
    limit = today - timedelta(days=STALE_DAYS)
    platforms = []
    for p in PLATFORMS:
        docs = []
        for doc in p["docs"]:
            item = {k: v for k, v in doc.items() if k != "note_code"}
            if doc.get("note_code"):
                item["note_code"] = doc["note_code"]
                item["note"] = NOTES[doc["note_code"]][lang]
            when = _date(doc.get("verified_at"))
            if doc["status"] == "read" and when is not None and when < limit:
                item.update(status="unread", unread_reason="not_read_this_round", quotes=[], last_attempt_at=doc["verified_at"])
                item.pop("verified_at", None)
            docs.append(item)
        entry: dict[str, Any] = {"platform": p["platform"], "display_name": p["display_name"], "vendor": p["vendor"],
                                 "coverage": "documented" if any(d["status"] == "read" for d in docs) else "unread", "docs": docs}
        if p["not_found"]:
            entry["no_official_documentation_found"] = [NOT_FOUND_TOPICS[t][lang] for t in p["not_found"]]
        platforms.append(entry)
    return {"schema_version": SCHEMA, "notice": NOTICE[lang], "platforms": platforms}


class PlatformQuote(BaseModel):
    tag: str
    text: str


class PlatformDoc(BaseModel):
    surface: str
    url: str
    title: str
    status: str
    verified_at: str | None = None
    unread_reason: str | None = None
    last_attempt_at: str | None = None
    quotes: list[PlatformQuote]
    conflict: bool | None = None
    conflict_peer_url: str | None = None
    admin_deployment_doc: bool | None = None
    note_code: str | None = None
    note: str | None = None


class PlatformEntry(BaseModel):
    platform: str
    display_name: str
    vendor: str
    coverage: str
    docs: list[PlatformDoc]
    no_official_documentation_found: list[str] | None = None


class PlatformsResponse(BaseModel):
    schema_version: str
    notice: str
    platforms: list[PlatformEntry]
