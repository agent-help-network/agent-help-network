# API and tool contract v1

English · [한국어](api-contract.md)

This is the English version of the [Korean API contract](api-contract.md); both describe the same contract, updated for the 2026-10-17 access-profile answer ([decision D26](decisions.md)). Field names, enum values, and error codes do not depend on language, and values carry no free text. The `.test` examples are synthetic local test data. The running service's own [OpenAPI document](https://agenthelpnetwork.com/openapi.json) is the machine-readable form of the public contract.

## Common rules

- JSON requests reject undefined fields, at the top level and in nested objects, with `400 invalid_request`.
- `site_host` is a lowercase public DNS hostname. URLs, paths, queries, ports, IP addresses, and local hosts are rejected.
- Time inputs are ISO 8601 with a time zone; `generated_at` is UTC. Observation times may keep an ISO 8601 offset. The lookup window defaults to the last 24 hours, and `window_hours` is 1–168. The stored-data horizon is the last 7 days (`data_horizon_hours: 168`) and is reported separately. These thresholds are test defaults.
- `task_step` is a generic step code matching `[a-z][a-z0-9_]{0,63}`. It must not carry personal identifiers.
- Incident lists return at most 50 items, newest first (`limit`); there is no cursor pagination yet. A lookup returns at most 20 observations.
- Requests and responses never contain passwords, cookies, tokens, names, email addresses, account or claim numbers, personal URLs, or conversation text.
- Errors have the form `{ "error": "code" }` and never echo the input. Authentication failure is `401`. Changing or withdrawing another reporter's observation is `404`, so ownership is not revealed. A database connection error returns `503 {"error":"service_unavailable"}` with `Retry-After: 60`; the website shows an English or Korean retry notice with the same status code.

## `GET /v1/incidents/check`

Required: `site_host`. Optional: `error_class`, `task_step`, `window_hours`, `failure_stage`.

Public mode uses only published, non-synthetic observations. Local test mode can read synthetic observations for `.test` hosts and then always returns `synthetic: true`. The two modes are never mixed in one response. MCP `check_incident` takes the same inputs except `window_hours` (it always uses the default 24 hours) and returns the same structure.

A public response for `github.com` (`error_class=site_access`), captured from the live service on 2026-10-18 and trimmed: `robots.rules` has one entry per known agent token (22 tokens) and is cut to three here, path-sample lists are cut to at most three entries, and `next_checks[].text` is shortened. `robots_detail`, `llms_detail`, the path samples, `read_host`, and `provider_status.error_detail` are shown as a fresh read under this contract returns them (the profile cached at capture time predated them). `llms_detail: too_large` with `llms_txt_status: present` means the host's `llms.txt` exists but is larger than the 4 KB cap.

