"""Words for AHN's own home-page check, shared by the MCP first line (D67) and the ``cause`` line (D82).

One age rule and one table of provider names, so a single answer never tells the same check's age two ways.
This module imports nothing from the rest of the package.
"""

from __future__ import annotations

# Display names of the bot-defense provider codes the home-page check stores.
VENDOR_NAMES = {"cloudflare": "Cloudflare", "datadome": "DataDome", "perimeterx": "PerimeterX", "aws_waf": "AWS WAF",
                "vercel": "Vercel", "generic_captcha": "a human-verification widget", "unknown": "an unidentified provider"}


def unfollowed_www_redirect(outcome, redirect) -> bool:
    """D88: a stored home-page check that ended at a redirect to the host's other name (www or apex) that AHN did not follow
    (``redirect.to_relation`` is ``www_alias`` and ``redirect.followed_www`` is false). The www name's own answer is not
    known, so this is no measurement of the home page, not a normal one. A followed www answer, any other same-host redirect
    (a path or locale move) and a row without a redirect record (stored before the record existed) are not matched.
    ``redirect`` is the stored mapping (or None)."""
    return (outcome == "redirected_same_host" and isinstance(redirect, dict)
            and redirect.get("to_relation") == "www_alias" and redirect.get("followed_www") is False)


def age_text(seconds: int) -> str:
    """The age of a check as ``ahn_check.age_seconds`` gives it, cut down (not rounded): just now (under 5 minutes), N min,
    N h (under 24 hours), then N day(s)."""
    seconds = max(0, int(seconds))
    if seconds < 300:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    days = seconds // 86400
    return f"{days} day ago" if days == 1 else f"{days} days ago"
