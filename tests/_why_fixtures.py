"""Fixtures for the explain tests: a made-up platform table (so the tests do not depend on the curated one), made-up hosts,
and a check() answer built by hand. Nothing here is read from a real site."""

from __future__ import annotations

from datetime import date, datetime, timezone

from ahn import explain as E, platform_policies as P

TODAY = date(2026, 11, 7)
NOW = "2026-11-07T01:00:00Z"
BASE = "https://docs.fixture-vendor.test/"


def doc(path, quotes, *, status="read", verified="2026-11-06", **extra):
    item = {"surface": "fixture", "url": BASE + path, "title": path, "status": status, "quotes": [{"tag": t, "text": x} for t, x in quotes]}
    if status == "read":
        item["verified_at"] = verified
    else:
        item.update(unread_reason=extra.pop("reason", "challenge_page"), last_attempt_at=verified)
    item.update(extra)
    return item


PLATFORMS = [
    {"platform": "claude_desktop_browser", "display_name": "Fixture Desktop", "vendor": "anthropic", "not_found": [], "docs": [
        doc("admin", [("banner_text", "This site is blocked by your organization."), ("admin_policy", "Your administrator can restrict sites."),
                      ("confirm_required", "The product asks before it acts."), ("identity", "Browser traffic comes from the user's machine."),
                      ("remedy", "Check that the site is allowed."), ("list_incomplete", "The list of sites is not complete.")],
            admin_deployment_doc=True)]},
    {"platform": "claude_in_chrome", "display_name": "Fixture Chrome", "vendor": "anthropic", "not_found": [], "docs": [
        doc("safely", [("confirm_required", "It asks permission before financial sites."), ("admin_policy", "Admins can restrict sites."),
                       ("list_incomplete", "The list is not complete.")], conflict=True, conflict_peer_url=BASE + "trouble"),
        doc("trouble", [("blocked_category", "The site may be in a default-blocked category."), ("admin_policy", "Your admin may have restricted this site."),
                        ("remedy", "Check that you granted permission for the site.")], conflict=True, conflict_peer_url=BASE + "safely")]},
    {"platform": "chatgpt_agent", "display_name": "Fixture Agent", "vendor": "openai", "not_found": [], "docs": [
        doc("agent", [("prohibited_action", "The agent does not complete purchases."), ("site_side", "Some sites limit automated visitors.")])]},
    {"platform": "meta_muse", "display_name": "Fixture Muse", "vendor": "meta", "not_found": [], "docs": [
        doc("muse", [("capability_note", "It browses for you.")]), doc("help", [], status="unread", reason="script_rendered")]},
    {"platform": "perplexity_comet", "display_name": "Fixture Comet", "vendor": "perplexity", "not_found": [], "docs": [
        doc("comet1", [], status="unread"), doc("comet2", [], status="unread")]},
]


def view(lang="en"):
    return P.view(TODAY, lang, PLATFORMS)


def fake_check(action="hand_off_to_human", do="hand_off_to_human", **extra):
    """A check() answer as GET /v1/incidents/check serializes it, with only the parts explain reads."""
    data = {"site_host": "x", "generated_at": NOW, "summary_line": "Fixture summary.", "next_checks": [], "observations": [],
            "recommendation": {"action": action, "confidence": "medium", "reasons": ["fixture"]}, "next_action": {"do": do}}
    data.update(extra)
    return data


def inputs(host="login-fixture-host.com", **kw):
    return E.parse_inputs(host, today=TODAY, **kw)


def run(host="login-fixture-host.com", check=None, changes=(), events=(), platforms=True, **kw):
    i = inputs(host, **kw)
    return E.build(i, check or fake_check(), view(i.lang) if platforms else None, list(changes), list(events), TODAY)


def layers(body):
    seen = []
    for row in body["what_happened"]["layers"]:
        if row["layer"] not in seen:
            seen.append(row["layer"])
    return seen


def ids(body):
    return [c["id"] for c in body["do_now"]["checks"]]


def at(day="2026-11-05", hour=3):
    y, m, d = (int(x) for x in day.split("-"))
    return datetime(y, m, d, hour, tzinfo=timezone.utc)