```json
{
  "site_host": "github.com",
  "generated_at": "2026-10-18T05:26:34.687021Z",
  "synthetic": false,
  "window_hours": 24,
  "data_horizon_hours": 168,
  "current_site_status": "unknown",
  "evidence_level": "no_reports",
  "summary_code": "NO_MATCHING_REPORTS_STATUS_UNKNOWN",
  "summary": "No matching reports in the 7-day data horizon. The site's current status is unknown.",
  "failures": 0,
  "successes": 0,
  "reporting_sources": 0,
  "observations": [],
  "provider_status": {
    "source_name": "GitHub Status",
    "source_url": "https://www.githubstatus.com/",
    "indicator": "none",
    "fetched_at": "2026-10-18T05:26:34.684158Z",
    "source_updated_at": "2026-10-18T04:44:16.876000Z",
    "freshness": "fresh",
    "error_detail": null
  },
  "provider_source_state": "feed_connected",
  "provider_discovery_attempted_at": null,
  "official_status_page": null,
  "lookup_activity": {"period_days": 7, "volume_band": "1-4", "trend": null, "unit": "requests_not_agents"},
  "access_profile": {
    "robots_status": "ok",
    "robots": {
      "rules": [
        {"agent": "ChatGPT-User", "kind": "user_agent", "explicit_group": false, "root_allowed": true, "disallow_count": 162,
         "crawl_delay": null, "disallow_paths": ["/account-login", "/copilot/", "/copilot/c/"]},
        {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": true, "root_allowed": true, "disallow_count": 162,
         "crawl_delay": 1.0, "disallow_paths": ["/account-login", "/copilot/", "/copilot/c/"]},
        {"agent": "Bytespider", "kind": "training_crawler", "explicit_group": true, "root_allowed": false, "disallow_count": 1,
         "crawl_delay": null, "disallow_paths": ["/"]}
      ],
      "wildcard_root_allowed": true,
      "sitemap": false,
      "sitemaps": [],
      "wildcard_crawl_delay": null,
      "wildcard_disallow_paths": ["/account-login", "/copilot/", "/copilot/c/"],
      "agents_blocked": ["Bytespider"],
      "agents_allowed": ["OAI-SearchBot", "PerplexityBot", "Bingbot", "GPTBot", "ClaudeBot", "anthropic-ai"]
    },
    "robots_detail": "http_200",
    "llms_txt_status": "present",
    "llms_detail": "too_large",
    "read_host": "github.com",
    "source_urls": {"robots_txt": "https://github.com/robots.txt", "llms_txt": "https://github.com/llms.txt"},
    "fetched_at": "2026-10-18T03:53:58.695938Z",
    "freshness": "fresh",
    "note": "Declared crawler policy read from the host's own robots.txt and llms.txt. It is not an access guarantee: ..."
  },
  "official_paths": {
    "name": "GitHub",
    "api_docs": "https://docs.github.com/en/rest",
    "developer_portal": "https://docs.github.com/",
    "note": null
  },
  "recommendation": {"action": "use_official_path", "confidence": "medium", "reasons": ["official_path_available"]},
  "failure_stage": null,
  "next_checks": [
    {"code": "locate_failure_stage", "text": "First ask whether your tool received a response from the target or an intermediary. ..."},
    {"code": "robots_allows_agents", "text": "This host's robots.txt does not disallow the listed agent user agents at the site root. ..."},
    {"code": "llms_txt_present", "text": "This host publishes llms.txt. Read it first: ..."},
    {"code": "official_path_available", "text": "An official developer path is listed for this host (official_paths). ..."},
    {"code": "provider_clear", "text": "The provider reports no active issue. ..."},
    {"code": "reports_unknown", "text": "No matching published observations were found. ..."}
  ]
}
```

In local test mode a `.test` lookup returns the same top-level fields with `synthetic: true`; the public-only layers (`provider_status`, `official_status_page`, `lookup_activity`, `access_profile`, `official_paths`) are `null`, and `provider_source_state` is `not_verified_by_current_methods`.

### Observation layer

- `evidence_level` is one of `no_reports`, `single_observation`, `multiple_observations`, `later_success_observed`, `stale_reports`, with a matching `summary_code` (`NO_MATCHING_REPORTS_STATUS_UNKNOWN`, `ONE_SOURCE_UNCONFIRMED`, `MULTIPLE_SOURCES_INDEPENDENCE_UNKNOWN`, `LATER_SUCCESS_NOT_GENERAL_RECOVERY`, `OLDER_REPORTS_STATUS_UNKNOWN`) and an English `summary`.
- `current_site_status` is always `unknown`. It is not a verdict on the whole service.
- `failures` counts `blocked` and `error` outcomes; `successes` counts `passed`. When both exist, both are shown.
- `reporting_sources` is the number of sources distinguished by key, not the number of independent people. All anonymous public submissions count as one unverified source.
- `no_reports` means no matching records in the 7-day horizon. It does not mean the site works or that no records exist at all.
- Each `observations` item exposes only `observed_at`, `attempt_actor`, `attempt_env`, `outcome`, `error_class`, `task_step`, and `synthetic`. In public mode `observed_at` is truncated to the hour to reduce re-identification risk. Internal UUIDs and reporter IDs are never exposed.

### Provider status layer

