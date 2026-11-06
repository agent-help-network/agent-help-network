# Frozen copy of ahn/response_guide.py as it was before D101 (main 27a9aac); the reference for the D101 comparison tests.
"""Server-made summary line, next action and feedback links for a lookup answer (D51).

Everything here is computed from the finished answer (``recommendation``, ``access_profile``, ``official_paths``,
``provider_status``) and never changes it. The sentences are a fixed table of English templates: the only value filled
in is a whole number of seconds, and no hostname, agent token, URL or text taken from a site or a provider ever reaches
a sentence. ``next_action.do`` is a pure function of ``recommendation.action`` (the four values that split ``unknown``
only say what can be done while no verdict exists). ``url`` is the row's own, except on a status_check use_official_path row (D76). When the host has a curated consumer path (D61), a use_official_path
row takes its CONSUMER_ROWS sentence and next_action gains ``customer_url``. Since D63 a proceed answer carries it too, and
on proceed rows and on the O1/O7 rows (no robots block on pages) the consumer path comes first: it is the sentence's lead
and ``url``, with a developer API as an "also listed" tail where the sentence has room. O2-O6 hand it to the user."""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from ahn.background_reads import FEED_RETRY_MINUTES
from ahn.lookup_signals import is_reserved_example
from ahn.official_paths import customer_url
from ahn.triage import blocked_governing_tokens, failure_observed

LOG = logging.getLogger(__name__)

SAFE_URL = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/%+=&?#-]*)?$")
URL_MAX = 200
LINE_MAX = 200
# D79: a status_check answer that rests on a robots block is the status sentence followed by the row's own robots sentence, so it
# may be longer than one sentence. Each part stays within LINE_MAX, the pair within this.
STATUS_LINE_MAX = 2 * LINE_MAX + 1
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
    # D85: the caller reported site_access and AHN's own home-page check (ahn_check) was refused too. H3-C (with a curated customer page) is EDGE_CONSUMER_ROWS.
    "H3": ("hand_off_to_human", "The site's edge refused both you and AHN's own home-page check (ahn_check). Do not switch tools or user agents to retry; tell your user, who can open the page themselves."),
    # D98: the caller reported site_access and AHN's own plain robots.txt read was refused too (no developer path). H3-R-C is EDGE_CONSUMER_ROWS.
    "H3-R": ("hand_off_to_human", "The site's edge refused both you and AHN's plain robots.txt read. Do not switch tools or user agents to retry; tell your user, who can open the page themselves."),
    "O1": ("use_official_path", "An official developer path is listed; for API work use it, not page automation; for a task in the user's own account, hand the step to your user. robots.txt is pending: look up once more in {n} s."),
    "O2": ("use_official_path", "The site's robots.txt blocks agent user agents at the site root. Stop retrying pages and use the listed official developer path instead."),
    "O3": ("use_official_path", "robots.txt blocks some agent tokens, and this applies to requests that send them (next_action.stop_if_you_are). Use the listed official developer path instead of page automation."),
    "O4": ("use_official_path", "The site's robots.txt blocks your vendor's agent under another name. Do not read that as permission; use the listed official developer path."),
    "O5": ("use_official_path", "The site refused even a plain automated robots.txt read. Page automation may be refused too; use the listed official developer path."),
    "O6": ("use_official_path", "The site's robots.txt disallows the root for agents it does not name (every token not in robots.agents_allowed). Do not read that as permission; use the listed official developer path."),
    "O7": ("use_official_path", "An official developer path is listed for this site. Prefer that documented API or portal over page automation for API work; for a task in the user's own account, hand the step to your user."),
    "O7-N": ("use_official_path", "An official developer API is listed with a no-sign-up option (official_paths.access.auth). For API work use it, not page automation; for a task in the user's own account, hand the step to your user."),
    "O7-K": ("use_official_path", "An official developer API needs a key or sign-up (official_paths.access.auth). For API work use one you have or ask before signing up; for a task in the user's own account, hand the step to your user."),
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
    "U6": ("no_clear_action", "The site refused even a plain automated robots.txt read, and no official path is listed. Page automation may be refused; hand off if needed."),
    "U7": ("no_clear_action", "The site's robots.txt could not be read, so no declared policy is known. No official path is listed; follow your operator's rules."),
    # D95: a service_error lookup on a host with a listed developer path and a robots.txt that could not be read (the path is reference).
    "U7-O": ("no_clear_action", "The site's robots.txt could not be read, so no declared policy is known. A developer API is listed (official_paths) for API work; for pages, tell your user."),
    # D95: the same for a host with no policy read of its own and no root domain to look up (a tenant host).
    "U9-O": ("no_clear_action", "No declared policy could be read for this host. A developer API is listed (official_paths) for API work; for pages, follow your operator's rules and tell your user."),
    "U8": ("lookup_apex_host", "Policy is read only for a site's main domain, not this subdomain. Look up next_action.lookup_host once, with the same fields you sent, for the site-wide declaration."),
    "U9": ("no_clear_action", "No declared policy could be read for this host and no official path is listed. Nothing here forbids the step; follow your operator's rules and the site's terms."),
}
# D69: a task_step=status_check lookup without a failure signal asks "is it up", so its summary line talks about status. Chosen from the
# provider_status / official_status_page fields of the answer; `do` and `url` stay those of the row. No URL or host is in a sentence.
STATUS_ROWS: dict[str, str] = {
    "T1": "The provider's status feed reports no active issue (provider_status). That covers the provider's own services, not your account; its page is next_action.status_url.",
    "T2": "A provider status feed is listed (provider_status), but it does not give a clear current state here; check the status page at next_action.status_url.",
    "T3": "AHN reads no status feed for this site, so it cannot say whether it is up. Its official status page is next_action.status_url; check it there.",
    "T3-S": "This site's status page needs your user's sign-in, so AHN cannot read it. To check if it is up, hand next_action.status_url to your user; next_action.do and url are for any other step on this site.",
    "T5": "AHN could not read this provider's status feed just now (provider_status), so it cannot say whether the site is up. Check the status page at next_action.status_url.",
    "T5-N": "AHN could not read this provider's status feed just now (provider_status), so it cannot say whether the site is up. Check the provider's own status page, or tell your user its status is unverified.",
    "T4": "AHN knows no official status page for this site, so it cannot say whether it is up. Check the site itself, or tell your user its status is unverified.",
    # D79: the status sentence put before a robots sentence. It never sends the caller to the site or to a page the robots block covers.
    "T4-R": "AHN knows no official status page for this site, so it cannot say whether it is up; tell your user its status is unverified.",
    "T3-R": "AHN reads no status feed for this site, so it cannot say whether it is up; tell your user its status is unverified.",
}
# D81 (wording only): the same status sentences when the page the sentence rests on is not what a status question needs.
# T3-A / T3-RA: the curated status page says it covers only the provider's API (official_status_page.covers == "api").
# T-EX: a name RFC 2606/6761 reserves for documentation; there is no service whose status could be checked.
STATUS_ROWS["T3-A"] = ("AHN reads no status feed for this site, so it cannot say whether it is up. Its status page, "
                       "next_action.status_url, covers only the provider's API, not the whole site; check it there for API status.")
