"""D61 live verifier etiquette and link checks, against a fake fetcher (no network).

scripts/verify_official_paths.py --consumer-only must read robots.txt before the first request to any hostname (redirect
targets included), treat an unreachable robots.txt as disallowing everything, stop a hostname at its first refusal or
challenge, and require every url to be linked from its source_url page at its final address.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def _verifier():
    spec = importlib.util.spec_from_file_location("verify_official_paths_fake", ROOT / "scripts" / "verify_official_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V = _verifier()
ALLOW = (200, None, "", b"User-agent: *\nAllow: /\n")
HOME = "https://www.example.com/"
HELP = "https://www.example.com/help"


def html(*hrefs: str, title: str = "Example", text: str = "Welcome. " * 50) -> tuple:
    links = " ".join(f'<a href="{href}">link</a>' for href in hrefs)
    return 200, None, title, f"<html><head><title>{title}</title></head><body>{text}{links}</body></html>".encode()


class FakeFetcher:
    """Answers from a dict url -> (status, location, title, body) or an exception instance; records every request."""

    def __init__(self, answers: dict) -> None:
        self.answers = answers
        self.requested: list[str] = []
        self.requests = 0

    def public_host(self, host: str) -> bool:
        return True

    def _get(self, url: str, want_body: bool):
        self.requested.append(url)
        self.requests += 1
        answer = self.answers.get(url, (404, None, "", b""))
        if isinstance(answer, Exception):
            raise answer
        return answer


def run(answers: dict, paths: list[dict]) -> tuple[list[str], FakeFetcher, object]:
    fetcher = FakeFetcher(answers)
    checker = V.ConsumerCheck(fetcher, out=lambda line: None)
    failures = checker.run({"example.com": {"paths": paths, "verified_at": "2026-10-31"}}, ["example.com"])
    return failures, fetcher, checker


def help_path(url: str = HELP, source: str = HOME) -> dict:
    return {"kind": "customer_help", "url": url, "source_url": source}


def test_a_clean_site_passes_and_robots_comes_first():
    failures, fetcher, _ = run({"https://www.example.com/robots.txt": ALLOW, HOME: html("/help?utm_source=x#top"),
                                HELP: html()}, [help_path()])
    assert failures == []
    assert fetcher.requested == ["https://www.example.com/robots.txt", HOME, HELP]


def test_robots_5xx_or_timeout_disallows_everything_and_nothing_else_is_requested():
    for robots in ((503, None, "", b""), httpx.ConnectTimeout("slow")):
        failures, fetcher, checker = run({"https://www.example.com/robots.txt": robots, HOME: html(HELP), HELP: html()}, [help_path()])
        assert fetcher.requested == ["https://www.example.com/robots.txt"]
        assert any("robots_unreachable" in line for line in failures), failures
        assert checker.stopped == {"www.example.com": "robots_unreachable"}


def test_robots_redirects_are_followed_and_refusals_stop_the_host():
    moved = {"https://www.example.com/robots.txt": (301, "https://www.example.com/robots-real.txt", "", b""),
             "https://www.example.com/robots-real.txt": (200, None, "", b"User-agent: *\nDisallow: /help\n"),
             HOME: html(HELP), HELP: html()}
    failures, fetcher, _ = run(moved, [help_path()])
    assert HELP not in fetcher.requested and any("robots_disallowed" in line for line in failures)
    failures, fetcher, _ = run({"https://www.example.com/robots.txt": (403, None, "", b"")}, [help_path()])
    assert fetcher.requested == ["https://www.example.com/robots.txt"] and any("robots_403" in line for line in failures)


def test_the_first_403_or_challenge_stops_every_later_request_on_that_host():
    second = "https://www.example.com/contact"
    for refused in ((403, None, "", b""), html(title="Example", text="Enter the characters you see below")):
        answers = {"https://www.example.com/robots.txt": ALLOW, HOME: refused, HELP: html(), second: html()}
        failures, fetcher, checker = run(answers, [help_path(), help_path(second)])
        assert fetcher.requested == ["https://www.example.com/robots.txt", HOME], fetcher.requested
        assert checker.stopped["www.example.com"].startswith("blocked:")
        assert any("skipped:blocked:" in line for line in failures), failures


def test_a_challenge_is_recognised_from_the_body_when_the_title_says_nothing():
    assert V.bot_page("Example", b"<html><body><p>Please verify you are a human</p></body></html>")
    assert V.bot_page("", b"<html><body>Sorry, we just need to make sure you're not a robot. captcha</body></html>")
    long_help = b"<html><body>" + b"How to reset your password and pass a captcha on sign-in. " * 200 + b"</body></html>"
    assert not V.bot_page("Help center", long_help)  # a long help page that mentions a captcha in passing is not a wall
    assert not V.bot_page("Help", b"<html><script>var captcha = 1;</script><body>Help topics</body></html>")


def test_a_redirect_target_host_has_its_robots_read_before_it_is_requested():
    other = "https://help.example.com/start"
    answers = {"https://www.example.com/robots.txt": ALLOW, HOME: html(HELP),
               HELP: (302, other, "", b""),
               "https://help.example.com/robots.txt": (200, None, "", b"User-agent: *\nDisallow: /\n"), other: html()}
    failures, fetcher, _ = run(answers, [help_path()])
    assert other not in fetcher.requested
    assert fetcher.requested.index("https://help.example.com/robots.txt") > fetcher.requested.index(HELP)
    assert any("robots_disallowed" in line for line in failures), failures


def test_every_kind_must_be_linked_from_its_source_page():
    entry = "https://www.example.com/refill"
    answers = {"https://www.example.com/robots.txt": ALLOW, HOME: html("/help"), HELP: html(), entry: html()}
    failures, _, _ = run(answers, [help_path(), {"kind": "entry_link", "url": entry, "source_url": HOME}])
    assert failures == ["example.com: entry_link not linked from its source_url page: " + entry]
    # A help centre listed as its own source needs no link.
    failures, _, _ = run({"https://www.example.com/robots.txt": ALLOW, HELP: html()}, [help_path(HELP, HELP)])
    assert failures == []


def test_link_matching_folds_slashes_case_and_tracking_but_keeps_listed_parameters():
    assert V.linked("https://www.example.com/help", ["https://WWW.example.com/help/?cid=x#a"])
    assert V.linked("https://www.example.com/help/", ["http://www.example.com/help"])
    assert not V.linked("https://www.example.com/help?topic=rx", ["https://www.example.com/help"])
    assert not V.linked("https://www.example.com/help", ["https://www.example.com/helpdesk"])
    assert V.linked("https://apps.apple.com/us/app/new-name/id42", ["https://apps.apple.com/us/app/old-name/id42?pt=1"])
    assert not V.linked("https://apps.apple.com/us/app/x/id42", ["https://apps.apple.com/us/app/x/id43"])
    assert V.linked("https://play.google.com/store/apps/details?id=com.x", ["https://play.google.com/store/apps/details?hl=en&id=com.x"])


def test_a_same_site_link_that_redirects_to_the_url_counts_as_linked():
    corporate = "https://corporate.example.com/contact"
    unrelated = "https://shop.example.com/"
    answers = {"https://corporate.example.com/robots.txt": ALLOW,
               corporate: html(unrelated, "https://helpcenter.example.com/Go?lnk=footer"),
               "https://helpcenter.example.com/robots.txt": ALLOW,
               "https://helpcenter.example.com/Go?lnk=footer": (301, HELP, "", b""),
               "https://www.example.com/robots.txt": ALLOW, HELP: html()}
    failures, fetcher, _ = run(answers, [help_path(HELP, corporate)])
    assert failures == [], failures
    assert fetcher.requested.index("https://www.example.com/robots.txt") < fetcher.requested.index(HELP)
    assert unrelated not in fetcher.requested  # only a link sharing a word with the listed path is followed


def test_a_url_that_redirects_must_be_listed_at_its_final_address():
    final = "https://apps.apple.com/us/app/example-rides/id42"
    listed = "https://apps.apple.com/us/app/example/id42"
    answers = {"https://www.example.com/robots.txt": ALLOW, HOME: html(listed), "https://apps.apple.com/robots.txt": ALLOW,
               listed: (301, final, "", b""), final: html()}
    failures, _, _ = run(answers, [help_path(HOME, HOME), {"kind": "official_app", "url": listed, "source_url": HOME}])
    assert failures == [f"example.com: url redirects; list the final address: {listed} -> {final}"]
    failures, _, _ = run(answers, [help_path(HOME, HOME), {"kind": "official_app", "url": final, "source_url": HOME}])
    assert failures == []  # the page still links the old name; the app id is what counts
