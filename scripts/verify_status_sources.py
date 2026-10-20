"""Weekly re-verification of the curated status mappings in ahn/status_refresh.py.

Read-only curation aid; not run in CI and never imported by the service. Nothing it writes changes the mapping: a curator
reads results.csv and edits ahn/status_refresh.py by hand, in a pull request.

    python scripts/verify_status_sources.py                         # every mapping, about 1 request/second
    python scripts/verify_status_sources.py --hosts github.com,stripe.com
    python scripts/verify_status_sources.py --since-cache           # resume an interrupted run of the same date

Every distinct URL is read once (hosts that share a feed or a page share the answer):

* SOURCES: the ``api_url`` is read the way the service reads it (no redirects followed, hard byte cap of its kind in
  FEED_KINDS) and parsed with the parser of its kind. Results: ``ok``, ``parse_failed`` (200 but the body is not that
  kind's feed), ``too_large``, ``http_NNN``, ``refused`` (401/403/429/202 or a bot-challenge page: the publisher
  turned this automated check away, which is not evidence that the feed is gone), ``redirect_off_host`` (a 3xx whose
  target is another host, a leading ``www.`` ignored: the status page has moved; the new address is in ``detail``), or a
  transport label: ``dns_failed``, ``connect_error``, ``tls_error``, ``timeout``, ``read_error``, ``not_public``,
  ``too_many_redirects``, ``error`` (same vocabulary as the service's failure labels). ``--check-source-pages`` also
  reads each ``page_url`` and flags one that redirects off its host.
* OFFICIAL_STATUS_PAGES (link only): the page is read with redirects followed by hand. ``ok`` needs a 200 that still
  looks like a status page by the discovery script's page rule (hand-curated, so a ``/status`` path segment counts);
  otherwise ``not_a_status_page``, ``refused``, ``http_NNN`` or a transport label. A page whose product is recognised
  (Atlassian Statuspage or incident.io, Instatus, Better Stack: DNS, header or page text, as in the discovery script)
  gets one more request on that product's public feed; when it parses with the existing parser the row is
  ``upgrade_candidate`` (the link could become a feed). ``--probe-all-feeds`` tries all three feed shapes on pages whose
  product is not recognised; ``--no-upgrade-probe`` skips the extra request.

Etiquette is the discovery script's own: at most one HTTPS request per second overall, 10 s timeout, the curation
User-Agent, https only, every hop checked for a public address and rate limited, status hosts only (the publisher's
front page is never read, no robots.txt).

Output under work/status-verify/<date>/: results.csv (one row per mapped host), summary.json, cache.json (per-URL
observations, so ``--since-cache`` repeats no request). Exit code 1 when any row is not ``ok`` (an upgrade candidate
counts) or the run stopped early.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

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
from ahn.status_refresh import FEED_KINDS, OFFICIAL_STATUS_PAGES, SOURCES, failure_label  # noqa: E402

WORK = ROOT / "work" / "status-verify"
REFUSED_STATUSES = {202, 401, 403, 429}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
# Product recognised on a link-only page -> (feed kind, public feed path) that could replace the link.
UPGRADE_FEEDS = {
    "Atlassian Statuspage": ("statuspage", "/api/v2/status.json"),
    "incident.io": ("statuspage", "/api/v2/status.json"),
    "Instatus": ("instatus", "/summary.json"),
    "Better Stack": ("betterstack", "/index.json"),
}
ALL_FEEDS = (("statuspage", "/api/v2/status.json"), ("instatus", "/summary.json"), ("betterstack", "/index.json"))
FIELDS = ["host", "mapping", "kind", "url", "result", "http", "final_url", "detail"]
OK_RESULT = "ok"


# ---------------------------------------------------------------------------------------------------------------
# Pure helpers (covered by tests/test_verify_status_sources.py)

def hop_label(host: str) -> str:
    """Why a hop was refused before any request: the name does not resolve, or it resolves to a non-public address."""
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return "dns_failed"
    return "not_public"


def transport_label(fetched: dict) -> str:
    """Short label for a fetch that ended in ``error`` (see Net.fetch)."""
    error = fetched["error"]
    if fetched.get("exc") is not None:
        return failure_label(fetched["exc"])
    if error == "non_public_or_non_https_hop":
        return hop_label((fetched.get("chain") or [""])[-1])
    return error or "error"


def off_host(url: str, target: str) -> bool:
    """Whether ``target`` is another host than ``url``; a leading ``www.`` on either side does not count as another host."""
    def bare(value: str) -> str:
        host = (urlsplit(value).hostname or "").lower()
        return host[4:] if host.startswith("www.") else host
    return bare(url) != bare(target)


def header(headers: dict, name: str) -> str:
    for key, value in headers.items():
        if key.lower() == name:
            return value
    return ""


def refused(fetched: dict, title: str = "") -> bool:
    final = urlsplit(fetched["final_url"])
    return (fetched["status"] in REFUSED_STATUSES or "challenge" in final.path.lower()
            or (final.hostname or "").startswith("unblock.") or bool(discover.BOT_WALL.search(title)))


def row_template(host: str, mapping: str, kind: str, url: str) -> dict:
    return {"host": host, "mapping": mapping, "kind": kind, "url": url, "result": "", "http": "", "final_url": "", "detail": ""}


def observe_feed(net, kind: str, url: str) -> dict:
    """Read one SOURCES api_url the way the service does and parse it. Returns {result, http, final_url, detail}."""
    if kind not in FEED_KINDS:
        return {"result": "unknown_kind", "http": "", "final_url": url, "detail": f"kind {kind!r} is not in FEED_KINDS"}
    cap, parse = FEED_KINDS[kind]
    fetched = net.fetch(url, cap=cap, follow=False)
    out = {"result": "", "http": fetched["status"] or "", "final_url": fetched["final_url"], "detail": ""}
    status = fetched["status"]
    if fetched["error"]:
        out["result"] = transport_label(fetched)
        out["detail"] = fetched["error"]
    elif status in REDIRECT_STATUSES:
        target = urljoin(url, header(fetched["headers"], "location"))
        if header(fetched["headers"], "location") and off_host(url, target):
            out.update(result="redirect_off_host", detail=f"moved to {target}")
        else:
            out.update(result=f"http_{status}", detail=f"redirects to {target}" if header(fetched["headers"], "location") else "")
    elif refused(fetched):
        out["result"] = "refused"
        out["detail"] = f"HTTP {status}"
    elif status != 200:
        out["result"] = f"http_{status}"
    elif len(fetched["body"]) >= cap:
        out.update(result="too_large", detail=f"more than {cap} bytes")
    elif discover.BOT_WALL.search(discover.title_of(fetched["body"])):
        out.update(result="refused", detail="bot challenge page")
    else:
        try:
            indicator, _ = parse(json.loads(fetched["body"]))
            out.update(result=OK_RESULT, detail=f"indicator {indicator}")
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            out.update(result="parse_failed", detail=str(exc)[:120])
    return out


def feed_parses(net, origin_host: str, kind: str, path: str) -> dict | None:
    """The public feed of a product on ``origin_host`` when it answers 200 on that very path and parses with the kind's parser."""
    cap, parse = FEED_KINDS[kind]
    reply = net.fetch(f"https://{origin_host}{path}", cap=cap)
    final = urlsplit(reply["final_url"])
    if reply["error"] or reply["status"] != 200 or final.path != path or (final.hostname or "") != origin_host or len(reply["body"]) >= cap:
        return None
    try:
        indicator, _ = parse(json.loads(reply["body"]))
    except (ValueError, TypeError, KeyError, RecursionError):
        return None
    return {"kind": kind, "api": f"https://{origin_host}{path}", "indicator": indicator}


