"""--help-pages of scripts/verify_official_paths.py (task routes): the read scope of curation, and how a robots-disallowed
page inside or outside it is counted. No network: the fetcher is a test double."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from ahn.task_routes_data import TASK_ROUTES_CURATED

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths_scope", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = _verifier()
HOME = "https://www.example.com/"


class Fetcher:
    def __init__(self, answers: dict) -> None:
        self.answers, self.requested, self.requests = answers, [], 0

    def public_host(self, host: str) -> bool:
        return True

    def _get(self, url: str, want_body: bool):
        self.requested.append(url)
        self.requests += 1
        return self.answers.get(url, (404, None, "", b""))


def page(*hrefs: str) -> tuple:
    return (200, None, "Example", ("<html><body>" + "".join(f'<a href="{h}">x</a>' for h in hrefs) + "Welcome. " * 50 + "</body></html>").encode())


def run(robots: str, url: str, *, help_pages: bool):
    answers = {"https://www.example.com/robots.txt": (200, None, "", robots.encode()), HOME: page(url), url: page()}
    fetcher = Fetcher(answers)
    checker = V.ConsumerCheck(fetcher, out=lambda line: None, help_pages=help_pages)
    failures = checker.run({"example.com": {"paths": [{"kind": "customer_help", "url": url, "source_url": HOME}]}}, ["example.com"])
    return failures, checker, fetcher


IN_SCOPE = ["/help", "/support/faq", "/terms", "/shipping-and-returns", "/warranty", "/claims", "/customer-service/contact",
            "/Policies/Refund-Policy", "/legal/cancellation"]
OUT_OF_SCOPE = ["/products/help-chair", "/search?q=refund", "/search/returns", "/cart", "/checkout/shipping", "/account/orders",
                "/login/help", "/signin", "/u/someone/warranty", "/p/12345/returns", "/s/help", "/my-orders/returns", "/user/support",
                "/community/help", "/forum/returns", "/reviews/warranty", "/profile/contact", "/item/refund", "/about", "/", "/careers",
                "/apply", "/help?query=x", "/help?search=x"]
OUT_OF_SCOPE_URLS = ["https://www.amazon.com/Lumbar-Support-Pillow/dp/B0ABC", "https://www.amazon.com/Contact-Lens-Case/dp/B0ABC",
                     "https://www.etsy.com/listing/123/delivery-truck-toy", "https://www.ebay.com/itm/Warranty-Card-Holder/123",
                     "https://www.reddit.com/r/help/comments/abc", "https://community.example.com/t/refund-help/123",
                     "https://example.com/questions/123/how-to-cancel", "https://example.com/account;jsessionid=1/help",
                     "https://example.com/products+returns", "https://example.com/help/?s=refund", "https://example.com/help?k=x",
                     "https://foo.gov.io/apply", "https://go.to/apply", "https://community.agency.gov/help",
                     "https://forum.example.com/help", "https://example.com/help?term=x", "https://example.com/help?keyword=x"]
IN_SCOPE_URLS = ["https://www.irs.gov/refunds/where-is-my-refund", "https://www.gov.uk/apply-passport",
                 "https://www.ato.gov.au/individuals/lodging-your-tax-return/file-a-return", "https://www.xx.go.jp/apply",
                 "https://service.gov.uk/apply", "https://example.com/warranties", "https://example.com/cancellations"]


@pytest.mark.parametrize("path", IN_SCOPE)
def test_help_and_policy_paths_are_in_scope(path):
    assert V.help_scope(path)


@pytest.mark.parametrize("path", OUT_OF_SCOPE)
def test_product_search_cart_account_and_user_content_paths_are_out_of_scope(path):
    assert not V.help_scope(path)


@pytest.mark.parametrize("url", OUT_OF_SCOPE_URLS)
def test_marketplace_user_content_and_lookalike_government_urls_are_out_of_scope(url):
    assert not V.url_in_help_scope(url)


@pytest.mark.parametrize("url", IN_SCOPE_URLS)
def test_refund_and_government_urls_are_in_scope(url):
    assert V.url_in_help_scope(url)


def test_the_link_wording_can_put_a_page_in_scope_but_never_a_excluded_one():
    assert V.help_scope("/x/12", "Warranty information")
    assert not V.help_scope("/x/12", "Our story")
    assert not V.help_scope("/product/12", "Warranty information")


def test_government_apply_and_submit_pages_are_in_scope_only_on_a_government_host():
    assert V.government_host("www.eeoc.gov") and V.government_host("tricare.mil") and V.government_host("service.gov.uk")
    assert not V.government_host("example.com") and not V.government_host("gov.example.com")
    assert V.government_host("www.gov.uk") and V.government_host("ato.gov.au") and V.government_host("xx.go.jp")
    assert not V.government_host("canada.ca") and not V.government_host("foo.gov.io") and not V.government_host("go.to")
    assert not V.government_host("gov.uk")
    for path in ("/apply", "/how-to-file", "/submit-a-form", "/uploading-documents"):
        assert V.help_scope(path, None, "agency.gov")
        assert not V.help_scope(path, None, "shop.example.com")
    assert not V.help_scope("/search/apply", None, "agency.gov")


def test_recorded_task_route_pages_are_in_scope():
    wanted = {"ring.com": "/warranty", "tricare.mil": "/patientresources/claims", "eeoc.gov": None, "healthcare.gov": None}
    seen = set()
    for host in wanted:
        for route in TASK_ROUTES_CURATED[host]:
            url = route["source_url"]
            if wanted[host] and urlsplit(url).path.lower() != wanted[host]:
                continue
            assert V.url_in_help_scope(url), url
            seen.add(host)
    assert seen == set(wanted)
    eeoc = {urlsplit(r["source_url"]).path for r in TASK_ROUTES_CURATED["eeoc.gov"]}
    assert len(eeoc) >= 2


def test_an_out_of_scope_path_that_robots_disallows_still_fails_with_the_option():
    url = "https://www.example.com/products/chair"
    failures, checker, fetcher = run("User-agent: *\nDisallow: /products\n", url, help_pages=True)
    assert any("robots_disallowed" in line for line in failures)
    assert checker.help_robots == {} and url not in fetcher.requested


def test_an_in_scope_path_that_robots_disallows_is_counted_not_failed_with_the_option():
    url = "https://www.example.com/help/returns"
    failures, checker, fetcher = run("User-agent: *\nDisallow: /help\n", url, help_pages=True)
    assert failures == []
    assert checker.help_robots == {url: "www.example.com"}
    assert url in fetcher.requested


def test_without_the_option_a_disallowed_help_path_fails_as_before():
    url = "https://www.example.com/help/returns"
    failures, checker, fetcher = run("User-agent: *\nDisallow: /help\n", url, help_pages=False)
    assert any("robots_disallowed" in line for line in failures)
    assert checker.help_robots == {} and url not in fetcher.requested


def test_the_option_does_not_soften_an_unreadable_or_refusing_robots_txt():
    url = "https://www.example.com/help"
    for status in (403, 500):
        answers = {"https://www.example.com/robots.txt": (status, None, "", b""), HOME: page(url), url: page()}
        fetcher = Fetcher(answers)
        checker = V.ConsumerCheck(fetcher, out=lambda line: None, help_pages=True)
        failures = checker.run({"example.com": {"paths": [{"kind": "customer_help", "url": url, "source_url": HOME}]}}, ["example.com"])
        assert failures and url not in fetcher.requested and checker.help_robots == {}


def test_the_option_is_only_for_task_routes():
    import sys
    argv = sys.argv
    sys.argv = ["verify_official_paths.py", "--help-pages"]
    try:
        with pytest.raises(SystemExit):
            V.main()
    finally:
        sys.argv = argv
