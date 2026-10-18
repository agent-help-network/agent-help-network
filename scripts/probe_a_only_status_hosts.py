"""Read-only triage of status.<domain> hosts with no CNAME answer.

A public page or Statuspage-shaped JSON response is a lead only. Publisher
ownership and product coverage still require separate review. No AHN writes.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import urllib3

from find_status_links_on_homepages import fetch_https, MAX_BODY


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"


def probe(row: dict[str, str]) -> dict[str, str]:
    host = row["status_host"]
    result = {"rank": row["rank"], "domain": row["domain"], "status_host": host,
              "page_http": "", "api_http": "", "api_page_name": "", "api_page_id": "", "note": ""}
    try:
        status, body, _ = fetch_https(host)
        result["page_http"] = str(status)
        if status != 200:
            return result
        if len(body) > MAX_BODY:
            result["note"] = "page_too_large"
        api_status, api_body, _ = fetch_https(host, "/api/v2/status.json")
        result["api_http"] = str(api_status)
        if api_status == 200 and len(api_body) <= 32_768:
            payload = json.loads(api_body)
            page = payload.get("page") if isinstance(payload, dict) else None
            state = payload.get("status") if isinstance(payload, dict) else None
            if (isinstance(page, dict) and isinstance(state, dict)
                    and isinstance(page.get("id"), str) and isinstance(page.get("name"), str)
                    and state.get("indicator") in {"none", "minor", "major", "critical", "unknown"}):
                result["api_page_name"] = page["name"][:100]
                result["api_page_id"] = page["id"][:100]
    except (OSError, ValueError, urllib3.exceptions.HTTPError, json.JSONDecodeError) as exc:
        result["note"] = type(exc).__name__
    return result


def main() -> None:
    with (WORK / "unmatched-status-dns.csv").open(encoding="utf-8", newline="") as stream:
        rows = [row for row in csv.DictReader(stream) if row["dns_result"] == "no_cname"]
    if len(rows) != 148:
        raise ValueError(f"expected 148 no-CNAME domains, got {len(rows)}")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, rows))
    results.sort(key=lambda row: int(row["rank"]))
    with (WORK / "a-only-status-hosts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    summary = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "domains": len(results),
               "page_http_counts": dict(Counter(row["page_http"] or "no_response" for row in results)),
               "statuspage_shaped_api_count": sum(bool(row["api_page_id"]) for row in results),
               "verification_state": "publisher_and_scope_unverified"}
    (WORK / "a-only-status-hosts-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
