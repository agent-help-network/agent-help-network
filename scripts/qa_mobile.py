"""Check responsive width and keyboard entry points against the local site."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE = os.environ.get("AHN_API_BASE", "http://127.0.0.1:8875").rstrip("/")
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROUTES = ("/en/", "/ko/", "/en/human", "/ko/human", "/en/search", "/ko/search", "/en/agents", "/ko/agents", "/en/connect", "/ko/connect", "/en/policy", "/ko/policy")


def submit_search(page, lang: str, failures: list, width: int) -> None:
    """Submit the home search form in the page's current language.

    The form and its submit button are found by structure, not copy: the button label differs per
    language ("View profile" / "프로필 보기") and changes whenever the site text does.
    """
    page.goto(f"{BASE}/{lang}/", wait_until="networkidle")
    search = page.locator(".ledger-search")
    search.locator("input[name=site_host]").fill("fresh.agenthelpnetwork.test")
    with page.expect_navigation():
        search.locator("button[type=submit]").click()
    if not page.url.startswith(f"{BASE}/{lang}/search"):
        failures.append((width, f"search submission ({lang})", page.url))


def main() -> None:
    if not CHROME.is_file():
        raise SystemExit("Chrome executable unavailable; set up a browser for this manual QA helper")
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        for width in (390, 768, 1280):
            page = browser.new_page(viewport={"width": width, "height": 844}, device_scale_factor=1)
            for route in ROUTES:
                response = page.goto(BASE + route, wait_until="networkidle")
                assert response is not None
                sizes = page.evaluate("({ viewport: window.innerWidth, document: document.documentElement.scrollWidth, body: document.body.scrollWidth })")
                if response.status != 200 or sizes["document"] > sizes["viewport"] + 1:
                    failures.append((width, route, response.status, sizes))
                if width == 390:
                    expected = page.evaluate("[...document.querySelectorAll('a[href],button,input,select')].filter(e => !e.disabled && getComputedStyle(e).visibility !== 'hidden').length")
                    focused = set()
                    for _ in range(expected):
                        page.keyboard.press("Tab")
                        state = page.evaluate("""() => {
                            const el = document.activeElement;
                            const box = el.getBoundingClientRect();
                            return {tag: el.tagName, text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30), outline: getComputedStyle(el).outlineStyle, visible: box.width > 0 && box.height > 0 && box.left < innerWidth && box.right > 0 && box.top < innerHeight && box.bottom > 0};
                        }""")
                        if state["outline"] == "none" or not state["visible"]:
                            failures.append((width, route, "keyboard focus", state))
                        focused.add((state["tag"], state["text"]))
                    if len(focused) < min(expected, 2):
                        failures.append((width, route, "keyboard traversal", expected, len(focused)))
            page.goto(BASE + "/en/", wait_until="networkidle")
            page.keyboard.press("Tab")
            first = page.evaluate("document.activeElement?.className")
            if first != "skip":
                failures.append((width, "keyboard first focus", first))
            page.keyboard.press("Enter")
            if page.evaluate("location.hash") != "#main":
                failures.append((width, "skip link target"))
            submit_search(page, "en", failures, width)
            page.goto(BASE + "/en/", wait_until="networkidle")
            language = page.get_by_role("link", name="한국어로 보기")
            language.click()
            if page.url.rstrip("/") != BASE + "/ko":
                failures.append((width, "language switch", page.url))
            submit_search(page, "ko", failures, width)
            page.close()
        browser.close()
    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS: 12 routes at 390/768/1280 px; no horizontal overflow; skip link, language switch, search flow in en and ko")


if __name__ == "__main__":
    main()