def observe_page(net, host: str, url: str, *, upgrade_probe: str = "known") -> dict:
    """Read one link-only page and note what it is. The judgement (which depends on the mapped host) is ``judge_page``.

    upgrade_probe: "known" = one extra request on the feed of a recognised product, "all" = every feed shape when the
    product is not recognised, "off" = none."""
    fetched = net.fetch(url, cap=discover.TITLE_CAP)
    title = discover.title_of(fetched["body"]) if fetched["body"] else ""
    obs = {"http": fetched["status"] or "", "final_url": fetched["final_url"], "title": title, "error": "", "refused": False,
           "cname_service": "", "service": "", "upgrade": None}
    if fetched["error"]:
        obs["error"] = transport_label(fetched)
        return obs
    obs["refused"] = refused(fetched, title)
    if fetched["status"] != 200 or obs["refused"]:
        return obs
    final_host = urlsplit(fetched["final_url"]).hostname or ""
    chain = list(dict.fromkeys(net.cname_chain(urlsplit(url).hostname or "") + (net.cname_chain(final_host) if final_host else [])))
    obs["cname_service"] = next((discover.service_for_cname(t) for t in chain if discover.service_for_cname(t)), "") or ""
    service, _ = discover.fingerprint(chain, fetched["headers"], fetched["body"].decode("utf-8", "replace"))
    obs["service"] = service
    if upgrade_probe == "off" or not final_host:
        return obs
    if service in UPGRADE_FEEDS:
        kinds = [UPGRADE_FEEDS[service]]
    elif upgrade_probe == "all":
        kinds = list(ALL_FEEDS)
    else:
        kinds = []
    for kind, path in kinds:
        found = feed_parses(net, final_host, kind, path)
        if found:
            obs["upgrade"] = found
            break
    return obs


