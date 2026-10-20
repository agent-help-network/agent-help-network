"""Weekly status-source discovery for the hosts agents actually looked up (demand-driven collection).

Curation aid; not run in CI and never imported by the service. It reads no database and needs no credentials: the input
is a plain host list, one host per line, that the operator exports from the service's looked-up hosts beforehand
(``#`` comments and blank lines are allowed; a URL or ``Host.Example/path`` is reduced to its host).

    python scripts/collect_demand_status.py --hosts-file work/demand-hosts.txt
    python scripts/collect_demand_status.py --hosts-file work/demand-hosts.txt --stop-at 07:30   # a rerun resumes

Hosts that already have a mapping (SOURCES / OFFICIAL_STATUS_PAGES), that are not ``aggregate_eligible_host`` or that fold
onto another apex (``policy_host``) are dropped first and listed in input.json. The rest go through the discovery pipeline
of scripts/discover_status_sources.py unchanged (same candidates, evidence rules A/B/C, page rule, 1 request/second,
curation User-Agent, status hosts only), and the same files are written to work/status-discovery/demand-<date>/:
candidates.csv, final.csv, excluded.csv, summary.json and cache.json, plus input.json (what was dropped and why).
Nothing is applied to ahn/status_refresh.py: a curator reads final.csv and edits the mapping by hand, in a pull request.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_discovery():
    module = sys.modules.get("discover_status_sources")
    if module is None:
        spec = importlib.util.spec_from_file_location("discover_status_sources", ROOT / "scripts" / "discover_status_sources.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["discover_status_sources"] = module
        spec.loader.exec_module(module)
    return module


discover = _load_discovery()


def filter_hosts(hosts: list[str], mapped: set[str] | None = None) -> tuple[list[str], dict[str, list[str]]]:
    """(hosts worth discovering for, {reason: dropped hosts}). Order is kept; the reasons are discover.skip_reason's."""
    mapped = discover.mapped_hosts() if mapped is None else mapped
    keep: list[str] = []
    dropped: dict[str, list[str]] = {}
    for host in hosts:
        reason = discover.skip_reason(host, mapped)
        (dropped.setdefault(reason, []).append(host) if reason else keep.append(host))
    return keep, dropped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hosts-file", type=Path, required=True, help="plain host list, one per line, exported from the looked-up hosts")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="batch folder suffix (default: today, UTC); give the original date to resume")
    parser.add_argument("--statusphere", type=Path, help="local Statusphere export (optional; skipped when absent)")
    parser.add_argument("--min-interval", type=float, default=discover.MIN_INTERVAL, help="seconds between requests (never below the default in a real run)")
    parser.add_argument("--stop-at", help="local HH:MM at which to stop starting new hosts (progress is saved; a rerun resumes)")
    parser.add_argument("--max-requests", type=int, help="stop after this many HTTP requests")
    args = parser.parse_args()
    if args.min_interval < discover.MIN_INTERVAL:
        parser.error("--min-interval below 1 second is not allowed")
    if args.hosts_file.suffix.lower() == ".csv":
        parser.error("--hosts-file is a plain host list, one host per line")
    if not args.hosts_file.exists():
        parser.error(f"{args.hosts_file} not found")
    hosts = discover.read_hosts(args.hosts_file)
    keep, dropped = filter_hosts(hosts)
    batch = f"demand-{args.date}"
    batch_dir = discover.WORK / batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    kept_file = batch_dir / "hosts.txt"
    kept_file.write_text("".join(f"{host}\n" for host in keep), encoding="utf-8")
    (batch_dir / "input.json").write_text(json.dumps({
        "source": args.hosts_file.name, "hosts_in": len(hosts), "kept": len(keep),
        "dropped": {reason: items for reason, items in dropped.items()},
    }, indent=2) + "\n", encoding="utf-8")
    print(f"{len(hosts)} hosts in, {len(keep)} to discover, dropped: "
          + (", ".join(f"{reason} {len(items)}" for reason, items in dropped.items()) or "none"), flush=True)
    return discover.run_discovery(argparse.Namespace(
        hosts_file=kept_file, batch=batch, candidates_csv=None, statusphere=args.statusphere,
        min_interval=args.min_interval, stop_at=args.stop_at, max_requests=args.max_requests))


if __name__ == "__main__":
    raise SystemExit(main())
