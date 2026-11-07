#!/usr/bin/env python3
"""Weekly check of the vendor help pages in ahn/platform_policies.py (D107): has a page changed since the last reading?

For each page the table lists as read, fetch it once (https, vendor host only, robots.txt respected, one request per
second per host, 10 second timeout), normalise its text, take the SHA-256, and compare with the last stored hash.
Only a changed hash (or a first reading) is recorded, in platform_doc_events: address and hashes, never the page.
A refusal (401, 403, 429), a verification screen, a robots.txt disallow, or a robots.txt that cannot be read (5xx or a network
error: RFC 9309 treats it as a complete disallow) stops that host for the rest of the run;
there is no second try, no other user agent and no cached copy. The script never edits the table: a changed page
needs a person to re-read the quotations.

  python scripts/check_platform_docs.py            # print the report as JSON, store nothing
  python scripts/check_platform_docs.py --store    # also record changes (needs AHN_DATABASE_URL)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import time
import http.client
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahn import platform_policies  # noqa: E402

USER_AGENT = "AgentHelpNetwork-curation/0.1 (+https://agenthelpnetwork.com/en/policy)"
MIN_INTERVAL = 1.0
TIMEOUT = 10.0
BODY_CAP = 2_000_000
REFUSAL = {401, 403, 429}
_DROP = re.compile(r"(?is)<(script|style|noscript|svg|nav|footer|header)\b.*?</\1>")
_TAG = re.compile(r"(?s)<[^>]+>")


def normalize_text(body: bytes) -> str:
    """The text of a page without markup, navigation, scripts or spacing; the same page always gives the same text."""
    text = html.unescape(_TAG.sub(" ", _DROP.sub(" ", body.decode("utf-8", "replace"))))
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def doc_hash(body: bytes) -> str:
    return hashlib.sha256(normalize_text(body).encode("utf-8")).hexdigest()


def read_pages() -> list[tuple[str, str, str]]:
    """(platform, url, vendor) of every page the table lists as read."""
    return [(p["platform"], d["url"], p["vendor"]) for p in platform_policies.PLATFORMS for d in p["docs"] if d["status"] == "read"]


def plan(stored: dict[str, str], seen: dict[str, str]) -> list[dict]:
    """The rows to record: a first reading (prev_hash None) or a changed hash. An unchanged page records nothing."""
    out = []
    for url, new in seen.items():
        prev = stored.get(url)
        if prev != new:
            out.append({"url": url, "prev_hash": prev, "new_hash": new})
    return out


class RobotsRules:
    """A small robots.txt matcher (RFC 9309): the group of our product token, else "*"; `*` and `$` in a rule; the longest
    matching rule wins and Allow wins a tie. The standard-library parser does not read `*` or `$`, so this one does."""

    def __init__(self, text: str) -> None:
        self.groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            field, _, value = line.partition(":")
            field, value = field.strip().lower(), value.strip()
            if field == "user-agent":
                if in_rules:
                    self.groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.lower())
            elif field in ("allow", "disallow") and agents:
                in_rules = True
                if value:  # an empty Disallow disallows nothing; an empty Allow adds nothing
                    rules.append((field == "allow", value))
        if agents:
            self.groups.append((agents, rules))

    @staticmethod
    def _matches(pattern: str, path: str) -> bool:
        anchored = pattern.endswith("$")
        body = pattern[:-1] if anchored else pattern
        regex = ".*".join(re.escape(part) for part in body.split("*"))
        return re.match(regex + ("$" if anchored else ""), path) is not None

    def can_fetch(self, agent: str, url: str) -> bool:
        token = agent.split("/", 1)[0].strip().lower()
        named = [r for names, r in self.groups if any(n != "*" and n in token for n in names)]
        chosen = named or [r for names, r in self.groups if "*" in names]
        rules = [rule for group in chosen for rule in group]
        parts = urlparse(url)
        path = unquote(parts.path or "/") + (("?" + parts.query) if parts.query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern in rules:
            if self._matches(unquote(pattern), path):
                candidate = (len(pattern), allow)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and allow):
                    best = candidate
        return best is None or best[1]


class Fetcher:
    def __init__(self, opener=None, clock=time.monotonic, sleep=time.sleep) -> None:
        self.opener, self.clock, self.sleep = opener or self._open, clock, sleep
        self.robots: dict[str, RobotsRules | None] = {}
        self.last: dict[str, float] = {}
        self.stopped: dict[str, str] = {}

    @staticmethod
    def _open(url: str) -> tuple[int, str, bytes]:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8", "Accept-Language": "en"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return r.status, r.geturl(), r.read(BODY_CAP)
        except urllib.error.HTTPError as e:
            return e.code, url, b""
        except (OSError, http.client.HTTPException, ValueError):  # URLError, timeout, reset, bad status line: status 0 = no answer
            return 0, url, b""

    def _wait(self, host: str) -> None:
        gap = MIN_INTERVAL - (self.clock() - self.last.get(host, -1e9))
        if gap > 0:
            self.sleep(gap)
        self.last[host] = self.clock()

    def _robots(self, host: str) -> RobotsRules | None:
        if host not in self.robots:
            self._wait(host)
            status, _, body = self.opener(f"https://{host}/robots.txt")
            if status == 200:
                self.robots[host] = RobotsRules(body.decode("utf-8", "replace"))
            elif status in REFUSAL:
                self.stopped[host] = f"robots.txt answered {status}"
                self.robots[host] = None
            elif status == 0 or status >= 500:  # RFC 9309: unreachable robots.txt means complete disallow
                self.stopped[host] = "robots.txt unreachable" if status == 0 else f"robots.txt answered {status}"
                self.robots[host] = None
            else:  # 404 and other 4xx: no robots.txt, nothing disallowed
                self.robots[host] = RobotsRules("")
        return self.robots[host]

    def get(self, url: str, allowed_hosts: frozenset[str]) -> tuple[str, str | None]:
        """("ok", hash) or (reason, None). Never retries, never tries another way."""
        host = urlparse(url).hostname or ""
        if host in self.stopped:
            return "host_stopped", None
        robots = self._robots(host)
        if host in self.stopped:
            return "host_stopped", None
        if robots is not None and not robots.can_fetch(USER_AGENT, url):
            return "robots_disallow", None
        self._wait(host)
        status, final, body = self.opener(url)
        if status in REFUSAL:
            self.stopped[host] = f"answered {status}"
            return f"http_{status}", None
        if status == 0:
            return "network_error", None
        if status != 200:
            return f"http_{status}", None
        if urlparse(final).hostname not in allowed_hosts:
            return "left_vendor_host", None
        text = normalize_text(body)
        if not text or "just a moment" in text.lower()[:300]:
            self.stopped[host] = "verification screen or empty page"
            return "challenge_or_empty", None
        return "ok", hashlib.sha256(text.encode("utf-8")).hexdigest()


def run(fetcher: Fetcher, stored: dict[str, str]) -> dict:
    seen: dict[str, str] = {}
    unread: dict[str, str] = {}
    platform_of: dict[str, str] = {}
    for platform, url, vendor in read_pages():
        platform_of[url] = platform
        reason, digest = fetcher.get(url, platform_policies.VENDOR_HOSTS[vendor])
        if digest:
            seen[url] = digest
        else:
            unread[url] = reason
    events = [{**e, "platform": platform_of[e["url"]]} for e in plan(stored, seen)]
    return {"checked": len(seen), "unread": unread, "events": events, "stopped_hosts": dict(fetcher.stopped)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--store", action="store_true", help="record changes in platform_doc_events (needs AHN_DATABASE_URL)")
    args = ap.parse_args()
    stored: dict[str, str] = {}
    conn = None
    if args.store:
        from ahn.db import connect
        from ahn.settings import Settings
        conn = connect(Settings.from_env())
        rows = conn.execute("SELECT DISTINCT ON (url) url, new_hash FROM platform_doc_events ORDER BY url, changed_at DESC, id DESC").fetchall()
        stored = {r["url"]: r["new_hash"] for r in rows}
    report = run(Fetcher(), stored)
    if conn is not None:
        for e in report["events"]:
            conn.execute("INSERT INTO platform_doc_events (platform, url, prev_hash, new_hash) VALUES (%s,%s,%s,%s)",
                         (e["platform"], e["url"], e["prev_hash"], e["new_hash"]))
        conn.commit()
        conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
