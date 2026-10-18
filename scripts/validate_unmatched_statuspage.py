"""Validate local DNS candidates with the production's bounded read-only path.

This does not register a source or count a user lookup. Publisher product scope
still needs review even when the DNS delegation and page ID both match.
"""

from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import urllib3


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ahn.status_refresh import _read_discovered_statuspage, failure_label

WORK = ROOT / "work" / "status-discovery"


def validate(row: dict[str, str]) -> dict[str, str]:
    result = {"rank": row["rank"], "domain": row["domain"], "status_host": row["status_host"],
              "result": "", "source_name": "", "page_url": "", "indicator_at_check": "", "note": ""}
    try:
        source, indicator, _ = _read_discovered_statuspage(row["domain"])
        result.update(result="verified_dns_and_api", source_name=source.name,
                      page_url=source.page_url, indicator_at_check=indicator,
                      note="publisher product scope not yet reviewed")
    except (ValueError, OSError, urllib3.exceptions.HTTPError, json.JSONDecodeError) as exc:
        # Research output only records the failure label, never response bodies.
        result.update(result="validation_failed", note=failure_label(exc))
    return result


def main() -> None:
    with (WORK / "unmatched-status-dns.csv").open(encoding="utf-8", newline="") as stream:
        candidates = [row for row in csv.DictReader(stream) if row["statuspage_cname_candidate"] == "true"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(validate, candidates))
    results.sort(key=lambda row: int(row["rank"]))
    with (WORK / "unmatched-statuspage-validation.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    good = sum(row["result"] == "verified_dns_and_api" for row in results)
    print(f"candidates={len(results)} verified_dns_and_api={good} validation_failed={len(results)-good}")


if __name__ == "__main__":
    main()
