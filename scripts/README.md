# scripts/

Curation aids and local tools. None of them runs in CI, none is imported by the service, and the status scripts never edit
a mapping: a curator reads the output and changes `ahn/status_refresh.py` by hand, in a pull request.

## Weekly status-source jobs

Rank-based expansion of the status mapping stopped at Tranco rank 10,000. Two weekly jobs replace it: re-verify what is
already mapped, and collect mappings for the hosts agents actually looked up.

Both follow the etiquette of `discover_status_sources.py`: at most one HTTPS request per second overall, 10 s timeout, the
curation User-Agent, https only, public addresses only, status hosts only. Neither reads a database or needs credentials.
Output goes under `work/` (git-ignored). Run them from the repository root.

### 1. Re-verify the existing mappings: `verify_status_sources.py`

```
python scripts/verify_status_sources.py
```

Reads every distinct URL once (about 850 requests, roughly 15 minutes at the 1 request/second pace); hosts that share a feed
or a page share the answer.

| Mapping | What is read | Result values |
| --- | --- | --- |
| `SOURCES` | the `api_url`, the way the service reads it (no redirects, the byte cap and parser of its kind) | `ok`, `parse_failed`, `too_large`, `http_NNN`, `refused` (401/403/429/202 or a challenge page), `redirect_off_host` (the feed moved to another host; the new address is in `detail`), or a transport label: `dns_failed`, `connect_error`, `tls_error`, `timeout`, `read_error`, `not_public`, `too_many_redirects` |
| `OFFICIAL_STATUS_PAGES` | the page, redirects followed | `ok` (200 and still a status page by the discovery script's page rule), `not_a_status_page`, `refused`, `http_NNN`, a transport label, or `upgrade_candidate` (the page's product is recognised and its public feed now parses, so the link could become a feed) |

Output: `work/status-verify/<date>/results.csv` (one row per mapped host), `summary.json` and `cache.json`. The exit code is 1
when any row is not `ok` (an upgrade candidate counts) or the run stopped early. `refused` means the publisher turned this
automated check away; it is not evidence that the page is gone, so look at it before changing a mapping.

Options:

- `--hosts a.com,b.org` limits the run to those mapped hosts.
- `--since-cache` resumes an interrupted run without repeating a request (add `--date YYYY-MM-DD`, UTC, to resume across midnight).
- `--check-source-pages` also reads each `SOURCES` `page_url` and flags one that redirects off its host.
- `--no-upgrade-probe` skips the one extra request that tests a recognised product's feed on link-only pages;
  `--probe-all-feeds` tests all three feed shapes on pages whose product is not recognised.
- `--max-requests N` stops after N requests.

Reading the result: `http_404`, `dns_failed`, `redirect_off_host` and `not_a_status_page` on a `SOURCES` host mean the mapping
probably needs a new address (find it with `discover_status_sources.py --hosts-file`); `parse_failed` needs a look at the feed;
`upgrade_candidate` rows are candidates to move from `OFFICIAL_STATUS_PAGES` to `SOURCES`.

### 2. Collect for the hosts agents looked up: `collect_demand_status.py`

The operator exports the hosts looked up on the service during the week into a plain text file, one host per line (`#`
comments allowed), for example `work/demand-hosts.txt`. Then:

```
python scripts/collect_demand_status.py --hosts-file work/demand-hosts.txt
```

Hosts that already have a mapping, are not `aggregate_eligible_host`, or fold onto another apex are dropped (listed in
`input.json`). The rest go through the discovery pipeline of `discover_status_sources.py` unchanged. Output:
`work/status-discovery/demand-<date>/` with `candidates.csv`, `final.csv`, `excluded.csv`, `summary.json`, `cache.json` and
`input.json`. A curator reads `final.csv` (ownership evidence A/B/C) and the `needs_review` rows of `excluded.csv`, and adds
mappings by hand. `--stop-at HH:MM` stops starting new hosts at that local time; rerunning with `--date` set to the original
date resumes from `cache.json`.