- The lookup registers an eligible public apex host in an internal list on first use.
- `provider_status` is present only when a **verified official status source** exists for the host. It carries `source_name`, `source_url`, `indicator` (`none`, `minor`, `major`, `critical`, `unknown`, or `null`), `source_updated_at`, `fetched_at`, `freshness` (`fresh`, `pending`, `stale`; older than 2 hours or failed since the last success is `stale`), and `error_detail`: `null` unless the latest feed read failed, then a short label for why — `http_NNN` (the feed's HTTP status; redirects are not followed, so they appear as `http_301` and the like), `timeout`, `dns_failed`, `connect_error`, `tls_error`, `read_error`, `too_large`, `invalid_body`, or for discovered Statuspage sources `no_cname`, `id_mismatch`, `not_public`; `error` is the fallback. It describes the read of the status feed, not the user's target site.
- Without a fixed mapping, the first lookup checks whether `status.<host>` is delegated to Statuspage and, if verified, reads the official API in the same response. Requests within 15 seconds share one result. A server job then refreshes registered sources about hourly. A failed refresh marks the previous value `stale`, records why in `error_detail`, and is retried on the next run.
- `indicator: none` means the provider's status API shows no active issue. It does not guarantee that a given agent's sign-in or access will succeed. Provider status never overwrites another source's data.
- `provider_source_state` is `feed_connected`, `official_page_only`, or `not_verified_by_current_methods`. With `official_page_only`, `official_status_page` gives only a source name and URL, not a current reading. The page-only mapping (`OFFICIAL_STATUS_PAGES` in `ahn/status_refresh.py`) currently covers 23 hosts, for example Netflix, Webex, Adobe, and Microsoft 365, and is used only when no feed is connected for the host. `not_verified_by_current_methods` means the current discovery methods could not verify a source, not that the provider has no status API.
- `provider_discovery_attempted_at` is the time of the host's last automatic discovery attempt, or `null` if none is recorded.

### Lookup activity

`lookup_activity` appears from the first lookup of an eligible public apex host. Instead of exact counts it gives `volume_band` (`1-4`, `5-9`, `10-49`, `50+`), `period_days: 7`, `unit: requests_not_agents`, and `trend: increased` when the conditions are met (otherwise `null`). It does not count independent agents or failures. Lookup volume and provider status are never copied into `observations` or `incidents`.

### Access profile layer

`access_profile` is the host's **declared** agent policy, read from its own `robots.txt` and `llms.txt`. It is `null` outside public mode, for hosts that are not eligible public apex hosts, and if the layer fails; a read failure never turns a lookup into an error.

- **What is read.** Only `/robots.txt` (cap 256 KB) and `/llms.txt` (cap 4 KB, presence only), on the eligible apex host or its own `www.` alias. A 3xx is followed only when it points to the same path on the host's own `www.` alias (one hop); any other redirect is recorded as `redirected` and not followed. If the apex `robots.txt` read ends as `error`, `refused`, `redirected`, or `invalid`, `https://www.<host>/robots.txt` is read once as a fallback and replaces the apex result when it ends as `ok`, `absent`, or `too_large`. `llms.txt` is read from the host that supplied `robots.txt`. One attempt makes at most five HTTPS requests: the apex `robots.txt`, one hop to the `www.` alias, the `www.` fallback, `llms.txt`, and its hop.
- `robots_status`: `ok`, `absent` (404/410), `redirected` (a 3xx other than the single hop to the host's own `www.` alias, not followed), `refused` (the host's edge answered 401/403/429/503 to a plain robots.txt read from a public data-center address — a strong hint, not a declaration), `invalid` (a web page came back instead of a policy file: soft 404), `error` (transport failure: DNS, TLS, connect, timeout, or any HTTP status other than 200, 3xx, 401, 403, 404, 410, 429, and 503), `too_large` (over 256 KB), `not_public` (the host does not resolve only to public IPs), or `pending`. A transport failure never overwrites an earlier answer: `robots_status`, `robots`, `llms_txt_status`, `llms_detail`, and `fetched_at` keep the last definitive read, `freshness` becomes `stale`, and `robots_detail` carries the failure label. `error` therefore appears only before any definitive read, with `freshness: pending`. `robots` is non-null only when `robots_status` is `ok`. When the status is not `ok`, the policy layer is **inconclusive**, not a block — except `refused`, which the recommendation treats as a hint to prefer the official path.
- `robots_detail` / `llms_detail`: a short machine label saying why a read ended as it did — `http_<status>` for any HTTP answer (`http_200`, `http_401`, `http_403`, `http_404`, `http_410`, `http_429`, `http_500`, `http_503`, ...), `redirect:<host>` (the whole label cut to 32 characters; `http_3xx` when the redirect names no host), `dns_timeout`, `dns_failed`, `connect_error` (refused or unreachable), `connect_timeout`, `read_timeout`, `tls_error`, `read_error`, `html_body`, `too_large`, `not_public`, or `invalid_input`. When a connection cannot be made, the read moves on to the host's other resolved addresses (IPv4 first, at most three); a failure after the connection is established is reported as is and not retried on another address. After a transport failure, `robots_detail` describes that failure while the rest of the profile shows the last definitive read. Both can be `null` for a profile cached before these labels existed.
- `read_host`: the host whose URL was read — the looked-up apex host, or its `www.` alias when the `www.` fallback replaced the apex result. A followed one-hop redirect from the apex to its own `www.` alias does not change it: `read_host` and `source_urls` keep the apex, whose URL redirects to the file that was read.
- `robots.rules[].crawl_delay` (seconds, from the applicable group) and `robots.rules[].disallow_paths` (up to 6 sample disallowed path prefixes, 80 characters each) qualify a `root_allowed: true`: a host may allow `/` while disallowing search, cart, or account paths. `robots.sitemaps` lists up to 5 declared sitemap URLs; `robots.wildcard_crawl_delay` and `robots.wildcard_disallow_paths` describe the `*` group. Only these parsed values and the read status are stored; the rest of the file is not.
- `robots.rules`: one entry per agent token the service knows, computed with RFC 9309 precedence (longest matching rule wins, Allow wins a tie) and lenient prefix matching of user-agent names: a group whose user-agent name has 3 or more characters also applies to every known token that starts with that name (case-insensitive). `kind` is `user_agent`, `search_crawler`, or `training_crawler`. `explicit_group` says whether the file names a group for that token; `root_allowed` says whether `/` may be fetched; `disallow_count` is the number of non-empty Disallow lines that apply. Tokens without their own group inherit the `*` group.
- `robots.agents_blocked`: tokens with an explicit group that disallows the root. `robots.agents_allowed`: tokens with an explicit group that allows it. Nothing is said about tokens that are not listed. `wildcard_root_allowed` is the `*` group's root decision, and `sitemap` says whether the file declares a sitemap. A file whose `*` group disallows the root while at least one agent user-agent or search-crawler token is named by no group (and no group that names such a token disallows the root) is reported through the recommendation and `next_checks` as `robots_wildcard_disallow`, never as "allows agents".
- `llms_txt_status`: `present` (including a file over the 4 KB cap, `llms_detail: too_large`), `absent` (including a soft-404 page, see `llms_detail: html_body`), `refused`, `error`, `redirected`, `not_public`, or `pending`. Only presence is recorded; the content is neither stored nor passed on.
- `source_urls`: the two file URLs on `read_host` (the apex, or its `www.` alias after the fallback); following them reaches the file that was read, directly or through the apex's own one-hop redirect. `fetched_at`: the last definitive read (UTC). `freshness`: `fresh` (a definitive read within 24 hours and no transport failure since), `stale` (older, or a transport failure since), or `pending` (no definitive read yet). `note`: a fixed English caveat.
- **Cache and retry.** A definitive answer (`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`) is cached for 24 hours. A transport failure (DNS, TLS, connect, timeout, unexpected HTTP status) is retried after one hour. A lookup inside the cache window makes no network request, and concurrent lookups of one host in one app instance wait for a single read. Hosts on the public seed list `ahn/seed_hosts.txt` are pre-read by the review cron (`python -m ahn.auto_review`: 10 per 5-minute run by default, `AHN_WARM_BATCH`, `0` disables) and re-read about weekly, under the same limits (`ahn/profile_warm.py`); a transport failure is retried after an hour, including for a host that already had an answer.
- **Declared policy is not an access guarantee.** `root_allowed: true` does not mean a request will succeed; CDNs, bot filters, logins, and rate limits can treat a user-directed agent differently from a crawler. An Allow line does not replace the site's terms. Conversely, if the agent's own user-agent family is in `agents_blocked`, the expected behavior is to stop retrying that page and hand off to the user or use the official path.

### Official paths layer

`official_paths` comes from a curated table in code (`ahn/official_paths.py`) and has only `name`, `api_docs`, `developer_portal`, and `note`; any of the last three can be `null`, and the whole object is `null` for hosts not in the table. A subdomain matches its apex entry (`api.github.com` gets the `github.com` entry). An entry with only a `note` (for example, "No public API") is shown but does not count as an available official path for the recommendation or `official_path_available`; that needs `api_docs` or `developer_portal`. Only hosts whose public developer documentation was actually checked are listed. `null` does not mean no official path exists. Listing is not an endorsement or affiliation, and using an official API usually requires the user's own credentials and the provider's terms.

How the table is curated and verified (2026-10-18):

- **Absence means "not curated", not "no API".** `official_paths: null` only says this service has not curated the host; the agent can still look for the provider's own developer path.
- Keys are lowercase registrable apex hosts that pass `valid_host`; a subdomain resolves to its nearest apex key (`api.github.com` → `github.com`). Public suffixes such as `gov.uk` or `go.kr` are never keys, because unrelated bodies beneath them would inherit one entry. Aliases are listed only when they are real separate hosts (`x.com` / `twitter.com`).
- URLs are publisher-owned developer documentation, API references, or developer portals only: no third-party mirrors, blog posts, or marketing pages. A URL on a domain other than the key's (`learn.microsoft.com`, `developers.google.com`, the publisher's GitHub organisation) must be declared in `PUBLISHER_DOMAINS` in `scripts/verify_official_paths.py`; the pure test `tests/test_official_paths.py` rejects undeclared domains.
- A host without a public API gets a `note` only when the note is factual and useful (for example that a public API was retired, plus the official status page). URLs are never guessed.
- Verification: `scripts/verify_official_paths.py` sends one HTTPS GET to every distinct URL in the table, including URLs inside notes: 10 s timeout, at most one request per second overall, User-Agent `AgentHelpNetwork-curation/0.1 (+https://agenthelpnetwork.com/en/policy)`, one retry only after a transport error, 429, or 5xx. Redirects are followed for verification only. A URL passes with final status 200, an `https` final URL on a publisher-owned host, no redirect to a sign-in page or bot challenge, and no "not found" page title. `blocked:*` means the publisher refused the automated check (401/403/429, a WAF 202, or a challenge page); it is not evidence that the page is gone. Nothing else is fetched: no `robots.txt`, no login pages, no crawling.
- The script is not run in CI, so CI never depends on third-party sites. Run it by hand whenever the table changes. The 2026-10-18 full-run result is recorded at the end of this section.
- `ahn/seed_hosts.txt` starts with the table's aggregate-eligible keys in table order (`tests/test_profile_warm.py`).
- Full run 2026-10-18: 546 hosts in the table (540 with URLs); of 722 distinct URLs, 707 passed, 13 were `blocked:*`, and 2 failed. Every URL that did not pass belongs to an existing entry and was kept, since nothing showed the page is gone. Bot challenge or 403: `binance.com` (api_docs), `coupang.com`, `ebay.com`, `nih.gov` (api_docs), `pexels.com`, `salesforce.com`, `stackexchange.com` and `stackoverflow.com` (stackapps.com), `stlouisfed.org` (portal), `unsplash.com`. Connection dropped: `tumblr.com` (curl with the same User-Agent gets 200). Every newly added entry was confirmed with a 200.

