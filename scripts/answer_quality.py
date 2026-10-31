"""Weekly answer-quality check on a frozen set of failure hosts (ahn/quality/failure_hosts_v1.txt).

Two measurements, both about the answer a first lookup of a host gets:

  offline  Coverage from the code tables only, no network: how many set hosts have a seed entry, an official
           path (api_docs or developer_portal), a status mapping (feed or page) and a current access bundle.
  live     One GET of /v1/incidents/check per host, at most one request per second, with a User-Agent that says
           it is this check. Metrics: share of answers an agent can act on, share still pending, share with no data.

An answer is actionable (M4) when it lists an official path (api_docs or developer_portal), or when
recommendation.action is stop_declared_block, wait_provider_incident or hand_off_to_human. M4 therefore counts a
developer API alone. M4c is the consumer-task measure: next_action.customer_url is present, or the action is one of
those three, or the action is proceed and no robots group of an agent token disallows the root. A developer path
without customer_url, and unknown, are not M4c. consumer_path_coverage is the share of answers whose official_paths
carries a consumer entry. The lookup carries no error_class, like the first lookup of an agent that has not
classified its failure yet, so hand_off_to_human and wait_provider_incident show up only when the data alone calls for them.

--vendor-pass VENDOR (D48) adds a second live pass that sends agent_vendor=VENDOR for the same hosts (same pacing,
so twice the requests). It is stored beside the first pass as live_vendor, with the hosts whose action differs between
the two answers; the change against the previous run still compares the first pass only.

A run writes <out-dir>/<date>.json (set version, metrics, one row per host, change against the previous run in
that folder). Results are not committed to the public repository (work/ is ignored); the weekly job passes the
operator's internal folder as --out-dir. Stdout gets the summary (metrics and changes, no rows).

    python scripts/answer_quality.py                       # offline + live, writes work/answer-quality/<date>.json
    python scripts/answer_quality.py --offline-only        # no network
    python scripts/answer_quality.py --vendor-pass anthropic  # also score each host as the Claude Code plugin asks
    python scripts/answer_quality.py --out-dir D:\\results --base-url https://agenthelpnetwork.com
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ahn.models import valid_host  # noqa: E402
from ahn.official_paths import official_paths  # noqa: E402
from ahn.profile_warm import load_seed_hosts  # noqa: E402
from ahn.service import aggregate_eligible_host, policy_host  # noqa: E402
from ahn.status_refresh import OFFICIAL_STATUS_PAGES, SOURCES  # noqa: E402
from ahn.triage import accepted_vendor, declared_block, profile_pending  # noqa: E402

SCHEMA_VERSION = 2  # 2: live rows gain customer_url, m4c, consumer_path; live metrics gain m4c and consumer_path_coverage
READABLE_SCHEMAS = frozenset({1, SCHEMA_VERSION})  # a v1 file still serves as the previous run (its m4c deltas are None)
DEFAULT_SET = ROOT / "ahn" / "quality" / "failure_hosts_v1.txt"
DEFAULT_OUT_DIR = ROOT / "work" / "answer-quality"
DEFAULT_BASE_URL = "https://agenthelpnetwork.com"
# Names the check, so that the server can leave it out of its counts of real use (it is not an agent in trouble).
USER_AGENT = "ahn-quality-check/1 (+https://agenthelpnetwork.com)"
MIN_INTERVAL = 1.0  # seconds between two lookups: at most one request per second
TIMEOUT = 20.0
ACTIONABLE_ACTIONS = frozenset({"stop_declared_block", "wait_provider_incident", "hand_off_to_human"})
CONSUMER_ACTIONS = frozenset({"hand_off_to_human", "stop_declared_block", "wait_provider_incident"})
COVERAGE_KEYS = ("seed", "official_path", "status", "access_bundle")

Fetch = Callable[[str, dict, float], "tuple[int, str]"]


# --------------------------------------------------------------------------- the set

def load_set(path: Path | str = DEFAULT_SET) -> list[tuple[str, str]]:
    """(host, source) in file order. A host must be a valid, eligible, unique public name."""
    rows: list[tuple[str, str]] = []
    seen: set[str] = set()
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        host, source = parts[0], (parts[1] if len(parts) > 1 else "unknown")
        try:
            valid_host(host)
        except ValueError:
            raise ValueError(f"{path}:{number}: not a valid public host name: {host!r}") from None
        if not aggregate_eligible_host(host):
            raise ValueError(f"{path}:{number}: not an eligible registrable host: {host}")
        if host in seen:
            raise ValueError(f"{path}:{number}: duplicate host: {host}")
        seen.add(host)
        rows.append((host, source))
    return rows


def set_version(path: Path | str) -> str:
    """'v1' from failure_hosts_v1.txt; the file stem when the name carries no version."""
    stem = Path(path).stem
    tail = stem.rsplit("_", 1)[-1]
    return tail if tail.startswith("v") and tail[1:].isdigit() else stem


def file_sha256(path: Path | str) -> str:
    """SHA-256 of the file with CRLF folded to LF, so a Windows checkout and a Linux checkout agree."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