STATUS_ROWS["T3-RA"] = ("AHN reads no status feed for this site, so it cannot say whether it is up; the status page it knows covers "
                        "only the provider's API. Tell your user the site's status is unverified.")
STATUS_ROWS["T-EX"] = "This is a reserved example domain (RFC 2606) for documentation; it runs no service with a status to check."
# D81: a robots sentence that follows the status sentence in a status_check answer says the developer path is for anything
# beyond the status question (an agent that only asks "is it up" was sent to partner API documents). Only the plain rows with
# no customer path; every sentence stays within LINE_MAX. Keyed by the row id, one table per wording of D63.
_BEYOND = "for anything beyond the status question, use the listed official developer path."
STATUS_AFTER_ROWS: dict[str, str] = {
    "O2": "The site's robots.txt blocks agent user agents at the site root. Stop retrying pages; " + _BEYOND,
    "O3": "robots.txt blocks some agent tokens, and this applies to requests that send them (next_action.stop_if_you_are). For anything beyond the status question, use the listed official developer path.",
    "O4": "The site's robots.txt blocks your vendor's agent under another name. Do not read that as permission; " + _BEYOND,
    "O5": "The site refused even a plain automated robots.txt read. Page automation may be refused too; " + _BEYOND,
    "O6": "The site's robots.txt disallows the root for agents it does not name. Do not read that as permission; " + _BEYOND,
}
STATUS_AFTER_ROWS_BEFORE: dict[str, str] = {
    "O3": "The site's robots.txt blocks some agent tokens (next_action.stop_if_you_are). For anything beyond the status question, use the listed official developer path.",
    "O6": "The site's robots.txt disallows the root for agents it does not name. Do not read that as permission; " + _BEYOND,
}
# D96: the status page itself failed to open for the caller (the looked-up host is the status page's host). The status answer cannot send the
# caller to that page, so it hands the step to the user with the reading AHN has (or says the status is unverified). Keyed by the T row.
STATUS_ROWS["T1-F"] = ("The provider's status feed reports no active issue (provider_status, see its fetched_at). That status page did not open "
                       "for you; give your user this reading, not a guess.")
STATUS_ROWS["T2-F"] = ("A provider status feed is listed (provider_status), but it gives no clear current state, and the status page did not "
                       "open for you. Tell your user the status is unverified.")
