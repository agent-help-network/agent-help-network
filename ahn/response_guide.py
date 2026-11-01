"""Server-made summary line, next action and feedback links for a lookup answer (D51).

Everything here is computed from the finished answer (``recommendation``, ``access_profile``, ``official_paths``,
``provider_status``) and never changes it. The sentences are a fixed table of English templates: the only value filled
in is a whole number of seconds, and no hostname, agent token, URL or text taken from a site or a provider ever reaches
a sentence. ``next_action.do`` is a pure function of ``recommendation.action`` (the four values that split ``unknown``
only say what can be done while no verdict exists). When the host has a curated consumer path (D61), a use_official_path
row takes its CONSUMER_ROWS sentence and next_action gains ``customer_url``. Since D63 a proceed answer carries it too, and
on proceed rows and on the O1/O7 rows (no robots block on pages) the consumer path comes first: it is the sentence's lead
and ``url``, with a developer API as an "also listed" tail where the sentence has room. O2-O6 hand it to the user."""

from __future__ import annotations

import logging
import re

from .background_reads import FEED_RETRY_MINUTES
from .official_paths import customer_url
from .triage import blocked_governing_tokens

LOG = logging.getLogger(__name__)

SAFE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/%+=&?#-]*)?$")
URL_MAX = 200
LINE_MAX = 200
MAX_STOP_TOKENS = 16
RETRY_MAX = 3600

# Constant public pages (D51). A value left empty is left out of the answer; null is never sent.
FEEDBACK_URL = "https://github.com/agent-help-network/agent-help-network/discussions/categories/field-reports"
CHANGELOG_URL = "https://github.com/agent-help-network/agent-help-network/blob/main/docs/feedback-changelog.md"
# The link points at the section that says which wording of the verdicts the answer follows (D63 off: the page itself).
CHANGELOG_ANCHOR = "#wording-1031"

WAIT_SECONDS = FEED_RETRY_MINUTES * 60
_WAIT_IN = f"look up again in about {FEED_RETRY_MINUTES} minutes"