# --------------------------------------------------------------------------- offline coverage

def official_listed(official: dict | None) -> bool:
    """Same test as ahn.triage.recommendation: an api_docs or developer_portal link."""
    return bool(official and (official.get("api_docs") or official.get("developer_portal")))


def offline_row(host: str, seeds: frozenset[str], today: date | None = None) -> dict:
    site = policy_host(host)
    official = official_paths(site, today)
    status = "feed" if site in SOURCES else "page" if site in OFFICIAL_STATUS_PAGES else None
    return {
        "seed": site in seeds,
        "official_path": official_listed(official),
        "status": status,
        "access_bundle": bool(official and official.get("access")),
    }


def pct(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


def offline_metrics(rows: list[dict]) -> dict:
    total = len(rows)
    flags = {
        "seed": sum(r["seed"] for r in rows),
        "official_path": sum(r["official_path"] for r in rows),
        "status": sum(r["status"] is not None for r in rows),
        "access_bundle": sum(r["access_bundle"] for r in rows),
    }
    metrics = {"hosts": total}
    for key, count in flags.items():
        metrics[key] = {"count": count, "pct": pct(count, total)}
    metrics["status_feed"] = sum(r["status"] == "feed" for r in rows)
    metrics["status_page_only"] = sum(r["status"] == "page" for r in rows)
    all_four = sum(1 for r in rows if r["seed"] and r["official_path"] and r["status"] and r["access_bundle"])
    metrics["all_four"] = {"count": all_four, "pct": pct(all_four, total)}
    none_at_all = sum(1 for r in rows if not (r["seed"] or r["official_path"] or r["status"] or r["access_bundle"]))
    metrics["none"] = {"count": none_at_all, "pct": pct(none_at_all, total)}
    return metrics


# --------------------------------------------------------------------------- live answers

def classify_answer(body: dict) -> dict:
    """Reduce one /v1/incidents/check response to the fields the metrics need (nothing else is kept)."""
    recommendation = body.get("recommendation") or {}
    action = recommendation.get("action")
    official = body.get("official_paths")
    listed = official_listed(official)
    profile = body.get("access_profile")
    provider = body.get("provider_status")
    page = body.get("official_status_page")
    pending = profile_pending(profile)
    actionable = listed or action in ACTIONABLE_ACTIONS
    no_data = action == "unknown" and not listed and not provider and not page
    customer = (body.get("next_action") or {}).get("customer_url") or None
    return {
        "action": action,
        "confidence": recommendation.get("confidence"),
        "reasons": list(recommendation.get("reasons") or []),
        "official_path": listed,
        "access_bundle": bool(official and official.get("access")),
        "provider_state": body.get("provider_source_state"),
        "profile_freshness": (profile or {}).get("freshness"),
        "robots_status": (profile or {}).get("robots_status"),
        "profile_pending": pending,
        "customer_url": customer,
        "consumer_path": bool(official and official.get("consumer")),
        "actionable": actionable,
        "m4c": consumer_actionable(body, action, customer),
        "no_data": no_data,
    }


def consumer_actionable(body: dict, action: str | None, customer: str | None) -> bool:
    """M4c: the answer gives a path a consumer task can use. Stricter than M4, which counts a developer API alone.

    True for a customer_url, a hand-off/stop/wait action, or proceed on a host with no explicit robots block for any
    agent token (triage.declared_block, the same test the verdict uses). use_official_path without customer_url
    (developer path only) and unknown are not consumer-actionable.
    """
    if customer:
        return True
    if action in CONSUMER_ACTIONS:
        return True
    return action == "proceed" and not declared_block(body.get("access_profile"))


def default_fetch(url: str, headers: dict, timeout: float) -> tuple[int, str]:
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read(1_000_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read(10_000).decode("utf-8", "replace")


def live_check(host: str, base_url: str, fetch: Fetch, agent_vendor: str | None = None) -> dict:
    query = {"site_host": host, **({"agent_vendor": agent_vendor} if agent_vendor else {})}
    url = f"{base_url.rstrip('/')}/v1/incidents/check?{urllib.parse.urlencode(query)}"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        status, text = fetch(url, headers, TIMEOUT)
    except Exception as exc:  # transport failure: recorded, never raised
        return {"ok": False, "http_status": None, "error": type(exc).__name__}
    if status != 200:
        return {"ok": False, "http_status": status, "error": f"http_{status}"}
    try:
        body = json.loads(text)
    except ValueError:
        return {"ok": False, "http_status": status, "error": "invalid_json"}
    if not isinstance(body, dict):
        return {"ok": False, "http_status": status, "error": "invalid_json"}
    return {"ok": True, "http_status": status, **classify_answer(body)}


def run_live(hosts: list[str], base_url: str, *, fetch: Fetch = default_fetch,
             sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
             min_interval: float = MIN_INTERVAL, agent_vendor: str | None = None) -> dict[str, dict]:
    """One lookup per host, never faster than one per min_interval seconds."""
    results: dict[str, dict] = {}
    last_start: float | None = None
    for host in hosts:
        if last_start is not None:
            wait = min_interval - (clock() - last_start)
            if wait > 0:
                sleep(wait)
        last_start = clock()
        results[host] = live_check(host, base_url, fetch, agent_vendor)
    return results


def live_metrics(results: dict[str, dict]) -> dict:
    answered = [r for r in results.values() if r.get("ok")]
    n = len(answered)
    actions: dict[str, int] = {}
    for r in answered:
        actions[str(r["action"])] = actions.get(str(r["action"]), 0) + 1
    counts = {
        "actionable": sum(r["actionable"] for r in answered),
        "m4c": sum(bool(r.get("m4c")) for r in answered),
        "consumer_path_coverage": sum(bool(r.get("consumer_path")) for r in answered),
        "profile_pending": sum(r["profile_pending"] for r in answered),
        "no_data": sum(r["no_data"] for r in answered),
        "official_path": sum(r["official_path"] for r in answered),
        "access_bundle": sum(r["access_bundle"] for r in answered),
    }
    metrics: dict = {"hosts": len(results), "answered": n, "errors": len(results) - n}
    for key, count in counts.items():
        metrics[key] = {"count": count, "pct": pct(count, n)}
    metrics["actions"] = dict(sorted(actions.items(), key=lambda kv: (-kv[1], kv[0])))
    return metrics


# --------------------------------------------------------------------------- change against the previous run

def previous_result(out_dir: Path, today: str) -> dict | None:
    """The newest <date>.json in out_dir dated before today; None when there is none or it cannot be read."""
    candidates = sorted(p for p in out_dir.glob("????-??-??.json") if p.stem < today) if out_dir.is_dir() else []
    for path in reversed(candidates):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema_version") in READABLE_SCHEMAS:
            data["_file"] = path.name
            return data
    return None


def _delta(now: dict | None, before: dict | None, key: str) -> float | None:
    if not now or not before or key not in now or key not in before:
        return None
    return round(now[key]["pct"] - before[key]["pct"], 1)


def compare(current: dict, previous: dict | None) -> dict:
    """Metric deltas (percentage points) and the hosts that got worse since the previous run."""
    if previous is None:
        return {"previous_run": None}
    same_set = previous.get("set", {}).get("version") == current["set"]["version"]
    change: dict = {"previous_run": previous.get("run_date"), "previous_file": previous.get("_file"), "same_set": same_set}
    cur_off, old_off = current.get("offline", {}).get("metrics"), previous.get("offline", {}).get("metrics")
    change["offline_pct_points"] = {k: _delta(cur_off, old_off, k) for k in (*COVERAGE_KEYS, "all_four")}
    cur_live, old_live = current.get("live", {}).get("metrics"), previous.get("live", {}).get("metrics")
    change["live_pct_points"] = {k: _delta(cur_live, old_live, k) for k in ("actionable", "m4c", "consumer_path_coverage", "profile_pending", "no_data")}
    old_hosts = {h["host"]: h for h in previous.get("hosts", [])}
    worse: list[dict] = []
    better: list[str] = []
    consumer_better: list[str] = []
    action_changes: list[dict] = []
    for row in current["hosts"]:
        old = old_hosts.get(row["host"])
        if old is None:
            continue
        reasons: list[str] = []
        for key in COVERAGE_KEYS:
            if old["offline"].get(key) and not row["offline"].get(key):
                reasons.append(f"lost_{key}")
        new_live, old_live_row = row.get("live"), old.get("live")
        if new_live is not None and old_live_row is not None:
            if old_live_row.get("ok") and not new_live.get("ok"):
                reasons.append("lookup_failed")
            elif old_live_row.get("ok") and new_live.get("ok"):
                if old_live_row["actionable"] and not new_live["actionable"]:
                    reasons.append("no_longer_actionable")
                if not old_live_row["no_data"] and new_live["no_data"]:
                    reasons.append("became_no_data")
                if old_live_row["action"] != new_live["action"]:
                    action_changes.append({"host": row["host"], "from": old_live_row["action"], "to": new_live["action"]})
                if new_live["actionable"] and not old_live_row["actionable"]:
                    better.append(row["host"])
                if "m4c" in old_live_row and "m4c" in new_live:  # a v1 row has no m4c: nothing to compare
                    if old_live_row["m4c"] and not new_live["m4c"]:
                        reasons.append("no_longer_consumer_actionable")
                    elif new_live["m4c"] and not old_live_row["m4c"]:
                        consumer_better.append(row["host"])
        if reasons:
            worse.append({"host": row["host"], "source": row["source"], "reasons": reasons})
    change["worse_hosts"] = worse
    change["better_hosts"] = better
    change["consumer_better_hosts"] = consumer_better
    change["action_changes"] = action_changes
    change["new_hosts"] = [h["host"] for h in current["hosts"] if h["host"] not in old_hosts]
    change["dropped_hosts"] = sorted(set(old_hosts) - {h["host"] for h in current["hosts"]})
    return change


# --------------------------------------------------------------------------- assembly

def by_source(rows: list[dict], live: bool) -> dict:
    out: dict[str, dict] = {}
    for row in rows:
        entry = out.setdefault(row["source"], {"hosts": 0, "seed": 0, "official_path": 0, "actionable": 0, "no_data": 0, "answered": 0})
        entry["hosts"] += 1
        entry["seed"] += row["offline"]["seed"]
        entry["official_path"] += row["offline"]["official_path"]
        if live and row.get("live") and row["live"].get("ok"):
            entry["answered"] += 1
            entry["actionable"] += row["live"]["actionable"]
            entry["no_data"] += row["live"]["no_data"]
    if not live:
        for entry in out.values():
            del entry["actionable"], entry["no_data"], entry["answered"]
    return out


def build_result(set_path: Path | str, *, run_date: str, base_url: str, live: bool, fetch: Fetch = default_fetch,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 previous: dict | None = None, today: date | None = None, min_interval: float = MIN_INTERVAL,
                 vendor_pass: str | None = None) -> dict:
    members = load_set(set_path)
    seeds = frozenset(load_seed_hosts())
    rows = [{"host": host, "source": source, "offline": offline_row(host, seeds, today)} for host, source in members]
    result: dict = {
        "schema_version": SCHEMA_VERSION,
        "run_date": run_date,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "set": {"version": set_version(set_path), "file": Path(set_path).name, "sha256": file_sha256(set_path), "hosts": len(rows)},
        "offline": {"metrics": offline_metrics([r["offline"] for r in rows])},
        "hosts": rows,
    }
    if live:
        answers = run_live([r["host"] for r in rows], base_url, fetch=fetch, sleep=sleep, clock=clock, min_interval=min_interval)
        for row in rows:
            row["live"] = answers[row["host"]]
        result["live"] = {"base_url": base_url, "user_agent": USER_AGENT, "metrics": live_metrics(answers)}
        if vendor_pass:
            # D48: the same hosts as a caller that names its fetcher's vendor. Kept apart from "live" so the change
            # against the previous run keeps comparing like with like.
            with_vendor = run_live([r["host"] for r in rows], base_url, fetch=fetch, sleep=sleep, clock=clock,
                                   min_interval=min_interval, agent_vendor=vendor_pass)
            differs = []
            for row in rows:
                row["live_vendor"] = with_vendor[row["host"]]
                plain, named = row["live"], row["live_vendor"]
                if plain.get("ok") and named.get("ok") and plain["action"] != named["action"]:
                    differs.append({"host": row["host"], "without": plain["action"], "with": named["action"]})
            result["live_vendor"] = {"agent_vendor": vendor_pass, "metrics": live_metrics(with_vendor), "action_differs": differs}
    result["by_source"] = by_source(rows, live)
    result["changes"] = compare(result, previous)
    return result


def summary(result: dict) -> dict:
    """The result without the per-host rows."""
    return {k: v for k, v in result.items() if k != "hosts"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Weekly answer-quality check on the failure host set.")
    parser.add_argument("--set", dest="set_path", default=str(DEFAULT_SET), help="host set file (default: the v1 set)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="folder for <date>.json (default: work/answer-quality)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--offline-only", action="store_true", help="coverage only; no request is made")
    parser.add_argument("--date", default=None, help="run date YYYY-MM-DD (default: today, UTC)")
    parser.add_argument("--no-save", action="store_true", help="print only; do not write the result file")
    parser.add_argument("--vendor-pass", default=None, metavar="VENDOR",
                        help="also score every host with agent_vendor=VENDOR (a second live pass, D48)")
    args = parser.parse_args(argv)
    if args.vendor_pass is not None and accepted_vendor(args.vendor_pass) is None:
        parser.error("--vendor-pass must be one of the agent_vendor values (see docs/api-contract.en.md)")

    run_date = args.date or datetime.now(timezone.utc).date().isoformat()
    date.fromisoformat(run_date)
    out_dir = Path(args.out_dir)
    previous = previous_result(out_dir, run_date)
    result = build_result(args.set_path, run_date=run_date, base_url=args.base_url, live=not args.offline_only, previous=previous,
                          fetch=default_fetch, sleep=time.sleep, clock=time.monotonic, vendor_pass=args.vendor_pass)
    if not args.no_save:
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / f"{run_date}.json"
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["saved_to"] = str(target)
    sys.stdout.write(json.dumps(summary(result), ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
