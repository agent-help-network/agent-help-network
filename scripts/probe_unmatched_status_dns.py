"""Read-only DNS triage for Tranco top-1,000 domains absent from the seed list.

The output is a local research artifact. DNS alone does not verify a publisher
feed and this script never calls the Agent Help Network service or database.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import dns.exception
import dns.resolver


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"
STATUSPAGE_CNAME = re.compile(r"^[a-z0-9]{8,32}\.stspg-customer\.com\.$")
WORKERS = 10


def load_csv(name: str) -> list[dict[str, str]]:
    with (WORK / name).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def probe(row: dict[str, str]) -> dict[str, str]:
    domain = row["domain"]
    result = {"rank": row["rank"], "domain": domain, "status_host": f"status.{domain}",
              "cname": "", "dns_result": "", "statuspage_cname_candidate": "false"}
    resolver = dns.resolver.Resolver()
    resolver.timeout = 0.8
    resolver.lifetime = 1.2
    try:
        targets = {str(answer.target).lower() for answer in resolver.resolve(result["status_host"], "CNAME")}
        if len(targets) == 1:
            result["cname"] = targets.pop()
            result["dns_result"] = "one_cname"
            result["statuspage_cname_candidate"] = str(bool(STATUSPAGE_CNAME.fullmatch(result["cname"]))).lower()
        else:
            result["dns_result"] = "multiple_cnames"
    except dns.resolver.NXDOMAIN:
        result["dns_result"] = "nxdomain"
    except dns.resolver.NoAnswer:
        result["dns_result"] = "no_cname"
    except dns.resolver.Timeout:
        result["dns_result"] = "timeout"
    except (dns.exception.DNSException, OSError):
        result["dns_result"] = "dns_error"
    return result


def main() -> None:
    ranked = load_csv("tranco-top-1000.csv")
    seeded = {row["ranked_domain"] for row in load_csv("candidate-domain-results.csv")}
    unmatched = [row for row in ranked if row["domain"] not in seeded]
    if len(unmatched) != 895:
        raise ValueError(f"expected 895 unmatched domains, got {len(unmatched)}")
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(probe, unmatched))
    results.sort(key=lambda row: int(row["rank"]))
    with (WORK / "unmatched-status-dns.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    summary = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "unmatched_domains": len(results),
        "dns_results": dict(Counter(row["dns_result"] for row in results)),
        "statuspage_cname_candidates": sum(row["statuspage_cname_candidate"] == "true" for row in results),
        "verification_state": "dns_candidate_only",
    }
    (WORK / "unmatched-status-dns-summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