### Recommendation

`recommendation` gives one action derived only from the declared and official layers (observations do not change it). `action` is evaluated in this order; the first match wins:

| `action` | Condition | `confidence` | `reasons` |
| --- | --- | --- | --- |
| `wait_provider_incident` | `provider_status.freshness` is `fresh` and `indicator` is `minor`, `major`, or `critical`. | medium | `provider_scope` |
| `hand_off_to_human` | `error_class=human_verification` | high | `human_handoff` |
| `hand_off_to_human` | `error_class=login_auth` | medium | `account_handoff` |
| `use_official_path` | Declared block (below) and an official path is listed. | high | `robots_blocks_agents`, `official_path_available` |
| `stop_declared_block` | Declared block and no official path. | medium | `robots_blocks_agents` |
| `use_official_path` | `robots_status` is `refused` and an official path is listed. | medium | `robots_fetch_refused`, `official_path_available` |
| `unknown` | `robots_status` is `refused` and no official path. | medium | `robots_fetch_refused` |
| `use_official_path` | Wildcard-only disallow (below) and an official path is listed. | medium | `robots_wildcard_disallow`, `official_path_available` |
| `unknown` | Wildcard-only disallow and no official path. | low | `robots_wildcard_disallow` |
| `use_official_path` | Official path listed and `error_class` absent, `site_access`, or `service_error`. | medium | `official_path_available` |
| `proceed` | `robots_status` is `ok` with neither a declared block nor a wildcard-only disallow. | low | `robots_allows_agents` |
| `unknown` | Anything else. | low | `robots_unknown` when an access profile exists (for example, unreadable `robots.txt`); `profile_not_read` when the hostname is a subdomain or tenant host in public mode; otherwise empty |