STATUS_ROWS["T3-F"] = ("AHN reads no status feed for this site, and its status page did not open for you, so AHN cannot say whether it is up. "
                       "Tell your user the status is unverified.")
STATUS_ROWS["T5-F"] = ("AHN could not read this provider's status feed just now, and the status page did not open for you. "
                       "Tell your user the status is unverified.")
# D100: an incident.io status page keeps its overall indicator at "none" while an incident that names no component is open, so the
# indicator alone cannot say "no issue". T6: the page lists an unresolved incident (provider_status.active_incidents) though the
# indicator reads operational. T1-U: the page has that shape and AHN did not read its incident list. T6-F: T6 and the status page
# did not open for the caller (a T1-U page that failed to open takes T2-F: the status is unverified). Information only: `do`, `url`
# and the recommendation are those of every other status_check row.
STATUS_ROWS["T6"] = ("The provider's status page lists an unresolved incident (provider_status.active_incidents), though its overall "
                     "indicator reads operational. Check next_action.status_url; it may be partly affected.")
STATUS_ROWS["T1-U"] = ("The provider's status feed's overall indicator reads operational, but AHN did not read its incident list. "
                       "Check next_action.status_url before telling your user it is up.")
STATUS_ROWS["T6-F"] = ("The provider's status page lists an unresolved incident (provider_status.active_incidents), and that page did "
                       "not open for you. Tell your user the service may be partly affected.")
