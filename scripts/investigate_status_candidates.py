"""One-time, read-only follow-up for candidate pages without Statuspage JSON.

This does not certify publisher ownership or write to the service database.
Output stays under ignored work/status-discovery/. Only HTTPS public hosts are
visited; redirects are bounded and rechecked before each request.
"""

from __future__ import annotations

import csv
import html
import ipaddress
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"
MAX_BODY = 65_536
MAX_REDIRECTS = 2
WORKERS = 5
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
ALT_FEED = re.compile(r"<link\b(?=[^>]*\brel\s*=\s*['\"]?alternate)(?=[^>]*\btype\s*=\s*['\"]?(?:application/(?:rss|atom|json)|text/xml))[^>]*>", re.I)
HREF = re.compile(r"\bhref\s*=\s*['\"]([^'\"]+)", re.I)


def allowed_url(url: str) -> bool:
    part = urlsplit(url)
    if part.scheme != "https" or not part.hostname or part.username or part.password or part.port not in (None, 443):
        return False
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(part.hostname, 443, type=socket.SOCK_STREAM)}
        return bool(addresses) and len(addresses) <= 16 and all(ipaddress.ip_address(ip).is_global for ip in addresses)
    except (OSError, ValueError):
        return False


def inspect(row: dict[str, str]) -> dict[str, str]:
    result = {"ranked_domain": row["ranked_domain"], "candidate_url": row["status_url"],
              "final_url": "", "http": "", "content_type": "", "title": "", "alternate_feeds": "",
              "final_api_http": "", "final_api_page": "", "final_api_id": "", "note": ""}
    if row["probe_note"] == "third_party_aggregator_skipped":
        result["note"] = "third_party_aggregator_skipped"
        return result
    part = urlsplit(row["status_url"])
    url = "https://" + (part.hostname or "") + (part.path or "/")
    if part.query:
        url += "?" + part.query
    with httpx.Client(timeout=httpx.Timeout(7.0), follow_redirects=False, trust_env=False) as client:
        for hop in range(MAX_REDIRECTS + 1):
            if not allowed_url(url):
                result["note"] = "unsafe_or_unresolvable_url"
                return result
            try:
                with client.stream("GET", url, headers={"Accept": "text/html,application/json;q=0.8,application/xml;q=0.5", "User-Agent": "AHN status source research/1.0"}) as response:
                    result["http"] = str(response.status_code)
                    result["final_url"] = str(response.url)
                    result["content_type"] = response.headers.get("content-type", "")[:100]
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location", "")
                        if not location or hop == MAX_REDIRECTS:
                            result["note"] = "redirect_limit_or_missing_location"
                            return result
                        url = urljoin(url, location)
                        continue
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > MAX_BODY:
                            result["note"] = "body_truncated"
                            break
                    body = bytes(data[:MAX_BODY]).decode("utf-8", errors="replace")
                    match = TITLE.search(body)
                    if match:
                        result["title"] = re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()[:160]
                    feeds = []
                    for tag in ALT_FEED.findall(body):
                        link = HREF.search(tag)
                        if link:
                            feed = urljoin(url, html.unescape(link.group(1)))
                            if feed.startswith("https://"):
                                feeds.append(feed)
                    result["alternate_feeds"] = " | ".join(feeds[:5])
                    if urlsplit(url).hostname != part.hostname:
                        api_url = "https://" + (urlsplit(url).hostname or "") + "/api/v2/status.json"
                        if allowed_url(api_url):
                            try:
                                with client.stream("GET", api_url, headers={"Accept": "application/json"}) as api_response:
                                    result["final_api_http"] = str(api_response.status_code)
                                    if api_response.status_code == 200:
                                        api_bytes = bytearray()
                                        for api_chunk in api_response.iter_bytes():
                                            api_bytes.extend(api_chunk)
                                            if len(api_bytes) > 32_768:
                                                break
                                        if len(api_bytes) <= 32_768:
                                            payload = json.loads(api_bytes)
                                            page, status = payload.get("page"), payload.get("status")
                                            if isinstance(page, dict) and isinstance(status, dict) and status.get("indicator") in {"none", "minor", "major", "critical", "unknown"}:
                                                result["final_api_page"] = str(page.get("name", ""))[:100]
                                                result["final_api_id"] = str(page.get("id", ""))[:100]
                            except (httpx.HTTPError, ValueError, json.JSONDecodeError):
                                pass
                    return result
            except (httpx.HTTPError, ValueError) as exc:
                result["note"] = type(exc).__name__
                return result
    return result


def main() -> None:
    with (WORK / "candidate-probe-results.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if not row["api_page_id"]]
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(inspect, rows))
    results.sort(key=lambda row: (row["ranked_domain"], row["candidate_url"]))
    path = WORK / "candidate-followup-results.csv"
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"inspected={len(results)} domains={len({row['ranked_domain'] for row in results})} output={path}")


if __name__ == "__main__":
    main()
