"""Record the public Claude Code plugin demo (v4) with burned-in English captions and a WebVTT file.

The video shows what the Agent Help Network hook (plugins/claude-code/ahn/hooks) does after a failed web
step. Each scene feeds an example WebFetch result to the real hook, which asks the live service about the
hostname only, and then replays the hook's real output in a plain terminal page. The tool results are
examples, not live fetches of those sites: the video says so on screen.

  1. HTTP 403: one line of guidance (declared policy, official path)
  2. A host the service has never seen: "pending" at once, "fresh" a few seconds later
  3. HTTP 404: a wrong address, so no lookup and no text
  4. HTTP 429 with Retry-After: the wait the site asks for

Nothing is submitted to the service as an observation. The recording needs one live lookup per scene
(two for scene 2), and it contains no local path, user name, account or token: the terminal page is
rendered from a template, the commands shown are the exact payloads that were run, and the hook's cache
lives in a temporary directory that is removed afterwards.

Usage:
  python scripts/record_plugin_demo.py                  look up live, then record
  python scripts/record_plugin_demo.py --collect-only   look up live and save the answers, no recording
  python scripts/record_plugin_demo.py --reuse FILE     record from saved answers (no lookups)
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "plugins" / "claude-code" / "ahn" / "hooks" / "run-hook.sh"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
# Outside ahn/static on purpose: the web service does not serve docs/, so the file costs no bandwidth and
# is delivered only through a commit-pinned jsDelivr URL.
OUTPUT = ROOT / "docs" / "media" / "agent-help-network-demo-v4.webm"
CAPTIONS = OUTPUT.with_suffix(".vtt")
BASE = "https://agenthelpnetwork.com"

BLOCKED_HOST = "nytimes.com"       # scene 1: declares a block for several agents and has an official path
UNSEEN_HOST = "lua.org"       # scene 2: must be a host the service has never looked up (checked)
NOT_FOUND_HOST = "example.org"     # scene 3: never looked up
RATE_HOST = "github.com"           # scene 4
RETRY_AFTER = 30

# The page is mostly static text, so a low-bitrate two-pass VP8 encode stays small and sharp.
TARGET_BITRATE = "300k"
MAX_BYTES = 4 * 1000 * 1000        # v3 is 4.1 MB; v4 must not be larger
VIEWPORT = {"width": 1280, "height": 720}
FPS = 15

# A caption stays on screen for at least this long: a fixed part plus time to read it.
MIN_SECONDS = 4.0
SECONDS_PER_CHARACTER = 1 / 14
FIRST_CAPTION_AT = 1.0             # seconds of title card before the first caption


def webfetch(url: str, code: int, text: str, headers: dict | None = None) -> bytes:
    response = {"code": code, "codeText": text}
    if headers:
        response["headers"] = headers
    return json.dumps({
        "hook_event_name": "PostToolUse", "tool_name": "WebFetch",
        "tool_input": {"url": url}, "tool_response": response,
    }, separators=(",", ":")).encode()


def run_hook(payload: bytes, data_dir: str, debug: bool = False) -> tuple[str, str]:
    """Run the plugin's own hook script; return (additionalContext or "", stderr debug note)."""
    sh = shutil.which("sh")
    if not sh:
        raise SystemExit("sh (Git Bash) is required to run the hook script")
    env = {**os.environ, "CLAUDE_PLUGIN_DATA": data_dir}
    for name in ("AHN_HOOK_DISABLE", "AHN_HOOK_SKIP_HOSTS", "AHN_HOOK_FAKE_RESPONSE", "AHN_HOOK_DEBUG"):
        env.pop(name, None)
    if debug:
        env["AHN_HOOK_DEBUG"] = "1"
    done = subprocess.run([sh, HOOK.as_posix()], input=payload, capture_output=True, env=env, timeout=30, check=True)
    out = done.stdout.decode("utf-8")
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"] if out.strip() else ""
    return context, done.stderr.decode("utf-8").strip()


