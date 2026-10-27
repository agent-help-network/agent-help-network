"""Pure checks for scripts/discover_status_sources.py (no network, no database)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("discover_status_sources", ROOT / "scripts" / "discover_status_sources.py")
discover = importlib.util.module_from_spec(_spec)
sys.modules["discover_status_sources"] = discover
_spec.loader.exec_module(discover)


# --- registrable domain and shared hosting -----------------------------------------------------------------

@pytest.mark.parametrize("host, expected", [
    ("status.example.com", "example.com"),
    ("example.com", "example.com"),
    ("status.bbc.co.uk", "bbc.co.uk"),
    ("naver.co.kr", "naver.co.kr"),
    ("someone.github.io", "someone.github.io"),   # shared hosting: each subdomain is its own registrable owner
    ("a.b.netlify.app", "b.netlify.app"),
])
def test_registrable_domain(host, expected):
    assert discover.registrable(host) == expected


@pytest.mark.parametrize("host", ["acme.statuspage.io", "acme.instatus.com", "x.github.io", "page.betteruptime.com", "abc.stspg-customer.com"])
def test_status_products_and_page_hosts_are_shared_hosting(host):
    assert discover.is_shared_hosting(host)


def test_a_publisher_domain_is_not_shared_hosting():
    assert not discover.is_shared_hosting("status.example.com")


# --- ownership evidence ------------------------------------------------------------------------------------

def test_a_same_registered_domain():
    code, detail, url = discover.judge_evidence("example.com", "status.example.com")
    assert code == "A" and url == "" and "registered domain" in detail


def test_a_works_below_a_two_label_public_suffix():
    assert discover.judge_evidence("bbc.co.uk", "status.bbc.co.uk")[0] == "A"
    assert discover.judge_evidence("bbc.co.uk", "status.other.co.uk")[0] == "needs_review"


def test_a_never_matches_through_shared_hosting():
    # A page on acme.statuspage.io shares the registrable domain statuspage.io with the site statuspage.io only by accident.
    assert discover.judge_evidence("statuspage.io", "acme.statuspage.io")[0] == "needs_review"
    assert discover.judge_evidence("github.io", "someone.github.io")[0] == "needs_review"


def test_a_lookalike_domain_is_not_a_match():
    assert discover.judge_evidence("example.com", "status.example.com.evil.net")[0] == "needs_review"
    assert discover.judge_evidence("example.com", "examplestatus.com")[0] == "needs_review"


def test_b_own_status_subdomain_redirects_to_another_domain():
    code, detail, _ = discover.judge_evidence("perplexity.ai", "perplexitystatus.com", via_own_status=True)
    assert code == "B" and "status.perplexity.ai" in detail


def test_b_does_not_apply_to_a_shared_hosting_site():
    assert discover.judge_evidence("github.io", "elsewhere.com", via_own_status=True)[0] == "needs_review"


def test_c_documentation_link_records_its_url():
    code, _, url = discover.judge_evidence("npmjs.com", "status.npmjs.org", doc_pages=["https://docs.example/a", "https://docs.example/b"])
    assert code == "C" and url == "https://docs.example/a"


def test_a_wins_over_b_and_c():
    assert discover.judge_evidence("example.com", "status.example.com", via_own_status=True, doc_pages=["https://x.test/"])[0] == "A"


def test_no_rule_means_needs_review():
    code, _, url = discover.judge_evidence("example.com", "examplestatus.io")
    assert code == "needs_review" and url == ""


# --- link extraction ---------------------------------------------------------------------------------------

DOC = """
<html><body>
  <a href="https://status.acme.dev/">Status</a>
  <a href="/status">Status</a>
  <a href="https://acmestatus.com/incidents">All systems</a>
  <a href="https://twitter.com/acmestatus">Follow</a>
  <a href="https://www.statusgator.com/services/acme">Status</a>
  <a href="https://developer.statuspage.io/">Statuspage API</a>
  <a href="http://insecure.status.acme.dev/">System status</a>
  <a href="https://docs.acme.dev/errors">Status codes</a>
  <a href="https://example.org/about#team">About</a>
  <a href="mailto:a@b.c">Mail</a>
