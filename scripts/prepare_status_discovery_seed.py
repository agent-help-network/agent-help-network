"""Prepare local, unverified status-page candidates for a cold-start pilot.

Inputs are downloaded separately from Tranco and Statusphere. This script does
not visit candidate sites or add anything to the production status_sources table.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work" / "status-discovery"
TRANCO_ARCHIVE = WORK / "tranco-top-1m.csv.zip"
STATUSPHERE_SOURCE = WORK / "statusphere-status-pages.go"
STATUSPHERE_LICENSE = WORK / "statusphere-LICENSE.md"
ENTRY = re.compile(r'\{\s*URL:\s*"([^"\n]+)"\s*,\s*Name:\s*"([^"\n]+)"', re.MULTILINE)
LIMIT = 1_000
GENERIC_RANK_STEMS = frozenset({"cloud", "email", "files", "mail", "music", "news", "office", "store", "video"})
THIRD_PARTY_AGGREGATORS = frozenset({
    "isitdownrightnow.com", "statusgator.com", "updownradar.com", "uptime.com",
})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_ranked_domains() -> list[tuple[int, str]]:
    rows: list[tuple[int, str]] = []
    with zipfile.ZipFile(TRANCO_ARCHIVE) as archive:
        with archive.open("top-1m.csv") as source:
            for raw in source:
                rank_text, domain = raw.decode("utf-8").strip().split(",", 1)
                if not domain or not domain.isascii():
                    raise ValueError("invalid Tranco domain")
                rows.append((int(rank_text), domain.lower()))
                if len(rows) >= LIMIT:
                    break
    if len(rows) != LIMIT or rows[0][0] != 1 or rows[-1][0] != LIMIT:
        raise ValueError("unexpected Tranco ranking format")
    return rows


def read_candidates() -> list[dict[str, str]]:
    source = STATUSPHERE_SOURCE.read_text(encoding="utf-8")
    candidates: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_url, name in ENTRY.findall(source):
        parts = urlsplit(raw_url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            continue
        host = parts.hostname.lower()
        if not host.isascii():
            continue
        url = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))
        if url in seen:
            continue
        seen.add(url)
        candidates.append({"name": name, "status_url": url, "status_host": host})
    if len(candidates) < 100:
        raise ValueError("Statusphere parse produced too few candidates")
    return candidates


def ranked_host_match(status_host: str, ranks: dict[str, int]) -> str | None:
    labels = status_host.split(".")
    # A subdomain of a ranked domain is only a candidate, never proof of origin.
    for offset in range(len(labels) - 1):
        domain = ".".join(labels[offset:])
        if domain in ranks:
            return domain
    return None


def ranked_name_matches(name: str, ranks: dict[str, int]) -> list[str]:
    """Suggest names for manual review; a label is never publisher proof."""
    tokens = re.findall(r"[a-z0-9]+", name.lower())
    if not tokens:
        return []
    joined = "".join(tokens)
    matches = []
    for domain in ranks:
        stem = domain.split(".", 1)[0]
        if stem not in GENERIC_RANK_STEMS and len(stem) >= 4 and (stem == joined or stem == tokens[0]):
            matches.append(domain)
    return matches


def review_flags(candidate: dict[str, str], ranked_domain: str, match_basis: str) -> str:
    flags: list[str] = []
    if urlsplit(candidate["status_url"]).scheme != "https":
        flags.append("plain_http")
    host = candidate["status_host"]
    if any(host == aggregator or host.endswith("." + aggregator) for aggregator in THIRD_PARTY_AGGREGATORS):
        flags.append("third_party_aggregator_exclude_from_official_feed")
    if match_basis == "third_party_label_matches_ranked_domain_name":
        flags.append("publisher_ownership_unverified")
    if candidate["name"].lower().replace("-", " ") != ranked_domain.split(".", 1)[0]:
        flags.append("service_scope_requires_review")
    return ";".join(flags) or "provider_scope_requires_review"


def write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    for path in (TRANCO_ARCHIVE, STATUSPHERE_SOURCE, STATUSPHERE_LICENSE):
        if not path.is_file():
            raise SystemExit(f"missing source input: {path}")
    ranks = read_ranked_domains()
    rank_by_domain = dict((domain, rank) for rank, domain in ranks)
    candidates = read_candidates()
    overlap: list[dict[str, str | int]] = []
    name_overlap: list[dict[str, str | int]] = []
    for candidate in candidates:
        domain = ranked_host_match(candidate["status_host"], rank_by_domain)
        candidate["ranked_domain_candidate"] = domain or ""
        candidate["verification_state"] = "unverified_candidate"
        candidate["review_flags"] = ""
        if domain:
            basis = "status_host_is_subdomain_of_ranked_domain"
            candidate["review_flags"] = review_flags(candidate, domain, basis)
            overlap.append({
                "rank": rank_by_domain[domain],
                "ranked_domain": domain,
                "status_name": candidate["name"],
                "status_url": candidate["status_url"],
                "match_basis": basis,
                "verification_state": "unverified_candidate",
                "review_flags": candidate["review_flags"],
            })
        else:
            for possible_domain in ranked_name_matches(candidate["name"], rank_by_domain):
                basis = "third_party_label_matches_ranked_domain_name"
                flags = review_flags(candidate, possible_domain, basis)
                name_overlap.append({
                    "rank": rank_by_domain[possible_domain],
                    "ranked_domain": possible_domain,
                    "status_name": candidate["name"],
                    "status_url": candidate["status_url"],
                    "match_basis": basis,
                    "verification_state": "unverified_candidate",
                    "review_flags": flags,
                })
    overlap.sort(key=lambda row: (row["rank"], row["status_url"]))
    name_overlap.sort(key=lambda row: (row["rank"], row["status_url"]))
    write_csv(
        WORK / "tranco-top-1000.csv", ["rank", "domain"],
        [{"rank": rank, "domain": domain} for rank, domain in ranks],
    )
    write_csv(
        WORK / "statusphere-candidates.csv",
        ["name", "status_url", "status_host", "ranked_domain_candidate", "verification_state", "review_flags"],
        candidates,
    )
    write_csv(
        WORK / "ranked-overlap-candidates.csv",
        ["rank", "ranked_domain", "status_name", "status_url", "match_basis", "verification_state", "review_flags"],
        overlap,
    )
    write_csv(
        WORK / "ranked-name-candidates.csv",
        ["rank", "ranked_domain", "status_name", "status_url", "match_basis", "verification_state", "review_flags"],
        name_overlap,
    )
    summary = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "ranked_domains": len(ranks),
        "statusphere_candidate_urls": len(candidates),
        "statusphere_http_urls": Counter(urlsplit(item["status_url"]).scheme for item in candidates).get("http", 0),
        "overlap_candidate_urls": len(overlap),
        "overlap_unique_ranked_domains": len({item["ranked_domain"] for item in overlap}),
        "name_only_candidate_urls": len(name_overlap),
        "name_only_unique_ranked_domains": len({item["ranked_domain"] for item in name_overlap}),
        "name_only_aggregator_urls": sum("third_party_aggregator" in item["review_flags"] for item in name_overlap),
        "verification_state": "unverified_candidates_only",
        "source_urls": {
            "tranco": "https://tranco-list.eu/top-1m.csv.zip",
            "statusphere": "https://github.com/metoro-io/statusphere/blob/main/common/status_pages/status_pages.go",
            "statusphere_license": "https://github.com/metoro-io/statusphere/blob/main/LICENSE.md",
        },
        "source_sha256": {
            "tranco_zip": sha256(TRANCO_ARCHIVE),
            "statusphere_go": sha256(STATUSPHERE_SOURCE),
            "statusphere_license": sha256(STATUSPHERE_LICENSE),
        },
    }
    (WORK / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
