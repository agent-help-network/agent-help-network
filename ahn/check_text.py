"""Words for AHN's own home-page check, shared by the MCP first line (D67) and the ``cause`` line (D82).

One age rule and one table of provider names, so a single answer never tells the same check's age two ways.
This module imports nothing from the rest of the package.
"""

from __future__ import annotations

# Display names of the bot-defense provider codes the home-page check stores.
VENDOR_NAMES = {"cloudflare": "Cloudflare", "datadome": "DataDome", "perimeterx": "PerimeterX", "aws_waf": "AWS WAF",
                "vercel": "Vercel", "generic_captcha": "a human-verification widget", "unknown": "an unidentified provider"}


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
