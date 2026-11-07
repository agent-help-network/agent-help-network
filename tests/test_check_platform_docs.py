"""D107: scripts/check_platform_docs.py. No network: the fetcher is given a fake opener."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("check_platform_docs", ROOT / "scripts" / "check_platform_docs.py")
cpd = importlib.util.module_from_spec(spec)
sys.modules["check_platform_docs"] = cpd
spec.loader.exec_module(cpd)

PAGE = b"<html><nav>menu</nav><body><h1>Title</h1><p>It\xe2\x80\x99s  fine .</p><script>x=1</script></body></html>"


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def fetcher(responses, log):
    def opener(url):
        log.append(url)
        r = responses.get(url, responses.get("*", 404))
        status, body = r if isinstance(r, tuple) else (r, PAGE)
        return status, url, body
    clock = Clock()
    return cpd.Fetcher(opener=opener, clock=clock.now, sleep=clock.sleep), clock


def test_the_same_page_gives_the_same_hash_whatever_its_markup_or_spacing():
    a = b"<p>Hello   world .</p><nav>x</nav>"
    b = b"<div>Hello world.</div><script>var a;</script>"
    assert cpd.normalize_text(a) == cpd.normalize_text(b) == "Hello world."
    assert cpd.doc_hash(a) == cpd.doc_hash(b) != cpd.doc_hash(b"<p>Hello world!</p>")


def test_plan_records_a_first_reading_and_a_change_but_not_a_repeat():
    assert cpd.plan({}, {"u": "h1"}) == [{"url": "u", "prev_hash": None, "new_hash": "h1"}]
    assert cpd.plan({"u": "h1"}, {"u": "h1"}) == []
    assert cpd.plan({"u": "h1"}, {"u": "h2"}) == [{"url": "u", "prev_hash": "h1", "new_hash": "h2"}]


def test_only_pages_listed_as_read_are_checked_and_all_are_vendor_https_pages():
    pages = cpd.read_pages()
    assert pages and all(u.startswith("https://") for _, u, _ in pages)
    unread = {d["url"] for p in cpd.platform_policies.PLATFORMS for d in p["docs"] if d["status"] == "unread"}
    assert not unread & {u for _, u, _ in pages}


def test_a_refusal_stops_the_host_and_nothing_else_on_it_is_requested():
    log = []
    f, _ = fetcher({"https://h.test/robots.txt": 404, "https://h.test/a": 403}, log)
    allowed = frozenset({"h.test"})
    assert f.get("https://h.test/a", allowed) == ("http_403", None)
    assert f.get("https://h.test/b", allowed) == ("host_stopped", None)
    assert log == ["https://h.test/robots.txt", "https://h.test/a"]  # no retry, no second page


def test_a_robots_refusal_or_a_disallow_means_no_page_request():
    log = []
    f, _ = fetcher({"https://h.test/robots.txt": 403}, log)
    assert f.get("https://h.test/a", frozenset({"h.test"})) == ("host_stopped", None)
    assert log == ["https://h.test/robots.txt"]
    log2 = []
    f2, _ = fetcher({"https://h.test/robots.txt": (200, b"User-agent: *\nDisallow: /a\n")}, log2)
    assert f2.get("https://h.test/a", frozenset({"h.test"})) == ("robots_disallow", None)
    assert log2 == ["https://h.test/robots.txt"]


def test_a_verification_screen_is_not_hashed_as_a_page():
    log = []
    f, _ = fetcher({"https://h.test/robots.txt": 404, "*": (200, b"<html><body>Just a moment...</body></html>")}, log)
    assert f.get("https://h.test/a", frozenset({"h.test"})) == ("challenge_or_empty", None)
    assert f.get("https://h.test/b", frozenset({"h.test"})) == ("host_stopped", None)


def test_requests_to_one_host_are_a_second_apart():
    log = []
    f, clock = fetcher({"https://h.test/robots.txt": 404, "*": 200}, log)
    allowed = frozenset({"h.test"})
    assert f.get("https://h.test/a", allowed)[0] == "ok" and f.get("https://h.test/b", allowed)[0] == "ok"
    assert len(log) == 3 and clock.t >= 2.0


def test_run_reports_changes_unread_pages_and_stopped_hosts():
    pages = cpd.read_pages()
    responses = {"*": 200, "https://help.openai.com/robots.txt": 403}
    for host in {cpd.urlparse(u).hostname for _, u, _ in pages} - {"help.openai.com"}:
        responses[f"https://{host}/robots.txt"] = 404
    log = []
    report = cpd.run(fetcher(responses, log)[0], {})
    assert report["events"] and all(e["prev_hash"] is None and e["platform"] for e in report["events"])
    assert "help.openai.com" in report["stopped_hosts"]
    assert not any(u.startswith("https://help.openai.com/en/") for u in log)  # robots refusal: no page request there
    assert all(u.startswith("https://help.openai.com/") for u in report["unread"])
    again = cpd.run(fetcher(responses, [])[0], {e["url"]: e["new_hash"] for e in report["events"]})
    assert again["events"] == []


def test_a_network_error_is_recorded_as_a_reason_and_the_run_goes_on():
    import socket
    import urllib.error
    import urllib.request

    outcomes = iter([urllib.error.URLError("no route"), socket.timeout("slow"), ConnectionResetError("reset")])

    def boom(*a, **k):
        raise next(outcomes)

    real = urllib.request.urlopen
    urllib.request.urlopen = boom
    try:
        results = [cpd.Fetcher._open("https://h.test/a") for _ in range(3)]
    finally:
        urllib.request.urlopen = real
    assert all(r[0] == 0 and r[2] == b"" for r in results)
    log = []
    f, _ = fetcher({"https://h.test/robots.txt": 404, "https://h.test/a": 0, "https://h.test/b": 200}, log)
    allowed = frozenset({"h.test"})
    assert f.get("https://h.test/a", allowed) == ("network_error", None)
    assert f.get("https://h.test/b", allowed)[0] == "ok"  # one page failing does not stop the host


def test_robots_that_cannot_be_read_stops_the_host_as_a_complete_disallow():
    for status in (500, 503, 0):
        log = []
        f, _ = fetcher({"https://h.test/robots.txt": status}, log)
        assert f.get("https://h.test/a", frozenset({"h.test"})) == ("host_stopped", None)
        assert f.get("https://h.test/b", frozenset({"h.test"})) == ("host_stopped", None)
        assert log == ["https://h.test/robots.txt"] and "h.test" in f.stopped
    log = []
    f, _ = fetcher({"https://h.test/robots.txt": 410, "*": 200}, log)
    assert f.get("https://h.test/a", frozenset({"h.test"}))[0] == "ok"  # 4xx other than a refusal: no robots.txt


def test_robots_wildcards_and_end_anchors_are_read():
    ua = cpd.USER_AGENT
    r = cpd.RobotsRules("User-agent: *\nDisallow: /*/private/\nDisallow: /*.pdf$\nDisallow: /a*b\nAllow: /a*b/ok\n")
    assert not r.can_fetch(ua, "https://h.test/x/private/y")
    assert not r.can_fetch(ua, "https://h.test/doc.pdf")
    assert r.can_fetch(ua, "https://h.test/doc.pdf.html")  # `$` anchors the end
    assert r.can_fetch(ua, "https://h.test/public/page")
    assert not r.can_fetch(ua, "https://h.test/aXXb/y")
    assert r.can_fetch(ua, "https://h.test/aXXb/ok")  # the longer Allow wins
    tie = cpd.RobotsRules("User-agent: *\nDisallow: /p\nAllow: /p\n")
    assert tie.can_fetch(ua, "https://h.test/p")  # Allow wins a tie
    query = cpd.RobotsRules("User-agent: *\nDisallow: /*?*authuser=\n")
    assert query.can_fetch(ua, "https://h.test/a")
    assert not query.can_fetch(ua, "https://h.test/a?x=1&authuser=2")
    assert cpd.RobotsRules("User-agent: *\nDisallow:\n").can_fetch(ua, "https://h.test/anything")
    assert not cpd.RobotsRules("User-agent: *\r\nDisallow: /\r\n").can_fetch(ua, "https://h.test/anything")


def test_robots_group_selection_prefers_our_token_over_the_star_group():
    ua = cpd.USER_AGENT
    text = "User-agent: *\nDisallow: /\n\nUser-agent: OtherBot\nUser-agent: AgentHelpNetwork-curation\nAllow: /\n"
    assert cpd.RobotsRules(text).can_fetch(ua, "https://h.test/page")
    assert not cpd.RobotsRules("User-agent: OtherBot\nAllow: /\n\nUser-agent: *\nDisallow: /\n").can_fetch(ua, "https://h.test/page")
    assert cpd.RobotsRules("User-agent: OtherBot\nDisallow: /\n").can_fetch(ua, "https://h.test/page")  # no group for us: allowed