STATUS_FAILED_ROW = {"T1": "T1-F", "T2": "T2-F", "T5": "T5-F", "T3": "T3-F", "T3-S": "T3-F", "T6": "T6-F", "T1-U": "T2-F"}
# D96: a failure of these kinds goes to a person whatever the lookup asked; the status answer does not apply to it.
STATUS_HANDOFF_ERRORS = frozenset({"human_verification", "login_auth"})
STATUS_KEEP_DO = frozenset({"wait_for_provider", "hand_off_to_human"})
# A row decided by the site's own robots policy keeps its sentence under status_check: the T sentences say nothing about the block.
STATUS_KEEP_REASONS = frozenset({"robots_blocks_agents", "robots_fetch_refused", "robots_wildcard_disallow", "robots_names_vendor_alias",
                                 "robots_blocks_some_agents"})
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
EDGE_CONSUMER_ROWS: dict[str, str] = {
    "H3": "The site's edge refused both you and AHN's own check (ahn_check). Do not switch tools to retry; hand your user the step and the site's customer page, next_action.customer_url.",
    "H3-R": "The site's edge refused both you and AHN's plain robots.txt read. Do not switch tools or user agents to retry; hand your user the site's customer page, next_action.customer_url.",
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
    "O5": "The site refused even a plain robots.txt read; pages may refuse too. Use the listed official path." + CONSUMER_HAND_TAIL,
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
# D66 (AHN_PROCEED_WITHOUT_FAILURE=d65): every proceed row says that no block is declared and that a refusal the caller saw came from
# its tool, the site's edge or a login, so it is told to its user. Replaces the P row's sentence; the verdict and row are unchanged.
_REFUSED = "If a page refused you, that came from your tool, the site's edge or a login; tell your user."
BLOCKED_PROCEED_ROWS: dict[str, str] = {
    "P1": "robots.txt blocks no listed agent vendor at the root. If a page refused you, the refusal came from your tool, the site's edge or a login; tell your user. Else continue modestly.",
    "P1-V": "robots.txt does not block your vendor's agent tokens at the root. " + _REFUSED + " Else continue.",
    "P2": "robots.txt blocks some agent tokens, none a listed vendor's. If yours is in access_profile.robots.agents_blocked, stop. If a page refused you, tell your user; else continue.",
    "P2-V": "robots.txt blocks other agents, not your vendor's. " + _REFUSED + " Else continue modestly.",
    "P3": "This site publishes no robots.txt, so nothing declared blocks agents. " + _REFUSED + " Else continue.",
}
BLOCKED_PROCEED_CONSUMER_ROWS: dict[str, str] = {
    "P1": "robots.txt blocks no listed agent vendor at the root. If a page refused you, tell your user; for a task in their own account, hand them next_action.customer_url.",
    "P1-V": "robots.txt does not block your vendor's agents at the root. If a page refused you, tell your user; for their own account, hand them next_action.customer_url.",
    "P2": "robots.txt blocks some agents, none a listed vendor's; stop if yours is in robots.agents_blocked. If refused, tell your user and hand them next_action.customer_url.",
    "P2-V": "robots.txt blocks other agents, not your vendor's. If a page refused you, tell your user; for their own account, hand them next_action.customer_url.",
    "P3": "This site publishes no robots.txt. If a page refused you, tell your user; for a task in their own account, hand them next_action.customer_url.",
}
# Rows where a curated consumer path is also next_action.url (the others keep the developer path). D65: a proceed row gives no url, so "continue" and "go here" do not compete; customer_url still names the page.
CUSTOMER_FIRST_ROWS = frozenset({"O1", "O7", "O7-N", "O7-K"})
# Rows whose retry_after_seconds is the pending profile's own value (a read is queued).
RETRY_ROWS = frozenset({"O1", "U1", "U2"})
# The last row of each action: where a combination the table does not list falls to (logged; tests forbid it).
# D87: the first line of a lookup that reports a failure says what the failure is. SERVICE_ERROR_ROWS replace the sentence of a proceed row
# (P1..P3) when the caller reported error_class=service_error: no refusal is implied, and AHN, which sees hosts and not single pages, says
# it cannot judge this page's 5xx. FAILED_U3 replaces U3 for any lookup with a failure signal (error_class or failure_stage): the robots
# condition stays, and the caller is told not to switch tools or user agents to retry. Only the sentence changes; the row, ``do`` and
# every other key are those of the row (with a curated customer path the 5xx sentence also hands the user next_action.customer_url). AHN_FAILURE_KIND_LINES=0 keeps the sentences before D87.
_FIVE_XX = "AHN sees hosts, not pages, so it cannot judge this 5xx. Retry once later; if it persists, tell your user."
SERVICE_ERROR_ROWS: dict[str, str] = {
    "P1": "robots.txt blocks no listed agent vendor at the root. " + _FIVE_XX,
    "P1-V": "robots.txt does not block your vendor's agent tokens at the root. " + _FIVE_XX,
    "P2": "robots.txt blocks some tokens; stop if yours is in robots.agents_blocked. " + _FIVE_XX,
    "P2-V": "robots.txt blocks other agents, not your vendor's. " + _FIVE_XX,
    "P3": "This site publishes no robots.txt, so nothing declared blocks agents. " + _FIVE_XX,
}
# With a curated customer path the 5xx sentence keeps the way to the person: the same prefixes, a shorter 5xx clause and the customer_url hand-over.
_FIVE_XX_CONSUMER = "AHN cannot judge a single page's 5xx. Retry once later; if it persists, tell your user and hand them next_action.customer_url."
SERVICE_ERROR_CONSUMER_ROWS: dict[str, str] = {row: sentence.removesuffix(_FIVE_XX) + _FIVE_XX_CONSUMER for row, sentence in SERVICE_ERROR_ROWS.items()}
# D92: a lookup that reports no response at all (failure_stage=before_target_response) is not a 5xx: nothing was received to be a status
# code. The same prefixes, then a clause that matches next_checks (check your tool and path first). Chosen only when error_class is
# service_error and failure_stage is before_target_response; any other service_error keeps the 5xx sentences above.
_NO_RESPONSE = "No response came (timeout or connection). Check your tool and path; retry once later; if it persists, tell your user."
NO_RESPONSE_ROWS: dict[str, str] = {row: sentence.removesuffix(_FIVE_XX) + _NO_RESPONSE for row, sentence in SERVICE_ERROR_ROWS.items()}
_NO_RESPONSE_CONSUMER = "No response came (timeout or connection). Check your tool; retry once later, then hand your user next_action.customer_url."
NO_RESPONSE_CONSUMER_ROWS: dict[str, str] = {row: sentence.removesuffix(_FIVE_XX) + _NO_RESPONSE_CONSUMER for row, sentence in SERVICE_ERROR_ROWS.items()}
# The list in next_action.stop_if_you_are holds the listed vendors' tokens that robots.txt blocks at the root, at most MAX_STOP_TOKENS; the
# file may block others too, so the sentence says "some tokens, including those", never "only".
_U3_STOP = "robots.txt blocks some tokens, including those in next_action.stop_if_you_are; if your requests send one, stop. "
FAILED_U3 = _U3_STOP + "Either way, do not switch tools or user agents to retry after a refusal; tell your user."
FAILED_U3_SERVICE_ERROR = _U3_STOP + "Either way, AHN cannot judge this 5xx. Retry once later; if it persists, tell your user."
FAILED_U3_NO_RESPONSE = _U3_STOP + "Either way, no response came; check your tool, retry once later, then tell your user."
# D95: the failure-kind sentences of U7-O (a developer API is listed, robots.txt unread; the failure is a 5xx or no response).
U7_O_NO_RESPONSE = "No response came (timeout or connection), and AHN could not read the site's robots.txt either. Check your tool and path; retry once later; then tell your user."
U7_O_SERVICE_ERROR = "AHN cannot judge a single page's 5xx, and could not read the site's robots.txt. Retry once later; if it persists, tell your user. API work: official_paths."
# D97: the same failure-kind sentences for the three rows that list no developer path (a customer path may be curated) (U6 the site refused AHN's plain robots.txt read,
# U7 robots.txt unread, U9 no policy read for the host). Chosen only with the D87 switch on, for error_class=service_error (no response, or any
# other 5xx). With a curated customer path the clause hands the user next_action.customer_url (``url`` stays null).
NO_PATH_ROWS = ("U6", "U7", "U9")
_NO_PATH_CAUSE = {"U6": "the site refused AHN's plain robots.txt read too", "U7": "AHN could not read the site's robots.txt either",
                  "U9": "AHN has no declared policy for this host"}
_NO_PATH_CAUSE_5XX = {"U6": "; the site also refused AHN's plain robots.txt read", "U7": ", and could not read the site's robots.txt",
                      "U9": ", and has no declared policy for this host"}
_NO_PATH_NO_RESPONSE = "No response came (timeout or connection), and "
_NO_PATH_5XX = "AHN cannot judge a single page's 5xx"
_NO_PATH_RETRY = "Retry once later; if it persists, tell your user."
NO_PATH_NO_RESPONSE_ROWS: dict[str, str] = {row: f"{_NO_PATH_NO_RESPONSE}{_NO_PATH_CAUSE[row]}. Check your tool and path; retry once later; then tell your user."
                                            for row in NO_PATH_ROWS}
NO_PATH_NO_RESPONSE_CONSUMER_ROWS: dict[str, str] = {row: f"{_NO_PATH_NO_RESPONSE}{_NO_PATH_CAUSE[row]}. Check your tool; retry once later, then hand your user next_action.customer_url."
                                                     for row in NO_PATH_ROWS}
NO_PATH_SERVICE_ERROR_ROWS: dict[str, str] = {row: f"{_NO_PATH_5XX}{_NO_PATH_CAUSE_5XX[row]}. {_NO_PATH_RETRY}" for row in NO_PATH_ROWS}
NO_PATH_SERVICE_ERROR_CONSUMER_ROWS: dict[str, str] = {row: f"{_NO_PATH_5XX}{_NO_PATH_CAUSE_5XX[row]}. Retry once later; if it persists, tell your user and hand them next_action.customer_url."
                                                       for row in NO_PATH_ROWS}
# D97: with no failure signal, a curated customer path replaces "no official path is listed": the page is named, ``url`` stays null.
_NO_PATH_CUSTOMER = "A customer page is listed (next_action.customer_url) for tasks a person does on the site."
NO_PATH_CONSUMER_ROWS: dict[str, str] = {
    "U6": "The site refused even a plain automated robots.txt read. Page automation may be refused; hand off if needed. " + _NO_PATH_CUSTOMER,
    "U7": "The site's robots.txt could not be read, so no declared policy is known. " + _NO_PATH_CUSTOMER + " Follow your operator's rules.",
    "U9": "No declared policy could be read for this host. " + _NO_PATH_CUSTOMER + " Follow your operator's rules and the site's terms.",
}
DEFAULT_ROW = {"wait_provider_incident": "W1", "hand_off_to_human": "H2", "use_official_path": "O7",
               "stop_declared_block": "S1", "proceed": "P1", "unknown": "U9"}


def apex_host(site_host: str) -> str | None:
    """The shortest registrable apex above site_host that the service reads policy for, else None."""
    from ahn.service import PUBLIC_SUFFIXES_2, aggregate_eligible_host  # service imports this module
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
        if "edge_refusal_confirmed" in reasons:
            return "H3", True
        if "edge_refusal_robots" in reasons:
            return "H3-R", True
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
                return ("U7-O" if row == "U7" and "official_path_available" in reasons else row), True
        if "profile_not_read" in reasons:
            if apex_host(site_host):
                return "U8", True
            return ("U9-O" if "official_path_available" in reasons else "U9"), True
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


def feed_unreadable(provider_status: dict | None) -> bool:
    """The status feed has no current reading: its last success is stale (older than FEED_STALE_HOURS, or never succeeded and
    failed, or its source no longer verifies). One failed read after a recent success is not: D46 keeps that statement."""
    return bool(provider_status and provider_status.get("freshness") == "stale")


def same_site(status_url: str, site_host: str) -> bool:
    """Whether the status page is served from the looked-up site's own registrable domain (www and other subdomains included)."""
    page = (urlparse(status_url).hostname or "").lower()
    site = site_host.lower()
    return bool(page) and (apex_host(page) or page) == (apex_host(site) or site)


def unresolved_incidents(provider_status: dict | None) -> bool:
    """D100: a fresh reading whose overall indicator reads operational and that lists at least one unresolved incident (a whole
    number of 1 or more; a missing or other count is not one). Only an incident.io page can be this: a Statuspage page with the
    indicator none is not asked for its lists."""
    if not (provider_status and provider_status.get("freshness") == "fresh" and provider_status.get("indicator") == "none"):
        return False
    count = provider_status.get("active_incidents")
    return type(count) is int and count >= 1


def status_row(provider_status: dict | None, official_status_page: dict | None, status_url: str | None,
               d100: bool = False, incidentio_unread: bool = False) -> str:
    """D69: the STATUS_ROWS id for a status_check lookup. A row that points at status_url needs one; without it T4.

    d100 (D100): a fresh "none" reading that lists an unresolved incident is T6, and one whose incident list was not read
    (incidentio_unread: the page has the incident.io shape and no count is stored) is T1-U, not T1."""
    if provider_status and feed_unreadable(provider_status):
        return "T5" if status_url else "T5-N"
    if status_url is None:
        return "T4"
    if provider_status:
        if d100 and unresolved_incidents(provider_status):
            return "T6"
        if (d100 and incidentio_unread and provider_status.get("freshness") == "fresh" and provider_status.get("indicator") == "none"
                and provider_status.get("active_incidents") is None):
            return "T1-U"
        return "T1" if provider_status.get("freshness") == "fresh" and provider_status.get("indicator") == "none" else "T2"
    if not official_status_page:
        return "T4"
    return "T3-S" if official_status_page.get("requires_sign_in") else "T3"


def status_check_applies(task_step: str | None, status_guide: bool, one_action: bool, error_class: str | None,
                         failure_stage: str | None) -> bool:
    """Whether guide() gets status_check=True. Before D96: task_step=status_check and no failure signal. With D96
    (AHN_D96_STATUS_CHECK_ONE_ACTION) a failure signal no longer switches the status answer off, except a human_verification
    or login_auth failure, which goes to a person."""
    if not (status_guide and task_step == "status_check"):
        return False
    if not failure_observed(error_class, failure_stage):
        return True
    return one_action and error_class not in STATUS_HANDOFF_ERRORS


def _status_url(result: dict, provider_status: dict | None) -> str | None:
    for candidate in ((provider_status or {}).get("source_url"), (result.get("official_status_page") or {}).get("source_url")):
        if candidate:
            found = safe_url(candidate)
            if found is None:
                LOG.warning("response_guide_url_dropped")
                continue
            return found
    return None


def _status_mode(recommendation: dict, row: str, do: str, status_url: str | None, status_id: str, site_host: str,
                 robots_kept: bool, failed: bool, one_action: bool, reserved: bool = False) -> str | None:
    """How a status_check answer treats the row: "self_failed" (D96: the status page is the host that failed), "official_path" (D76: the
    status page is the url of a use_official_path row), "widened" (D96: the same for any row that does not wait, hand off, stop or
    re-look-up), "sentence" (D69), "after" (D79: the robots sentence follows) or None (the row's own sentence)."""
    page_host = (urlparse(status_url).hostname or "").lower() if status_url else ""
    keeps = do in STATUS_KEEP_DO or do == ROWS["S1"][0] or row in RETRY_ROWS or row == "P2" or robots_kept
    if one_action and failed and status_url and not keeps and page_host == site_host.lower():
        return "self_failed"
    if (status_url and row not in RETRY_ROWS and not (robots_kept and same_site(status_url, site_host))
            # D77: a sign-in page is never the url, so under a robots block nothing justifies replacing the robots sentence.
            and not (robots_kept and status_id == "T3-S")):
        if recommendation.get("action") == "use_official_path":
            return "official_path"
        if one_action and not keeps and status_id != "T3-S" and not reserved:  # D81: a reserved example name has no service to check
            return "widened"
    if (do not in STATUS_KEEP_DO and do != ROWS["S1"][0] and row not in RETRY_ROWS and not robots_kept
            and row != "P2"
            and not (do == "lookup_apex_host" and status_url is None)):
        return "sentence"
    if (robots_kept and do not in STATUS_KEEP_DO and do != "proceed" and row not in RETRY_ROWS
            and not (do == "lookup_apex_host" and status_url is None)):
        return "after"
    return None


def guide(result: dict, proceed_without_failure: bool = True, blocked_default: bool = False, status_check: bool = False,
          status_guide: bool = False, error_class: str | None = None, failure_stage: str | None = None,
          failure_kind_lines: bool = False, one_action: bool = False, d100: bool = False, incidentio_unread: bool = False) -> dict:
    """The three added keys for a finished answer: summary_line, next_action and (when any is set) links.

    proceed_without_failure is AHN_PROCEED_WITHOUT_FAILURE (D63). Off, the table, the row choice, ``url`` and
    ``customer_url`` are those from before D63. blocked_default (D66, d65 mode, not for a status_check or preflight lookup)
    swaps the sentence of a proceed row for the BLOCKED_PROCEED rows. status_check (D69, only when the caller sent task_step=status_check
    and no failure signal) replaces the sentence by a STATUS_ROWS one unless the row waits, hands off, stops, waits for a re-lookup
    (O1, U1, U2) or rests on a robots block. status_guide (the same switch, D69) adds next_action.status_url to any answer whose
    status page is known, whatever the task_step. ``do`` and the other keys are those of the row. The one exception (D76): on a
    status_check use_official_path row without a re-lookup, ``url`` is the status page, unless a robots block applies and the
    status page is on the looked-up site's own domain. D77: a status page that needs the user's sign-in (T3-S) is never the
    ``url`` (it goes out as ``status_url`` only), and under a robots block it keeps the robots sentence too.
    d100 (D100, AHN_D100_INCIDENTIO_INCIDENTS, only with status_check): the status sentence follows the incidents the page lists:
    T6 for a fresh "none" reading that lists an unresolved incident, T1-U (incidentio_unread) for an incident.io page whose list
    was not read, T6-F when that status page failed to open for the caller. Nothing but the sentence changes.
    failure_kind_lines (D87, AHN_FAILURE_KIND_LINES, with the D63 switch on, never for a status_check lookup) lets the sentence of a
    lookup that reports a failure (error_class or failure_stage) follow the kind of failure: SERVICE_ERROR_ROWS for a proceed row when
    error_class is service_error, FAILED_U3 for U3. Nothing but the sentence changes.
    one_action (D96, AHN_D96_STATUS_CHECK_ONE_ACTION, only with status_check): a status_check answer gives the status page as the one next
    action (do use_official_path, url the status page) on every row that does not wait, hand off, stop, wait for a re-lookup or rest on a
    robots block, not only on a use_official_path row; and when the lookup reports a failure and the status page is on the looked-up host
    itself, it hands the step to the user (do hand_off_to_human, url null, STATUS_ROWS T*-F). A lookup that reports a failure keeps its own
    sentence (D87) wherever no status sentence applies, and on a robots block unless the status page is on another site. With no status page
    known, a developer path is never the url: url is null, do is hand_off_to_human after a failure (sentence T4-R) or no_clear_action.
    D97 (failure_kind_lines, never for a status_check lookup): U6, U7 and U9 (no official path of any kind) take the NO_PATH_* sentences after
    a service_error (no response or 5xx), and, with a curated customer path, name next_action.customer_url (``url`` stays null; with no
    failure signal NO_PATH_CONSUMER_ROWS). Nothing but the sentence, and customer_url on those rows, changes."""
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
    if (do == "use_official_path" and row in CONSUMER_ROWS) or (on and (do == "proceed" or row in ("H3", "H3-R"))):
        raw = customer_url(result.get("official_paths"))
        customer = safe_url(raw) if raw else None
        if raw and customer is None:
            LOG.warning("response_guide_url_dropped")
        if customer:
            if row in ("H3", "H3-R"):
                sentence = EDGE_CONSUMER_ROWS[row]  # D85 (D98: H3-R the same way): only reached with the D63 switch on, like the proceed answer it replaces
            elif on:
                sentence = PROCEED_CONSUMER_ROWS[row] if do == "proceed" else CONSUMER_ROWS[row]
            else:
                sentence = CONSUMER_ROWS_BEFORE.get(row, CONSUMER_ROWS[row])
    if on and do == "proceed" and customer is None and "official_path_available" in (recommendation.get("reasons") or ()):
        sentence = PROCEED_OFFICIAL_ROWS[row]
    if on and blocked_default and do == "proceed":
        sentence = (BLOCKED_PROCEED_CONSUMER_ROWS if customer else BLOCKED_PROCEED_ROWS)[row]
    status_url = _status_url(result, provider_status) if (status_check or status_guide) else None
    mode = None
    status_id = None
    if status_check:
        failed = bool(one_action and (error_class is not None or failure_stage is not None))
        status_id = status_row(provider_status, result.get("official_status_page"), status_url, d100, incidentio_unread)
        robots_kept = bool(set(recommendation.get("reasons") or ()) & STATUS_KEEP_REASONS)
        mode = _status_mode(recommendation, row, do, status_url, status_id, result["site_host"], robots_kept, failed, one_action,
                            is_reserved_example(result["site_host"]))
        if failed and mode in (None, "after"):
            status_check = False  # D96: no status sentence applies to this failed lookup; it is answered as before D96
            mode = None
    if on and failure_kind_lines and not status_check and (error_class is not None or failure_stage is not None):
        if do == "proceed" and error_class == "service_error":
            no_response = error_class == "service_error" and failure_stage == "before_target_response"
            table = (NO_RESPONSE_CONSUMER_ROWS if customer else NO_RESPONSE_ROWS) if no_response else (SERVICE_ERROR_CONSUMER_ROWS if customer else SERVICE_ERROR_ROWS)
            sentence = table[row]
        elif row in ("U7-O", "U9-O") and error_class == "service_error":
            sentence = U7_O_NO_RESPONSE if failure_stage == "before_target_response" else U7_O_SERVICE_ERROR
        elif row == "U3":
            if error_class == "service_error":
                sentence = FAILED_U3_NO_RESPONSE if failure_stage == "before_target_response" else FAILED_U3_SERVICE_ERROR
            else:
                sentence = FAILED_U3
    if on and failure_kind_lines and not status_check and row in NO_PATH_ROWS:
        # D97: U6/U7/U9 (no official path of any kind) after a 5xx or no response, or with a curated customer path and no failure signal
        signal = error_class is not None or failure_stage is not None
        if error_class == "service_error" or not signal:
            raw = customer_url(result.get("official_paths"))
            customer = safe_url(raw) if raw else None
            if raw and customer is None:
                LOG.warning("response_guide_url_dropped")
            if error_class == "service_error":
                if failure_stage == "before_target_response":
                    sentence = (NO_PATH_NO_RESPONSE_CONSUMER_ROWS if customer else NO_PATH_NO_RESPONSE_ROWS)[row]
                else:
                    sentence = (NO_PATH_SERVICE_ERROR_CONSUMER_ROWS if customer else NO_PATH_SERVICE_ERROR_ROWS)[row]
            elif customer:
                sentence = NO_PATH_CONSUMER_ROWS[row]
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
    if (status_check or status_guide) and status_url:
        action["status_url"] = status_url
    if status_check:
        reserved = is_reserved_example(result["site_host"])  # D81
        covers_api = (result.get("official_status_page") or {}).get("covers") == "api"
        if mode == "self_failed":
            # D96: the status page is what failed; sending the caller back to it (or to a developer page) answers nothing.
            sentence = STATUS_ROWS[STATUS_FAILED_ROW[status_id]]
            action["do"], action["url"] = "hand_off_to_human", None
            action.pop("lookup_host", None)
        elif mode in ("official_path", "widened"):
            # D76: a use_official_path row with no re-lookup to wait for answers "is it up" with the status page: sentence and
            # url say the same thing. A robots block on the site's own pages does not answer the question, but a status page on the
            # same site is not pointed at either (the row stays). D96 (widened): any other row that does not wait, hand off, stop or
            # re-look-up does the same, so a status question has one action; ``do`` becomes use_official_path.
            sentence = STATUS_ROWS["T3-A" if status_id == "T3" and covers_api else status_id]
            if status_id != "T3-S":  # D77: a status page that needs the user's sign-in is handed over as status_url, never made the url
                action["url"] = status_url
            if mode == "widened":
                action["do"] = "use_official_path"
                action.pop("lookup_host", None)
        elif mode == "sentence":
            # D77: P2 (a block naming other agents, no agent_vendor sent) ends in a conditional stop that no T sentence carries, so it
            # keeps its own sentence; a subdomain with no status page of its own keeps U8 (the site-wide lookup may carry the status).
            lead = status_id
            if status_id == "T4" and failed and not reserved:
                lead = "T4-R"  # D96: the lookup reports a failure, so "check the site itself" is not what it needs; say it is unverified
            sentence = STATUS_ROWS["T-EX" if reserved else "T3-A" if status_id == "T3" and covers_api else lead]
            if one_action and status_url is None and action["url"] is not None and action["url"] != customer:
                # D96: no status page is known, so the row's url can only be a developer path, which is never the url of a status
                # answer (it stays in official_paths). The step is the user's when a failure was reported, else it has no action.
                action["url"] = None
                action["do"] = "hand_off_to_human" if failed else "no_clear_action"
                action.pop("lookup_host", None)
        elif mode == "after":
            # D79: the first sentence answers "is it up"; the row's robots sentence is kept, after it. ``do`` and ``url`` stay.
            # The status sentence does not say "check the site itself" (the robots sentence that follows says otherwise), and a status
            # page on the looked-up site's own domain is not pointed at (D76).
            lead = status_id
            if status_id == "T4" or (status_id == "T3" and same_site(status_url, result["site_host"])):
                lead = "T4-R" if status_id == "T4" else "T3-R"
            if lead == "T3-R" and covers_api:
                lead = "T3-RA"
            if reserved:
                lead = "T-EX"
            elif lead == "T3" and covers_api:
                lead = "T3-A"
            if customer is None:  # D81: the developer path is not a way to check status
                sentence = (STATUS_AFTER_ROWS if on else STATUS_AFTER_ROWS_BEFORE).get(row, sentence)
            sentence = f"{STATUS_ROWS[lead]} {sentence}"
    out = {"summary_line": sentence, "next_action": action}
    found = links(on)
    if found:
        out["links"] = found
    return out
