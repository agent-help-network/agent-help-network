"""Record a public, read-only walkthrough for plugin review.

The isolated browser context contains no operator session or credentials. The
walkthrough submits no observations; it only reads public pages and the public
lookup API.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
OUTPUT = ROOT / "ahn" / "static" / "agent-help-network-review-demo-v3.webm"
BASE = "https://agenthelpnetwork.com"
PROFILE_HOST = "github.com"
BLOCKED_HOST = "nytimes.com"
# Playwright records VP8 in realtime mode, which is several times larger than
# needed for mostly static pages. A two-pass re-encode keeps the upload small.
TARGET_BITRATE = "360k"
MAX_BYTES = 5 * 1024 * 1024


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


def scroll_to(page, selector: str, offset: int = 24) -> None:
    """Smoothly bring an element to the top of the viewport."""
    page.locator(selector).first.wait_for()
    page.evaluate("""([selector, offset]) => {
      const el = document.querySelector(selector);
      const top = el.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({top, behavior: 'smooth'});
    }""", [selector, offset])
    pause(page, 900)


def look_up(page, host: str) -> None:
    page.goto(BASE + "/en/search", wait_until="domcontentloaded")
    page.get_by_label("PUBLIC HOSTNAME").fill(host)
    page.get_by_role("button", name="Look up").click()
    page.get_by_role("heading", name=host).wait_for()


def show_json_excerpt(page, host: str, message: str) -> None:
    """Open the live JSON answer and keep only the fields the walkthrough explains."""
    page.goto(BASE + "/v1/incidents/check?site_host=" + host, wait_until="domcontentloaded")
    page.evaluate("""() => {
      const pre = document.querySelector('pre');
      const data = JSON.parse(pre.textContent);
      const robots = (data.access_profile || {}).robots || {};
      const blocked = robots.agents_blocked || [];
      const provider = data.provider_status || {};
      const excerpt = {
        site_host: data.site_host,
        recommendation: data.recommendation,
        access_profile: {
          robots_status: data.access_profile.robots_status,
          agents_blocked: blocked.length > 6 ? blocked.slice(0, 6).concat(['... ' + (blocked.length - 6) + ' more']) : blocked,
          wildcard_root_allowed: robots.wildcard_root_allowed,
          llms_txt_status: data.access_profile.llms_txt_status
        },
        official_paths: data.official_paths && {
          name: data.official_paths.name,
          api_docs: data.official_paths.api_docs
        },
        provider_status: provider.source_name ? {
          source_name: provider.source_name,
          indicator: provider.indicator,
          freshness: provider.freshness
        } : null,
        evidence_level: data.evidence_level,
        current_site_status: data.current_site_status
      };
      pre.textContent = JSON.stringify(excerpt, null, 2);
      pre.style.cssText = 'font:17px/1.35 Consolas,monospace;white-space:pre-wrap;margin:22px 42px';
    }""")
    caption(page, message)


def check_examples() -> None:
    """Refuse to record if the live answers no longer match the captions."""
    for host, reason in ((PROFILE_HOST, None), (BLOCKED_HOST, "robots_blocks_agents")):
        with urlopen(BASE + "/v1/incidents/check?site_host=" + host, timeout=30) as response:
            data = json.load(response)
        recommendation = data.get("recommendation") or {}
        if recommendation.get("action") != "use_official_path" or not data.get("official_paths"):
            raise SystemExit(f"{host} no longer recommends an official path; update the walkthrough")
        if reason and reason not in recommendation.get("reasons", []):
            raise SystemExit(f"{host} no longer declares an agent block; choose another example")
        if data.get("evidence_level") != "no_reports":
            raise SystemExit(f"{host} now has published reports; update the no_reports captions")


def playwright_ffmpeg() -> Path:
    """Reuse the ffmpeg build Playwright installs for video recording."""
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    builds = sorted(root.glob("ffmpeg-*/ffmpeg-win64.exe"))
    if not builds:
        raise SystemExit("Playwright's ffmpeg is required; run: playwright install ffmpeg")
    return builds[-1]


def compress(source: Path, target: Path) -> None:
    ffmpeg = str(playwright_ffmpeg())
    log = source.with_suffix("")
    common = ["-c:v", "libvpx", "-b:v", TARGET_BITRATE, "-maxrate", "800k", "-bufsize", "1600k",
              "-qmin", "4", "-qmax", "56", "-deadline", "good", "-cpu-used", "2", "-g", "250",
              "-passlogfile", str(log)]
    first = source.with_name("pass1.webm")
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *common,
                    "-pass", "1", "-f", "webm", str(first)], check=True)
    subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source), *common,
                    "-pass", "2", str(target)], check=True)
    first.unlink(missing_ok=True)
    if target.stat().st_size > MAX_BYTES:
        raise SystemExit(f"{target.name} is larger than {MAX_BYTES} bytes; lower TARGET_BITRATE")


def main() -> None:
    if not CHROME.is_file():
        raise SystemExit("Chrome is required for the review recording")
    check_examples()
    staging = ROOT / "work" / "review-video-staging"
    staging.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            record_video_dir=str(staging),
            record_video_size={"width": 1280, "height": 720},
            locale="en-US",
        )
        page = context.new_page()
        try:
            page.goto(BASE + "/en/", wait_until="domcontentloaded")
            caption(page, "Agent Help Network answers one question for a public hostname: what does this host allow agents to do?")
            pause(page, 4200)
            scroll_to(page, "#machine-first")
            caption(page, "One read-only call, no account: a REST GET or the MCP tool check_incident. Send only a public hostname.")
            pause(page, 4600)
            scroll_to(page, ".ledger-actions", 60)
            caption(page, "Every answer carries recommendation.action, derived from robots.txt, official paths, error class, or provider status.")
            pause(page, 4600)

            look_up(page, PROFILE_HOST)
            scroll_to(page, ".ledger-results")
            caption(page, "The same lookup on the search page. The site never tests your own access; each layer is labeled by source.")
            pause(page, 4400)
            scroll_to(page, ".ledger-results .ledger-source-summary", 60)
            caption(page, "01 Declared agent policy, read from GitHub's own robots.txt. Only Bytespider is blocked; the AI agents listed are allowed.")
            pause(page, 4800)
            scroll_to(page, ".ledger-row:nth-of-type(2)")
            caption(page, "02 Official paths, curated from GitHub's public developer pages, then the next checks for the task.")
            pause(page, 4400)
            scroll_to(page, ".ledger-table ~ .ledger-table .ledger-row")
            caption(page, "03 Provider-declared status: GitHub's own status feed, shown with its fetch time.")
            pause(page, 4400)
            show_json_excerpt(page, PROFILE_HOST, "The JSON for github.com: recommendation use_official_path, because a documented API exists.")
            pause(page, 5200)

            look_up(page, BLOCKED_HOST)
            scroll_to(page, ".ledger-results")
            caption(page, "nytimes.com declares a block: its robots.txt disallows the site root for ChatGPT-User, Claude-User and others.")
            pause(page, 5200)
            scroll_to(page, ".ledger-row:nth-of-type(2)")
            caption(page, "So the agent should stop retrying pages and use the NYTimes Developer Network instead.")
            pause(page, 4400)
            show_json_excerpt(page, BLOCKED_HOST, "recommendation: use_official_path with high confidence, for reasons robots_blocks_agents and official_path_available.")
            pause(page, 5600)

            page.go_back(wait_until="domcontentloaded")
            page.get_by_role("heading", name=BLOCKED_HOST).wait_for()
            scroll_to(page, ".ledger-empty", 140)
            caption(page, "no_reports is the normal case. It means no published observation matched, so the current status stays unknown.")
            pause(page, 5400)

            page.goto(BASE + "/en/policy", wait_until="domcontentloaded")
            caption(page, "The privacy policy covers the publisher, data collected, purposes, recipients, retention, and your controls.")
            pause(page, 5600)
            scroll_to(page, ".policy-grid", 12)
            caption(page, "No account or free text: only hostnames and fixed codes. Reports are optional, reviewed, and can be withdrawn.")
            pause(page, 5200)

            page.goto(BASE + "/en/connect#install", wait_until="domcontentloaded")
            scroll_to(page, "#install", 12)
            caption(page, "Install: four ways to add the same lookup to Claude Code, Cursor, the OpenAI Agents SDK and other MCP clients.")
            pause(page, 5200)
            video = page.video
        finally:
            context.close()
        if video is None:
            raise SystemExit("Browser did not produce a video")
        recording = Path(video.path())
        browser.close()
    compress(recording, OUTPUT)
    print(f"Recorded {OUTPUT.name}: {OUTPUT.stat().st_size} bytes")


if __name__ == "__main__":
    main()
