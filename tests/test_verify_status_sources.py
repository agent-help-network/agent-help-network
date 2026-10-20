"""Pure checks for scripts/verify_status_sources.py and scripts/collect_demand_status.py (stub network, no database)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest

from ahn.status_refresh import Source

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify = _load("verify_status_sources")
collect = _load("collect_demand_status")
discover = verify.discover

STATUSPAGE = json.dumps({"page": {"id": "x", "name": "Acme", "updated_at": "2026-10-18T00:00:00Z"}, "status": {"indicator": "none"}})
INSTATUS = json.dumps({"page": {"name": "Acme", "status": "UP"}, "activeIncidents": []})
BETTERSTACK = json.dumps({"data": {"id": "1", "type": "status_page", "attributes": {"aggregate_state": "operational"}}})
API = "https://status.acme.com/api/v2/status.json"
PAGE = "https://status.acme.com/"


class StubNet:
    """Answers fetches from {url: (status, body[, final_url[, headers]])}; a missing url is a 404. Counts requests.

    ``errors`` maps a url to an exception (a transport failure) or to a bare Net.fetch error string."""

    def __init__(self, pages: dict, cnames: dict | None = None, errors: dict | None = None):
        self.pages, self.cnames, self.errors = pages, cnames or {}, errors or {}
        self.asked: list[str] = []
        self.requests = 0

    def cname_chain(self, host):
        return self.cnames.get(host, [])

    def fetch(self, url, *, cap=discover.TITLE_CAP, follow=True):
        self.asked.append(url)
        self.requests += 1
        host = urlsplit(url).hostname
        result = {"status": None, "final_url": url, "chain": [host], "body": b"", "headers": {}, "error": "", "exc": None}
        if url in self.errors:
            failure = self.errors[url]
            if isinstance(failure, Exception):
                result.update(error=type(failure).__name__, exc=failure)
            else:
                result.update(error=failure)
            return result
        found = self.pages.get(url, (404, ""))
        status, body = found[:2]
        final = found[2] if len(found) > 2 else None
        headers = found[3] if len(found) > 3 else {}
        body = body.encode() if isinstance(body, str) else body
        result.update(status=status, body=body[:cap] if status == 200 else b"", headers=headers)
        if final and follow:
            result.update(final_url=final, chain=[host, urlsplit(final).hostname])
        return result


def feed_obs(kind, url, pages, **kw):
    return verify.observe_feed(StubNet(pages, **kw), kind, url)


# --- SOURCES: one label each ----------------------------------------------------------------------------------

@pytest.mark.parametrize("kind, url, body", [
    ("statuspage", API, STATUSPAGE),
    ("instatus", "https://acme.instatus.com/summary.json", INSTATUS),
    ("betterstack", "https://status.acme.com/index.json", BETTERSTACK),
])
def test_a_feed_that_parses_with_its_kinds_parser_is_ok(kind, url, body):
    obs = feed_obs(kind, url, {url: (200, body)})
    assert obs["result"] == "ok" and obs["http"] == 200


def test_the_wrong_shape_or_not_json_is_parse_failed():
    assert feed_obs("statuspage", API, {API: (200, INSTATUS)})["result"] == "parse_failed"     # valid JSON, wrong kind
    assert feed_obs("statuspage", API, {API: (200, "<html><title>Acme</title></html>")})["result"] == "parse_failed"
    assert feed_obs("instatus", API, {API: (200, STATUSPAGE)})["result"] == "parse_failed"


def test_other_statuses_are_http_nnn():
    assert feed_obs("statuspage", API, {})["result"] == "http_404"
    assert feed_obs("statuspage", API, {API: (503, "")})["result"] == "http_503"
    assert feed_obs("statuspage", API, {API: (410, "")})["http"] == 410


@pytest.mark.parametrize("status", [401, 403, 429, 202])
def test_refusals_are_not_http_nnn(status):
    assert feed_obs("statuspage", API, {API: (status, "")})["result"] == "refused"


def test_a_bot_challenge_page_with_200_is_refused():
    assert feed_obs("statuspage", API, {API: (200, "<title>Just a moment...</title>")})["result"] == "refused"


def test_a_body_over_the_kinds_cap_is_too_large():
    cap = verify.FEED_KINDS["statuspage"][0]
    assert feed_obs("statuspage", API, {API: (200, b" " * (cap + 10))})["result"] == "too_large"


def test_transport_failures_use_the_service_labels():
    for exc, label in [(httpx.ConnectTimeout("t"), "timeout"), (httpx.ConnectError("refused"), "connect_error"),
                       (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), "dns_failed"), (httpx.ReadError("x"), "read_error")]:
        assert feed_obs("statuspage", API, {}, errors={API: exc})["result"] == label


def test_a_hop_that_is_not_public_is_labelled_by_dns(monkeypatch):
    monkeypatch.setattr(verify, "hop_label", lambda host: "dns_failed" if host == "gone.example" else "not_public")
    url = "https://gone.example/api/v2/status.json"
    assert feed_obs("statuspage", url, {}, errors={url: "non_public_or_non_https_hop"})["result"] == "dns_failed"
    assert feed_obs("statuspage", API, {}, errors={API: "non_public_or_non_https_hop"})["result"] == "not_public"
    assert feed_obs("statuspage", API, {}, errors={API: "too_many_redirects"})["result"] == "too_many_redirects"


def test_an_unknown_kind_is_reported_not_guessed():
    assert verify.observe_feed(StubNet({}), "mystery", API)["result"] == "unknown_kind"


def test_a_feed_is_read_without_following_redirects():
    net = StubNet({API: (200, STATUSPAGE)})
    seen = []
    original = net.fetch
    net.fetch = lambda url, **kw: (seen.append(kw), original(url, **kw))[1]
    verify.observe_feed(net, "statuspage", API)
    assert seen == [{"cap": verify.FEED_KINDS["statuspage"][0], "follow": False}]


# --- redirect off the host -------------------------------------------------------------------------------------------

def test_a_redirect_to_another_host_is_flagged_with_the_new_address():
    obs = feed_obs("statuspage", API, {API: (301, "", None, {"location": "https://acme.statuspage.io/api/v2/status.json"})})
    assert obs["result"] == "redirect_off_host" and "acme.statuspage.io" in obs["detail"]


def test_a_relative_or_www_only_redirect_is_just_the_http_status():
    assert feed_obs("statuspage", API, {API: (302, "", None, {"location": "/api/v2/status.json"})})["result"] == "http_302"
    bare = "https://acme.com/api/v2/status.json"
    assert feed_obs("statuspage", bare, {bare: (301, "", None, {"Location": "https://www.acme.com/api/v2/status.json"})})["result"] == "http_301"
    assert not verify.off_host("https://www.acme.com/x", "https://acme.com/y")
    assert verify.off_host("https://acme.com/", "https://status.acme.com/")


def test_check_source_pages_flags_a_page_that_moved(tmp_path, monkeypatch):
    pages = {API: (200, STATUSPAGE), PAGE: (200, "<title>Acme</title>", "https://acmestatus.net/")}
    args = _args(tmp_path, monkeypatch, check_source_pages=True)
    monkeypatch.setattr(verify, "SOURCES", {"acme.com": _source()})
    monkeypatch.setattr(verify, "OFFICIAL_STATUS_PAGES", {})
    rows, _ = verify.run(args, StubNet(pages))
    assert rows[0]["result"] == "redirect_off_host" and "acmestatus.net" in rows[0]["detail"]
    quiet = StubNet({API: (200, STATUSPAGE), PAGE: (200, "<title>Acme</title>")})
    assert verify.run(_args(tmp_path, monkeypatch, check_source_pages=True), quiet)[0][0]["result"] == "ok" and quiet.requests == 2
    assert verify.run(_args(tmp_path, monkeypatch), StubNet({API: (200, STATUSPAGE)}))[0][0]["result"] == "ok"  # the page is read only on request


# --- link-only pages --------------------------------------------------------------------------------------------------

def link_row(pages, url=PAGE, host="acme.com", probe="known", **kw):
    net = StubNet(pages, **kw)
    return verify.page_row(host, url, verify.observe_page(net, host, url, upgrade_probe=probe)), net


def test_a_page_that_still_reads_as_a_status_page_is_ok():
    row, net = link_row({PAGE: (200, "<title>Acme Status</title>")})
    assert row["result"] == "ok" and net.requests == 1


def test_a_page_that_became_a_home_page_or_parked_is_not_a_status_page():
    assert link_row({PAGE: (200, "<title>Acme - Home</title>", "https://www.acme.com/")})[0]["result"] == "not_a_status_page"
    assert link_row({PAGE: (200, "<title>Buy this domain</title>")})[0]["result"] == "not_a_status_page"


def test_link_page_labels():
    assert link_row({})[0]["result"] == "http_404"
    assert link_row({PAGE: (500, "")})[0]["result"] == "http_500"
    assert link_row({PAGE: (403, "")})[0]["result"] == "refused"
    assert link_row({PAGE: (200, "<title>Attention Required! | Cloudflare</title>")})[0]["result"] == "refused"
    assert link_row({}, errors={PAGE: httpx.ConnectTimeout("t")})[0]["result"] == "timeout"


def test_a_hand_curated_status_path_on_the_publishers_domain_is_ok():
    url = "https://developer.acme.com/api-status"
    assert link_row({url: (200, "<title>Acme Developers</title>")}, url=url)[0]["result"] == "ok"


def test_a_link_page_on_a_known_product_with_a_parsing_feed_is_an_upgrade_candidate():
    pages = {PAGE: (200, "<title>Acme Status</title>"), "https://status.acme.com/api/v2/status.json": (200, STATUSPAGE)}
    row, net = link_row(pages, cnames={"status.acme.com": ["abc.stspg-customer.com."]})
    assert row["result"] == "upgrade_candidate" and "statuspage feed parses" in row["detail"] and net.requests == 2
    pages = {PAGE: (200, "<title>Acme Status</title>"), "https://status.acme.com/summary.json": (200, INSTATUS)}
    assert link_row(pages, cnames={"status.acme.com": ["acme.instatus.com."]})[0]["detail"].startswith("instatus feed parses")
    pages = {PAGE: (200, "<title>Acme Status</title>"), "https://status.acme.com/index.json": (200, BETTERSTACK)}
    assert link_row(pages, cnames={"status.acme.com": ["acme.betteruptime.com."]})[0]["result"] == "upgrade_candidate"


def test_a_recognised_product_whose_feed_does_not_parse_stays_ok():
    pages = {PAGE: (200, "<title>Acme Status</title>"), "https://status.acme.com/api/v2/status.json": (200, "<html>no</html>")}
    row, net = link_row(pages, cnames={"status.acme.com": ["abc.stspg-customer.com."]})
    assert row["result"] == "ok" and net.requests == 2 and "Atlassian Statuspage" in row["detail"]


def test_an_unknown_product_gets_no_extra_request_unless_asked():
    pages = {PAGE: (200, "<title>Acme Status</title>"), "https://status.acme.com/api/v2/status.json": (200, STATUSPAGE)}
    row, net = link_row(pages)
    assert row["result"] == "ok" and net.requests == 1
    row, net = link_row(pages, probe="all")
    assert row["result"] == "upgrade_candidate" and net.requests == 2
    row, net = link_row(pages, probe="off", cnames={"status.acme.com": ["abc.stspg-customer.com."]})
    assert row["result"] == "ok" and net.requests == 1


def test_a_page_that_moved_to_another_domain_is_still_ok_but_says_so():
    row, _ = link_row({PAGE: (200, "<title>Acme Status</title>", "https://acmestatus.net/")})
    assert row["result"] == "ok" and "acmestatus.net" in row["detail"] and row["final_url"] == "https://acmestatus.net/"


# --- whole run ----------------------------------------------------------------------------------------------------------

def _source():
    return Source("Acme Status", "https://status.acme.com/", API)


def _args(tmp_path, monkeypatch, **extra):
    monkeypatch.setattr(verify, "WORK", tmp_path)
    options = dict(hosts=None, since_cache=False, date="2026-10-19", check_source_pages=False, upgrade_probe="known")
    return SimpleNamespace(**{**options, **extra})


def _tables(monkeypatch):
    monkeypatch.setattr(verify, "SOURCES", {
        "acme.com": _source(), "acme.net": _source(),  # share one feed: one request
        "good.org": Source("Good", "https://status.good.org/", "https://status.good.org/api/v2/status.json"),
        "gone.io": Source("Gone", "https://status.gone.io/", "https://status.gone.io/api/v2/status.json"),
    })
    monkeypatch.setattr(verify, "OFFICIAL_STATUS_PAGES", {"link.com": ("Link Status", "https://status.link.com/")})
    return {API: (200, STATUSPAGE), "https://status.good.org/api/v2/status.json": (200, STATUSPAGE),
            "https://status.gone.io/api/v2/status.json": (404, ""), "https://status.link.com/": (200, "<title>Link Status</title>")}


def test_a_run_writes_results_and_summary_and_shares_one_request_per_url(tmp_path, monkeypatch):
    pages = _tables(monkeypatch)
    net = StubNet(pages)
    rows, state = verify.run(_args(tmp_path, monkeypatch), net)
    assert state["complete"] and [r["host"] for r in rows] == ["acme.com", "acme.net", "good.org", "gone.io", "link.com"]
    assert net.requests == 4 and net.asked.count(API) == 1
    summary = json.loads((tmp_path / "2026-10-19" / "summary.json").read_text(encoding="utf-8"))
    assert (summary["checked"], summary["ok"], summary["not_ok"], summary["complete"]) == (5, 4, 1, True)
    assert summary["results"] == {"ok": 4, "http_404": 1} and summary["requests"] == 4
    header, *lines = (tmp_path / "2026-10-19" / "results.csv").read_text(encoding="utf-8").splitlines()
    assert header == "host,mapping,kind,url,result,http,final_url,detail" and len(lines) == 5 and "gone.io,feed,statuspage" in lines[3]


def test_since_cache_resumes_without_repeating_requests(tmp_path, monkeypatch):
    pages = _tables(monkeypatch)
    first = StubNet(pages)
    args = _args(tmp_path, monkeypatch)
    args.hosts = {"acme.com", "good.org"}
    verify.run(args, first)
    assert first.requests == 2
    second = StubNet(pages)
    args = _args(tmp_path, monkeypatch)
    args.since_cache = True
    rows, _ = verify.run(args, second)
    assert second.requests == 2 and len(rows) == 5          # only gone.io and link.com are new (acme.net shares acme.com's feed)
    fresh = StubNet(pages)
    verify.run(_args(tmp_path, monkeypatch), fresh)          # without the flag nothing is reused
    assert fresh.requests == 4


def test_a_request_budget_stops_the_run_and_a_resume_finishes_it(tmp_path, monkeypatch):
    pages = _tables(monkeypatch)

    class Budget(StubNet):
        def fetch(self, url, **kw):
            if self.requests >= 2:
                raise RuntimeError("request budget exhausted")
            return super().fetch(url, **kw)

    rows, state = verify.run(_args(tmp_path, monkeypatch), Budget(pages))
    assert not state["complete"] and len(rows) == 3 and "budget" in state["stopped"]       # acme.com, acme.net (shared), good.org
    summary = json.loads((tmp_path / "2026-10-19" / "summary.json").read_text(encoding="utf-8"))
    assert summary["complete"] is False
    args = _args(tmp_path, monkeypatch)
    args.since_cache = True
    rows, state = verify.run(args, StubNet(pages))
    assert state["complete"] and len(rows) == 5


def test_main_exits_1_when_anything_is_not_ok_and_0_when_all_ok(tmp_path, monkeypatch, capsys):
    pages = _tables(monkeypatch)
    monkeypatch.setattr(verify, "WORK", tmp_path)
    monkeypatch.setattr(verify.discover, "Net", lambda *a, **k: StubNet(pages))
    monkeypatch.setattr(sys, "argv", ["verify_status_sources.py", "--date", "2026-10-19"])
    assert verify.main() == 1
    out = capsys.readouterr().out
    assert "gone.io" in out and "http_404" in out and "4 ok, 1 not ok" in out
    monkeypatch.setattr(sys, "argv", ["verify_status_sources.py", "--date", "2026-10-20", "--hosts", "acme.com,good.org,nowhere.example"])
    assert verify.main() == 0
    assert "nowhere.example" in capsys.readouterr().out   # named but not mapped: said so


def test_an_upgrade_candidate_makes_the_run_exit_1(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "SOURCES", {})
    monkeypatch.setattr(verify, "OFFICIAL_STATUS_PAGES", {"acme.com": ("Acme Status", PAGE)})
    pages = {PAGE: (200, "<title>Acme Status</title>"), API: (200, STATUSPAGE)}
    monkeypatch.setattr(verify, "WORK", tmp_path)
    monkeypatch.setattr(verify.discover, "Net", lambda *a, **k: StubNet(pages, cnames={"status.acme.com": ["abc.stspg-customer.com."]}))
    monkeypatch.setattr(sys, "argv", ["verify_status_sources.py", "--date", "2026-10-19"])
    assert verify.main() == 1
    assert json.loads((tmp_path / "2026-10-19" / "summary.json").read_text(encoding="utf-8"))["upgrade_candidates"] == ["acme.com"]


def test_main_refuses_a_faster_pace(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["verify_status_sources.py", "--min-interval", "0.1"])
    with pytest.raises(SystemExit):
        verify.main()
    assert "below 1 second" in capsys.readouterr().err


def test_the_real_tables_are_all_in_scope_with_known_kinds():
    items = verify.plan(None)
    assert len(items) == len(verify.SOURCES) + len(verify.OFFICIAL_STATUS_PAGES)
    assert {kind for _, mapping, kind, _ in items if mapping == "feed"} <= set(verify.FEED_KINDS)
    assert [host for host, *_ in verify.plan({"github.com", "netflix.com"})] == ["github.com", "netflix.com"]


# --- collect_demand_status ------------------------------------------------------------------------------------------------

def test_demand_input_drops_mapped_and_ineligible_hosts_and_keeps_the_rest():
    hosts = ["github.com", "newsite.org", "www.other.example", "netflix.com", "tenant.example.com", "fresh.io"]
    keep, dropped = collect.filter_hosts(hosts)
    assert keep == ["newsite.org", "fresh.io"]
    assert dropped["already_mapped"] == ["github.com", "netflix.com"]
    assert dropped["not_aggregate_eligible"] == ["www.other.example", "tenant.example.com"]


def test_demand_filter_uses_the_given_mapping_and_keeps_order():
    keep, dropped = collect.filter_hosts(["b.io", "a.io", "c.io"], mapped={"a.io"})
    assert keep == ["b.io", "c.io"] and dropped == {"already_mapped": ["a.io"]}


def test_demand_run_feeds_only_the_filtered_list_to_discovery(tmp_path, monkeypatch, capsys):
    source = tmp_path / "looked-up.txt"
    source.write_text("# looked up this week\nGitHub.com\nhttps://Fresh.io/path\nfresh.io\nwww.skip.example\n\n", encoding="utf-8")
    monkeypatch.setattr(discover, "WORK", tmp_path / "status-discovery")
    seen = {}

    def fake_run(args):
        seen.update(hosts=discover.read_hosts(args.hosts_file), batch=args.batch, candidates_csv=args.candidates_csv)
        return 0

    monkeypatch.setattr(discover, "run_discovery", fake_run)
    monkeypatch.setattr(sys, "argv", ["collect_demand_status.py", "--hosts-file", str(source), "--date", "2026-10-19"])
    assert collect.main() == 0
    assert seen == {"hosts": ["fresh.io"], "batch": "demand-2026-10-19", "candidates_csv": None}
    info = json.loads((tmp_path / "status-discovery" / "demand-2026-10-19" / "input.json").read_text(encoding="utf-8"))
    assert info["hosts_in"] == 3 and info["kept"] == 1
    assert info["dropped"] == {"already_mapped": ["github.com"], "not_aggregate_eligible": ["www.skip.example"]}
    assert "3 hosts in, 1 to discover" in capsys.readouterr().out


def test_demand_run_refuses_a_faster_pace_and_a_csv(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["collect_demand_status.py", "--hosts-file", "x.txt", "--min-interval", "0.5"])
    with pytest.raises(SystemExit):
        collect.main()
    assert "below 1 second" in capsys.readouterr().err
    monkeypatch.setattr(sys, "argv", ["collect_demand_status.py", "--hosts-file", "x.csv"])
    with pytest.raises(SystemExit):
        collect.main()
    assert "plain host list" in capsys.readouterr().err


# --- the shared Net.fetch gained follow=False -------------------------------------------------------------------------------

def test_net_fetch_can_return_the_first_answer_without_following_a_redirect():
    net = discover.Net(min_interval=0)
    net.public_host = lambda host: True
    calls = []

    def fake_get(url, cap, want_body):
        calls.append(url)
        return (301, "https://elsewhere.test/x", b"", {"location": "https://elsewhere.test/x"}) if len(calls) == 1 else (200, None, b"ok", {})

    net._get = fake_get
    first = net.fetch("https://a.test/x", follow=False)
    assert first["status"] == 301 and first["final_url"] == "https://a.test/x" and calls == ["https://a.test/x"]
    calls.clear()
    followed = net.fetch("https://a.test/x")
    assert followed["status"] == 200 and followed["final_url"] == "https://elsewhere.test/x" and followed["exc"] is None