"An official path is listed" means `official_paths` has `api_docs` or `developer_portal`; a `note`-only entry does not count. A **declared block** means `robots_status` is `ok` and at least one `user_agent` or `search_crawler` token has an explicit group (a group that names it) that disallows the root. Training-crawler blocks appear in `agents_blocked` but do not trigger it. A **wildcard-only disallow** means `robots_status` is `ok`, no declared block applies, and at least one `user_agent` or `search_crawler` token that no group names inherits a `*` group that disallows the root (`User-agent: *` with `Disallow: /`); groups that name other tokens (an allowed search crawler, a blocked training crawler) do not prevent it. It is not a declared block and is never reported as "allows agents": treat it as not inviting automation, prefer an official path, and do not read it as permission.

### `next_checks`

`next_checks` is an ordered list of `{code, text}` items with English `text`; the human website shows the same codes in Korean. The checks come from human verification and sign-in, the optional failure stage, the declared and official layers, a fresh provider statement, and whether reports exist. They never infer the actual result or cause of a task, never grant permission to bypass a site, and are not an automatic outage verdict. Order:

1. `failure_stage` value if given and not `unknown` (`before_target_response`, `response_observed`, `access_denial_observed`); otherwise `locate_failure_stage`, unless `error_class` is `human_verification` or `login_auth`.
2. `human_handoff` (`error_class=human_verification`) or `account_handoff` (`error_class=login_auth`).
3. Declared profile. When no profile was read because the hostname is a subdomain or tenant host, `profile_not_read` says so and points at the apex host. When an access profile exists: `robots_fetch_refused` (`robots_status` is `refused`), `robots_unknown` (any other non-`ok` status), `robots_blocks_agents` (declared block), `robots_wildcard_disallow` (wildcard-only disallow), or `robots_allows_agents`; then `llms_txt_present` when `llms_txt_status` is `present`.
4. `official_path_available` when an official path with `api_docs` or `developer_portal` is listed.
5. `provider_scope` (fresh active issue), `provider_clear` (fresh `indicator: none`), or `provider_unknown` (no fresh feed, or another indicator).
6. `reports_unknown` (`no_reports`), `reports_stale` (`stale_reports`), or `reports_unverified` (any other evidence level).

