"""Ask Agent Help Network when a Browser Use agent is stopped by a block page, and hand its answer back to the model.

The only Browser Use code here is ``register_tool`` (a custom tool registered with ``@tools.action``); everything else is
plain Python, so the branches on ``next_action.do`` can be tested without Browser Use installed.

What leaves the machine: the hostname of the page and two fixed codes (an error class and ``via=browser``). No path,
query, page text or title. What comes back is read, never acted on by this file: it opens no page, solves nothing and logs
in nowhere. Every branch ends in "use the official path", "ask the user", "wait", "look up once more" or "stop".
"""

import asyncio
import math
import time
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

import ahn_client as ahn
from ahn_client._hosts import normalize_host  # the client's own host rule (case, trailing dot, port, www); not copied here

ADAPTER = "browser-use"  # goes into the User-Agent (ahn-client/<version> (+browser-use; ...)) so the service can count this path

# What the model reports about the page it is stuck on -> the service's error_class.
ERROR_CLASS = {
    "access_denied": "site_access",                   # 403, "access denied", blocked
    "verification_challenge": "human_verification",   # CAPTCHA or "verify you are human"
    "login_required": "login_auth",                   # a sign-in wall
    "server_error": "service_error",                  # 429, 5xx, timeouts
}

# next_action.do, from docs/api-contract.en.md ("Summary line and next action").
DO_VALUES = ("wait_for_provider", "hand_off_to_human", "use_official_path", "stop_automation", "proceed",
             "retry_lookup_once", "stop_if_listed", "lookup_apex_host", "no_clear_action")
# For an answer without next_action (AHN_RESPONSE_GUIDE=0 on the service): the contract defines do as a function of the
# recommendation, so the same branch can be taken. "unknown" splits four ways on the service; here it is the cautious one.
DO_FOR_RECOMMENDATION = {"wait_provider_incident": "wait_for_provider", "hand_off_to_human": "hand_off_to_human",
                         "use_official_path": "use_official_path", "stop_declared_block": "stop_automation",
                         "proceed": "proceed", "unknown": "no_clear_action"}

WAIT_STEP = 30  # Browser Use's `wait` action sleeps at most this many seconds per call (0.13.14)
NO_BYPASS = "Do not try to get around the block."

NO_ANSWER = ("Agent Help Network gave no answer (not a public hostname, lookups disabled, or no connection). Do not retry the page in a loop. "
             "Tell the user what the page showed and ask how to continue. " + NO_BYPASS)


def read_answer(answer: Any) -> Tuple[str, Dict[str, Any]]:
    """(summary_line, next_action) from an ``ahn_client.Result`` (0.6.0). The client has already checked every field; ``do`` falls
    back to the recommendation when the service sent no ``next_action``, and to ``no_clear_action`` when it sent one this client
    cannot read."""
    line = answer.summary_line or answer.summary()  # the service's sentence, else the client's own line from validated fields
    source = answer.next_action
    if source is not None:
        seconds = source.retry_after_seconds
        if seconds is None and source.do == "retry_lookup_once":
            seconds = answer.lookup_again_seconds  # the profile's own wait, when next_action carries none
        return line, {"do": source.do, "url": source.url, "retry_after_seconds": seconds,
                      "stop_if_you_are": list(source.stop_if_you_are), "lookup_host": source.lookup_host}
    do = "no_clear_action" if answer.raw.get("next_action") is not None else DO_FOR_RECOMMENDATION.get(answer.recommendation, "no_clear_action")
    return line, {"do": do, "url": None, "retry_after_seconds": None, "stop_if_you_are": [], "lookup_host": None}


def _host_key(url: str) -> str:
    """The key for the retry bookkeeping (never sent anywhere): the host as ahn-client reads it, so ``Example.com``,
    ``example.com.``, ``example.com:443`` and ``www.example.com`` are one host. Not a public hostname: the page URL, lower case."""
    return normalize_host(url) or str(url).strip().lower()


