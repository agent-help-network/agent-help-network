"""Run local axe-core WCAG checks in Chrome; requires a locally installed axe-core."""

from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


BASE = os.environ.get("AHN_API_BASE", "http://127.0.0.1:8875").rstrip("/")
AXE = Path(os.environ.get("AHN_AXE_PATH", str(Path(os.environ["LOCALAPPDATA"]) / "AgentHelpNetwork" / "qa-tools" / "node_modules" / "axe-core" / "axe.min.js")))
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ROUTES = ("/en/", "/ko/", "/en/human", "/ko/human", "/en/search?site_host=example.org", "/ko/search?site_host=example.org", "/en/agents", "/ko/agents", "/en/connect", "/ko/connect", "/en/policy", "/ko/policy")


def main() -> None:
    if not AXE.is_file() or not CHROME.is_file():
        raise SystemExit("Chrome and a local axe-core installation are required; see README")
    failures = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        for width in (390, 1280):
            page = browser.new_page(viewport={"width": width, "height": 844})
            for route in ROUTES:
                response = page.goto(BASE + route, wait_until="networkidle")
                assert response is not None and response.status == 200
                page.add_script_tag(path=str(AXE))
                result = page.evaluate("""async () => axe.run(document, {
                    runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa','wcag22aa']}
                })""")
                for violation in result["violations"]:
                    failures.append((width, route, violation["id"], [node["target"] for node in violation["nodes"]]))
            page.close()
        browser.close()
    if failures:
        for failure in failures:
            print("FAIL", failure)
        raise SystemExit(1)
    print("PASS: axe-core WCAG 2/2.1/2.2 A/AA automated checks on 12 routes at 390 and 1280 px")


if __name__ == "__main__":
    main()