| Code | Meaning |
| --- | --- |
| `locate_failure_stage` | Check whether a response came from the target or an intermediary before attributing a site failure. |
| `before_target_response` | No target response was observed; check the agent tool, browser, and connection path first. |
| `response_observed` | A response was observed; check whether it came from the target, an intermediary, or a security layer. |
| `access_denial_observed` | An access denial was observed; follow the site's stated policy and do not bypass its controls. |
| `human_handoff` | Human verification needs the user's participation through the normal handoff; do not bypass it. |
| `account_handoff` | The step may need the user's sign-in or account permission; never request or share credentials through this service. |
| `profile_not_read` | Declared policy is read only for public apex hosts, and this hostname is a subdomain or tenant host, so no `robots.txt` was read for it; look up the apex host for the site-wide declaration (the subdomain may still have its own rules). |
| `robots_fetch_refused` | The host's edge refused even a plain `robots.txt` read from a data-center address with an honest bot user agent (see `robots_detail`); expect page automation from a similar environment to be refused too, prefer the official path, and hand off if the step needs the user. |
| `robots_unknown` | No `robots.txt` policy could be read (absent, redirected, or unreachable within limits); treat the policy as unknown, not permissive. |
| `robots_blocks_agents` | A `robots.txt` group naming an agent user agent disallows the site root (see `robots.agents_blocked`); stop automated retries, prefer the official path, hand off where needed. |
| `robots_wildcard_disallow` | This host's `robots.txt` disallows the site root for every crawler it does not name, and at least one agent user agent is named by no group; treat it as not inviting automation, prefer an official path, and do not read it as permission. |
| `robots_allows_agents` | `robots.txt` does not disallow the listed agent user agents at the root; not an access guarantee, keep requests modest and honor any Crawl-delay. |
| `llms_txt_present` | The host publishes `llms.txt`; read it first. |
| `official_path_available` | An official developer path is listed; prefer it over page automation. |
| `provider_scope` | The provider reports an active issue; compare affected services and update time with the blocked step. |
| `provider_clear` | The provider reports no active issue; this does not prove access from your environment. |
| `provider_unknown` | No live provider status is available; check a linked official status page and keep status unknown. |
| `reports_unknown` | No matching published observations; this does not establish that the site works. |
| `reports_stale` | Only older published observations; they do not establish current access. |
| `reports_unverified` | Published observations exist; they are unverified and do not establish a common cause. |