</body></html>
"""


def test_extract_links_resolves_relative_urls_and_keeps_https_only():
    links = dict(discover.extract_links(DOC, "https://docs.acme.dev/start"))
    assert links["https://docs.acme.dev/status"] == "Status"
    assert "http://insecure.status.acme.dev/" not in links
    assert "https://example.org/about" in links  # fragment dropped


def test_status_links_picks_status_hosts_and_status_link_text():
    found = discover.status_links(DOC, "https://docs.acme.dev/start")
    assert "https://status.acme.dev/" in found
    assert "https://acmestatus.com/incidents" in found


def test_status_links_ignores_aggregators_social_vendor_pages_and_same_host_paths():
    found = discover.status_links(DOC, "https://docs.acme.dev/start")
    joined = " ".join(found)
    assert "twitter.com" not in joined and "statusgator" not in joined and "developer.statuspage.io" not in joined
    assert "docs.acme.dev" not in joined  # the documentation host's own /status path and "Status codes"


def test_status_links_survives_broken_markup():
    assert discover.status_links("<a href='https://status.x.dev/'>Status<div><a", "https://x.dev/") == ["https://status.x.dev/"]


# --- page, provider and candidate rules ---------------------------------------------------------------------

@pytest.mark.parametrize("title, url, expected", [
    ("Acme Status", "https://status.acme.com/", True),
    ("Welcome to Acme", "https://acmestatus.com/", False),           # a "status" host label alone, with nothing else on the page, is not enough
    ("Acme - All systems operational", "https://example.net/s", True),
    ("Acme - Home", "https://www.acme.com/", False),                  # status.<apex> redirected to the home page
    ("", "https://status.acme.com/", False),
    ("Just a moment...", "https://status.acme.com/", False),
    ("404 Not Found - status", "https://status.acme.com/", False),
])
def test_looks_like_status_page(title, url, expected):
    assert discover.looks_like_status_page(title, url) is expected


@pytest.mark.parametrize("target, service", [
    ("abcdefghijkl.stspg-customer.com.", "Atlassian Statuspage"),
    ("acme.instatus.com", "Instatus"),
    ("statuspage.betteruptime.com.", "Better Stack"),
    ("pages.incident.io", "incident.io"),
    ("example.net.", None),
])
def test_service_for_cname(target, service):
    assert discover.service_for_cname(target) == service


def test_fingerprint_prefers_dns_then_headers_then_markers():
    assert discover.fingerprint(["x.instatus.com."], {}, "")[0] == "Instatus"
    assert discover.fingerprint([], {"X-Statuspage-Version": "1"}, "")[0] == "Atlassian Statuspage"
    assert discover.fingerprint([], {}, "<footer>Powered by incident.io</footer>")[0] == "incident.io"
    assert discover.fingerprint([], {}, "plain page") == ("self_built_or_unknown", "")


def test_candidate_names_are_first_party_first_and_exclude_the_site_itself():
    hosts = [host for host, _ in discover.candidate_urls("perplexity.ai")]
    assert hosts[0] == "status.perplexity.ai"
    assert "perplexitystatus.com" in hosts and "perplexity.statuspage.io" in hosts
    assert discover.candidate_urls("example.com")[0] == ("status.example.com", "status_subdomain")
    assert "status.bbc.com" in [h for h, _ in discover.candidate_urls("bbc.co.uk")]
    assert "status.example.com" not in [h for h, _ in discover.candidate_urls("status.example.com")]


# --- host selection ------------------------------------------------------------------------------------------

def test_mapped_hosts_are_skipped_before_anything_else():
    mapped = discover.mapped_hosts()
    assert "github.com" in mapped and "netflix.com" in mapped
    assert discover.skip_reason("github.com", mapped) == "already_mapped"


def test_hosts_that_are_not_aggregate_eligible_or_fold_onto_an_apex_are_skipped():
    assert discover.skip_reason("tenant.example.com", set()) == "not_aggregate_eligible"
    assert discover.skip_reason("www.example.com", set()) == "not_aggregate_eligible"
    assert discover.skip_reason("xn--bcher-kva.com", set()) == "not_aggregate_eligible"
    assert discover.skip_reason("example.com", set()) is None
    assert discover.skip_reason("bbc.co.uk", set()) is None


def test_read_hosts_accepts_lists_and_csv_and_normalises(tmp_path):
    plain = tmp_path / "hosts.txt"
    plain.write_text("# seed\nExample.com\nhttps://Foo.org/path # comment\nexample.com\n\n", encoding="utf-8")
    assert discover.read_hosts(plain) == ["example.com", "foo.org"]
    table = tmp_path / "excluded.csv"
    table.write_text("host,candidate,reason\nx.com,https://s.x.com/,why\ny.org,,\n", encoding="utf-8")
    assert discover.read_hosts(table) == ["x.com", "y.org"]


# --- decision and output -------------------------------------------------------------------------------------

def _probe(host, kind, final_host=None, **extra):
    return discover.Probe(host=host, origin="name_rule", kind=kind, final_host=final_host or host,
                          page=f"https://{final_host or host}/", **extra)


def test_decide_prefers_verified_evidence_then_feeds():
    link = (_probe("status.example.com", "link"), ("A", "d", ""))
    feed = (_probe("examplestatus.com", "feed"), ("needs_review", "d", ""))
    assert discover.decide("example.com", [feed, link])[0:2] == ("final", link[0])
    feed_a = (_probe("status.example.com", "feed"), ("A", "d", ""))
    assert discover.decide("example.com", [link, feed_a])[1] is feed_a[0]


def test_decide_sends_unowned_and_blocked_candidates_to_review():
    unowned = (_probe("examplestatus.com", "link"), ("needs_review", "no rule", ""))
    blocked = (_probe("status.example.com", "blocked", note="blocked:403"), ("needs_review", "blocked:403", ""))
    assert discover.decide("example.com", [unowned])[0] == "needs_review"
    disposition, best, _, reason = discover.decide("example.com", [blocked])
    assert disposition == "needs_review" and best is blocked[0] and "refused" in reason


def test_decide_excludes_when_nothing_answers():
    dead = (_probe("status.example.com", "none", note="no_public_dns"), ("needs_review", "no_public_dns", ""))
    disposition, best, _, reason = discover.decide("example.com", [dead])
    assert disposition == "excluded" and best is None and "no_public_dns" in reason


def test_display_name_and_scope_flag():
    probe = _probe("status.stripe.com", "feed", page_name="Stripe")
    assert discover.display_name("stripe.com", probe) == "Stripe Status"
    assert discover.scope_flag("stripe.com", probe) == ""
    other = _probe("www.metastatus.com", "link", title="Meta Status")
    assert discover.scope_flag("meta.com", other) == ""   # "meta" appears in the page's own name
    assert discover.scope_flag("zoho.com", _probe("us.zohostatus.com", "link", title="Status")) == ""
    assert discover.scope_flag("wix.com", _probe("status.example.net", "link", title="Cloud Status")) == "page_name_differs"


def test_statusphere_file_is_optional(tmp_path):
    assert discover.load_statusphere(None)[0] == {} and "skipped" in discover.load_statusphere(None)[1]
    missing = discover.load_statusphere(tmp_path / "none.go")
    assert missing[0] == {} and "not found" in missing[1]


def test_statusphere_go_list_is_matched_by_brand_label(tmp_path):
    path = tmp_path / "status_pages.go"
    path.write_text('''package status_pages
var StatusPages = []api.StatusPage{
	{
		URL:  "https://status.circleci.com",
		Name: "Circle CI",
	}, {
		URL:  "http://codeship.statuspage.io",
		Name: "codeship",
	}, {
		URL:  "https://currents.instatus.com",
		Name: "Currents",
	},
}
''', encoding="utf-8")
    data, note = discover.load_statusphere(path)
    assert data["circleci"] == ["status.circleci.com"] and "loaded 3 entries" in note
    assert data["codeship"] == ["codeship.statuspage.io"]      # shared hosting: the first label is the brand
    assert "statuspage" not in data and "instatus" not in data  # never matched to the hosting product itself
    assert data["currents"] == ["currents.instatus.com"]


def test_statusphere_candidates_are_probed_but_never_trusted(tmp_path):
    net = StubNet({"https://circle-status.example.net/api/v2/status.json": (200, FEED)})
    result = discover.discover_host(net, "circleci.com", {"circleci": ["circle-status.example.net"]}, {})
    probe, judged = next((p, j) for p, j in result["probes"] if p.host == "circle-status.example.net")
    assert probe.origin == "statusphere" and probe.kind == "feed" and judged[0] == "needs_review"


def test_a_run_never_goes_below_one_request_per_second(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["discover_status_sources.py", "--hosts-file", "x.txt", "--batch", "t", "--min-interval", "0.1"])
    with pytest.raises(SystemExit):
        discover.main()
    assert "below 1 second" in capsys.readouterr().err


def test_vendor_marketing_pages_are_not_status_pages():
    # An unclaimed Statuspage custom domain redirects to Atlassian's product page, whose path says "statuspage".
    assert not discover.looks_like_status_page("Statuspage | Atlassian", "https://www.atlassian.com/software/statuspage")
    assert not discover.looks_like_status_page("Status page software", "https://www.statuspage.io/")
    assert discover.looks_like_status_page("Atlassian Status", "https://status.atlassian.com/")


# --- end to end with a stub network ------------------------------------------------------------------------------

class StubNet:
    """Answers fetches from a table; anything not listed is a 404. Records every URL asked for."""

    def __init__(self, pages: dict, cnames: dict | None = None):
        self.pages, self.cnames, self.asked = pages, cnames or {}, []

    def public_host(self, host):
        return True

    def cname_chain(self, host):
        return self.cnames.get(host, [])

    def fetch(self, url, *, cap=0, follow=True):
        self.asked.append(url)
        found = self.pages.get(url)
        if found is None:
            return {"status": 404, "final_url": url, "chain": [discover.urlsplit(url).hostname], "body": b"", "headers": {}, "error": ""}
        status, body, *rest = found
        final = rest[0] if rest else url
        return {"status": status, "final_url": final, "chain": [discover.urlsplit(url).hostname, discover.urlsplit(final).hostname],
                "body": body if isinstance(body, bytes) else body.encode(), "headers": rest[1] if len(rest) > 1 else {}, "error": ""}


FEED = json.dumps({"page": {"id": "abc", "name": "Acme", "updated_at": "2026-10-18T00:00:00Z"}, "status": {"indicator": "none"}})


def test_a_status_subdomain_with_a_feed_is_a_final_feed():
    net = StubNet({"https://status.acme.com/api/v2/status.json": (200, FEED)})
    result = discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    disposition, best, judged, _ = discover.decide("acme.com", result["probes"])
    assert disposition == "final" and best.kind == "feed" and judged[0] == "A"
    assert best.page == "https://status.acme.com/" and best.api == "https://status.acme.com/api/v2/status.json"


def test_own_status_subdomain_redirecting_elsewhere_is_b():
    net = StubNet({"https://status.acme.com/api/v2/status.json": (404, ""),
                   "https://status.acme.com/": (200, "<title>Acme Status</title>", "https://acmestatus.net/")})
    result = discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    disposition, best, judged, _ = discover.decide("acme.com", result["probes"])
    assert (disposition, judged[0], best.page) == ("final", "B", "https://acmestatus.net/")


def test_supplied_evidence_page_must_be_the_publishers_and_must_link_the_candidate():
    status_page = {"https://acmestatus.net/api/v2/status.json": (404, ""), "https://acmestatus.net/": (200, "<title>Acme Status</title>")}
    linked = StubNet({**status_page, "https://docs.acme.com/help": (200, '<a href="https://acmestatus.net/">Status</a>')})
    ok = discover.discover_host(linked, "acme.com", {}, {}, supplied=[("https://acmestatus.net/", "https://docs.acme.com/help")])
    assert discover.decide("acme.com", ok["probes"])[2] == ("C", "the publisher's own documentation links to this page", "https://docs.acme.com/help")
    foreign = StubNet({**status_page, "https://blog.example.net/acme": (200, '<a href="https://acmestatus.net/">Status</a>')})
    no = discover.discover_host(foreign, "acme.com", {}, {}, supplied=[("https://acmestatus.net/", "https://blog.example.net/acme")])
    assert discover.decide("acme.com", no["probes"])[0] == "needs_review"   # evidence page is not on the publisher's domain
    unlinked = StubNet({**status_page, "https://docs.acme.com/help": (200, '<a href="https://other.example/">x</a>')})
    none = discover.discover_host(unlinked, "acme.com", {}, {}, supplied=[("https://acmestatus.net/", "https://docs.acme.com/help")])
    assert discover.decide("acme.com", none["probes"])[0] == "needs_review"


def test_directed_runs_probe_only_what_was_supplied():
    net = StubNet({})
    discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    assert all("status.acme.com" in url for url in net.asked)


def test_a_refused_page_on_a_delegated_publisher_host_is_a_but_a_bare_one_is_not():
    refused = {"https://status.acme.com/api/v2/status.json": (403, ""), "https://status.acme.com/": (403, "")}
    delegated = StubNet(refused, cnames={"status.acme.com": ["acme.instatus.com."]})
    result = discover.discover_host(delegated, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    disposition, best, judged, _ = discover.decide("acme.com", result["probes"])
    assert disposition == "final" and judged[0] == "A" and "Instatus" in judged[1] and best.page == "https://status.acme.com/"
    bare = StubNet(refused)
    result = discover.discover_host(bare, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    assert discover.decide("acme.com", result["probes"])[0] == "needs_review"


def test_an_inactive_statuspage_placeholder_is_not_a_page():
    net = StubNet({"https://acme.statuspage.io/": (200, "<title>Acme Status - Page Inactive</title>", "https://acme.statuspage.io/inactive")})
    result = discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://acme.statuspage.io/", "")])
    assert discover.decide("acme.com", result["probes"])[0] == "excluded"


def test_status_link_text_forms():
    page = ('<a href="https://connect.example.net/s">API status page</a><a href="https://a.example.org/">System Status:</a>'
            '<a href="https://mdn.example.org/http">HTTP status</a><a href="https://b.example.org/">Status codes</a>')
    assert discover.status_links(page, "https://developers.example.com/") == ["https://connect.example.net/s", "https://a.example.org/"]


def test_a_status_path_on_a_publisher_host_counts_as_a_status_page():
    assert discover.looks_like_status_page("Connectivity Portal", "https://connect.example.com/status", supplied=True)
    assert not discover.looks_like_status_page("Connectivity Portal", "https://connect.example.com/status")  # found by rule, not supplied
    assert not discover.looks_like_status_page("Connectivity Portal", "https://connect.example.com/home")


def test_a_refused_page_that_the_publishers_docs_name_is_c_and_plain_text_mentions_count():
    refused = {"https://status.acme.com/api/v2/status.json": (403, ""), "https://status.acme.com/": (403, ""),
               "https://docs.acme.com/errors": (200, "<p>See https://status.acme.com/ for incidents</p>")}
    net = StubNet(refused)
    result = discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "https://docs.acme.com/errors")])
    disposition, _, judged, _ = discover.decide("acme.com", result["probes"])
    assert disposition == "final" and judged[0] == "C" and judged[2] == "https://docs.acme.com/errors" and "refused" in judged[1]


def test_status_path_means_a_status_segment_not_any_path_containing_the_word():
    assert discover.looks_like_status_page("Developer Portal", "https://developer.example.com/support/api-status", supplied=True)
    assert not discover.looks_like_status_page("Buy this domain", "https://www.atom.com/name/SnapStatus")


def test_recognise_json_shapes():
    instatus = b'{"page": {"name": "A", "url": "https://a.example", "status": "UP"}, "activeIncidents": []}'
    betterstack = b'{"data": {"id": "1", "type": "status_page", "attributes": {"company_name": "A", "aggregate_state": "operational"}}}'
    assert discover.recognise_json("/summary.json", instatus) == "Instatus"
    assert discover.recognise_json("/index.json", betterstack) == "Better Stack"
    assert discover.recognise_json("/index.json", instatus) is None       # right shape, wrong path
    assert discover.recognise_json("/summary.json", b"<html>") is None
    assert discover.recognise_json("/summary.json", b"[1, 2]") is None
    assert discover.recognise_json("/summary.json", b'{"page": {"status": "operational"}}') is None  # Statuspage's own summary.json is not Instatus


def test_more_status_products_are_recognised_by_page_text():
    assert discover.fingerprint([], {}, "<title>Codeberg - Uptime Kuma</title>")[0] == "Uptime Kuma"
    assert discover.fingerprint([], {}, "<title>Pingdom Public Reports Overview</title>")[0] == "Pingdom"


def test_a_redirect_that_drops_the_path_still_finds_the_feed_on_the_landing_host():
    net = StubNet({"https://status.acme.com/api/v2/status.json": (200, "<html>home</html>", "https://www.acmestatus.net/"),
                   "https://status.acme.com/": (200, "<title>Acme Status</title>", "https://www.acmestatus.net/"),
                   "https://www.acmestatus.net/api/v2/status.json": (200, FEED)})
    result = discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    disposition, best, judged, _ = discover.decide("acme.com", result["probes"])
    assert (disposition, best.kind, judged[0]) == ("final", "feed", "B")
    assert best.page == "https://www.acmestatus.net/" and best.api == "https://www.acmestatus.net/api/v2/status.json"


def test_next_clock_time_is_always_in_the_future():
    from datetime import datetime
    target = discover.next_clock_time("07:30")
    assert target > datetime.now() and (target.hour, target.minute) == (7, 30)


def test_a_redirect_onto_the_publishers_own_site_must_say_it_is_a_system_status_page():
    kwargs = {"site_host": "itch.io"}
    assert not discover.looks_like_status_page("Status - itch.io", "https://itch.io/profile/status", **kwargs)           # a profile 'status'
    assert not discover.looks_like_status_page("status on DeviantArt", "https://www.deviantart.com/status", site_host="deviantart.com")
    assert not discover.looks_like_status_page("Speedtest by Ookla", "https://www.speedtest.net/server/status", site_host="speedtest.net")
    assert discover.looks_like_status_page("Acme System Status", "https://www.acme.com/system-status", site_host="acme.com")
    assert discover.looks_like_status_page("Acme Status", "https://status.acme.com/", site_host="acme.com")  # a status-named host is not 'the main site'


def test_a_status_named_host_with_a_generic_title_needs_a_known_status_product_behind_it():
    generic = "Access via Synology"
    assert not discover.looks_like_status_page(generic, "https://status.quickconnect.to/", site_host="quickconnect.to")
    assert discover.looks_like_status_page(generic, "https://status.quickconnect.to/", site_host="quickconnect.to", cname_service="Instatus")


def test_recheck_applies_the_current_rule_to_cached_probes():
    old = discover.Probe(host="status.itch.io", origin="status_subdomain", kind="link", page="https://itch.io/profile/status", title="Status - itch.io", final_host="itch.io")
    assert discover.recheck(old, "itch.io").kind == "none"
    fine = discover.Probe(host="status.acme.com", origin="status_subdomain", kind="link", page="https://status.acme.com/", title="Acme Status", final_host="status.acme.com")
    assert discover.recheck(fine, "acme.com") is fine


def test_a_hand_supplied_status_path_on_the_publishers_own_domain_is_accepted():
    # The main-site rule would reject a generic title there; a curator-supplied /status address is the exception.
    assert discover.looks_like_status_page("Mastercard Developers", "https://developer.mastercard.com/api-status", site_host="mastercard.com", supplied=True)
    assert not discover.looks_like_status_page("Mastercard Developers", "https://developer.mastercard.com/api-status", site_host="mastercard.com")


# --- robots.txt before a feed path (same decision as the service: ahn.feed_robots) ---------------------------------

FEED_URL = "https://status.acme.com/api/v2/status.json"
ROBOTS_URL = "https://status.acme.com/robots.txt"


def _probe_with_robots(robots):
    pages = {FEED_URL: (200, FEED)}
    if robots is not None:
        pages[ROBOTS_URL] = robots
    net = StubNet(pages)
    return net, discover.probe_candidate(net, "status.acme.com", "supplied", "/", "acme.com")


def test_robots_allowing_the_feed_path_lets_it_be_read():
    net, probe = _probe_with_robots((200, "User-agent: *\nDisallow: /private/\n"))
    assert probe.kind == "feed" and probe.robots == ""
    assert net.asked.index(ROBOTS_URL) < net.asked.index(FEED_URL)


def test_robots_disallowing_the_feed_path_means_it_is_not_requested():
    net, probe = _probe_with_robots((200, "User-agent: *\nDisallow: /api/\n"))
    assert FEED_URL not in net.asked
    assert probe.kind != "feed" and probe.robots == "robots_blocked:/api/v2/status.json"
    # a group naming the product token wins over `*`, as in the service
    net, probe = _probe_with_robots((200, "User-agent: *\nDisallow: /api/\n\nUser-agent: AgentHelpNetwork\nAllow: /api/\n"))
    assert probe.kind == "feed" and probe.robots == ""


def test_missing_robots_txt_allows_the_feed_path():
    net, probe = _probe_with_robots(None)  # the stub answers 404: no rules
    assert probe.kind == "feed" and probe.robots == "" and ROBOTS_URL in net.asked


def test_unreadable_robots_txt_is_complete_disallow_like_the_service():
    net, probe = _probe_with_robots((503, ""))
    assert FEED_URL not in net.asked and probe.robots == "robots_unreadable:/api/v2/status.json"
    net, probe = _probe_with_robots((403, ""))  # another 4xx: "unavailable", every path allowed (RFC 9309 2.3.1.3)
    assert probe.kind == "feed" and probe.robots == ""


def test_robots_txt_is_read_once_per_host():
    net = StubNet({ROBOTS_URL: (200, "User-agent: *\nAllow: /\n")})
    assert discover.robots_verdict(net, "status.acme.com", "/summary.json") == ""
    assert discover.robots_verdict(net, "status.acme.com", "/index.json") == ""
    assert net.asked.count(ROBOTS_URL) == 1


def test_a_blocked_feed_shows_in_the_exclusion_reason():
    net = StubNet({ROBOTS_URL: (200, "User-agent: *\nDisallow: /\n")})
    result = discover.discover_host(net, "acme.com", {}, {}, supplied=[("https://status.acme.com/", "")])
    disposition, _, _, reason = discover.decide("acme.com", result["probes"])
    assert disposition == "excluded" and "robots_blocked:/api/v2/status.json" in reason


def test_robots_txt_redirecting_apex_to_www_is_followed_once_like_the_service():
    net = StubNet({"https://acme.com/robots.txt": (301, "", "https://acme.com/robots.txt", {"location": "https://www.acme.com/robots.txt"}),
                   "https://www.acme.com/robots.txt": (200, "User-agent: *\nDisallow: /api/\n")})
    assert discover.robots_verdict(net, "acme.com", "/api/v2/status.json") == "robots_blocked"
    other = StubNet({"https://acme.com/robots.txt": (301, "", "https://acme.com/robots.txt", {"location": "https://elsewhere.example/robots.txt"})})
    assert discover.robots_verdict(other, "acme.com", "/api/v2/status.json") == "robots_unreadable"
