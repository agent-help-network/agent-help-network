"""Playwright: when a page will not load, ask Agent Help Network before retrying.

    pip install ahn-client playwright && playwright install chromium
    python playwright_example.py https://example.com/some/page

The lookup sends only the hostname and a coarse error class. It never helps get past a
site's controls: if the answer says stop or hand off, this script does.
"""

import sys

import ahn_client as ahn
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def decide(answer):
    """Turn the recommendation into one of: 'use_official_path', 'hand_off', 'wait', 'stop', 'retry_once'."""
    action = answer.recommendation if answer else "unknown"
    if action == "use_official_path":
        return "use_official_path"
    if action == "hand_off_to_human":
        return "hand_off"
    if action == "wait_provider_incident":
        return "wait"
    if action == "stop_declared_block":
        return "stop"
    return "retry_once"  # proceed / unknown: policy does not forbid a modest second attempt


def main(url):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        try:
            page.goto(url, timeout=20_000)
            print("loaded:", page.title())
            return 0
        except PlaywrightError as exc:  # TimeoutError is a subclass of Error
            error_class, stage = ahn.classify(exc)
            # error_class is None for a 404 or a bad request: not a block, so nothing is looked up
            answer = ahn.check(url, error_class, stage, **ahn.signals(exc, via="browser")) if error_class else None  # sends the hostname and enum codes, no path
            if answer:
                print(answer.summary())
            step = decide(answer)
            if step == "use_official_path":
                print("Use the official path instead of the page:", answer.official_url or "(see the answer)")
            elif step == "hand_off":
                print("This step needs you (sign-in or human verification). Please do it yourself, then re-run.")
            elif step == "wait":
                print("The provider reports an incident. Try again later; do not retry in a loop.")
            elif step == "stop":
                print("The site's robots.txt declares agents blocked. Stopping; no workaround attempted.")
            else:
                print("No reason to stop was found. One more attempt, no faster than before.")
                try:
                    page.goto(url, timeout=40_000)
                    print("loaded on the second attempt:", page.title())
                    return 0
                except PlaywrightError as second:
                    print("Still failing:", type(second).__name__)
            return 1
        finally:
            browser.close()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "https://example.com/"))
