"""Probe status-looking links published on ranked domains' own homepages.

The linked page is still only a candidate: a brand may link to a narrow or
parent-company status, and a public HTML page may lack a machine-readable API.
Outputs are local research artifacts and do not change AHN production data.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from probe_status_candidates import probe


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"


def main() -> None:
    candidates = []
    with (WORK / "remaining-homepage-status-links.csv").open(encoding="utf-8", newline="") as stream:
        for source in csv.DictReader(stream):
            for url in source["candidate_urls"].split(" | "):
                if not url:
                    continue
                parts = urlsplit(url)
                host = parts.hostname or ""
                if not (host.startswith("status.") or host.endswith(".statuspage.io")
                        or parts.path.rstrip("/").endswith("/system-status")):
                    continue
                candidates.append({"rank": source["rank"], "ranked_domain": source["domain"],
                                   "status_name": "homepage_link_candidate", "status_url": url,
                                   "match_basis": "linked_from_ranked_domain_homepage",
                                   "verification_state": "publisher_scope_unverified", "review_flags": ""})
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(probe, candidates))
    results.sort(key=lambda row: (int(row["rank"]), row["status_url"]))
    with (WORK / "homepage-status-link-probes.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    summary = {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
               "candidate_domains": len({row["ranked_domain"] for row in results}),
               "candidate_urls": len(results),
               "statuspage_shaped_api_urls": sum(bool(row["api_page_id"]) for row in results),
               "api_http_counts": dict(Counter(row["api_http"] or "no_response" for row in results)),
               "verification_state": "publisher_scope_unverified"}
    (WORK / "homepage-status-link-probes-summary.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
