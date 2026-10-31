# scripts/

Curation aids and local tools. None of them runs in CI, none is imported by the service, and the status scripts never edit
a mapping: a curator reads the output and changes `ahn/status_refresh.py` by hand, in a pull request.

## Access bundles for official paths (`OFFICIAL_ACCESS`)

`official_paths.access` tells an agent how to use an official path: auth methods, where to get a key, free tier, rate
limits, sandbox, OpenAPI document, official MCP endpoints, and terms. The field contract is in
[the API contract](../docs/api-contract.en.md#access-bundle-official_pathsaccess-2026-10-20); the data is the
`OFFICIAL_ACCESS` table in `ahn/official_paths.py`. Budget about 20-30 minutes per host. Use the existing bundles
(`github.com`, `stripe.com`, ...) as examples.

**Which hosts.** Only keys that already have `api_docs` or `developer_portal` in `OFFICIAL_PATHS`, in agent-demand order
(hosts agents look up or fail on first, see D35 in `docs/decisions.md`). Skip a host whose API is partner-only with no public
sign-up; its official path is enough.

**How to read.** Read every page yourself, with the same etiquette as the scripts: at most one request per second, the
curation User-Agent, https only, public addresses only, publisher pages only. Never use a third-party copy (API catalogues,
spec mirrors, blog posts, MCP registries, Postman collections), an archive, a cached copy, or another User-Agent. A page that
answers 401, 403, 429, a challenge, or a sign-in wall leaves its field out; a page whose text only appears after scripts
run cannot back a quote. Never guess a URL from a pattern.

**Which domains.** A URL must be on the key's registrable domain, on a domain declared for the key in `PUBLISHER_DOMAINS`
(`scripts/verify_official_paths.py`, with a comment saying why it is the publisher's), or, for `github.com` and
`raw.githubusercontent.com`, inside an organisation declared for the key in `PUBLISHER_GITHUB_ORGS` with a comment naming the
publisher page that links to that organisation.

**Field by field.** Leave out every field you cannot fill (never `null`, `""` or `[]`).

| Field | Fill it when | Notes |
| --- | --- | --- |
| `auth` | the page at `source_url` names the method | One entry per method; one page can back several. Static key, secret, app token, integration token, app-only bearer: `api_key`. User approves an app: `oauth2` (`oauth1` for OAuth 1.0a). Device flow or "TV and limited-input devices": `device_code`. Personal access token: `pat`. `none` only when a page says some endpoints need no credentials. A new method needs a contract change. |
| `key_signup_url` | a documentation page explains how to get a key or token | Not a console URL behind a sign-in. |
| `free_tier` | a sentence states how much can be used free of charge or on what condition (YouTube's "10,000 units per day" quota is the model) | `quote` copied verbatim, at most 200 characters, plus `source_url`. **Never a pricing slogan ("No setup fees"), a free-trial advert, a price-list header, or "pay only for what you use"**: a per-transaction or pay-as-you-go service has no free tier unless a sentence names a free amount. Leave it out when the only evidence is a table cell or an image. |
| `rate_limits_url`, `sandbox_url` | a documentation page covers it | Link only; never copy test card numbers or test credentials. |
| `openapi_url` | the publisher publishes an OpenAPI or Swagger document | The JSON or YAML file itself, not a Swagger UI page. The script checks the body. |
| `official_mcp` | a publisher page gives an MCP endpoint | `endpoint` copied exactly from `doc_url`. Never connect to it. The official MCP registry is for finding candidates only (a domain-verified name such as `com.stripe/*` whose remote URL is on the same registrable domain, checked with the Public Suffix List); the evidence is the publisher's own documentation page, and an endpoint that page does not print is left out. |
| `terms` | the publisher has terms or a policy for API or developer use | `url` plus one verbatim sentence, or a clause that keeps its meaning on its own, at most 200 characters. Prefer one about API use, automation, rate limits, or credentials. No `...` edits, no paraphrase, no stance or summary (terms are linked and quoted, never judged). |
| `verified_at` | always | The UTC date you read the pages. |

**Aliases.** When another key in `OFFICIAL_PATHS` is the same publisher and API (`twitter.com` for `x.com`), add it to the alias
loop under `OFFICIAL_ACCESS` instead of copying the bundle.

**Check, then open a pull request.**

```
python -m pytest tests/test_official_access.py tests/test_official_paths.py
python scripts/verify_official_paths.py --access-only --hosts a.com,b.org --json work/access-verify.json
```

The pure tests cover shape, the method list, the 200-character cap, publisher-owned domains, and dates. The live run must
end with every URL `ok` and every quote and MCP endpoint `found`. When a quote is `not_found`, markup on the page may split
it: choose another sentence. When a URL is `blocked:*`, remove that field. In the pull request, list the hosts, each field you
left out and why (refused, not documented, scripts-only page), and the run's summary line.

**Re-verify every 90 days.** A bundle older than 90 days is left out of answers. Run
`python scripts/verify_official_paths.py --access-only`, fix what changed, and update `verified_at` of the bundles you
re-read; the script prints `stale:` lines for bundles already hidden.

## Weekly status-source jobs

Rank-based expansion of the status mapping stopped at Tranco rank 10,000. Two weekly jobs replace it: re-verify what is
already mapped, and collect mappings for the hosts agents actually looked up.

Both follow the etiquette of `discover_status_sources.py`: at most one HTTPS request per second overall, 10 s timeout, the
curation User-Agent, https only, public addresses only, status hosts only. Neither reads a database or needs credentials.
Output goes under `work/` (git-ignored). Run them from the repository root.

`discover_status_sources.py` also follows the service's robots rule: before it requests a feed path (`/api/v2/status.json`, `/summary.json`, `/index.json`) it reads that status host's `/robots.txt` once and decides with `ahn.feed_robots` (RFC 9309, same as the service). A disallowed path, or one whose robots.txt could not be read (5xx, 429, timeout), is not requested and shows as `robots_blocked` / `robots_unreadable` in `candidates.csv`; a missing or 4xx robots.txt allows it. No other file on the host is probed.

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

## Weekly answer-quality check: `answer_quality.py`

Measures what a first lookup of a host answers, on a frozen set of 100 hosts where agents get blocked or ask for help
(`ahn/quality/failure_hosts_v1.txt`; the selection rules are in its header; a changed list is a new file, `..._v2.txt`).

```
python scripts/answer_quality.py --offline-only --no-save     # coverage from the code tables only, no network
python scripts/answer_quality.py --out-dir <folder>           # offline + live, writes <folder>/<date>.json
python scripts/answer_quality.py --vendor-pass anthropic      # and a second live pass with agent_vendor=anthropic (D48)
```

- **Offline coverage:** the share of set hosts with a seed entry, an official path (`api_docs` or `developer_portal`), a status
  mapping (feed or page only) and a current access bundle. Reads the same tables the service reads; no request.
- **Live answers:** one `GET /v1/incidents/check` per host, at most one per second, with the User-Agent
  `ahn-quality-check/1 (+https://agenthelpnetwork.com)` and no `error_class`. An answer is actionable when it lists an official
  path or its `recommendation.action` is `stop_declared_block`, `wait_provider_incident` or `hand_off_to_human`. Also reported:
  the share still `profile_pending` and the share with no data (action `unknown`, no official path, no status). A failed
  lookup is counted as an error and is left out of the percentages; it is never retried.
- **Vendor pass (D48):** `--vendor-pass <agent_vendor>` asks every host a second time with that `agent_vendor` (same pacing,
  twice the requests) and stores it as `live_vendor` with the hosts whose action differs from the first pass. The change
  against the previous run compares the first pass only. Since D48 a lookup without `agent_vendor` answers a host that
  blocks only some vendors with `unknown` instead of `stop_declared_block`, so a run across that change can list such hosts
  as no longer actionable.
- **Result:** the JSON file has the metrics, one row per host and the change against the newest earlier file in the folder
  (metric deltas, hosts that lost coverage or stopped being actionable, hosts that improved). Stdout prints the same without
  the rows. The default folder, `work/`, is ignored by git; results are not committed.

The lookups are real lookups: they count in the service's lookup totals unless the server leaves out requests whose
User-Agent starts with `ahn-quality-check/`.
