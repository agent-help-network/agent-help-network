"""One-time, read-only probe of locally prepared status-page candidates.

This is a research aid. A 200 response or a Statuspage-shaped API response is
not evidence that a page is published by the ranked domain's operator.
Outputs stay under ignored work/status-discovery/ and are never imported into
the service's source registry automatically.
"""

from __future__ import annotations

import csv
import ipaddress
import json
import re
import socket
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import dns.resolver


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"
KNOWN_AGGREGATORS = frozenset({
    "isitdownrightnow.com", "statusgator.com", "updownradar.com", "uptime.com",
})
MAX_WORKERS = 6
BODY_CAP = 32_768
STATUSPAGE_CNAME = re.compile(r"^([a-z0-9]{8,32})\.stspg-customer\.com\.$")


def read_rows() -> list[dict[str, str]]:
    rows = []
    for filename in ("ranked-overlap-candidates.csv", "ranked-name-candidates.csv"):
        with (WORK / filename).open(encoding="utf-8", newline="") as stream:
            rows.extend(csv.DictReader(stream))
    return rows


def safe_public_host(host: str) -> bool:
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
        return bool(addresses) and len(addresses) <= 16 and all(ipaddress.ip_address(ip).is_global for ip in addresses)
    except (OSError, ValueError):
        return False


def limited_get(client: httpx.Client, url: str) -> tuple[int, bytes]:
    with client.stream("GET", url, headers={"Accept": "application/json,text/html;q=0.5"}) as response:
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > BODY_CAP:
                raise ValueError("response_too_large")
        return response.status_code, bytes(data)


def probe(row: dict[str, str]) -> dict[str, str]:
    result = dict(row)
    result.update(page_http="", api_http="", api_page_name="", api_page_id="", api_indicator="", auto_discovery_eligible="false", probe_note="")
    parts = urlsplit(row["status_url"])
    host = parts.hostname or ""
    if any(host == item or host.endswith("." + item) for item in KNOWN_AGGREGATORS):
        result["probe_note"] = "third_party_aggregator_skipped"
        return result
    if not host or parts.username or parts.password or parts.port not in (None, 443):
        result["probe_note"] = "unsupported_url_skipped"
        return result
    if not safe_public_host(host):
        result["probe_note"] = "public_dns_not_confirmed"
        return result
    # Historical HTTP links are only tried over HTTPS; never downgrade.
    page_url = "https://" + host + (parts.path or "/")
    if parts.query:
        page_url += "?" + parts.query
    api_url = "https://" + host + "/api/v2/status.json"
    with httpx.Client(timeout=httpx.Timeout(6.0), follow_redirects=False, trust_env=False) as client:
        try:
            status, body = limited_get(client, api_url)
            result["api_http"] = str(status)
            if status == 200:
                payload = json.loads(body)
                page, state = payload.get("page"), payload.get("status")
                if isinstance(page, dict) and isinstance(state, dict):
                    if isinstance(page.get("id"), str) and isinstance(page.get("name"), str) and state.get("indicator") in {"none", "minor", "major", "critical", "unknown"}:
                        result["api_page_name"] = page["name"][:100]
                        result["api_page_id"] = page["id"][:100]
                        result["api_indicator"] = state["indicator"]
                    else:
                        result["probe_note"] = "api_shape_unrecognized"
                else:
                    result["probe_note"] = "api_shape_unrecognized"
        except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
            result["probe_note"] = "api_" + type(exc).__name__
        try:
            with client.stream("GET", page_url, headers={"Accept": "text/html"}) as response:
                result["page_http"] = str(response.status_code)
        except httpx.HTTPError as exc:
            result["probe_note"] += (";" if result["probe_note"] else "") + "page_" + type(exc).__name__
    if result["api_page_id"] and host == "status." + row["ranked_domain"]:
        try:
            target = next(iter(str(answer.target).lower() for answer in dns.resolver.resolve(host, "CNAME", lifetime=2)))
            match = STATUSPAGE_CNAME.fullmatch(target)
            if match and match.group(1) == result["api_page_id"]:
                result["auto_discovery_eligible"] = "true"
        except (OSError, dns.exception.DNSException, StopIteration):
            pass
    return result


def main() -> None:
    rows = read_rows()
    if len(rows) < 100:
        raise SystemExit("missing candidate seed")
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(probe, row) for row in rows]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda row: (int(row["rank"]), row["ranked_domain"], row["status_url"]))
    fields = list(results[0])
    with (WORK / "candidate-probe-results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    by_domain: dict[str, list[dict[str, str]]] = defaultdict(list)
    for item in results:
        by_domain[item["ranked_domain"]].append(item)
    domains = []
    for domain, items in sorted(by_domain.items(), key=lambda pair: min(int(item["rank"]) for item in pair[1])):
        domains.append({
            "rank": min(int(item["rank"]) for item in items),
            "ranked_domain": domain,
            "candidate_urls": len(items),
            "api_shape_urls": sum(bool(item["api_page_id"]) for item in items),
            "page_2xx_urls": sum(item["page_http"].startswith("2") for item in items),
            "aggregator_skipped_urls": sum(item["probe_note"] == "third_party_aggregator_skipped" for item in items),
            "auto_discovery_eligible": any(item["auto_discovery_eligible"] == "true" for item in items),
            "probe_result": "api_candidate" if any(item["api_page_id"] for item in items) else "no_confirmed_api",
            "verification_state": "publisher_and_scope_unverified",
        })
    with (WORK / "candidate-domain-results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(domains[0]))
        writer.writeheader()
        writer.writerows(domains)
    summary = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_domains": len(domains),
        "candidate_urls": len(results),
        "api_shape_urls": sum(bool(item["api_page_id"]) for item in results),
        "domains_with_api_shape": sum(item["api_shape_urls"] > 0 for item in domains),
        "domains_without_api_shape": sum(item["api_shape_urls"] == 0 for item in domains),
        "auto_discovery_eligible_domains": sum(item["auto_discovery_eligible"] for item in domains),
        "page_2xx_urls": sum(item["page_http"].startswith("2") for item in results),
        "aggregator_skipped_urls": sum(item["probe_note"] == "third_party_aggregator_skipped" for item in results),
        "api_http_counts": dict(Counter(item["api_http"] or "no_response" for item in results)),
        "verification_state": "publisher_and_scope_unverified",
    }
    (WORK / "candidate-probe-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