# Template id -> (do, sentence). "{n}" is the only placeholder.
ROWS: dict[str, tuple[str, str]] = {
    "W1": ("wait_for_provider", f"The provider reports an active issue. Wait instead of retrying, and {_WAIT_IN}; its status page lists the affected services."),
    "W2": ("wait_for_provider", f"The provider reports scheduled maintenance. Wait for it to end instead of retrying in a loop, and {_WAIT_IN}."),
    "W3": ("wait_for_provider", f"The provider reports an issue that may reach only some regions. If a service you use is affected, wait; {_WAIT_IN}."),
    "W4": ("wait_for_provider", f"The provider's status page shows a major or critical problem beyond any posted incident. Wait instead of retrying; {_WAIT_IN}."),
    # Same condition as next_checks provider_update_time_unknown: wait only if recent and naming a service you use.
    "W5": ("wait_for_provider", "The provider shows a minor issue with no update time we can read, so it may be old. If its status page dates it recently and names a service you use, wait; otherwise continue."),
    # D55: no incident announced, only maintenance in progress (provider_maintenance_only). Same waits, honest about why.
    "W6": ("wait_for_provider", f"The provider has posted no incident, only maintenance in progress on some regions or points of presence. If a service you use is affected, wait; {_WAIT_IN}."),
    "W7": ("wait_for_provider", f"The provider has posted no incident, only maintenance in progress, while its status page shows an issue. Wait instead of retrying in a loop; {_WAIT_IN}."),
    # D57: an open incident read from the page's Atom history (provider_history_incident): no severity or scope, low confidence.
    "W8": ("wait_for_provider", f"The provider's incident history lists an open incident but no severity or affected services. If its status page names a service you use, wait; {_WAIT_IN}."),
    "H1": ("hand_off_to_human", "This step needs human verification. Hand it to the user through your normal handoff; do not try to solve or bypass the check."),
    "H2": ("hand_off_to_human", "This step likely needs the user's sign-in or permission. Hand it to the user; never ask for, store or pass on credentials."),
    "O1": ("use_official_path", "An official developer path is listed; use it instead of page automation. The site's robots.txt is still being read: look up once more in {n} s."),
    "O2": ("use_official_path", "The site's robots.txt blocks agent user agents at the site root. Stop retrying pages and use the listed official developer path instead."),
    "O3": ("use_official_path", "robots.txt blocks some agent tokens, and this applies to requests that send them (next_action.stop_if_you_are). Use the listed official developer path instead of page automation."),
    "O4": ("use_official_path", "The site's robots.txt blocks your vendor's agent under another name. Do not read that as permission; use the listed official developer path."),
    "O5": ("use_official_path", "The site refused even a plain automated robots.txt read. Expect page automation to be refused too; use the listed official developer path."),
    "O6": ("use_official_path", "The site's robots.txt disallows the root for agents it does not name (every token not in robots.agents_allowed). Do not read that as permission; use the listed official developer path."),
    "O7": ("use_official_path", "An official developer path is listed for this site. Prefer that documented API or portal over page automation."),
    "O7-N": ("use_official_path", "An official developer API is listed and has a no-sign-up option (official_paths.access.auth). Use it instead of retrying page automation."),
    "O7-K": ("use_official_path", "An official developer API is listed; it needs a key or app registration (official_paths.access.auth). Use one you have; else ask the user before signing up."),
    "S1": ("stop_automation", "The site's robots.txt explicitly blocks agent user agents at the site root, and no official path is listed. Stop automated retries; hand the step to the user if needed."),
    "P1": ("proceed", "robots.txt does not block any listed agent vendor at the site root. Continue with modest requests; paths below the root may differ, and this is not an access guarantee."),
    "P1-V": ("proceed", "robots.txt does not block your vendor's agent tokens at the site root. Continue with modest requests; paths below the root may differ, and this is not an access guarantee."),
    "P2": ("proceed", "robots.txt blocks some agent tokens, but none this answer checks for a listed vendor. Continue with modest requests unless yours is in access_profile.robots.agents_blocked."),
    "P2-V": ("proceed", "robots.txt blocks some other agents, not your vendor's tokens, at the site root. Continue with modest requests; this is not an access guarantee."),
    "P3": ("proceed", "This site publishes no robots.txt, so it declares no restriction for agents. Continue with modest requests; not an access guarantee, and the site's terms still apply."),
    "U1": ("retry_lookup_once", "The site's robots.txt is being read now. Look up once more in {n} s; if the answer is still pending then, treat the policy as unknown. Do not poll."),
    "U2": ("no_clear_action", "An earlier read of the site's robots.txt failed and another is queued. Treat its policy as unknown for now; one more lookup in {n} s may help."),
    "U3": ("stop_if_listed", "robots.txt blocks some agent tokens; this applies to requests that send them (next_action.stop_if_you_are). If yours is one, stop retrying pages; else send agent_vendor."),
    "U4": ("no_clear_action", "The site's robots.txt blocks your vendor's agent under another name. Do not read that as permission; tell the user before automating pages."),
    "U5": ("no_clear_action", "The site's robots.txt disallows the root for agents it does not name (every token not in robots.agents_allowed), and no official path is listed. Do not read this as permission."),
    "U6": ("no_clear_action", "The site refused even a plain automated robots.txt read, and no official path is listed. Expect page automation to be refused; hand off if needed."),
    "U7": ("no_clear_action", "The site's robots.txt could not be read, so no declared policy is known. No official path is listed; follow your operator's rules."),
    "U8": ("lookup_apex_host", "Policy is read only for a site's main domain, not this subdomain. Look up next_action.lookup_host for the site-wide declaration."),
    "U9": ("no_clear_action", "No declared policy could be read for this host and no official path is listed. Nothing here forbids the step; follow your operator's rules and the site's terms."),
}
DO_VALUES = tuple(dict.fromkeys(do for do, _ in ROWS.values()))

