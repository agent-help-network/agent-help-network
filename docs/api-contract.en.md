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

Public mode uses only published, non-synthetic observations. Local test mode can read synthetic observations for `.test` hosts and then always returns `synthetic: true`. The two modes are never mixed in one response. MCP `check_incident` takes the same inputs and returns the same structure.

A live public response for `github.com` (`error_class=site_access`, captured 2026-10-18), trimmed: `robots.rules` has one entry per known agent token (22 tokens) and is cut to three here, and `next_checks[].text` is shortened.

```json
{
  "site_host": "github.com",
  "generated_at": "2026-10-18T04:41:37.175110Z",
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
    "fetched_at": "2026-10-18T04:41:37.171405Z",
    "source_updated_at": "2026-10-18T02:29:25.654000Z",
    "freshness": "fresh"
  },
  "provider_source_state": "feed_connected",
  "provider_discovery_attempted_at": null,
  "official_status_page": null,
  "lookup_activity": {"period_days": 7, "volume_band": "1-4", "trend": null, "unit": "requests_not_agents"},
  "access_profile": {
    "robots_status": "ok",
    "robots": {
      "rules": [
        {"agent": "ChatGPT-User", "kind": "user_agent", "explicit_group": false, "root_allowed": true, "disallow_count": 162},
        {"agent": "GPTBot", "kind": "training_crawler", "explicit_group": true, "root_allowed": true, "disallow_count": 162},
        {"agent": "Bytespider", "kind": "training_crawler", "explicit_group": true, "root_allowed": false, "disallow_count": 1}
      ],
      "wildcard_root_allowed": true,
      "sitemap": false,
      "agents_blocked": ["Bytespider"],
      "agents_allowed": ["OAI-SearchBot", "PerplexityBot", "Bingbot", "GPTBot", "ClaudeBot", "anthropic-ai"]
    },
    "llms_txt_status": "present",
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
- `provider_status` is present only when a **verified official status source** exists for the host. It carries `source_name`, `source_url`, `indicator` (`none`, `minor`, `major`, `critical`, `unknown`, or `null`), `source_updated_at`, `fetched_at`, and `freshness` (`fresh`, `pending`, `stale`; older than 2 hours or failed since the last success is `stale`).
- Without a fixed mapping, the first lookup checks whether `status.<host>` is delegated to Statuspage and, if verified, reads the official API in the same response. Requests within 15 seconds share one result. A server job then refreshes registered sources about hourly. A failed refresh marks the previous value `stale`.
- `indicator: none` means the provider's status API shows no active issue. It does not guarantee that a given agent's sign-in or access will succeed. Provider status never overwrites another source's data.
- `provider_source_state` is `feed_connected`, `official_page_only`, or `not_verified_by_current_methods`. With `official_page_only`, `official_status_page` gives only a source name and URL, not a current reading (the page-only mappings confirmed so far are Netflix and Webex). `not_verified_by_current_methods` means the current discovery methods could not verify a source, not that the provider has no status API.
- `provider_discovery_attempted_at` is the time of the host's last automatic discovery attempt, or `null` if none is recorded.

### Lookup activity

`lookup_activity` appears from the first lookup of an eligible public apex host. Instead of exact counts it gives `volume_band` (`1-4`, `5-9`, `10-49`, `50+`), `period_days: 7`, `unit: requests_not_agents`, and `trend: increased` when the conditions are met (otherwise `null`). It does not count independent agents or failures. Lookup volume and provider status are never copied into `observations` or `incidents`.

### Access profile layer

`access_profile` is the host's **declared** agent policy, read from its own `robots.txt` and `llms.txt`. It is `null` outside public mode, for hosts that are not eligible public apex hosts, and if the layer fails; a read failure never turns a lookup into an error.

- `robots_status`: `ok`, `absent` (404/410), `redirected` (a 3xx other than the single apex → `www.` hop, not followed), `error`, `too_large` (over 64 KB), `not_public` (the host does not resolve only to public IPs), or `pending`. When it is not `ok`, `robots` is `null` or the last successful value, and the policy layer is **inconclusive**, not a block.
- `robots.rules`: one entry per agent token the service knows, computed with RFC 9309 matching. `kind` is `user_agent`, `search_crawler`, or `training_crawler`. `explicit_group` says whether the file names a group for that token; `root_allowed` says whether `/` may be fetched; `disallow_count` is the number of non-empty Disallow lines that apply. Tokens without their own group inherit the `*` group.
- `robots.agents_blocked`: tokens with an explicit group that disallows the root. `robots.agents_allowed`: tokens with an explicit group that allows it. Nothing is said about tokens that are not listed. `wildcard_root_allowed` is the `*` group's root decision, and `sitemap` says whether the file declares a sitemap.
- `llms_txt_status`: `present`, `absent`, `error`, `redirected`, `not_public`, or `pending`. Only presence is recorded; the content is neither stored nor passed on.
- `source_urls`: the two file URLs on the looked-up host. `fetched_at`: last successful read (UTC). `freshness`: `fresh` (within 24 hours), `stale`, or `pending`. `note`: a fixed English caveat.
- Reads happen at most once per host per day, success or failure. A lookup with a fresh cache makes no network request. Hosts on the public seed list `ahn/seed_hosts.txt` are pre-read by the review cron (`python -m ahn.auto_review`: up to 10 per 5-minute run, `AHN_WARM_BATCH`, `0` disables) and re-read about weekly, under the same limits (`ahn/profile_warm.py`).
- **Declared policy is not an access guarantee.** `root_allowed: true` does not mean a request will succeed; CDNs, bot filters, logins, and rate limits can treat a user-directed agent differently from a crawler. An Allow line does not replace the site's terms. Conversely, if the agent's own user-agent family is in `agents_blocked`, the expected behavior is to stop retrying that page and hand off to the user or use the official path.

### Official paths layer

`official_paths` comes from a curated table in code (`ahn/official_paths.py`) and has only `name`, `api_docs`, `developer_portal`, and `note`; any of the last three can be `null`, and the whole object is `null` for hosts not in the table. Only hosts whose public developer documentation was actually checked are listed. `null` does not mean no official path exists. Listing is not an endorsement or affiliation, and using an official API usually requires the user's own credentials and the provider's terms.

### Recommendation

`recommendation` gives one action derived only from the declared and official layers (observations do not change it). `action` is evaluated in this order; the first match wins:

| `action` | Condition | `confidence` | `reasons` |
| --- | --- | --- | --- |
| `wait_provider_incident` | `provider_status.freshness` is `fresh` and `indicator` is `minor`, `major`, or `critical`. | medium | `provider_scope` |
| `hand_off_to_human` | `error_class=human_verification` | high | `human_handoff` |
| `hand_off_to_human` | `error_class=login_auth` | medium | `account_handoff` |
| `use_official_path` | Declared block (below) and an official path with `api_docs` or `developer_portal`. | high | `robots_blocks_agents`, `official_path_available` |
| `stop_declared_block` | Declared block and no official path. | medium | `robots_blocks_agents` |
| `use_official_path` | Official path listed and `error_class` absent, `site_access`, or `service_error`. | medium | `official_path_available` |
| `proceed` | `robots_status` is `ok` with no declared block. | low | `robots_allows_agents` |
| `unknown` | Anything else, such as unreadable `robots.txt` or no access profile. | low | `robots_unknown` when an access profile exists, otherwise empty |

A **declared block** means `robots_status` is `ok` and at least one `user_agent` or `search_crawler` token has an explicit group that disallows the root. Training-crawler blocks appear in `agents_blocked` but do not trigger it.

### `next_checks`

`next_checks` is an ordered list of `{code, text}` items with English `text`; the human website shows the same codes in Korean. The checks come from human verification and sign-in, the optional failure stage, the declared and official layers, a fresh provider statement, and whether reports exist. They never infer the actual result or cause of a task, never grant permission to bypass a site, and are not an automatic outage verdict. Order:

1. `failure_stage` value if given and not `unknown` (`before_target_response`, `response_observed`, `access_denial_observed`); otherwise `locate_failure_stage`, unless `error_class` is `human_verification` or `login_auth`.
2. `human_handoff` (`error_class=human_verification`) or `account_handoff` (`error_class=login_auth`).
3. When an access profile exists: `robots_unknown` (`robots_status` not `ok`), `robots_blocks_agents` (declared block), or `robots_allows_agents`; then `llms_txt_present` when `llms_txt_status` is `present`.
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
| `robots_blocks_agents` | `robots.txt` explicitly disallows an agent user agent at the root; stop automated retries, prefer the official path, hand off where needed. |
| `robots_allows_agents` | `robots.txt` does not disallow the listed agent user agents at the root; not an access guarantee, keep requests modest. |
| `robots_unknown` | No `robots.txt` policy could be read; treat the policy as unknown, not permissive. |
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

`check_incident` creates no report but has server-side effects: host registration, a daily per-host lookup count, and at most one `robots.txt`/`llms.txt` read per host per day. Because these never change what the caller sees, it is annotated `readOnlyHint=true` and `idempotentHint=true` from 2026-10-17. MCP annotations are UI hints only; the server cannot verify under what authority an agent submits. Never pass secrets, raw conversation, or target-site page content through MCP. Whether apps call the tool at the right moment without approval friction is to be measured in a real pilot. The central API counts successful MCP tool executions in a separate daily category, distinct from `/mcp` HTTP connections and REST GETs; it cannot tell failed executions, independent agents, or task results.

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