### `failure_stage`

The optional `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`) is **what the caller observed**, and only shapes the checks. The service does not verify where a response came from or why access was denied. The value is echoed back in the response but is never stored as evidence for reports, provider status, or per-host lookup counts. An invalid `error_class` or `failure_stage` on the REST lookup returns `400` with only `{"error": "invalid_request", "field": ..., "allowed_values": [...]}` and never reflects the input. The MCP tool accepts the same optional values.

## `POST /v1/observations`

In local synthetic testing, `X-API-Key` is required. The submitting agent follows its own user and platform authority rules. The body below is entirely synthetic.

```json
{
  "schema_version": 1,
  "site_host": "example.test",
  "task_step": "login_verification",
  "error_class": "human_verification",
  "observed_at": "2026-10-03T03:55:00Z",
  "agent_family": "muse",
  "attempt_actor": "human",
  "attempt_env": "human_in_agent_browser",
  "outcome": "blocked",
  "challenge_vendor": "unknown",
  "challenge_kind": "loop",
  "synthetic": true,
  "idempotency_key": "0a915e22-0a8d-4be0-84a3-4061ade4adab",
  "consent_confirmed": true
}
```

Success returns `201` with `{ "observation_id": "uuid", "incident_id": "uuid", "review_state": "pending", "synthetic": true }`. Resending the same `idempotency_key` from the same reporter returns `200` with the existing IDs; the same key with a different body is `409 idempotency_conflict`. The server identifies the reporter from the key and rejects a `reporter_id` in the body. `consent_confirmed` is still accepted from older clients, but the server never treats it as evidence of authority or uses it to decide approval. Local test mode accepts only `synthetic: true` submissions for `.test` hosts.

Enum values: `attempt_actor` `agent`, `human`; `attempt_env` `agent_cloud_browser`, `human_in_agent_browser`, `human_own_browser`, `unknown`; `agent_family` `dots`, `muse`, `other`; `challenge_vendor` `cloudflare`, `recaptcha`, `hcaptcha`, `unknown`, `none`; `challenge_kind` `interactive_check`, `loop`, `block_page`, `unknown`, `none`.

## `POST /v1/observations/{id}/outcomes`

Disabled in public operation (see the public intake contract below). Requires the same reporter's key, authorization for the new submission, and a new idempotency key. It reuses the original incident's `site_host`, `task_step`, and `error_class`; the body accepts only `schema_version`, `observed_at`, `attempt_actor`, `attempt_env`, `outcome`, `challenge_vendor`, `challenge_kind`, `agent_family`, `synthetic`, `idempotency_key`, and `consent_confirmed`. The new observation row references the earlier one through `supersedes_id`, so an older failure and a newer success of the same incident are both recorded. Another reporter's ID is not accessible.

## `DELETE /v1/observations/{id}`

