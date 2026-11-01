"""``AHNLookupTool``: a LangChain tool the model calls when a web step is blocked or failing.

It asks Agent Help Network what a host declares for agents (its own robots.txt and llms.txt), where its official
developer path is, what the provider's status feed says, and gives one recommended action. It only reads. The lookup
sends the hostname and fixed codes; a URL path, a query, a body and credentials are never sent. The answer is a one-line
summary followed by a small JSON object built from validated fields only (free text from a site or a feed is not
copied into it). The tool never raises for a failed or slow lookup (2 seconds at most): it says there was no answer.
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Literal, Optional, Type

from ahn_client._adapter import DEFAULT_TIMEOUT, abounded_check, bounded_check
from ahn_client._hosts import eligible_host
from ahn_client._result import Result
from ahn_client._signals import valid_signals
from langchain_core.callbacks import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

ADAPTER = "langchain"  # the label that goes into the User-Agent, the same as the middleware's

NOT_LOOKED_UP = (
    "Agent Help Network was not asked: this is not a public hostname (local, private, an address, a single label) "
    "or lookups are disabled in this environment. Nothing was sent."
)
NO_ANSWER = (
    "Agent Help Network gave no answer for this host (no connection, a slow lookup, or an unreadable reply). "
    "Nothing was changed; continue with your own judgment."
)

DESCRIPTION = (
    "Look up a public website's hostname on Agent Help Network when a web step is blocked or failing: a 403 or "
    "'access denied', a login wall, a CAPTCHA or 'verify you are human' page, a 429, a 5xx or a timeout, or when "
    "asked whether a site is down or blocks agents. After a failure, send error_class and failure_stage; for a pure "
    "up/down check where nothing was refused, send task_step='status_check'. Returns the host's declared agent policy (robots.txt, "
    "llms.txt), its official developer path, provider status and one recommended action, as one summary line "
    "plus JSON. Read-only. Only the hostname and the optional codes are sent, never a URL path, query or content. "
    "The answer points to official paths, waiting, or a person; it never explains how to get around a site's controls."
)


class AHNLookupInput(BaseModel):
    """Arguments of the lookup. Only these leave the machine."""

    hostname: str = Field(description="The public hostname of the site that blocked or failed, for example 'example.com'. Do not include a path or query.")
    error_class: Optional[Literal["site_access", "login_auth", "human_verification", "service_error"]] = Field(
        default=None,
        description="What happened: site_access (403/451/402 or a refusal), login_auth (401 or a login wall), "
        "human_verification (a CAPTCHA or challenge page), service_error (429, 408, 5xx, a timeout). Leave out if unsure.")
    failure_stage: Optional[Literal["before_target_response", "response_observed", "access_denial_observed", "unknown"]] = Field(
        default=None,
        description="Where it failed: before_target_response (no connection or no reply), response_observed (a reply "
        "arrived), access_denial_observed (an explicit denial arrived), unknown. Leave out if unsure.")
    http_status: Optional[int] = Field(
        default=None, description="The HTTP status the site returned, only one of 401, 402, 403, 407, 408, 429, 451 or 500-599. Leave out if unknown.")
    challenge: Optional[Literal["header", "none"]] = Field(
        default=None, description="'header' if the reply carried a challenge marker header, else 'none'. Leave out if unknown.")
    retry_after: Optional[Literal["none", "lt_60", "60_3600", "gt_3600"]] = Field(
        default=None, description="The Retry-After the site sent, as a band in seconds. Leave out if unknown.")
    via: Optional[Literal["http_fetch", "browser", "unknown"]] = Field(
        default=None, description="How the page was requested. Leave out if unknown.")
    task_step: Optional[Literal["status_check"]] = Field(
        default=None,
        description="Send 'status_check' only when you are just checking whether the site is up (or re-checking an "
        "earlier answer) and nothing was refused. Leave out after a failure: send error_class and failure_stage instead.")


def format_result(result: Result, site_retry_after: Any = None) -> str:
    """The one-line summary, a blank line, then a JSON object of validated fields.

    The summary starts with the service's own advice (``summary_line``) when it sent one. ``next_action`` and ``remember``
    are the service's guidance as validated fields. ``site_retry_after`` is the site's Retry-After as seconds or a band code;
    it only lengthens ``retry_after_seconds`` here and is never sent anywhere."""
    data = {
        "host": result.host,
        "recommendation": result.recommendation,
        "confidence": result.confidence,
        "reasons": list(result.reasons),
        "evidence_level": result.evidence_level,
        "next_checks": list(result.next_checks),
    }
    if result.official_url:
        data["official_url"] = result.official_url
    if result.blocked_agents:
        data["blocked_agents"] = list(result.blocked_agents)
    wait = result.wait_seconds(site_retry_after)  # None after a stop, a hand-off or an official path
    if wait is not None:
        data["retry_after_seconds"] = wait
    if result.lookup_again_seconds is not None:
        data["lookup_again_seconds"] = result.lookup_again_seconds  # the policy is being read: one repeat lookup, not a site wait
    if result.next_action is not None:
        data["next_action"] = result.next_action.to_dict()
    if result.remember:
        data["remember"] = result.remember
    data["details_url"] = result.url
    return result.summary(site_retry_after) + "\n\n" + json.dumps(data, separators=(",", ":"))


_warned = False


def _unexpected(error: Exception) -> str:
    """An exception that is not a network failure (those never raise) means a bug or an incompatible ahn-client:
    say so once, as a warning, and still give the agent a plain no-answer text so its run goes on."""
    global _warned
    if not _warned:
        _warned = True
        warnings.warn(
            f"langchain-ahn: the lookup failed unexpectedly ({type(error).__name__}: {error}); "
            "this may be an incompatible ahn-client version. The tool returns 'no answer' meanwhile.",
            RuntimeWarning, stacklevel=3)
    return NO_ANSWER


def _target(hostname: str, error_class: Any, failure_stage: Any, http_status: Any, challenge: Any,
            retry_after: Any, via: Any, task_step: Any = None) -> Any:
    """``(host, error_class, failure_stage, codes)``, or None when nothing may be sent."""
    if not isinstance(hostname, str):
        return None
    hostname = hostname.strip()
    if any(c.isspace() or ord(c) < 32 for c in hostname):
        return None  # a hostname has no whitespace or control characters; do not let eligible_host glue the parts together
    host = eligible_host(hostname)
    if host is None:
        return None
    codes = valid_signals({"http_status": http_status, "challenge": challenge, "retry_after": retry_after, "via": via})
    if task_step == "status_check":  # the one word this tool sends; the service reads it as "nothing was refused"
        codes = {**codes, "task_step": task_step}
    return host, error_class, failure_stage, codes or None


class AHNLookupTool(BaseTool):
    """Ask Agent Help Network about a host when a web step is blocked or failing (read-only)."""

    name: str = "ahn_lookup"
    description: str = DESCRIPTION
    args_schema: Type[BaseModel] = AHNLookupInput
    timeout: float = DEFAULT_TIMEOUT
    """Seconds for the whole lookup, DNS included; the tool returns a no-answer text when it is exceeded."""

    def _run(self, hostname: str, error_class: Optional[str] = None, failure_stage: Optional[str] = None,
             http_status: Optional[int] = None, challenge: Optional[str] = None, retry_after: Optional[str] = None,
             via: Optional[str] = None, task_step: Optional[str] = None, run_manager: Optional[CallbackManagerForToolRun] = None) -> str:
        try:
            target = _target(hostname, error_class, failure_stage, http_status, challenge, retry_after, via, task_step)
            if target is None:
                return NOT_LOOKED_UP
            host, ec, stage, codes = target
            result = bounded_check(host, ec, stage, adapter=ADAPTER, timeout=self.timeout, extra=codes)
            return format_result(result, retry_after) if result else NO_ANSWER
        except Exception as error:
            return _unexpected(error)

    async def _arun(self, hostname: str, error_class: Optional[str] = None, failure_stage: Optional[str] = None,
                    http_status: Optional[int] = None, challenge: Optional[str] = None, retry_after: Optional[str] = None,
                    via: Optional[str] = None, task_step: Optional[str] = None, run_manager: Optional[AsyncCallbackManagerForToolRun] = None) -> str:
        try:
            target = _target(hostname, error_class, failure_stage, http_status, challenge, retry_after, via, task_step)
            if target is None:
                return NOT_LOOKED_UP
            host, ec, stage, codes = target
            result = await abounded_check(host, ec, stage, adapter=ADAPTER, timeout=self.timeout, extra=codes)
            return format_result(result, retry_after) if result else NO_ANSWER
        except Exception as error:
            return _unexpected(error)