# D61: the same use_official_path rows when official_paths.consumer is curated for the host. The service does not know
# whether the caller is building on the site's data or doing a task in the user's own account, and some developer APIs do
# reach the user's account (OAuth), so the sentence asserts neither: a shorter lead with the same meaning and `do`, then a
# tail that says the API may not reach it. Where robots.txt or a refused read blocks pages (O2-O6) the tail hands the
# customer page to the user instead of pointing the agent at the same site's pages; O1 and O7 only point to it.
# Each whole sentence stays within LINE_MAX.
# AHN_PROCEED_WITHOUT_FAILURE=0 (D63 off): the sentences and rules of the table before D63. Only what D63 changed is listed.
ROWS_BEFORE = {
    "O3": "The site's robots.txt blocks some agent tokens (next_action.stop_if_you_are). Use the listed official developer path instead of page automation.",
    "O6": "The site's robots.txt disallows the root for agents it does not name. Do not read that as permission; use the listed official developer path.",
    "U3": "The site's robots.txt blocks some agent tokens. If your requests send one in next_action.stop_if_you_are, stop retrying pages; else send agent_vendor.",
    "U5": "The site's robots.txt disallows the root for agents it does not name, and no official path is listed. Do not read this as permission.",
    "P1": "The site's robots.txt does not block the agent tokens checked for you at the root. Continue with modest requests; declared policy is not an access guarantee.",
    "P2": "The site's robots.txt blocks some other agents, not the tokens checked for you. Continue with modest requests; this is not an access guarantee.",
    "U7": "The site's robots.txt could not be read, so its agent policy is unknown, not permissive. No official path is listed; follow your operator's rules.",
    "U9": "No declared policy or official path is available for this host. Treat its agent policy as unknown, not as permission.",
}
CONSUMER_SEE_TAIL_BEFORE = " The API may not reach the user's own account; the site's page is next_action.customer_url."
HAND_TAIL_BEFORE = " The API may not reach the user's own account; hand the user next_action.customer_url."
CONSUMER_ROWS_BEFORE = {
    "O3": "robots.txt blocks some agent tokens (next_action.stop_if_you_are). Use the listed official path, not pages." + HAND_TAIL_BEFORE,
    "O6": "robots.txt disallows the root for agents it does not name; not permission. Use the listed official path." + HAND_TAIL_BEFORE,
    "O1": "Official path listed; use it instead of page automation. robots.txt is pending: look up once more in {n} s." + CONSUMER_SEE_TAIL_BEFORE,
    "O7": "An official developer API or portal is listed; prefer it over page automation." + CONSUMER_SEE_TAIL_BEFORE,
}
CONSUMER_HAND_TAIL = " The API may not reach the user's own account; hand the user next_action.customer_url."
# D63: where robots.txt does not block pages (O1, O7) the customer page comes first and the developer API is the tail.
CONSUMER_FIRST_TAIL = " A developer API is also listed (official_paths)."
_CONSUMER_O7 = "For a task in the user's own account, use the site's customer page (next_action.customer_url) instead of retrying page automation." + CONSUMER_FIRST_TAIL
CONSUMER_ROWS: dict[str, str] = {
    "O1": "For a task in the user's own account, use the site's customer page (next_action.customer_url). robots.txt is pending: look up once more in {n} s." + CONSUMER_FIRST_TAIL,
    "O2": "The site's robots.txt blocks agent user agents at the root. Stop retrying pages; use the listed official path." + CONSUMER_HAND_TAIL,
    "O3": "robots.txt blocks the tokens in next_action.stop_if_you_are, for requests that send them. Use the official path." + CONSUMER_HAND_TAIL,
    "O4": "robots.txt blocks your vendor's agent under another name; not permission. Use the listed official path." + CONSUMER_HAND_TAIL,
    "O5": "The site refused even a plain robots.txt read; expect pages to refuse too. Use the listed official path." + CONSUMER_HAND_TAIL,
    "O6": "robots.txt disallows the root for tokens not in robots.agents_allowed; not permission. Use the official path." + CONSUMER_HAND_TAIL,
    "O7": _CONSUMER_O7,
    "O7-N": _CONSUMER_O7,
    "O7-K": _CONSUMER_O7,
}
# D63: proceed rows with a developer API listed (no consumer path) and with a consumer path. With a consumer path only its
# tail is added: both tails would not fit in LINE_MAX, and the developer API stays in official_paths.
PROCEED_OFFICIAL_TAIL = " A developer API is also listed (official_paths)."
PROCEED_CONSUMER_TAIL = " For tasks in the user's own account, the site's customer page is next_action.customer_url."
PROCEED_OFFICIAL_ROWS: dict[str, str] = {
    "P1": "robots.txt does not block any listed agent vendor at the site root. Continue with modest requests; not an access guarantee." + PROCEED_OFFICIAL_TAIL,
    "P1-V": "robots.txt does not block your vendor's agent tokens at the site root. Continue with modest requests; not an access guarantee." + PROCEED_OFFICIAL_TAIL,
    "P2": "robots.txt blocks some agent tokens, none checked for a listed vendor. Continue modestly unless yours is in access_profile.robots.agents_blocked." + PROCEED_OFFICIAL_TAIL,
    "P2-V": "robots.txt blocks some other agents, not your vendor's tokens. Continue with modest requests; not an access guarantee." + PROCEED_OFFICIAL_TAIL,
    "P3": "This site publishes no robots.txt: no declared restriction for agents. Continue modestly; not an access guarantee, and site terms still apply." + PROCEED_OFFICIAL_TAIL,
}
PROCEED_CONSUMER_ROWS: dict[str, str] = {
    "P1": "robots.txt blocks no listed agent vendor at the site root. Continue modestly; not an access guarantee." + PROCEED_CONSUMER_TAIL,
    "P1-V": "robots.txt does not block your vendor's agents at the site root. Continue modestly; not an access guarantee." + PROCEED_CONSUMER_TAIL,
    "P2": "robots.txt blocks some agents, none checked for a vendor. Continue unless yours is in robots.agents_blocked." + PROCEED_CONSUMER_TAIL,
    "P2-V": "robots.txt blocks other agents, not your vendor's. Continue modestly; not an access guarantee." + PROCEED_CONSUMER_TAIL,
    "P3": "This site publishes no robots.txt: no declared restriction. Continue modestly; not an access guarantee." + PROCEED_CONSUMER_TAIL,
}
# Rows where a curated consumer path is also next_action.url (the others keep the developer path, or none on proceed).
CUSTOMER_FIRST_ROWS = frozenset({"O1", "O7", "O7-N", "O7-K", "P1", "P1-V", "P2", "P2-V", "P3"})
# Rows whose retry_after_seconds is the pending profile's own value (a read is queued).
RETRY_ROWS = frozenset({"O1", "U1", "U2"})
# The last row of each action: where a combination the table does not list falls to (logged; tests forbid it).
DEFAULT_ROW = {"wait_provider_incident": "W1", "hand_off_to_human": "H2", "use_official_path": "O7",
               "stop_declared_block": "S1", "proceed": "P1", "unknown": "U9"}


