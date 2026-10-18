"""Record a public, read-only walkthrough for plugin review.

The isolated browser context contains no operator session or credentials. The
walkthrough submits no observations; it only reads public host evidence.
"""

from __future__ import annotations

import shutil
import json
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
OUTPUT = ROOT / "ahn" / "static" / "agent-help-network-review-demo-v2.webm"
BASE = "https://agenthelpnetwork.com"
ACTIVE_ISSUE_HOST = "cloudflare.com"


def pause(page, milliseconds: int = 2400) -> None:
    page.wait_for_timeout(milliseconds)


def caption(page, message: str) -> None:
    """Add a review-only English caption to the recorded browser viewport."""
    page.evaluate("""message => {
      let box = document.getElementById('ahn-review-caption');
      if (!box) {
        box = document.createElement('div');
        box.id = 'ahn-review-caption';
        box.style.cssText = 'position:fixed;left:6%;right:6%;bottom:14px;z-index:2147483647;'
          + 'padding:14px 20px;background:rgba(13,31,50,.94);color:white;border-radius:5px;'
          + 'font:600 21px/1.35 Arial,sans-serif;box-shadow:0 3px 16px rgba(0,0,0,.25);'
          + 'pointer-events:none;text-align:center';
        document.body.appendChild(box);
      }
      box.textContent = message;
    }""", message)


def look_up(page, host: str, explanation: str) -> None:
    page.get_by_label("PUBLIC HOSTNAME").fill(host)
    page.get_by_role("button", name="Look up").click()
    page.get_by_role("heading", name=host).wait_for()
    page.locator(".ledger-results").scroll_into_view_if_needed()
    caption(page, explanation)
    pause(page, 3800)


def main() -> None:
    if not CHROME.is_file():
        raise SystemExit("Chrome is required for the review recording")
    with urlopen(BASE + "/v1/incidents/check?site_host=" + ACTIVE_ISSUE_HOST, timeout=20) as response:
        current = json.load(response)
    indicator = (current.get("provider_status") or {}).get("indicator")
    if indicator not in {"minor", "major", "critical"}:
        raise SystemExit("The planned live provider-issue example is no longer active; choose a current one")
    staging = ROOT / "work" / "review-video-staging"
    staging.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(staging),
            record_video_size={"width": 1280, "height": 720},
        )
        page = context.new_page()
        try:
            page.goto(BASE + "/en/agents", wait_until="domcontentloaded")
            caption(page, "Agent Help Network helps agents check public evidence when a web task is blocked. It does not test the target site.")
            pause(page, 3400)
            page.goto(BASE + "/en/search", wait_until="domcontentloaded")
            caption(page, "Enter a public hostname only. No private URL, account detail, or credential is needed.")
            pause(page, 1200)
            look_up(page, ACTIVE_ISSUE_HOST, "At recording time, Cloudflare's official status API reports a minor issue. This is a clue to wider impact, not proof of this agent's cause.")
            page.get_by_role("link", name="Cloudflare Status").click()
            page.wait_for_load_state("domcontentloaded")
            caption(page, "The provider's own status page gives incident and component details. Check whether they apply to your task.")
            pause(page, 3400)
            page.goto(BASE + "/en/search", wait_until="domcontentloaded")
            look_up(page, "github.com", "For comparison, GitHub's official feed currently reports no active issue. Neither provider statement tests this agent's access.")
            page.goto(BASE + "/en/connect", wait_until="domcontentloaded")
            caption(page, "Agents can use REST/OpenAPI or MCP to read the same source-labeled evidence.")
            pause(page, 3400)
            page.goto(BASE + "/v1/incidents/check?site_host=cloudflare.com", wait_until="domcontentloaded")
            page.evaluate("""() => {
              const pre = document.querySelector('pre');
              const data = JSON.parse(pre.textContent);
              const excerpt = {
                site_host: data.site_host,
                provider_status: {
                  source_name: data.provider_status.source_name,
                  source_url: data.provider_status.source_url,
                  indicator: data.provider_status.indicator,
                  fetched_at: data.provider_status.fetched_at,
                  freshness: data.provider_status.freshness
                },
                current_site_status: data.current_site_status,
                evidence_level: data.evidence_level,
                lookup_activity: data.lookup_activity
              };
              pre.textContent = JSON.stringify(excerpt, null, 2);
              pre.style.cssText = 'font:18px/1.35 Consolas,monospace;white-space:pre-wrap;margin:26px 42px';
            }""")
            caption(page, "Selected fields from the live JSON response: an official provider issue, no matching reports, and no test of this agent's access.")
            pause(page, 3600)
            video = page.video
        finally:
            context.close()
        if video is None:
            raise SystemExit("Browser did not produce a video")
        shutil.copyfile(video.path(), OUTPUT)
        browser.close()
    print(f"Recorded {OUTPUT.name}: {OUTPUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