def lookup(url: str) -> dict:
    """One more read of the URL the hook printed as "Full JSON", as the hook's own answer tells the agent to do."""
    if not url.startswith(BASE + "/v1/incidents/check?"):
        raise SystemExit("unexpected lookup URL: " + url)
    request = Request(url, headers={"User-Agent": "ahn-demo-recording/1"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def scene_blocked(data_dir: str) -> dict:
    payload = webfetch(f"https://{BLOCKED_HOST}/", 403, "Forbidden")
    context, _ = run_hook(payload, data_dir)
    if "recommendation=use_official_path" not in context or BLOCKED_HOST not in context:
        raise SystemExit(f"{BLOCKED_HOST} no longer recommends an official path; update the walkthrough:\n{context}")
    return {"payload": payload.decode(), "context": context}


def scene_unseen(data_dir: str) -> dict:
    payload = webfetch(f"https://{UNSEEN_HOST}/", 403, "Forbidden")
    first, _ = run_hook(payload, data_dir)
    if "being read now; look up once more in" not in first:
        raise SystemExit(f"{UNSEEN_HOST} was not unseen (no pending answer); choose another host with --unseen-host:\n{first}")
    wait = int(first.split("look up once more in ")[1].split(" s")[0])
    url = first.split("Full JSON: ")[1].split(". Declared policy is not")[0]
    time.sleep(wait + 1)
    second = lookup(url)
    profile = second.get("access_profile") or {}
    if profile.get("freshness") != "fresh":
        raise SystemExit(f"{UNSEEN_HOST} is still {profile.get('freshness')} after {wait + 1} s; record again later")
    robots = profile.get("robots") or {}
    excerpt = {
        "site_host": second.get("site_host"),
        "access_profile": {
            "freshness": profile.get("freshness"),
            "robots_status": profile.get("robots_status"),
            "llms_txt_status": profile.get("llms_txt_status"),
            "agents_blocked": (robots.get("agents_blocked") or [])[:4],
        },
        "recommendation": second.get("recommendation"),
    }
    return {"payload": payload.decode(), "context": first, "wait": wait, "url": url, "excerpt": excerpt}


def scene_missing(data_dir: str) -> dict:
    payload = webfetch(f"https://{NOT_FOUND_HOST}/no-such-page", 404, "Not Found")
    context, note = run_hook(payload, data_dir, debug=True)
    if context:
        raise SystemExit("the hook answered a 404; it should add nothing")
    return {"payload": payload.decode(), "note": note}


def scene_limited(data_dir: str) -> dict:
    payload = webfetch(f"https://{RATE_HOST}/", 429, "Too Many Requests", {"Retry-After": str(RETRY_AFTER)})
    context, _ = run_hook(payload, data_dir)
    if f"the site asks to retry after {RETRY_AFTER} s" not in context:
        raise SystemExit(f"the 429 answer carries no Retry-After note:\n{context}")
    return {"payload": payload.decode(), "context": context}


SCENES = {"blocked": scene_blocked, "unseen": scene_unseen, "missing": scene_missing, "limited": scene_limited}


def collect(names, data: dict) -> dict:
    """The live answers the scenes replay: one lookup per scene, two for the unseen host."""
    with tempfile.TemporaryDirectory(prefix="ahn-demo-") as data_dir:
        for name in names:
            data[name] = SCENES[name](data_dir)
    return data


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><title>AHN plugin demo</title>
<style>
  [hidden]{display:none!important}
  html,body{margin:0;height:100%;background:#0b1622;color:#e8eef5;font-family:Consolas,'Courier New',monospace}
  #bar{height:54px;display:flex;align-items:center;gap:16px;padding:0 28px;background:#14283c;border-bottom:1px solid #2a4560;
       font:700 22px Arial,sans-serif}
  #bar .tag{background:#2b6cb0;border-radius:4px;padding:3px 10px;font-size:18px}
  #term{position:absolute;top:54px;left:0;right:0;height:546px;box-sizing:border-box;padding:18px 32px;overflow:hidden;
        display:flex;flex-direction:column;justify-content:flex-start;font-size:20px;line-height:1.36}
  .line{white-space:pre-wrap;overflow-wrap:anywhere;margin:0 0 9px}
  .note{color:#8fb2d4}
  .cmd{color:#f4f7fb}.cmd:before{content:'$ ';color:#6fd08c}
  .out{color:#e8eef5;border-left:3px solid #2b6cb0;padding-left:14px}
  .out b{color:#ffd166;font-weight:700}
  .none{color:#9fb3c8;font-style:italic}
  #title{position:absolute;top:54px;left:0;right:0;height:546px;display:flex;flex-direction:column;justify-content:center;
         align-items:center;text-align:center;font-family:Arial,sans-serif}
  #title h1{font-size:50px;margin:0 0 14px}#title p{font-size:26px;margin:4px 0;color:#b9cde0}
  #caption{position:fixed;left:6%;right:6%;bottom:14px;z-index:9;min-height:62px;padding:14px 22px;box-sizing:border-box;
           background:rgba(255,255,255,.97);color:#0d1f32;border-radius:5px;box-shadow:0 3px 16px rgba(0,0,0,.4);
           font:700 25px/1.35 Arial,sans-serif;text-align:center;display:flex;align-items:center;justify-content:center}
</style>
<div id="bar"><span class="tag" id="tag">Plugin hook demo</span><span id="head">Agent Help Network for Claude Code</span></div>
<div id="title"><h1>Agent Help Network</h1><p>Claude Code plugin hook</p><p>What it adds after a failed web fetch</p></div>
<div id="term" hidden></div>
<div id="caption" hidden></div>
"""

SCRIPT = """
window.ahn = {
  caption(text) { const c = document.getElementById('caption'); c.textContent = text; c.hidden = false; },
  scene(tag, head) {
    document.getElementById('title').hidden = true;
    const t = document.getElementById('term'); t.hidden = false; t.textContent = '';
    document.getElementById('tag').textContent = tag; document.getElementById('head').textContent = head;
  },
  async add(kind, text, marks) {
    const el = document.createElement('div'); el.className = 'line ' + kind;
    document.getElementById('term').appendChild(el);
    if (kind === 'cmd') {
      for (let i = 1; i <= text.length; i += 6) { el.textContent = text.slice(0, i); await new Promise(r => setTimeout(r, 12)); }
      el.textContent = text;
      return;
    }
    let htmlText = text.replace(/&/g, '&amp;').replace(/</g, '&lt;');
    for (const m of (marks || [])) htmlText = htmlText.split(m).join('<b>' + m + '</b>');
    el.innerHTML = htmlText;
  }
};
"""


def caption_seconds(text: str) -> float:
    return max(MIN_SECONDS, 1.2 + len(text) * SECONDS_PER_CHARACTER)


class Timeline:
    """Burns captions into the page and records when each was on screen, for the WebVTT file."""

    def __init__(self, page):
        self.page = page
        self.cues: list[list] = []     # [start, end or None, text], as time.time() values

    @staticmethod
    def now() -> float:
        return time.time()

    def say(self, text: str) -> None:
        """Replace the caption. The previous one has already been on screen long enough (see settle)."""
        self.page.evaluate("text => window.ahn.caption(text)", text)
        at = self.now()
        if self.cues and self.cues[-1][1] is None:
            self.cues[-1][1] = at
        self.cues.append([at, None, text])

    def settle(self) -> None:
        """Wait until the current caption has been readable for long enough."""
        if self.cues and self.cues[-1][1] is None:
            left = self.cues[-1][0] + caption_seconds(self.cues[-1][2]) - self.now()
            if left > 0:
                self.page.wait_for_timeout(int(left * 1000))

    def finish(self) -> None:
        self.settle()
        self.cues[-1][1] = self.now() + 0.4


def vtt_time(seconds: float) -> str:
    whole, millis = divmod(round(seconds * 1000), 1000)
    return f"{whole // 3600:02d}:{whole % 3600 // 60:02d}:{whole % 60:02d}.{millis:03d}"


def write_vtt(cues: list[list], target: Path) -> None:
    lines = ["WEBVTT", ""]
    for number, (start, end, text) in enumerate(cues, 1):
        lines += [str(number), f"{vtt_time(start)} --> {vtt_time(end)}", text, ""]
    target.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def cmd_line(payload: str, debug: bool = False) -> str:
    return f"echo '{payload}' | " + ("AHN_HOOK_DEBUG=1 " if debug else "") + "sh hooks/run-hook.sh"


def play(page, data: dict) -> list[list]:
    t = Timeline(page)
    page.wait_for_timeout(int(FIRST_CAPTION_AT * 1000))
    t.say("Agent Help Network (AHN) plugin for Claude Code: a hook runs after a failed WebFetch. "
          "The next scenes feed it example tool results.")
    t.settle()

    def show(kind, text, marks=None):
        page.evaluate("([kind, text, marks]) => window.ahn.add(kind, text, marks)", [kind, text, marks or []])

    # Scene 1: 403
    page.evaluate("([a, b]) => window.ahn.scene(a, b)", ["Scene 1 of 4", "Example tool result: HTTP 403 Forbidden"])
    t.say("WebFetch gets 403 Forbidden → the AHN plugin looks up only the hostname and adds one line of guidance.")
    show("note", "# Example input: a WebFetch result for a host that answered 403")
    show("cmd", cmd_line(data["blocked"]["payload"]))
    page.wait_for_timeout(1200)
    show("note", "# Hook output, added to Claude's context (additionalContext):")
    show("out", data["blocked"]["context"], ["recommendation=use_official_path", "blocked agents:", "Official path:"])
    t.settle()
    t.say("It reports the host's declared agent policy and its official path. Declared policy is not an access guarantee.")
    t.settle()

    # Scene 2: pending, then fresh
    page.evaluate("([a, b]) => window.ahn.scene(a, b)", ["Scene 2 of 4", "Example tool result: HTTP 403 on a host AHN has not looked up before"])
    t.say("A host AHN has never seen answers 'pending' at once; its robots.txt is read in the background.")
    show("note", "# Example input: a WebFetch result for a host that is new to the service")
    show("cmd", cmd_line(data["unseen"]["payload"]))
    page.wait_for_timeout(1200)
    show("note", "# Hook output, added to Claude's context:")
    show("out", data["unseen"]["context"], ["Declared policy: pending", "look up once more in", "do not poll"])
    t.settle()
    wait = data["unseen"]["wait"]
    t.say("A few seconds later, one more lookup (not polling) returns 'fresh': the host's robots.txt and llms.txt have been read.")
    show("note", f"# {wait} s later, as the answer says: one more lookup of the same hostname (check_incident / REST)")
    page.wait_for_timeout((wait + 1) * 1000)
    show("cmd", f'curl -s "{data["unseen"]["url"]}"')
    page.wait_for_timeout(700)
    show("out", json.dumps(data["unseen"]["excerpt"]), ['"freshness": "fresh"'])
    t.settle()

    # Scene 3: 404
    page.evaluate("([a, b]) => window.ahn.scene(a, b)", ["Scene 3 of 4", "Example tool result: HTTP 404 Not Found"])
    t.say("A 404 is a wrong address, not a block: no lookup, no extra text.")
    show("note", "# Example input: a WebFetch result for a page that does not exist")
    show("cmd", cmd_line(data["missing"]["payload"], debug=True))
    page.wait_for_timeout(1200)
    show("out", data["missing"]["note"])
    show("none", "(no output: nothing is added to Claude's context, and no request goes to the service)")
    t.settle()

    # Scene 4: 429 with Retry-After
    page.evaluate("([a, b]) => window.ahn.scene(a, b)", ["Scene 4 of 4", f"Example tool result: HTTP 429 Too Many Requests, Retry-After: {RETRY_AFTER}"])
    t.say(f"HTTP 429 with Retry-After: {RETRY_AFTER} → the hook adds the wait the site asks for to its short answer.")
    show("note", "# Example input: a WebFetch result for a rate-limited host")
    show("cmd", cmd_line(data["limited"]["payload"]))
    page.wait_for_timeout(1200)
    show("note", "# Hook output, added to Claude's context:")
    show("out", data["limited"]["context"], [f"the site asks to retry after {RETRY_AFTER} s", "recommendation="])
    t.settle()
    t.say("The hook only reports. It never retries, waits or bypasses a limit for the agent.")
    t.settle()

    t.say("The hook sends only a hostname and coarse error codes, and only reads. Install: agenthelpnetwork.com/en/connect")
    t.finish()
    return t.cues


def playwright_ffmpeg() -> Path:
    """Reuse the ffmpeg build Playwright installs for video recording."""
    root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    builds = sorted(root.glob("ffmpeg-*/ffmpeg-win64.exe"))
    if not builds:
        raise SystemExit("Playwright's ffmpeg is required; run: playwright install ffmpeg")
    return builds[-1]


def encode(frames: list[tuple[float, Path]], start: float, end: float, staging: Path, target: Path) -> None:
    """Two-pass VP8 encode of the screencast at a constant FPS.

    Playwright's own ffmpeg is a stripped build: it reads a stream of JPEGs (image2pipe) and writes VP8,
    but has no fps or concat filter. So the constant-rate stream is built here: for each output frame
    the screencast frame that was on screen at that moment.
    """
    ffmpeg = str(playwright_ffmpeg())
    stream = staging / "frames.mjpeg"
    count, index = 0, 0
    with stream.open("wb") as out:
        while start + count / FPS < end:
            moment = start + count / FPS
            while index + 1 < len(frames) and frames[index + 1][0] <= moment:
                index += 1
            out.write(frames[index][1].read_bytes())
            count += 1
    source = ["-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", str(stream)]
    common = ["-c:v", "libvpx", "-b:v", TARGET_BITRATE, "-maxrate", "700k", "-bufsize", "1400k",
              "-qmin", "4", "-qmax", "52", "-deadline", "good", "-cpu-used", "2", "-g", "250",
              "-pix_fmt", "yuv420p", "-passlogfile", str(staging / "pass")]
    first = staging / "pass1.webm"
    run = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run([*run, *source, *common, "-pass", "1", "-f", "webm", str(first)], check=True)
    subprocess.run([*run, *source, *common, "-pass", "2", str(target)], check=True)
    if target.stat().st_size > MAX_BYTES:
        raise SystemExit(f"{target.name} is larger than {MAX_BYTES} bytes; lower TARGET_BITRATE")


def record(data: dict) -> None:
    if not CHROME.is_file():
        raise SystemExit("Chrome is required for the recording")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    staging = ROOT / "work" / "demo-video-staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    frames: list[tuple[float, Path]] = []   # (time.time() when shown, JPEG file)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=str(CHROME), headless=True)
        context = browser.new_context(viewport=VIEWPORT, locale="en-US")
        page = context.new_page()
        page.set_content(PAGE)
        page.add_script_tag(content=SCRIPT)
        # The page is captured with Chrome's screencast, which stamps every frame. The captions' times
        # come from the same clock, so the WebVTT file lines up with the picture.
        cdp = context.new_cdp_session(page)

        def on_frame(params) -> None:
            path = staging / f"f{len(frames):05d}.jpg"
            path.write_bytes(base64.b64decode(params["data"]))
            frames.append((params["metadata"]["timestamp"], path))
            cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})

        cdp.on("Page.screencastFrame", on_frame)
        cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 92, "maxWidth": VIEWPORT["width"],
                                          "maxHeight": VIEWPORT["height"], "everyNthFrame": 1})
        page.wait_for_timeout(500)
        try:
            cues = play(page, data)
            page.wait_for_timeout(600)
        finally:
            cdp.send("Page.stopScreencast")
            context.close()
            browser.close()
    if not frames or abs(frames[0][0] - time.time()) > 600:
        raise SystemExit("the screencast produced no frames or uses another clock")
    start, end = frames[0][0], cues[-1][1] + 0.3
    encode(frames, start, end, staging, OUTPUT)
    write_vtt([[a - start, b - start, text] for a, b, text in cues], CAPTIONS)
    shutil.rmtree(staging, ignore_errors=True)
    print(f"Recorded {OUTPUT.name}: {OUTPUT.stat().st_size} bytes, {end - start:.1f} s; {CAPTIONS.name}: {len(cues)} cues")


def main() -> None:
    global UNSEEN_HOST
    parser = argparse.ArgumentParser()
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument("--reuse", type=Path, help="saved answers from --collect-only; makes no lookups unless --redo")
    parser.add_argument("--redo", default="", help="with --reuse: comma-separated scenes to look up again (" + ", ".join(SCENES) + ")")
    parser.add_argument("--unseen-host", default=UNSEEN_HOST)
    args = parser.parse_args()
    UNSEEN_HOST = args.unseen_host
    saved = ROOT / "work" / "demo-video-answers.json"
    data = json.loads(args.reuse.read_text(encoding="utf-8")) if args.reuse else {}
    names = [name for name in SCENES if (name in args.redo.split(",") if args.reuse else True)]
    if names:
        data = collect(names, data)
        saved.parent.mkdir(parents=True, exist_ok=True)
        saved.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"Saved the live answers to {saved.relative_to(ROOT)}")
    if not args.collect_only:
        record(data)


if __name__ == "__main__":
    main()
