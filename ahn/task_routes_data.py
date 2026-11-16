"""Curated task routes (D117).

For a site and a kind of customer task, an obstacle and a route that the site's OWN documents name (a page that says a
cancellation is taken by phone only, or that a claim needs a particular form). Kept apart from ahn/official_paths.py: nothing
here feeds official_paths(), next_action, customer_url or the verdict.

Keys are apex hosts (ahn.task_routes.route_host); values are lists of _route(...) records. Every value is a closed value of
ahn.task_routes (the one exception is the optional condition_text, a short quotation of a condition the page states), every official_url follows ahn.task_routes.official_url_problem, and source_url is the
site's own page that states it, read on verified_at with the curation user agent from a public help or policy page (scope: scripts/README.md). An entry is
named only when the page states it outright; a hint is left out.

Checks: python scripts/verify_official_paths.py --task-routes-only. Procedure: scripts/README.md.
"""

from __future__ import annotations

from datetime import date

from .task_routes import condition_text_problem, period_for

# An entry older than this is left out of answers until it is verified again (same age as consumer paths).
TASK_ROUTE_MAX_AGE_DAYS = 90

# Reviewed exact URLs that a curated_official entry may carry although the link rule would refuse them (a path with up to 15
# digits in a row; never a query; D120). Each key maps to the same site's public index page that links it, so
# the address is shown to be a public page and not a personal one. A URL matches only when it is the same string. The index
# page itself follows the ordinary link rule. Each entry is reviewed by hand; scripts/verify_official_paths.py
# --task-routes-only reads every index page and checks that it links the exact URL.
CURATED_URL_EXCEPTIONS: dict[str, str] = {
    "https://www.samsung.com/us/support/troubleshoot/TSG10001565/": "https://www.samsung.com/us/support/contact/",
}


def _route(task_kind: str, *, obstacle: str, source_url: str, verified_at: str, routes: tuple[str, ...] | list[str] = (),
           worked: str | None = None, official_url: str | None = None, verification_kind: str | None = None,
           site_requires: str | None = None, document_kind: str | None = None, entry_point_kind: str | None = None,
           condition_text: str | None = None) -> dict:
    """A curated record: basis official_doc, outcome not_applicable, source curated_official, period from verified_at.
    ``condition_text`` is a condition the page at source_url states, quoted (1 to 200 characters, one line); a bad value fails here."""
    if condition_text is not None:
        condition_text = condition_text.strip()
        problem = condition_text_problem(condition_text)
        if problem:
            raise ValueError(f"invalid curated task route: condition_text {problem}")
    item = {"task_kind": task_kind, "obstacle": obstacle, "routes_tried": list(routes), "basis": "official_doc",
            "outcome": "not_applicable", "source_kind": "curated_official",
            "period": period_for(date.fromisoformat(verified_at)), "source_url": source_url, "verified_at": verified_at}
    optional = {"route_worked": worked, "official_url": official_url, "verification_kind": verification_kind,
                "site_requires": site_requires, "document_kind": document_kind, "entry_point_kind": entry_point_kind,
                "condition_text": condition_text}
    item.update({key: value for key, value in optional.items() if value is not None})
    return item