def judge_page(host: str, obs: dict) -> tuple[str, str]:
    """(result, detail) for a link-only page observation."""
    if obs["error"]:
        return obs["error"], ""
    if obs["refused"]:
        return "refused", f"HTTP {obs['http']}"
    if obs["http"] != 200:
        return f"http_{obs['http']}", ""
    if obs["upgrade"]:
        up = obs["upgrade"]
        return "upgrade_candidate", f"{up['kind']} feed parses at {up['api']} (indicator {up['indicator']})"
    if not discover.looks_like_status_page(obs["title"], obs["final_url"], site_host=host, cname_service=obs["cname_service"], supplied=True):
        return "not_a_status_page", f"title {obs['title']!r} at {obs['final_url']}"
    return OK_RESULT, f"product: {obs['service']}" if obs["service"] not in ("", "self_built_or_unknown") else ""


def page_row(host: str, url: str, obs: dict) -> dict:
    row = row_template(host, "link", "link", url)
    result, detail = judge_page(host, obs)
    final_host = urlsplit(obs["final_url"]).hostname or ""
    if result == OK_RESULT and final_host and discover.registrable(final_host) != discover.registrable(urlsplit(url).hostname or ""):
        detail = (detail + "; " if detail else "") + f"page now at {final_host}"
    row.update(result=result, http=obs["http"], final_url=obs["final_url"], detail=detail)
    return row


def feed_row(host: str, kind: str, url: str, obs: dict) -> dict:
    row = row_template(host, "feed", kind, url)
    row.update(result=obs["result"], http=obs["http"], final_url=obs["final_url"], detail=obs["detail"])
    return row


# ---------------------------------------------------------------------------------------------------------------
# Run

def plan(hosts: set[str] | None) -> list[tuple[str, str, str, str]]:
    """(host, mapping, kind, url) for every mapping in scope: SOURCES first, then the link-only pages."""
    items = [(host, "feed", source.kind, source.api_url) for host, source in SOURCES.items()]
    items += [(host, "link", "link", page) for host, (_, page) in OFFICIAL_STATUS_PAGES.items()]
    return [item for item in items if not hosts or item[0] in hosts]


def unit_key(mapping: str, kind: str, url: str) -> str:
    return f"{mapping}:{kind}:{url}"


def source_page_unit(host: str) -> tuple[str, str]:
    return f"srcpage:{SOURCES[host].page_url}", SOURCES[host].page_url


def run(args, net) -> tuple[list[dict], dict]:
    out_dir = WORK / args.date
    out_dir.mkdir(parents=True, exist_ok=True)
    cache_path = out_dir / "cache.json"
    cache: dict[str, dict] = json.loads(cache_path.read_text(encoding="utf-8")) if args.since_cache and cache_path.exists() else {}
    items = plan(args.hosts)
    state = {"complete": False, "stopped": ""}
    started = time.monotonic()

    def flush(rows: list[dict]) -> None:
        cache_path.write_text(json.dumps(cache), encoding="utf-8")
        write_outputs(out_dir, args, items, rows, state, net, started)

    def get(key: str, observe) -> dict:
        if key not in cache:
            cache[key] = observe()
        return cache[key]

    rows: list[dict] = []
    try:
        for index, (host, mapping, kind, url) in enumerate(items, 1):
            key = unit_key(mapping, kind, url)
            fresh = key not in cache
            try:
                if mapping == "feed":
                    obs = get(key, lambda: observe_feed(net, kind, url))
                    row = feed_row(host, kind, url, obs)
                    if args.check_source_pages:
                        pkey, purl = source_page_unit(host)
                        pobs = get(pkey, lambda: observe_page(net, host, purl, upgrade_probe="off"))
                        if row["result"] == OK_RESULT and pobs["http"] == 200 and not pobs["error"] and off_host(purl, pobs["final_url"]):
                            row.update(result="redirect_off_host", detail=f"page {purl} moved to {pobs['final_url']}")
                else:
                    obs = get(key, lambda: observe_page(net, host, url, upgrade_probe=args.upgrade_probe))
                    row = page_row(host, url, obs)
            except RuntimeError as exc:  # request budget
                state["stopped"] = str(exc)
                print(f"stopping: {exc}", flush=True)
                break
            rows.append(row)
            shown = "" if fresh else " (cached)"
            moved = f" -> {row['final_url']}" if row["final_url"] and row["final_url"] != url else ""
            print(f"[{index}/{len(items)}] {row['result']:<18} {row['http']!s:<4} {host:<26} {url}{moved}{shown}", flush=True)
            if len(rows) % 10 == 0:
                flush(rows)
        else:
            state["complete"] = True
    finally:
        flush(rows)
    return rows, state


