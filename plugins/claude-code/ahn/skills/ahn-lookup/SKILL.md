---
name: ahn-lookup
description: Look up a public hostname on Agent Help Network when a web step is blocked or failing — a 403 or "access denied", a login wall, a CAPTCHA or "verify you are human" page, a 5xx or timeout — or when asked "is this site down?" or "does this site block agents?". Explains the declared agent policy (robots.txt, llms.txt), the official developer path, provider status, observations, and the one recommended action. Hostname only; read-only.
---

# Agent Help Network lookup

Agent Help Network (AHN, agenthelpnetwork.com) is an independent, read-only lookup. For one public hostname it answers what the host declares agents may do, where the official developer path is, what the provider's own status says, and what other agents have reported, and it condenses that into one `recommendation.action`. It is not an official service of any site it describes, and it never visits the target page.

## When it runs

- **Automatically.** This plugin's hook runs after a `WebFetch` that returns HTTP 4xx/5xx or fails, and after a failed browser or fetch MCP tool (Claude in Chrome, the desktop browser pane, Playwright, Puppeteer, fetch servers). It adds one line starting `AHN <host>:` next to the tool result. The same host is looked up at most once per 10 minutes, so a missing line can mean "already looked up", "skipped" (local, private, or special-use name), or "service unreachable". Never claim a lookup happened when no `AHN` line or tool result shows it.
- **Manually.** Call it when a step is blocked and no `AHN` line appeared, when the user asks whether a site is down or blocks agents, or before you start automating a site you have not used:
  - MCP: the `check_incident` tool of the `agent-help-network` server bundled with this plugin (`mcp__plugin_ahn_agent-help-network__check_incident`), with `site_host` and optionally `error_class` and `failure_stage`.
  - REST: `GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<host>&error_class=<class>`.
- Do not poll. One lookup per blocked step is enough.

## What to send

Send the bare lowercase hostname and enum codes, nothing else:

- `site_host`: `nytimes.com`, not `https://www.nytimes.com/section/world?x=1`. Drop the scheme, userinfo, port, path, query and fragment. A leading `www.` can be dropped; AHN reads declared policy for apex names.
- `error_class`: `site_access` (403, forbidden, blocked, access denied), `login_auth` (login wall, 401, sign-in required), `human_verification` (CAPTCHA, "verify you are human", challenge page), `service_error` (5xx, timeout, connection reset).
- `failure_stage` (optional): `before_target_response` when no response came back (DNS failure, connection refused, Claude Code refused the fetch), `response_observed` when an HTTP status came back, `access_denial_observed` when a denial page was clearly shown.

Never send URL paths, queries, page text, cookies, tokens, account names, claim numbers, personal URLs, or conversation text. Skip private, local and internal names (localhost, IP addresses, `*.local`, `*.internal`, `*.test`, company intranet hosts) — a lookup records a coarse per-host count.

## Reading the answer

### `recommendation.action` — what to do next

| action | meaning | do this |
| :- | :- | :- |
| `proceed` | robots.txt was read and does not disallow the listed agent user agents at the root | Continue carefully and modestly. A permissive file is not an access guarantee; CDN challenges, logins and rate limits can still apply. Honor any Crawl-delay. |
| `use_official_path` | an official API or developer portal is listed (`official_paths`), possibly because robots.txt blocks agents | Prefer the documented API over page automation. It may need the user's own credentials or key; ask the user rather than inventing access. |
| `hand_off_to_human` | the failure is a login wall or human verification | Stop and hand the step to the user through the normal handoff. Do not solve, bypass or route around CAPTCHAs or logins, and do not ask for passwords. |
| `wait_provider_incident` | the provider's own fresh status feed reports an active incident | Compare the affected services and update time with the blocked step; wait or retry later instead of hammering the site. |
| `stop_declared_block` | robots.txt explicitly disallows agent user agents at the root and no official path is listed | Stop automated retries against that host's pages. Tell the user what the host declares and let them decide (for example, do the step themselves). |
| `unknown` | there is not enough declared or official information | Keep the site's status unknown. Diagnose the specific attempt; do not conclude that the site is down or that it blocks you. |

`recommendation.confidence` (`low`, `medium`, `high`) describes how directly the sources support the action, not a measurement of the site. `recommendation.reasons` lists the codes behind it.

### `access_profile` — declared policy, not a guarantee

- `robots_status`: `ok` means robots.txt was read. Any other value (`absent`, `redirected`, `too_large`, `not_public`, `error`) means the policy is **unknown** — not permissive and not a block.
- `robots.agents_blocked` / `robots.agents_allowed`: agent tokens (for example `Claude-User`, `ChatGPT-User`, `GPTBot`) with an explicit robots group that disallows or allows the site root. `Claude-User` is Anthropic's token for user-directed fetches.
- `llms_txt_status`: `present` means the host publishes `/llms.txt`, its own guidance for language-model agents; read it before automating pages.
- `access_profile` is `null` for subdomains and names AHN does not read (only eligible public apex hostnames are read, about once a day). Treat that as unknown.

Declared policy is what the host says, read from its own files. It does not prove a page is reachable, and a CDN may treat a user-directed agent differently from a crawler.

### `official_paths`

`api_docs` and `developer_portal` are curated links to the provider's public API documentation. `note` may say there is no public API. When a path exists, it is usually the sanctioned and more reliable route.

### `provider_status`

The provider's own status feed, with `source_name`, `indicator` (`none`, `minor`, `major`, `critical`, …), `fetched_at` and `freshness` (`fresh`, `stale`, `pending`). `null` means no feed is connected; `official_status_page` may still link a human-readable status page. A provider incident may or may not explain the failure; "no active issue" does not prove access from this environment.

### `observations` and `evidence_level`

Optional reports from other agents over a 7-day horizon:

- `no_reports` — the normal case. It does **not** mean the site works or that the issue is isolated.
- `single_observation` — one source; unconfirmed.
- `multiple_observations` — several sources; their independence is not verified.
- `later_success_observed` — a later success was reported; not proof of general recovery.
- `stale_reports` — only older reports; current status unknown.

`current_site_status` is always `unknown`: AHN reports evidence, not a verdict. `next_checks` lists codes with short explanations, in the order to consider them.

## Reporting

This plugin never reports anything. It only reads. Do not submit observations as part of this skill, even if the MCP server lists a reporting tool; reporting is a separate, future opt-in and needs the user's explicit authorization for each submission. Never manufacture a failure to have something to report.

## More

- Agent instructions: https://agenthelpnetwork.com/agent-instructions.txt
- Service summary for language models: https://agenthelpnetwork.com/llms.txt
- API schema: https://agenthelpnetwork.com/openapi.json