def instruction(action: Mapping[str, Any], *, repeated_retry: bool = False) -> str:
    """The sentence(s) that tell the model what to do for one ``next_action.do``. Text only: nothing is performed here."""
    do, url, seconds = action["do"], action.get("url"), action.get("retry_after_seconds")
    if do == "retry_lookup_once" and repeated_retry:
        do = "no_clear_action"  # contract: a second retry_lookup_once is read as no_clear_action
    if do == "wait_for_provider":
        where = f" Status page: {url}." if url else ""
        later = f" The earliest useful re-check is in {seconds} s." if seconds else ""
        return "The provider reports a problem on its side. Stop working on this site now and tell the user; do not reload in a loop." + where + later
    if do == "hand_off_to_human":
        return ("This step needs the user (sign-in, permission or human verification). Stop and ask the user to do it themselves in their own "
                "browser. Never ask for or type credentials, and do not try to solve the check. " + NO_BYPASS)
    if do == "use_official_path":
        where = f" Documentation: {url}." if url else ""
        return "Do not keep driving this site's pages. Use its official API or developer path instead, or tell the user it is needed." + where
    if do == "stop_automation":
        return "The site declares automated agents blocked and lists no official path. Stop automating this site and tell the user. " + NO_BYPASS
    if do == "proceed":
        return ("The site's declared policy does not block agents like this one, but that is not a guarantee the page will load. "
                "Retry the page at most once, slowly; if it fails again, stop and tell the user. " + NO_BYPASS)
    if do == "retry_lookup_once":
        if seconds:
            wait = (f"Wait about {seconds} s (the wait action takes at most {WAIT_STEP} s per call, so repeat it), then call this tool again, "
                    "one time only. If you call it earlier it only reports the time left and does not use up that one lookup.")
        else:
            wait = "Call this tool again, one time only."
        return f"The site's policy is still being read. {wait} Do not poll. Do not reload the page meanwhile."
    if do == "stop_if_listed":
        names = ", ".join(action.get("stop_if_you_are") or []) or "(none listed)"
        return (f"The site blocks some agents by name: {names}. This answer cannot say whether that applies to this setup; do not guess. "
                "Stop retrying this page and ask the user whether their setup identifies as one of these names.")
    if do == "lookup_apex_host":
        host = action.get("lookup_host")
        return (f"Policy is read only for the site's main domain. Call this tool once with {host}." if host
                else "Policy is read only for the site's main domain, not this subdomain. Call this tool once with that main domain.")
    return "The answer gives no clear next step. Treat the site's policy as unknown, not as permission: stop retrying and ask the user how to continue. " + NO_BYPASS


class BlockGuide:
    """Looks a blocked page's host up and turns the answer into the text the model reads. Holds the state the contract needs for
    ``retry_lookup_once``: the one repeat lookup is only worth making once the service's wait (5 to 300 s) is over, and ahn-client
    answers any earlier call from its cache. So a call before that deadline makes no lookup and is not counted; the first call after
    it is the repeat, and a second ``retry_lookup_once`` then counts as ``no_clear_action``."""

    def __init__(self, *, timeout: float = 3.0, agent_vendor: Optional[str] = None, check=ahn.check,
                 clock: Callable[[], float] = time.monotonic):
        self.timeout, self.agent_vendor, self._check, self._clock = timeout, agent_vendor, check, clock
        self._retry_until: Dict[str, float] = {}  # host -> clock() time after which the one repeat lookup is due

    def lookup(self, url: str, reason: str) -> str:
        key = _host_key(url)
        deadline = self._retry_until.get(key)
        if deadline is not None and self._clock() < deadline:
            left = max(1, math.ceil(deadline - self._clock()))
            return (f"Agent Help Network is still reading this site's policy; {left} s left. No lookup was made and your one repeat lookup is "
                    f"not used up. Use the wait action in steps of up to {WAIT_STEP} s, then call this tool again. Do not reload the page meanwhile.")
        error_class = ERROR_CLASS.get(reason, "site_access")
        # Blocks for at most `timeout` seconds and never raises (only a wrong agent_vendor does).
        answer = self._check(url, error_class, "response_observed", timeout=self.timeout, adapter=ADAPTER, via="browser",
                             agent_vendor=self.agent_vendor)
        if answer is None:
            return NO_ANSWER
        line, action = read_answer(answer)
        repeated = False
        if action["do"] == "retry_lookup_once":
            if key in self._retry_until:
                repeated = True  # the deadline has passed, so this was the repeat lookup and the service still says "retry"
            else:
                self._retry_until[key] = self._clock() + (action["retry_after_seconds"] or 0)  # after the check: the client's cache ends by then
        else:
            self._retry_until.pop(key, None)
        shown = "no_clear_action" if repeated else action["do"]
        return f"{line}\nNext step ({shown}): {instruction(action, repeated_retry=repeated)}"


def register_tool(tools, guide: Optional[BlockGuide] = None):
    """Register ``check_blocked_site`` on a Browser Use ``Tools`` object (Browser Use 0.13: ``@tools.action``)."""
    # No `from __future__ import annotations` in this file: Browser Use builds the tool's parameter model from the real annotations.
    from typing import Literal

    from browser_use import ActionResult

    guide = guide or BlockGuide()

    @tools.action(
        description="Call this when the current page is a 403 or 'access denied', a CAPTCHA or 'verify you are human' page, a login wall, "
                    "a 429, or a server error (5xx). Pass the page URL and which kind of block it is. It asks Agent Help Network what the "
                    "site declares for agents and where its official API is, and returns one sentence plus the next step. Read-only; only "
                    "the hostname is sent. Do the next step it names: use the official path, ask the user, wait, look up once more, or stop. "
                    "Never try to get past the block."
    )
    async def check_blocked_site(url: str, reason: Literal["access_denied", "verification_challenge", "login_required", "server_error"]) -> ActionResult:
        text = await asyncio.to_thread(guide.lookup, url, reason)
        return ActionResult(extracted_content=text)

    return check_blocked_site