def write_outputs(out_dir: Path, args, items, rows: list[dict], state: dict, net, started: float) -> dict:
    with (out_dir / "results.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tally: dict[str, int] = {}
    for row in rows:
        tally[row["result"]] = tally.get(row["result"], 0) + 1
    summary = {
        "date": args.date, "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "user_agent": discover.USER_AGENT,
        "complete": state["complete"], "stopped": state["stopped"], "mapped_in_scope": len(items), "checked": len(rows),
        "ok": tally.get(OK_RESULT, 0), "not_ok": len(rows) - tally.get(OK_RESULT, 0),
        "results": dict(sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))),
        "redirect_off_host": [r["host"] for r in rows if r["result"] == "redirect_off_host"],
        "upgrade_candidates": [r["host"] for r in rows if r["result"] == "upgrade_candidate"],
        "requests": net.requests, "seconds": round(time.monotonic() - started),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return summary


def print_summary(rows: list[dict], summary: dict, out_dir: Path, limit: int = 40) -> None:
    bad = [r for r in rows if r["result"] != OK_RESULT]
    if bad:
        print(f"\n{'host':<28} {'result':<18} url")
        for row in bad[:limit]:
            print(f"{row['host']:<28} {row['result']:<18} {row['url']}" + (f"  [{row['detail']}]" if row["detail"] else ""))
        if len(bad) > limit:
            print(f"... and {len(bad) - limit} more in results.csv")
    print(f"\nmappings: {summary['checked']}/{summary['mapped_in_scope']} checked, {summary['ok']} ok, {summary['not_ok']} not ok; "
          f"requests: {summary['requests']}; complete: {summary['complete']}")
    print("results: " + ", ".join(f"{label} {count}" for label, count in summary["results"].items()))
    print(f"written to {out_dir}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--hosts", help="comma-separated mapped hosts to check (default: every SOURCES and OFFICIAL_STATUS_PAGES host)")
    parser.add_argument("--since-cache", action="store_true", help="resume: reuse the observations already in work/status-verify/<date>/cache.json")
    parser.add_argument("--date", default=datetime.now(timezone.utc).date().isoformat(), help="output folder name (default: today, UTC); give the original date to resume across midnight")
    parser.add_argument("--check-source-pages", action="store_true", help="also read each SOURCES page_url (one more request per distinct page) and flag a redirect off its host")
    parser.add_argument("--no-upgrade-probe", dest="upgrade_probe", action="store_const", const="off", default="known", help="do not make the extra feed request on link-only pages")
    parser.add_argument("--probe-all-feeds", dest="upgrade_probe", action="store_const", const="all", help="on link-only pages whose product is unknown, try all three public feed shapes (up to 3 extra requests per page)")
    parser.add_argument("--min-interval", type=float, default=discover.MIN_INTERVAL, help="seconds between requests (never below the default in a real run)")
    parser.add_argument("--max-requests", type=int, help="stop after this many HTTP requests (progress is saved; --since-cache resumes)")
    args = parser.parse_args()
    if args.min_interval < discover.MIN_INTERVAL:
        parser.error("--min-interval below 1 second is not allowed")
    wanted = {h.strip().lower() for h in args.hosts.split(",") if h.strip()} if args.hosts else None
    if wanted is not None:
        unknown = sorted(wanted - set(SOURCES) - set(OFFICIAL_STATUS_PAGES))
        if unknown:
            print(f"not mapped, ignored: {', '.join(unknown)}")
        wanted -= set(unknown)
        if not wanted:
            parser.error("--hosts names no mapped host")
    args.hosts = wanted
    net = discover.Net(args.min_interval, args.max_requests)
    rows, state = run(args, net)
    summary = json.loads((WORK / args.date / "summary.json").read_text(encoding="utf-8"))
    print_summary(rows, summary, WORK / args.date)
    return 1 if summary["not_ok"] or not summary["complete"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