TASK_ROUTES_CURATED: dict[str, list[dict]] = {
    "samsung.com": [
        _route("warranty_claim", obstacle="wrong_department_or_jurisdiction",
               official_url="https://www.samsung.com/us/support/warranty/",
               source_url="https://www.samsung.com/us/support/warranty/", verified_at="2026-11-14"),
        _route("account_recovery", obstacle="verification_step", verification_kind="app_approval",
               official_url="https://www.samsung.com/us/support/troubleshoot/TSG10001565/",
               source_url="https://www.samsung.com/us/support/troubleshoot/TSG10001565/", verified_at="2026-11-14"),
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
    "paypal.com": [
        _route("refund_request", obstacle="deadline_or_window", routes=["escalation_within_site"],
               official_url="https://www.paypal.com/us/cshelp/article/how-do-i-open-a-dispute-with-a-seller-help160",
               source_url="https://www.paypal.com/us/cshelp/article/how-do-i-open-a-dispute-with-a-seller-help160",
               verified_at="2026-11-14"),
    ],
    "ikea.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.ikea.com/us/en/customer-service/returns-claims/",
               source_url="https://www.ikea.com/us/en/customer-service/returns-claims/", verified_at="2026-11-14"),
        _route("return_or_exchange", obstacle="document_missing", document_kind="proof_of_purchase",
               official_url="https://www.ikea.com/us/en/customer-service/returns-claims/",
               source_url="https://www.ikea.com/us/en/customer-service/returns-claims/", verified_at="2026-11-14"),
        _route("return_or_exchange", obstacle="document_missing", document_kind="proof_of_identity",
               official_url="https://www.ikea.com/us/en/customer-service/returns-claims/",
               source_url="https://www.ikea.com/us/en/customer-service/returns-claims/", verified_at="2026-11-14"),
        _route("warranty_claim", obstacle="document_missing", document_kind="proof_of_purchase",
               official_url="https://www.ikea.com/us/en/customer-service/returns-claims/guarantee/",
               source_url="https://www.ikea.com/us/en/customer-service/returns-claims/guarantee/", verified_at="2026-11-14"),
    ],
    "playstation.com": [
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.playstation.com/en-us/support/store/ps-store-refund-request/",
               source_url="https://www.playstation.com/en-us/support/store/ps-store-refund-request/",
               verified_at="2026-11-14"),
        _route("account_recovery", obstacle="verification_step", verification_kind="email_code", routes=["support_chat"],
               official_url="https://www.playstation.com/en-us/support/account/recover-account/",
               source_url="https://www.playstation.com/en-us/support/account/recover-account/", verified_at="2026-11-14"),
    ],
    "nintendo.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["mail_or_fax"],
               official_url="https://www.nintendo.com/us/refund-return-policy/nintendo-store/",
               source_url="https://www.nintendo.com/us/refund-return-policy/nintendo-store/", verified_at="2026-11-14"),
        _route("return_or_exchange", obstacle="fee_required", routes=["mail_or_fax"],
               official_url="https://www.nintendo.com/us/refund-return-policy/nintendo-store/",
               source_url="https://www.nintendo.com/us/refund-return-policy/nintendo-store/", verified_at="2026-11-14"),
        _route("account_recovery", obstacle="verification_step", verification_kind="email_code",
               official_url="https://en-americas-support.nintendo.com/app/answers/detail/a_id/27501",
               source_url="https://en-americas-support.nintendo.com/app/answers/detail/a_id/27501", verified_at="2026-11-14"),
    ],
    "sonos.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["web_form"],
               official_url="https://www.sonos.com/en-us/legal/terms",
               source_url="https://www.sonos.com/en-us/legal/terms", verified_at="2026-11-14"),
        _route("warranty_claim", obstacle="deadline_or_window",
               official_url="https://www.sonos.com/en-us/legal/terms",
               source_url="https://www.sonos.com/en-us/legal/terms", verified_at="2026-11-14"),
    ],
    "peacocktv.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"],
               entry_point_kind="partner_or_third_party_portal", official_url="https://www.peacocktv.com/terms",
               source_url="https://www.peacocktv.com/terms", verified_at="2026-11-14"),
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.peacocktv.com/terms",
               source_url="https://www.peacocktv.com/terms", verified_at="2026-11-14"),
    ],
    "siriusxm.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"],
               entry_point_kind="partner_or_third_party_portal",
               official_url="https://www.siriusxm.com/help/manage-or-cancel-service",
               source_url="https://www.siriusxm.com/help/manage-or-cancel-service", verified_at="2026-11-14"),
    ],
    "ourpetpolicy.com": [
        _route("accommodation_request", obstacle="document_missing", document_kind="professional_letter",
               official_url="https://www.ourpetpolicy.com/resident-faq",
               source_url="https://www.ourpetpolicy.com/resident-faq", verified_at="2026-11-14"),
        _route("document_submission", obstacle="document_missing", document_kind="other",
               official_url="https://www.ourpetpolicy.com/resident-faq",
               source_url="https://www.ourpetpolicy.com/resident-faq", verified_at="2026-11-15",
               condition_text="Even if there are animals in your residence that aren't yours, you can note that when signing up. No need to create a profile or provide additional information beyond this."),
    ],
    "coned.com": [
        _route("address_or_profile_change", obstacle="deadline_or_window",
               official_url="https://www.coned.com/en/services-and-outages/service-management-page",
               source_url="https://www.coned.com/en/services-and-outages/service-management-page", verified_at="2026-11-15"),
    ],
    "delta.com": [
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.delta.com/us/en/change-cancel/cancel-flight",
               source_url="https://www.delta.com/us/en/change-cancel/cancel-flight", verified_at="2026-11-15"),
    ],
    "costco.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", routes=["office_visit", "phone", "support_chat"],
               official_url="https://www.costco.com/f/-/member-privileges-conditions",
               source_url="https://www.costco.com/f/-/member-privileges-conditions", verified_at="2026-11-15"),
    ],
    "ring.com": [
        _route("warranty_claim", obstacle="deadline_or_window", official_url="https://ring.com/warranty",
               source_url="https://ring.com/warranty", verified_at="2026-11-15"),
        _route("return_or_exchange", obstacle="deadline_or_window", official_url="https://ring.com/shipping-and-returns",
               source_url="https://ring.com/shipping-and-returns", verified_at="2026-11-15"),
    ],
    "statefarm.com": [
        _route("cancel_subscription", obstacle="wrong_channel", routes=["phone", "office_visit"],
               official_url="https://www.statefarm.com/help-center/account-management/account-essentials/cancel-policy-sf",
               source_url="https://www.statefarm.com/help-center/account-management/account-essentials/cancel-policy-sf",
               verified_at="2026-11-15"),
    ],
    "cricketwireless.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.cricketwireless.com/support/orders-and-activations/returns-and-exchanges.html",
               source_url="https://www.cricketwireless.com/support/orders-and-activations/returns-and-exchanges.html",
               verified_at="2026-11-15"),
    ],
    "ulta.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", official_url="https://www.ulta.com/guestservices/all",
               source_url="https://www.ulta.com/guestservices/all", verified_at="2026-11-15"),
        _route("return_or_exchange", obstacle="document_missing", document_kind="proof_of_identity",
               official_url="https://www.ulta.com/guestservices/all", source_url="https://www.ulta.com/guestservices/all",
               verified_at="2026-11-15"),
    ],
    "visible.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["support_chat"],
               official_url="https://www.visible.com/returns-policy",
               source_url="https://www.visible.com/returns-policy", verified_at="2026-11-15"),
        _route("return_or_exchange", obstacle="document_missing", document_kind="proof_of_purchase",
               official_url="https://www.visible.com/returns-policy",
               source_url="https://www.visible.com/returns-policy", verified_at="2026-11-15"),
        _route("other", obstacle="wrong_channel", routes=["support_chat", "official_app"],
               official_url="https://www.visible.com/m/help/contact-us",
               source_url="https://www.visible.com/m/help/contact-us", verified_at="2026-11-15"),
    ],
    "boostmobile.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["phone"],
               official_url="https://help.boostmobile.com/docs/return-policy",
               source_url="https://help.boostmobile.com/docs/return-policy", verified_at="2026-11-15"),
        _route("portal_login", obstacle="verification_step", routes=["phone"],
               official_url="https://help.boostmobile.com/docs/account-security",
               source_url="https://help.boostmobile.com/docs/account-security", verified_at="2026-11-15"),
    ],
    "anker.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["web_form"],
               official_url="https://www.anker.com/policies/refund-policy",
               source_url="https://www.anker.com/policies/refund-policy", verified_at="2026-11-15"),
        _route("warranty_claim", obstacle="document_missing", document_kind="proof_of_purchase",
               official_url="https://www.anker.com/policies/refund-policy",
               source_url="https://www.anker.com/policies/refund-policy", verified_at="2026-11-15"),
        _route("return_or_exchange", obstacle="fee_required",
               official_url="https://www.anker.com/policies/refund-policy",
               source_url="https://www.anker.com/policies/refund-policy", verified_at="2026-11-15",
               condition_text="For non-quality-related returns, the buyer is responsible for return shipping costs."),
    ],
    "logitech.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", routes=["web_form"],
               official_url="https://www.logitech.com/en-us/order-faqs",
               source_url="https://www.logitech.com/en-us/order-faqs", verified_at="2026-11-15"),
    ],
    "zappos.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.zappos.com/c/shipping-and-returns",
               source_url="https://www.zappos.com/c/shipping-and-returns", verified_at="2026-11-15"),
    ],
    "expressvpn.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"],
               official_url="https://www.expressvpn.com/support/manage-account/cancel-expressvpn-subscription/",
               source_url="https://www.expressvpn.com/support/manage-account/cancel-expressvpn-subscription/", verified_at="2026-11-15"),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.expressvpn.com/support/manage-account/what-is-expressvpns-refund-policy/",
               source_url="https://www.expressvpn.com/support/manage-account/what-is-expressvpns-refund-policy/", verified_at="2026-11-15",
               condition_text="ExpressVPN’s money-back guarantee is for first-time users only. Users who have previously received a refund are ineligible."),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.expressvpn.com/support/manage-account/what-is-expressvpns-refund-policy/",
               source_url="https://www.expressvpn.com/support/manage-account/what-is-expressvpns-refund-policy/", verified_at="2026-11-15",
               condition_text="Users whose subscriptions are billed as an in-app purchase through Google Play do not have access to the ExpressVPN 30-day money-back guarantee."),
    ],
    "steampowered.com": [
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://store.steampowered.com/steam_refunds/",
               source_url="https://store.steampowered.com/steam_refunds/", verified_at="2026-11-15"),
    ],
    "usps.com": [
        _route("insurance_claim", obstacle="deadline_or_window", official_url="https://www.usps.com/help/claims.htm",
               source_url="https://www.usps.com/help/claims.htm", verified_at="2026-11-15"),
    ],
    "doxo.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://www.doxo.com/w/user-support/can-i-cancel-a-payment-for-a-refund-once-the-payment-has-been-remitted-to-a-biller/",
               source_url="https://www.doxo.com/w/user-support/can-i-cancel-a-payment-for-a-refund-once-the-payment-has-been-remitted-to-a-biller/", verified_at="2026-11-15"),
    ],
    "offerup.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://offerup.com/terms",
               source_url="https://offerup.com/terms", verified_at="2026-11-15"),
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"], entry_point_kind="partner_or_third_party_portal",
               official_url="https://offerup.com/terms",
               source_url="https://offerup.com/terms", verified_at="2026-11-15",
               condition_text="Unless otherwise noted, Paid Services are charged, billed, and managed through your applicable Apple App Store or Google Play Store platform"),
    ],
    "privacy.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.privacy.com/terms",
               source_url="https://www.privacy.com/terms", verified_at="2026-11-15"),
    ],
    "ramseysolutions.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.ramseysolutions.com/company/policies/terms-of-use",
               source_url="https://www.ramseysolutions.com/company/policies/terms-of-use", verified_at="2026-11-15"),
    ],
    "ilovepdf.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.ilovepdf.com/help/terms",
               source_url="https://www.ilovepdf.com/help/terms", verified_at="2026-11-15"),
    ],
    "dreamhost.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.dreamhost.com/legal/terms-of-service/",
               source_url="https://www.dreamhost.com/legal/terms-of-service/", verified_at="2026-11-15"),
    ],
    "suno.com": [
        _route("billing_dispute", obstacle="deadline_or_window", official_url="https://suno.com/terms",
               source_url="https://suno.com/terms", verified_at="2026-11-15"),
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://suno.com/terms",
               source_url="https://suno.com/terms", verified_at="2026-11-15"),
    ],
    "tomtom.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", official_url="https://www.tomtom.com/en-gb/legal/terms-and-conditions/",
               source_url="https://www.tomtom.com/en-gb/legal/terms-and-conditions/", verified_at="2026-11-15"),
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"], entry_point_kind="partner_or_third_party_portal", official_url="https://www.tomtom.com/en-gb/legal/terms-and-conditions/",
               source_url="https://www.tomtom.com/en-gb/legal/terms-and-conditions/", verified_at="2026-11-15"),
    ],
    "wise.com": [
        _route("portal_login", obstacle="verification_step", verification_kind="new_device", official_url="https://wise.com/help/articles/6BKMUwOzYOCO24BANwWVvL/logging-in-from-a-new-device",
               source_url="https://wise.com/help/articles/6BKMUwOzYOCO24BANwWVvL/logging-in-from-a-new-device", verified_at="2026-11-15"),
    ],
    "wordpress.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://wordpress.com/support/refunds/",
               source_url="https://wordpress.com/support/refunds/", verified_at="2026-11-15"),
    ],
    "pcloud.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://www.pcloud.com/terms_and_conditions",
               source_url="https://www.pcloud.com/terms_and_conditions", verified_at="2026-11-15"),
    ],
    "upgrade.com": [
        _route("account_recovery", obstacle="code_expired", official_url="https://www.upgrade.com/help/account-management-payments/payments-billing/how-do-i-reset-my-password/",
               source_url="https://www.upgrade.com/help/account-management-payments/payments-billing/how-do-i-reset-my-password/", verified_at="2026-11-15"),
    ],
    "bitwarden.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://bitwarden.com/help/cancel-a-subscription/",
               source_url="https://bitwarden.com/help/cancel-a-subscription/", verified_at="2026-11-15"),
    ],
    "cursor.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"], entry_point_kind="partner_or_third_party_portal", official_url="https://cursor.com/help/account-and-billing/cancel",
               source_url="https://cursor.com/help/account-and-billing/cancel", verified_at="2026-11-15"),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://cursor.com/help/account-and-billing/refunds",
               source_url="https://cursor.com/help/account-and-billing/refunds", verified_at="2026-11-15",
               condition_text="These rules cover individual plans billed through cursor.com: the legacy Pro, Pro+, and Ultra plans, monthly and annual."),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://cursor.com/help/account-and-billing/refunds",
               source_url="https://cursor.com/help/account-and-billing/refunds", verified_at="2026-11-15",
               condition_text="You have not used the subscription during that billing period"),
    ],
    "brain.fm": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.brain.fm/terms",
               source_url="https://www.brain.fm/terms", verified_at="2026-11-15"),
        _route("cancel_subscription", obstacle="wrong_entry_point", routes=["different_entry_point"], entry_point_kind="partner_or_third_party_portal", official_url="https://www.brain.fm/terms",
               source_url="https://www.brain.fm/terms", verified_at="2026-11-15"),
    ],
    "functionofbeauty.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://functionofbeauty.com/pages/faq",
               source_url="https://functionofbeauty.com/pages/faq", verified_at="2026-11-15"),
    ],
    "huel.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://huel.com/pages/terms-and-conditions",
               source_url="https://huel.com/pages/terms-and-conditions", verified_at="2026-11-15"),
    ],
    "incogni.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://incogni.com/legal/terms-of-service",
               source_url="https://incogni.com/legal/terms-of-service", verified_at="2026-11-15"),
    ],
    "todoist.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://www.todoist.com/terms-of-service",
               source_url="https://www.todoist.com/terms-of-service", verified_at="2026-11-15"),
    ],
    "straighttalk.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", official_url="https://www.straighttalk.com/support/terms-conditions",
               source_url="https://www.straighttalk.com/support/terms-conditions", verified_at="2026-11-15"),
        _route("return_or_exchange", obstacle="wrong_entry_point", routes=["different_entry_point"], entry_point_kind="partner_or_third_party_portal", official_url="https://www.straighttalk.com/support/terms-conditions",
               source_url="https://www.straighttalk.com/support/terms-conditions", verified_at="2026-11-15"),
    ],
    "tracfone.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", official_url="https://www.tracfone.com/return-policy",
               source_url="https://www.tracfone.com/return-policy", verified_at="2026-11-15"),
    ],
    "tricare.mil": [
        _route("insurance_claim", obstacle="deadline_or_window", official_url="https://tricare.mil/PatientResources/Claims",
               source_url="https://tricare.mil/PatientResources/Claims", verified_at="2026-11-15"),
    ],
    "brilliant.org": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://brilliant.org/terms-of-use/",
               source_url="https://brilliant.org/terms-of-use/", verified_at="2026-11-15"),
    ],
    "alltrails.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://www.alltrails.com/terms",
               source_url="https://www.alltrails.com/terms", verified_at="2026-11-15"),
    ],
    "busuu.com": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://www.busuu.com/en/terms",
               source_url="https://www.busuu.com/en/terms", verified_at="2026-11-15"),
    ],
    "walmart.com": [
        _route("return_or_exchange", obstacle="deadline_or_window", official_url="https://corporate.walmart.com/policies",
               source_url="https://corporate.walmart.com/policies", verified_at="2026-11-15"),
    ],
    "mealpal.com": [
        _route("cancel_subscription", obstacle="deadline_or_window", official_url="https://mealpal.com/terms-and-conditions/",
               source_url="https://mealpal.com/terms-and-conditions/", verified_at="2026-11-15"),
    ],
    "interactivebrokers.com": [
        _route("account_recovery", obstacle="wrong_channel", routes=["phone"], official_url="https://www.interactivebrokers.com/en/support/customer-service.php",
               source_url="https://www.interactivebrokers.com/en/support/customer-service.php", verified_at="2026-11-15"),
    ],
    "eeoc.gov": [
        _route("complaint_filing", obstacle="deadline_or_window", official_url="https://www.eeoc.gov/time-limits-filing-charge",
               source_url="https://www.eeoc.gov/time-limits-filing-charge", verified_at="2026-11-15"),
        _route("complaint_filing", obstacle="wrong_channel", routes=["web_form", "office_visit", "mail_or_fax"], official_url="https://www.eeoc.gov/how-file-charge-employment-discrimination",
               source_url="https://www.eeoc.gov/how-file-charge-employment-discrimination", verified_at="2026-11-15"),
    ],
    "healthcare.gov": [
        _route("document_submission", obstacle="wrong_channel", routes=["web_form", "mail_or_fax"], official_url="https://www.healthcare.gov/tips-and-troubleshooting/uploading-documents/",
               source_url="https://www.healthcare.gov/tips-and-troubleshooting/uploading-documents/", verified_at="2026-11-15"),
    ],
    "irs.gov": [
        _route("refund_request", obstacle="deadline_or_window", official_url="https://www.irs.gov/refunds/about-refunds",
               source_url="https://www.irs.gov/refunds/about-refunds", verified_at="2026-11-15"),
    ],
    "helpwithmybank.gov": [
        _route("complaint_filing", obstacle="wrong_department_or_jurisdiction", routes=["regulator"], official_url="https://helpwithmybank.gov/file-a-complaint/index-file-a-complaint.html",
               source_url="https://helpwithmybank.gov/file-a-complaint/index-file-a-complaint.html", verified_at="2026-11-15"),
    ],
    "pnc.com": [
        _route("account_recovery", obstacle="verification_step", verification_kind="account_locked", official_url="https://www.pnc.com/en/customer-service/forgot-username-or-password.html",
               source_url="https://www.pnc.com/en/customer-service/forgot-username-or-password.html", verified_at="2026-11-15"),
    ],
    "thumbtack.com": [
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.thumbtack.com/guarantee",
               source_url="https://www.thumbtack.com/guarantee", verified_at="2026-11-15",
               condition_text="Our Money-Back Guarantee covers circumstances where you hire a pro on Thumbtack and the job doesn’t get done, or it isn’t done as agreed."),
    ],
    "labcorp.com": [
        _route("billing_dispute", obstacle="document_missing", routes=["mail_or_fax"], document_kind="other",
               official_url="https://www.labcorp.com/patients/billing",
               source_url="https://www.labcorp.com/patients/billing", verified_at="2026-11-15",
               condition_text="If your Labcorp bill doesn't match your EOB"),
    ],
    "underarmour.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.underarmour.com/en-us/returnlabel/",
               source_url="https://www.underarmour.com/en-us/returnlabel/", verified_at="2026-11-15",
               condition_text="are FREE within 90 days with proof of purchase"),
        _route("return_or_exchange", obstacle="document_missing", document_kind="proof_of_purchase",
               official_url="https://www.underarmour.com/en-us/returnlabel/",
               source_url="https://www.underarmour.com/en-us/returnlabel/", verified_at="2026-11-15",
               condition_text="are FREE within 90 days with proof of purchase"),
    ],
    "lafitness.com": [
        _route("cancel_subscription", obstacle="fee_required",
               official_url="https://www.lafitness.com/Pages/MembershipQuestions.aspx",
               source_url="https://www.lafitness.com/Pages/MembershipQuestions.aspx", verified_at="2026-11-15",
               condition_text="If your membership, personal training or other agreement has an initial term of three months or more"),
        _route("cancel_subscription", obstacle="fee_required",
               official_url="https://www.lafitness.com/Pages/MembershipQuestions.aspx",
               source_url="https://www.lafitness.com/Pages/MembershipQuestions.aspx", verified_at="2026-11-15",
               condition_text="you wish to cancel before the end of the initial term"),
        _route("cancel_subscription", obstacle="deadline_or_window", routes=["mail_or_fax"],
               official_url="https://www.lafitness.com/Pages/MembershipQuestions.aspx",
               source_url="https://www.lafitness.com/Pages/MembershipQuestions.aspx", verified_at="2026-11-15",
               condition_text="A mailed cancellation form postmarked at least 5 business days before your next billing date should result in no further billings."),
    ],
    "care.com": [
        _route("cancel_subscription", obstacle="fee_required",
               official_url="https://www.care.com/about/terms-of-use/",
               source_url="https://www.care.com/about/terms-of-use/", verified_at="2026-11-15",
               condition_text="any cancellation or other fees associated with your early termination and disclosed to you at the time you signed up for the subscription plan"),
    ],
    "docusign.com": [
        _route("cancel_subscription", obstacle="deadline_or_window",
               official_url="https://www.docusign.com/legal/terms-and-conditions",
               source_url="https://www.docusign.com/legal/terms-and-conditions", verified_at="2026-11-15",
               condition_text="for Web Plan Customers and Users"),
    ],
    "coursera.org": [
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.coursera.org/about/terms",
               source_url="https://www.coursera.org/about/terms", verified_at="2026-11-15",
               condition_text="For annual subscriptions, which are paid in full at the time of purchase, you must cancel within 14 days to receive a refund."),
    ],
    "clickup.com": [
        _route("cancel_subscription", obstacle="deadline_or_window",
               official_url="https://clickup.com/terms",
               source_url="https://clickup.com/terms", verified_at="2026-11-15",
               condition_text="For customers on an annual Services plan"),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://clickup.com/terms",
               source_url="https://clickup.com/terms", verified_at="2026-11-15",
               condition_text="ClickUp provides customers purchasing Services for the first time (or who are purchasing new products) with a satisfaction guarantee"),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://clickup.com/terms",
               source_url="https://clickup.com/terms", verified_at="2026-11-15",
               condition_text="The Satisfaction Guarantee does not apply to (a) Services initially purchased through or with the assistance of a ClickUp sales representative or reseller"),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://clickup.com/terms",
               source_url="https://clickup.com/terms", verified_at="2026-11-15",
               condition_text="(b) renewals or expansions of previously purchased Services or Upgraded Plans"),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://clickup.com/terms",
               source_url="https://clickup.com/terms", verified_at="2026-11-15",
               condition_text="(c) instances where you have completed a pilot or trial period of the Services prior to initial purchase"),
    ],
    "asana.com": [
        _route("cancel_subscription", obstacle="deadline_or_window",
               official_url="https://asana.com/terms/subscriber-terms",
               source_url="https://asana.com/terms/subscriber-terms", verified_at="2026-11-15",
               condition_text="Unless otherwise stated in the applicable Order Form"),
    ],
    "hostinger.com": [
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.hostinger.com/legal/refund-policy",
               source_url="https://www.hostinger.com/legal/refund-policy", verified_at="2026-11-15",
               condition_text="Some products have different policies or requirements for a refund associated with them, including some products that are not eligible for a refund under any circumstance."),
        _route("refund_request", obstacle="deadline_or_window",
               official_url="https://www.hostinger.com/legal/refund-policy",
               source_url="https://www.hostinger.com/legal/refund-policy", verified_at="2026-11-15",
               condition_text="Products purchased using cryptocurrencies, tokens or digital assets will not be refunded."),
    ],
    "evite.com": [
        _route("cancel_subscription", obstacle="deadline_or_window",
               official_url="https://support.evite.com/pricing-and-billing/billing-and-refunds/cancel-evite-pro-subscription",
               source_url="https://support.evite.com/pricing-and-billing/billing-and-refunds/cancel-evite-pro-subscription", verified_at="2026-11-15"),
        _route("refund_request", obstacle="wrong_entry_point", entry_point_kind="partner_or_third_party_portal", routes=["different_entry_point"],
               official_url="https://support.evite.com/pricing-and-billing/billing-and-refunds/refund-requests",
               source_url="https://support.evite.com/pricing-and-billing/billing-and-refunds/refund-requests", verified_at="2026-11-15"),
    ],
    "koa.com": [
        _route("refund_request", obstacle="deadline_or_window", routes=["phone"],
               official_url="https://koa.com/contact-us/faqs/8/801/",
               source_url="https://koa.com/contact-us/faqs/8/801/", verified_at="2026-11-15"),
    ],
    "webull.com": [
        _route("account_recovery", obstacle="verification_step", verification_kind="security_question",
               official_url="https://www.webull.com/help/faq/10886-Resetting-Your-Password",
               source_url="https://www.webull.com/help/faq/10886-Resetting-Your-Password", verified_at="2026-11-15"),
    ],
    "talbots.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.talbots.com/returns/returns.html",
               source_url="https://www.talbots.com/returns/returns.html", verified_at="2026-11-15"),
    ],
    "allbirds.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.allbirds.com/pages/help",
               source_url="https://www.allbirds.com/pages/help", verified_at="2026-11-15"),
    ],
    "ae.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.ae.com/us/en/content/help/return-policy",
               source_url="https://www.ae.com/us/en/content/help/return-policy", verified_at="2026-11-15"),
        _route("return_or_exchange", obstacle="wrong_entry_point", entry_point_kind="partner_or_third_party_portal", routes=["different_entry_point"],
               official_url="https://www.ae.com/us/en/content/help/return-policy",
               source_url="https://www.ae.com/us/en/content/help/return-policy", verified_at="2026-11-15"),
    ],
    "woot.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.woot.com/faq",
               source_url="https://www.woot.com/faq", verified_at="2026-11-15"),
    ],
    "calm.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", entry_point_kind="partner_or_third_party_portal", routes=["different_entry_point"],
               official_url="https://www.calm.com/terms",
               source_url="https://www.calm.com/terms", verified_at="2026-11-15"),
    ],
    "noom.com": [
        _route("cancel_subscription", obstacle="wrong_entry_point", entry_point_kind="partner_or_third_party_portal", routes=["different_entry_point"],
               official_url="https://www.noom.com/terms-and-conditions-of-use/",
               source_url="https://www.noom.com/terms-and-conditions-of-use/", verified_at="2026-11-15"),
        _route("cancel_subscription", obstacle="wrong_channel",
               official_url="https://www.noom.com/support/faqs/programs/2025/10/noom-refund-policy/",
               source_url="https://www.noom.com/support/faqs/programs/2025/10/noom-refund-policy/", verified_at="2026-11-15"),
    ],
    "handandstone.com": [
        _route("appointment_change_or_cancel", obstacle="deadline_or_window",
               official_url="https://handandstone.com/legal/membership-terms-and-conditions/",
               source_url="https://handandstone.com/legal/membership-terms-and-conditions/", verified_at="2026-11-15"),
    ],
    "tuftandneedle.com": [
        _route("return_or_exchange", obstacle="deadline_or_window",
               official_url="https://www.tuftandneedle.com/pages/returns",
               source_url="https://www.tuftandneedle.com/pages/returns", verified_at="2026-11-15"),
    ],
    "americangirl.com": [
        _route("return_or_exchange", obstacle="wrong_entry_point", entry_point_kind="partner_or_third_party_portal", routes=["different_entry_point"],
               official_url="https://www.americangirl.com/pages/returns",
               source_url="https://www.americangirl.com/pages/returns", verified_at="2026-11-15"),
    ],
    "soul-cycle.com": [
        _route("appointment_change_or_cancel", obstacle="deadline_or_window", routes=["phone"],
               official_url="https://soul-cycle.com/faq",
               source_url="https://soul-cycle.com/faq", verified_at="2026-11-15"),
    ],
    "sonesta.com": [
        _route("appointment_change_or_cancel", obstacle="wrong_entry_point", entry_point_kind="partner_or_third_party_portal", routes=["different_entry_point"],
               official_url="https://www.sonesta.com/terms-use",
               source_url="https://www.sonesta.com/terms-use", verified_at="2026-11-15"),
    ],
    "trugreen.com": [
        _route("cancel_subscription", obstacle="verification_step",
               official_url="https://www.trugreen.com/lawn-care-101/faqs",
               source_url="https://www.trugreen.com/lawn-care-101/faqs", verified_at="2026-11-15"),
    ],
    "bell.ca": [
        _route("refund_request", obstacle="site_requires_info_from_person", site_requires="account_holder_step",
               official_url="https://support.bell.ca/Billing-and-Accounts/Policies/Returns",
               source_url="https://support.bell.ca/Billing-and-Accounts/Policies/Returns", verified_at="2026-11-15"),
    ],
    "rogers.com": [
        _route("cancel_subscription", obstacle="wrong_channel", routes=["phone"],
               official_url="https://www.rogers.com/support/billing-accounts/how-do-i-cancel-my-services",
               source_url="https://www.rogers.com/support/billing-accounts/how-do-i-cancel-my-services", verified_at="2026-11-15"),
    ],
}