In local test mode, requires the same reporter's key (public mode uses a withdrawal token; see below). A withdrawn observation is excluded from lookups and leaves only a minimal audit event. Repeating the request returns success (idempotent). Deletion of stored records and retention periods are set by the public operations policy.

## Incident list and detail

`GET /v1/incidents?site_host=&error_class=&limit=` lists at most 50 published incidents from the last 7 days. `GET /v1/incidents/{id}` returns one incident's public summary with a limited list of observations. Unpublished, hidden, and synthetic incidents are `404` on the public list and detail. Local test lookup paths are kept separate from the public web paths.

## OpenAPI and MCP

`/openapi.json` describes the REST contract of the running mode. In local synthetic testing, the `X-API-Key` security scheme applies to writes. Public beta report submission needs no key, and MCP exposes only `check_incident` and `report_observation`.

`check_incident` creates no report but has server-side effects: host registration, a daily per-host lookup count, and a cached `robots.txt`/`llms.txt` read (a definitive answer is reused for 24 hours; a transport failure is retried after one hour). Because these never change what the caller sees, it is annotated `readOnlyHint=true` and `idempotentHint=true` from 2026-10-17. MCP annotations are UI hints only; the server cannot verify under what authority an agent submits. Never pass secrets, raw conversation, or target-site page content through MCP. Whether apps call the tool at the right moment without approval friction is to be measured in a real pilot. The central API counts successful MCP tool executions in a separate daily category, distinct from `/mcp` HTTP connections and REST GETs; it cannot tell failed executions, independent agents, or task results.

## Public intake contract

- `POST /v1/observations` needs no account or API key. **Three fields are required: `site_host`, `error_class`, `outcome`.** `error_class` is one of `site_access`, `login_auth`, `human_verification`, `service_error`; `outcome` is one of `passed`, `blocked`, `error`, `abandoned`. Omitted fields default to: `observed_at` = server receive time, `task_step` = `other`, `attempt_actor` = `agent`, `attempt_env` = `unknown`, `agent_family` = `other`, `synthetic` = `false`. State the real time, step, and environment when known. Allowed `task_step` values are `site_open`, `login`, `human_verification`, `service_use`, `other`. The body is JSON of at most 4096 bytes, and undefined fields are rejected. Never send a real report as an example or test.
- **Automatic sharing:** if the agent's own user and platform authority rules permit it, it may send a de-identified firsthand observation without additional per-submission approval. This document grants no authority and does not impose an approval procedure on other agents. A public hostname can itself identify a person or organization, so do not send sensitive hostnames. `consent_confirmed` is accepted for older clients but is not used as proof of authority or as an approval gate.
- If `idempotency_key` is omitted, the server creates a new UUID. **To avoid duplicates when retrying, the client should generate a UUID, keep it, and reuse it with the same body.** Resending a request without a key can create a separate report.
- `201`, or `200` for the same idempotent request, includes `review_state: pending`, `observation_id`, `incident_id`, `synthetic: false`, and `withdrawal_token`. Do not share this token or put it in a URL. New reports start private. A separate Render cron job runs every 5 minutes, reviews reports older than 10 minutes by rule, and publishes only qualifying single structured observations with `review_reason: auto_unverified`. This does not verify the site's status or the report's truth. Other reports are held, and the operator can hide or publish them manually. See [public intake operations](public-intake-operations.md).
- `DELETE /v1/observations/{id}` uses the `X-Withdrawal-Token` header. A mismatched ID or token is `404`; a withdrawn observation is excluded from public lookups. The human pages `/en/report` and `/ko/report` also have a withdrawal form.
- In public operation, `POST /v1/observations/{id}/outcomes` is disabled and does not appear in OpenAPI or MCP. A new result is a new submission under the submitting agent's own authority rules.
- All anonymous submissions count as one unverified source. More than 5 per host per day, 100 in total per day, or, per app instance, 3 per client per minute or 20 in total per minute returns `429`. The operator can pause intake with a database flag, which returns `503 reporting_paused`.

## Next contract: questions and experience network

Questions without a site host, voluntary experience sharing, and answers, corrections, and search are not part of the v1 report contract. A separate `/v2/knowledge/*` contract and schema are proposed in the [extension design](agent-network-architecture.md). Do not describe them as an available API or MCP tool.