def apex_host(site_host: str) -> str | None:
    """The shortest registrable apex above site_host that the service reads policy for, else None."""
    from .service import PUBLIC_SUFFIXES_2, aggregate_eligible_host  # service imports this module
    labels = site_host.split(".")
    for width in (2, 3):
        if len(labels) <= width:
            break
        candidate = ".".join(labels[-width:])
        if candidate in PUBLIC_SUFFIXES_2:
            continue
        if aggregate_eligible_host(candidate) and candidate != site_host:
            return candidate
    return None


def select_row(recommendation: dict, access_profile: dict | None, provider_status: dict | None,
               site_host: str, official: dict | None = None, *, variants: bool = True) -> tuple[str, bool]:
    """(template id, whether the table lists this combination). First match in table order within the action."""
    action = recommendation.get("action")
    reasons = set(recommendation.get("reasons") or ())
    if action == "wait_provider_incident":
        if "provider_history_incident" in reasons:
            return "W8", True
        if "provider_maintenance_only" in reasons:
            # D55: say that nothing but maintenance is announced. A maintenance page that is not regional keeps W2,
            # which already says so.
            if "provider_scope_partial" in reasons:
                return "W6", True
            return ("W2" if (provider_status or {}).get("indicator") == "maintenance" else "W7"), True
        if "provider_scope_partial" in reasons:
            return "W3", True
        if "provider_update_time_unknown" in reasons:
            # D54: a low-confidence wait on a minor page with no usable time; say so instead of W1's plain wait.
            return "W5", True
        if "provider_scope" in reasons:
            return ("W2" if (provider_status or {}).get("indicator") == "maintenance" else "W1"), True
        if reasons & {"provider_components_only", "provider_incident_stale"}:
            return "W4", True
    elif action == "hand_off_to_human":
        if "human_handoff" in reasons:
            return "H1", True
        if "account_handoff" in reasons:
            return "H2", True
    elif action == "use_official_path":
        for code, row in (("profile_pending", "O1"), ("robots_blocks_agents", "O2"), ("robots_blocks_some_agents", "O3"),
                          ("robots_names_vendor_alias", "O4"), ("robots_fetch_refused", "O5"),
                          ("robots_wildcard_disallow", "O6")):
            if code in reasons:
                return row, True
        if "official_path_available" in reasons and not reasons - {"official_path_available", "robots_blocks_not_your_agent"}:
            # D63: say whether the listed API can be used without signing up (official_paths.access.auth, curated).
            if not variants:
                return "O7", True
            auth = {a.get("method") for a in ((official or {}).get("access") or {}).get("auth") or []}
            if "none" in auth:
                return "O7-N", True
            return ("O7-K" if auth else "O7"), True
    elif action == "stop_declared_block":
        if "robots_blocks_agents" in reasons:
            return "S1", True
    elif action == "proceed":
        vendor = variants and bool(recommendation.get("agent_vendor"))
        if "robots_blocks_not_your_agent" in reasons:
            return ("P2-V" if vendor else "P2"), True
        if "robots_allows_agents" in reasons:
            if variants and (access_profile or {}).get("robots_status") == "absent":
                return "P3", True
            return ("P1-V" if vendor else "P1"), True
    elif action == "unknown":
        if "profile_pending" in reasons:
            # A pending profile whose earlier read failed was stored with robots_status "error" (a first lookup's
            # placeholder says "pending"): another read is queued, but the first one already failed.
            return ("U2" if (access_profile or {}).get("robots_status") == "error" else "U1"), True
        for code, row in (("robots_blocks_some_agents", "U3"), ("robots_names_vendor_alias", "U4"),
                          ("robots_wildcard_disallow", "U5"), ("robots_fetch_refused", "U6"), ("robots_unknown", "U7")):
            if code in reasons:
                return row, True
        if "profile_not_read" in reasons:
            return ("U8" if apex_host(site_host) else "U9"), True
        if not reasons:
            return "U9", True
    return DEFAULT_ROW.get(action, "U9"), False


