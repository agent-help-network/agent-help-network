"""Curated task routes (D117).

For a site and a kind of customer task, an obstacle and a route that the site's OWN documents name (a page that says a
cancellation is taken by phone only, or that a claim needs a particular form). Kept apart from ahn/official_paths.py: nothing
here feeds official_paths(), next_action, customer_url or the verdict.

Keys are apex hosts (ahn.task_routes.route_host); values are lists of _route(...) records. Every value is a closed value of
ahn.task_routes (no quoted text), every official_url follows ahn.task_routes.official_url_problem, and source_url is the
site's own page that states it, read on verified_at with the curation user agent after robots.txt allowed it. An entry is
named only when the page states it outright; a hint is left out.

Checks: python scripts/verify_official_paths.py --task-routes-only. Procedure: scripts/README.md.
"""

from __future__ import annotations

from datetime import date

from .task_routes import period_for

# An entry older than this is left out of answers until it is verified again (same age as consumer paths).
TASK_ROUTE_MAX_AGE_DAYS = 90

# Reviewed exact URLs that a curated_official entry may carry although the link rule would refuse them (a long identifier in
# the path, or a query of id, pageId or docId; D120). Each key maps to the same site's public index page that links it, so
# the address is shown to be a public page and not a personal one. A URL matches only when it is the same string. The index
# page itself follows the ordinary link rule. Empty until entries are reviewed; scripts/verify_official_paths.py
# --task-routes-only reads every index page and checks that it links the exact URL.
CURATED_URL_EXCEPTIONS: dict[str, str] = {}


def _route(task_kind: str, *, obstacle: str, source_url: str, verified_at: str, routes: tuple[str, ...] | list[str] = (),
           worked: str | None = None, official_url: str | None = None, verification_kind: str | None = None,
           site_requires: str | None = None, document_kind: str | None = None, entry_point_kind: str | None = None) -> dict:
    """A curated record: basis official_doc, outcome not_applicable, source curated_official, period from verified_at."""
    item = {"task_kind": task_kind, "obstacle": obstacle, "routes_tried": list(routes), "basis": "official_doc",
            "outcome": "not_applicable", "source_kind": "curated_official",
            "period": period_for(date.fromisoformat(verified_at)), "source_url": source_url, "verified_at": verified_at}
    optional = {"route_worked": worked, "official_url": official_url, "verification_kind": verification_kind,
                "site_requires": site_requires, "document_kind": document_kind, "entry_point_kind": entry_point_kind}
    item.update({key: value for key, value in optional.items() if value is not None})
    return item


TASK_ROUTES_CURATED: dict[str, list[dict]] = {
    "samsung.com": [
        _route("warranty_claim", obstacle="wrong_department_or_jurisdiction",
               official_url="https://www.samsung.com/us/support/warranty/",
               source_url="https://www.samsung.com/us/support/warranty/", verified_at="2026-11-14"),
    ],
    "dell.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["web_form"],
               official_url="https://www.dell.com/en-us/shop/lp/return-policy",
               source_url="https://www.dell.com/en-us/shop/lp/return-policy", verified_at="2026-11-14"),
    ],
    "microsoft.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"],
               entry_point_kind="partner_or_third_party_portal",
               official_url="https://support.microsoft.com/en-us/accounts-billing/subscriptions/cancel-your-microsoft-subscription",
               source_url="https://support.microsoft.com/en-us/accounts-billing/subscriptions/cancel-your-microsoft-subscription",
               verified_at="2026-11-14"),
        _route("refund_request", obstacle="wrong_entry_point", routes=["different_entry_point"],
               entry_point_kind="partner_or_third_party_portal",
               official_url="https://support.microsoft.com/en-us/accounts-billing/subscriptions/cancel-your-microsoft-subscription",
               source_url="https://support.microsoft.com/en-us/accounts-billing/subscriptions/cancel-your-microsoft-subscription",
               verified_at="2026-11-14"),
        _route("refund_request", obstacle="eligibility_unclear", routes=["web_form"],
               official_url="https://support.microsoft.com/en-us/accounts-billing/subscriptions/get-a-refund-for-apps-and-games-purchased-from-microsoft-store",
               source_url="https://support.microsoft.com/en-us/accounts-billing/subscriptions/get-a-refund-for-apps-and-games-purchased-from-microsoft-store",
               verified_at="2026-11-14"),
        _route("warranty_claim", obstacle="document_missing", routes=["web_form"], document_kind="proof_of_purchase",
               official_url="https://support.microsoft.com/en-us/surface/update-your-surface-warranty-with-your-proof-of-purchase",
               source_url="https://support.microsoft.com/en-us/surface/update-your-surface-warranty-with-your-proof-of-purchase",
               verified_at="2026-11-14"),
        _route("account_recovery", obstacle="verification_step", verification_kind="account_locked",
               official_url="https://support.microsoft.com/en-us/accounts-billing/manage/microsoft-account-has-been-locked",
               source_url="https://support.microsoft.com/en-us/accounts-billing/manage/microsoft-account-has-been-locked",
               verified_at="2026-11-14"),
    ],
    "spotify.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"],
               entry_point_kind="partner_or_third_party_portal",
               official_url="https://support.spotify.com/us/article/cancel-premium/",
               source_url="https://support.spotify.com/us/article/cancel-premium/", verified_at="2026-11-14"),
        _route("refund_request", obstacle="wrong_entry_point", routes=["different_entry_point"],
               entry_point_kind="partner_or_third_party_portal",
               official_url="https://support.spotify.com/us/article/refund-policy/",
               source_url="https://support.spotify.com/us/article/refund-policy/", verified_at="2026-11-14"),
        _route("account_recovery", obstacle="document_missing", document_kind="proof_of_ownership",
               official_url="https://support.spotify.com/us/article/disabled-accounts/",
               source_url="https://support.spotify.com/us/article/disabled-accounts/", verified_at="2026-11-14"),
    ],
    "verizon.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.verizon.com/support/return-policy/",
               source_url="https://www.verizon.com/support/return-policy/", verified_at="2026-11-14"),
        _route("return_or_exchange", obstacle="fee_required", official_url="https://www.verizon.com/support/return-policy/",
               source_url="https://www.verizon.com/support/return-policy/", verified_at="2026-11-14"),
    ],
}
