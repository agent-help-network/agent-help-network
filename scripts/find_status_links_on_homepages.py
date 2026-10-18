"""Read-only homepage link triage for the 746 domains without status CNAMEs.

Only public HTTPS homepages (and one same-domain HTTPS redirect) are fetched.
Links are unverified leads, never imported into the service or counted as lookups.
No content body is retained. This does not determine that a status API exists.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import socket
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

import urllib3


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"
WORKERS = 8
MAX_BODY = 2_000_000
USER_AGENT = "AgentHelpNetwork-StatusResearch/0.1 (+https://agenthelpnetwork.com/)"
POSITIVE_TEXT = ("system status", "service status", "platform status", "status page",
                 "service health", "system health", "incident history", "current incidents")
NEGATIVE_TEXT = ("order status", "shipment status", "shipping status", "application status",
                 "claim status", "case status", "visa status", "status code", "status update")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.href: str | None = None
        self.text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.href = next((value for key, value in attrs if key == "href" and value), None)
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.href is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self.href is not None:
            self.links.append((self.href, " ".join(" ".join(self.text).split())[:120]))
            self.href = None
            self.text = []


def public_ip(host: str) -> str:
    addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
    if not addresses or len(addresses) > 16 or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("no_public_address")
    return sorted(addresses)[0]


def fetch_https(host: str, path: str = "/") -> tuple[int, bytes, str]:
    ip = public_ip(host)
    pool = urllib3.HTTPSConnectionPool(
        ip, port=443, server_hostname=host, assert_hostname=host,
        timeout=urllib3.Timeout(connect=2.0, read=4.0), retries=False,
    )
    try:
        response = pool.request(
            "GET", path, headers={"Host": host, "User-Agent": USER_AGENT, "Accept": "text/html"},
            redirect=False, preload_content=False,
        )
        try:
            body = response.read(MAX_BODY + 1)
            return response.status, body, response.headers.get("location", "")
        finally:
            response.release_conn()
    finally:
        pool.close()


def candidate_links(page_url: str, body: bytes) -> list[str]:
    parser = LinkParser()
    parser.feed(body.decode("utf-8", errors="ignore"))
    results: list[str] = []
    for href, label in parser.links:
        absolute = urljoin(page_url, href)
        parts = urlsplit(absolute)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.port not in (None, 443):
            continue
        text = label.lower()
        if any(item in text for item in NEGATIVE_TEXT):
            continue
        target_host = parts.hostname.lower()
        status_host = target_host.startswith("status.") or ".status." in target_host
        strong_label = any(item in text for item in POSITIVE_TEXT)
        short_label = text.strip() in {"status", "health", "incidents", "outages"}
        if not (status_host or strong_label or short_label):
            continue
        clean_url = urlunsplit(("https", target_host, parts.path or "/", "", ""))
        if clean_url not in results:
            results.append(clean_url)
    return results[:8]


def scan(row: dict[str, str]) -> dict[str, str]:
    domain = row["domain"]
    result = {"rank": row["rank"], "domain": domain, "homepage_result": "", "final_host": "",
              "redirect_target": "",
              "candidate_count": "0", "candidate_urls": ""}
    host = domain
    try:
        path = "/"
        status, body, location = fetch_https(host, path)
        for _ in range(3):
            if status not in {301, 302, 303, 307, 308} or not location:
                break
            parts = urlsplit(urljoin(f"https://{host}{path}", location))
            if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                    or parts.port not in (None, 443)):
                break
            result["redirect_target"] = parts.hostname
            if parts.hostname != domain and not parts.hostname.endswith("." + domain):
                break
            host = parts.hostname
            path = parts.path or "/"
            if parts.query:
                path += "?" + parts.query
            status, body, location = fetch_https(host, path)
        result["final_host"] = host
        if status != 200:
            result["homepage_result"] = f"http_{status}"
        elif len(body) > MAX_BODY:
            result["homepage_result"] = "body_too_large"
        else:
            result["homepage_result"] = "parsed"
            urls = candidate_links(f"https://{host}/", body)
            result["candidate_count"] = str(len(urls))
            result["candidate_urls"] = " | ".join(urls)
    except (OSError, ValueError, urllib3.exceptions.HTTPError) as exc:
        result["homepage_result"] = type(exc).__name__
    return result


def main() -> None:
    with (WORK / "unmatched-status-dns.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["dns_result"] != "one_cname"]
    if len(rows) != 746:
        raise ValueError(f"expected 746 remaining domains, got {len(rows)}")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(scan, rows))
    results.sort(key=lambda row: int(row["rank"]))
    with (WORK / "remaining-homepage-status-links.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    summary = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "domains": len(results),
               "homepage_results": dict(Counter(row["homepage_result"] for row in results)),
               "domains_with_link_candidates": sum(int(row["candidate_count"]) > 0 for row in results),
               "verification_state": "unverified_homepage_links_only"}
    (WORK / "remaining-homepage-status-links-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
