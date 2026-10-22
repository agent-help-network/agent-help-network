"""Server-made summary line, next action and feedback links for a lookup answer (D51).

Everything here is computed from the finished answer (``recommendation``, ``access_profile``, ``official_paths``,
``provider_status``) and never changes it. The sentences are a fixed table of English templates: the only value filled
in is a whole number of seconds, and no hostname, agent token, URL or text taken from a site or a provider ever reaches
a sentence. ``next_action.do`` is a pure function of ``recommendation.action`` (the four values that split ``unknown``
only say what can be done while no verdict exists)."""

from __future__ import annotations

import logging
import re

from .background_reads import FEED_RETRY_MINUTES
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

WAIT_SECONDS = FEED_RETRY_MINUTES * 60
_WAIT_IN = f"look up again in about {FEED_RETRY_MINUTES} minutes"

# Template id -> (do, sentence). "{n}" is the only placeholder.
ROWS: dict[str, tuple[str, str]] = {
    "W1": ("wait_for_provider", f"The provider reports an active issue. Wait instead of retrying, and {_WAIT_IN}; its status page lists the affected services."),
    "W2": ("wait_for_provider", f"The provider reports scheduled maintenance. Wait for it to end instead of retrying in a loop, and {_WAIT_IN}."),
    "W3": ("wait_for_provider", f"The provider reports an issue that may reach only some regions. If a service you use is affected, wait; {_WAIT_IN}."),
    "W4": ("wait_for_provider", f"The provider's status page shows a major or critical problem beyond any posted incident. Wait instead of retrying; {_WAIT_IN}."),
    "H1": ("hand_off_to_human", "This step needs human verification. Hand it to the user through your normal handoff; do not try to solve or bypass the check."),
    "H2": ("hand_off_to_human", "This step likely needs the user's sign-in or permission. Hand it to the user; never ask for, store or pass on credentials."),
    "O1": ("use_official_path", "An official developer path is listed; use it instead of page automation. The site's robots.txt is still being read: look up once more in {n} s."),
    "O2": ("use_official_path", "The site's robots.txt blocks agent user agents at the site root. Stop retrying pages and use the listed official developer path instead."),
    "O3": ("use_official_path", "The site's robots.txt blocks some agent tokens (next_action.stop_if_you_are). Use the listed official developer path instead of page automation."),
    "O4": ("use_official_path", "The site's robots.txt blocks your vendor's agent under another name. Do not read that as permission; use the listed official developer path."),
    "O5": ("use_official_path", "The site refused even a plain automated robots.txt read. Expect page automation to be refused too; use the listed official developer path."),
    "O6": ("use_official_path", "The site's robots.txt disallows the root for agents it does not name. Do not read that as permission; use the listed official developer path."),
    "O7": ("use_official_path", "An official developer path is listed for this site. Prefer that documented API or portal over page automation."),
    "S1": ("stop_automation", "The site's robots.txt explicitly blocks agent user agents at the site root, and no official path is listed. Stop automated retries; hand the step to the user if needed."),
    "P1": ("proceed", "The site's robots.txt does not block the agent tokens checked for you at the root. Continue with modest requests; declared policy is not an access guarantee."),
    "P2": ("proceed", "The site's robots.txt blocks some other agents, not the tokens checked for you. Continue with modest requests; this is not an access guarantee."),
    "U1": ("retry_lookup_once", "The site's robots.txt is being read now. Look up once more in {n} s; if the answer is still pending then, treat the policy as unknown. Do not poll."),
    "U2": ("no_clear_action", "An earlier read of the site's robots.txt failed and another is queued. Treat its policy as unknown for now; one more lookup in {n} s may help."),
    "U3": ("stop_if_listed", "The site's robots.txt blocks some agent tokens. If your requests send one in next_action.stop_if_you_are, stop retrying pages; else send agent_vendor."),
    "U4": ("no_clear_action", "The site's robots.txt blocks your vendor's agent under another name. Do not read that as permission; tell the user before automating pages."),
    "U5": ("no_clear_action", "The site's robots.txt disallows the root for agents it does not name, and no official path is listed. Do not read this as permission."),
    "U6": ("no_clear_action", "The site refused even a plain automated robots.txt read, and no official path is listed. Expect page automation to be refused; hand off if needed."),
    "U7": ("no_clear_action", "The site's robots.txt could not be read, so its agent policy is unknown, not permissive. No official path is listed; follow your operator's rules."),
    "U8": ("lookup_apex_host", "Policy is read only for a site's main domain, not this subdomain. Look up next_action.lookup_host for the site-wide declaration."),
    "U9": ("no_clear_action", "No declared policy or official path is available for this host. Treat its agent policy as unknown, not as permission."),
}
DO_VALUES = tuple(dict.fromkeys(do for do, _ in ROWS.values()))
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
               site_host: str) -> tuple[str, bool]:
    """(template id, whether the table lists this combination). First match in table order within the action."""
    action = recommendation.get("action")
    reasons = set(recommendation.get("reasons") or ())
    if action == "wait_provider_incident":
        if "provider_scope_partial" in reasons:
            return "W3", True
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
            return "O7", True
    elif action == "stop_declared_block":
        if "robots_blocks_agents" in reasons:
            return "S1", True
    elif action == "proceed":
        if "robots_blocks_not_your_agent" in reasons:
            return "P2", True
        if "robots_allows_agents" in reasons:
            return "P1", True
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


def links() -> dict | None:
    found = {key: value for key, value in (("feedback", FEEDBACK_URL), ("changelog", CHANGELOG_URL)) if value}
    return found or None


def guide(result: dict) -> dict:
    """The three added keys for a finished answer: summary_line, next_action and (when any is set) links."""
    recommendation = result.get("recommendation") or {}
    access_profile = result.get("access_profile")
    provider_status = result.get("provider_status")
    row, listed = select_row(recommendation, access_profile, provider_status, result["site_host"])
    if not listed:
        LOG.warning("response_guide_unmapped")  # a code only: no host, no text
    do, sentence = ROWS[row]
    retry = None
    if row in RETRY_ROWS:
        retry = _seconds((access_profile or {}).get("retry_after_seconds"))
    elif do == "wait_for_provider":
        retry = WAIT_SECONDS
    if "{n}" in sentence:
        sentence = sentence.replace("{n}", str(retry if retry is not None else 0))
    action = {"do": do, "url": _url_for(do, result.get("official_paths"), provider_status, result.get("official_status_page")),
              "retry_after_seconds": retry}
    if "robots_blocks_some_agents" in (recommendation.get("reasons") or ()) and access_profile:
        tokens = blocked_governing_tokens(access_profile)[:MAX_STOP_TOKENS]
        if tokens:
            action["stop_if_you_are"] = tokens
    if do == "lookup_apex_host":
        action["lookup_host"] = apex_host(result["site_host"])
    out = {"summary_line": sentence, "next_action": action}
    found = links()
    if found:
        out["links"] = found
    return out