def safe_url(value) -> str | None:
    return value if isinstance(value, str) and len(value) <= URL_MAX and SAFE_URL.fullmatch(value) else None


def _url_for(do: str, official: dict | None, provider_status: dict | None, official_status_page: dict | None) -> str | None:
    if do == "use_official_path" and official:
        candidates = (official.get("api_docs"), official.get("developer_portal"))
    elif do == "wait_for_provider":
        candidates = ((provider_status or {}).get("source_url"), (official_status_page or {}).get("source_url"))
    else:
        return None
    for value in candidates:
        if value:
            url = safe_url(value)
            if url is None:
                LOG.warning("response_guide_url_dropped")
            return url
    return None


def _seconds(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return max(0, min(RETRY_MAX, value))


def links(versioned: bool = True) -> dict | None:
    changelog = safe_url(CHANGELOG_URL + CHANGELOG_ANCHOR) if versioned and CHANGELOG_URL else CHANGELOG_URL
    found = {key: value for key, value in (("feedback", FEEDBACK_URL), ("changelog", changelog)) if value}
    return found or None


def guide(result: dict, proceed_without_failure: bool = True) -> dict:
    """The three added keys for a finished answer: summary_line, next_action and (when any is set) links.

    proceed_without_failure is AHN_PROCEED_WITHOUT_FAILURE (D63). Off, the table, the row choice, ``url`` and
    ``customer_url`` are those from before D63."""
    recommendation = result.get("recommendation") or {}
    access_profile = result.get("access_profile")
    provider_status = result.get("provider_status")
    on = proceed_without_failure
    row, listed = select_row(recommendation, access_profile, provider_status, result["site_host"], result.get("official_paths"),
                             variants=on)
    if not listed:
        LOG.warning("response_guide_unmapped")  # a code only: no host, no text
    do, sentence = ROWS[row]
    if not on and row in ROWS_BEFORE:
        sentence = ROWS_BEFORE[row]
    customer = None
    if (do == "use_official_path" and row in CONSUMER_ROWS) or (on and do == "proceed"):
        raw = customer_url(result.get("official_paths"))
        customer = safe_url(raw) if raw else None
        if raw and customer is None:
            LOG.warning("response_guide_url_dropped")
        if customer:
            if on:
                sentence = PROCEED_CONSUMER_ROWS[row] if do == "proceed" else CONSUMER_ROWS[row]
            else:
                sentence = CONSUMER_ROWS_BEFORE.get(row, CONSUMER_ROWS[row])
    if on and do == "proceed" and customer is None and "official_path_available" in (recommendation.get("reasons") or ()):
        sentence = PROCEED_OFFICIAL_ROWS[row]
    retry = None
    if row in RETRY_ROWS:
        retry = _seconds((access_profile or {}).get("retry_after_seconds"))
    elif do == "wait_for_provider":
        retry = WAIT_SECONDS
    if "{n}" in sentence:
        sentence = sentence.replace("{n}", str(retry if retry is not None else 0))
    url = customer if on and customer and row in CUSTOMER_FIRST_ROWS else _url_for(do, result.get("official_paths"), provider_status,
                                                                              result.get("official_status_page"))
    action = {"do": do, "url": url, "retry_after_seconds": retry}
    if "robots_blocks_some_agents" in (recommendation.get("reasons") or ()) and access_profile:
        tokens = blocked_governing_tokens(access_profile)[:MAX_STOP_TOKENS]
        if tokens:
            action["stop_if_you_are"] = tokens
    if do == "lookup_apex_host":
        action["lookup_host"] = apex_host(result["site_host"])
    if customer:
        action["customer_url"] = customer
    out = {"summary_line": sentence, "next_action": action}
    found = links(on)
    if found:
        out["links"] = found
    return out
