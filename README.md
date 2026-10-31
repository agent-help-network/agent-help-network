# Agent Help Network

English · [한국어](README.ko.md)

**When a web step is blocked (403, login wall, CAPTCHA, 5xx), ask once: what does this host allow agents to do, what is its official path, is it up — and what to do next.** Free, read-only, no account. Remote MCP · REST · Claude Code plugin · Python client.

[![PyPI](https://img.shields.io/pypi/v/ahn-client?label=ahn-client)](https://pypi.org/project/ahn-client/) [![License](https://img.shields.io/github/license/agent-help-network/agent-help-network)](LICENSE) [![MCP Registry](https://img.shields.io/badge/MCP%20Registry-com.agenthelpnetwork%2Fagent--help--network-blue)](https://registry.modelcontextprotocol.io/v0.1/servers?search=agent-help-network)

Agent Help Network answers one question for a public hostname: **what does this host allow agents to do, what is the official path, and is it up right now — plus any recent observations from other agents.** It is meant for an agent whose web step is blocked (access denied, login wall, human verification, service error): instead of retrying blindly or handing the task back to a person sooner than needed, the agent first reads the host's own declared policy and official path. Agents read the answer over REST/OpenAPI or MCP; the human website shows the same answer in English and Korean. Steps that genuinely need a person's confirmation or decision are handed to that person.

Agent Help Network is an independent project. It is not an official service of, and is not affiliated with, endorsed by, or sponsored by, any AI provider or platform, any site it describes, or any provider whose status it links.

## Install

Reading is free and needs no account.

```bash
claude plugin marketplace add agent-help-network/agent-help-network   # Claude Code plugin (source: plugins/claude-code/ahn)
claude plugin install ahn@agent-help-network
pip install ahn-client                                                 # Python client (source: clients/python)
pip install "ahn-client[langchain]"                                    # or [crewai], [openai-agents], [claude-agent-sdk]: framework adapters
```

**Install in a client:** the [connect page](https://agenthelpnetwork.com/en/connect#install) has setup for the Claude Code plugin, Cursor, the OpenAI Agents SDK, and any other remote MCP client. For Python code (Playwright, Browser Use, plain HTTP) use the read-only [`ahn-client`](clients/python/README.md) package from PyPI, which has no dependencies and sends only a hostname and enum codes. From version 0.3.0 it also has optional adapters that add a one-line answer when a web tool fails inside LangChain v1 (`[langchain]`), CrewAI (`[crewai]`), the OpenAI Agents SDK (`[openai-agents]`) or the Claude Agent SDK (`[claude-agent-sdk]`), and a [Browser Use example](examples/browser-use/README.md). None of these is an official OpenAI, Meta, Anthropic, Muse, or dots integration.

**Install in your coding agent:** remote HTTP server, no account or key. Each client asks you to confirm before saving. Formats are from each vendor's docs: [Cursor](https://cursor.com/docs/context/mcp/install-links), [VS Code](https://code.visualstudio.com/api/extension-guides/ai/mcp), [Cline](https://docs.cline.bot/mcp/adding-and-configuring-servers), [Codex](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

- Cursor and VS Code one-click buttons: the [connect page](https://agenthelpnetwork.com/en/agents#install) (GitHub does not render `cursor://` or `vscode:` links, and the vendors document no https redirect for them)
- VS Code, in `mcp.json`: `{"servers": {"agent-help-network": {"type": "http", "url": "https://agenthelpnetwork.com/mcp"}}}`
- Cline, in `cline_mcp_settings.json`: `{"mcpServers": {"agent-help-network": {"type": "streamableHttp", "url": "https://agenthelpnetwork.com/mcp"}}}`
- Codex, in `~/.codex/config.toml`:

```toml
[mcp_servers.agent-help-network]
url = "https://agenthelpnetwork.com/mcp"
```

**Plugin demo video:** [watch the Claude Code plugin work](https://cdn.jsdelivr.net/gh/agent-help-network/agent-help-network@1d2c2dd0693259ebd6701277e81b04290958a959/docs/media/agent-help-network-demo-v4.webm) (WebM, 1 min 18 s, with English captions; [captions file](https://cdn.jsdelivr.net/gh/agent-help-network/agent-help-network@1d2c2dd0693259ebd6701277e81b04290958a959/docs/media/agent-help-network-demo-v4.vtt)). GitHub does not play WebM inline, so this opens the file; the same video is embedded on the [connect page](https://agenthelpnetwork.com/en/connect#demo).

## A real response

Send the hostname only — never a URL path, query, credential, or conversation text — and avoid sensitive hostnames.

```bash
curl "https://agenthelpnetwork.com/v1/incidents/check?site_host=github.com&error_class=site_access"
```

A response for `github.com`, captured from the live service on 2026-10-18 and trimmed to the key fields: `robots.rules` held 22 agent-token entries at capture time (it has one entry per token in `AGENT_USER_AGENTS` in [`ahn/access_profile.py`](ahn/access_profile.py), and that table has grown since, so `agents_blocked` and `agents_allowed` below list only the tokens known then) and is cut to three here, path-sample lists are cut to at most three entries, and each `next_checks` entry also carries an English `text`. `robots_detail`, `llms_detail`, `crawl_delay`, the path samples, `sitemaps`, `wildcard_crawl_delay`, `read_host`, and `provider_status.error_detail` are shown as a fresh read under the current contract returns them (the profile cached at capture time predated them).
`summary_line` and `next_action` are the short form: read them first. They are computed after the answer is decided and never change `recommendation` or `next_checks`, which stay the verdict (D51; fields in the [contract](docs/api-contract.en.md)).

```json
{
  "site_host": "github.com",
  "summary_line": "An official developer path is listed for this site. Prefer that documented API or portal over page automation.",
  "next_action": {"do": "use_official_path", "url": "https://docs.github.com/en/rest", "retry_after_seconds": null},
  "links": {
    "feedback": "https://github.com/agent-help-network/agent-help-network/discussions/categories/field-reports",
    "changelog": "https://github.com/agent-help-network/agent-help-network/blob/main/docs/feedback-changelog.md"
  },
  "current_site_status": "unknown",
  "evidence_level": "no_reports",
  "summary_code": "NO_MATCHING_REPORTS_STATUS_UNKNOWN",
  "observations": [],
  "recommendation": {
    "action": "use_official_path",
    "confidence": "medium",
    "reasons": ["official_path_available"]
  },
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
    "freshness": "fresh"
  },
  "official_paths": {
    "name": "GitHub",
    "api_docs": "https://docs.github.com/en/rest",
    "developer_portal": "https://docs.github.com/",
    "note": null
  },
  "provider_status": {
    "source_name": "GitHub Status",
    "source_url": "https://www.githubstatus.com/",
    "indicator": "none",
    "freshness": "fresh",
    "error_detail": null
  },
  "provider_source_state": "feed_connected",
  "next_checks": [
    {"code": "locate_failure_stage"},
    {"code": "robots_allows_agents"},
    {"code": "llms_txt_present"},
    {"code": "official_path_available"},
    {"code": "provider_clear"},
    {"code": "reports_unknown"}
  ]
}
```

Values change over time; call the service rather than copying this example. The full field reference is in the [API contract (English)](docs/api-contract.en.md).

## When to call it

Call it after a web step has failed, not before. Look the host up when a fetch or browser step reports a block or an outage: HTTP 401, 402, 403, 407, 408, 429, 451 or 5xx, a login wall, a human-verification page, a timeout, or a refused connection. A 404 or another plain 4xx is not a block and needs no lookup. Ask once per host; do not poll.

| Runtime | What happens on a failed web step | Install |
| --- | --- | --- |
| Claude Code | The plugin adds one line after a failed `WebFetch` or browser step ([when a line is added](plugins/claude-code/ahn/README.md#when-a-line-is-added)). | `claude plugin install ahn@agent-help-network` |
| LangChain v1, CrewAI, OpenAI Agents SDK, Claude Agent SDK | The adapter adds one line to the failed tool result (CrewAI: to your callback and the log, not the prompt). | `pip install "ahn-client[langchain]"`, `[crewai]`, `[openai-agents]` or `[claude-agent-sdk]` |
| Any other MCP client | You call the `check_incident` tool. | `https://agenthelpnetwork.com/mcp` |
| Plain Python, Playwright, Browser Use, REST | You call `ahn.check()` or the REST endpoint in your `except` block. | `pip install ahn-client` |

## Limits

- It acts only after a failure. A tool that returns the text of a 403 page as an ordinary result is not a failure to the framework, so no adapter sees it: make the tool raise (`response.raise_for_status()`) or report an error. The Claude Code plugin likewise does not look up a browser step that "succeeds" but shows a block page, or `curl` run through Bash; use the `ahn-lookup` skill or the MCP tool by hand.
- A failure that is not a block or an outage (404, 422, a typo in an address) adds nothing, and the run goes on as it would have.
- The answer is declared policy and published sources, not an access guarantee, and `no_reports` does not mean a site works (see [What the answer contains](#what-the-answer-contains)).
- The first lookup of a host can come back with `access_profile.freshness: pending`: ask once more after `retry_after_seconds`, do not poll. A subdomain or tenant host answers `profile_not_read`: look up the apex host.
- It never attempts your task or visits the target page (see [What we read and never do](#what-we-read-and-never-do)).

## REST and MCP reference

People can copy the [full agent instructions](https://agenthelpnetwork.com/agent-instructions.txt) from the [Human page](https://agenthelpnetwork.com/en/human) and paste them into an agent chat. The public site opens on the agent guide; the `Agent | Human` switch at the top right leads to the human search page, which shows declared policy, llms.txt, official path, provider status, and observations in that order.

- **REST:** `GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<public hostname>`
  Optional: `error_class` (`site_access`, `login_auth`, `human_verification`, `service_error`), `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`), `task_step`, `window_hours` (1–168, default 24), and four optional lookup codes the client already knows: `http_status` (401, 402, 403, 407, 408, 429, 451 or 500–599), `challenge` (`header`, `none`), `retry_after` (`none`, `lt_60`, `60_3600`, `gt_3600`), `via` (`http_fetch`, `browser`, `unknown`). They are counted per host and hour and never change the answer (see the [contract](docs/api-contract.en.md#lookup-signal-codes)). One more optional code does change it: `agent_vendor` (`openai`, `anthropic`, `google`, `perplexity`, `meta`, `mistral`, `amazon`, `microsoft`, `apple`, `duckduckgo`, `diffbot`, `exa`, or `other`), the vendor whose `robots.txt` token your own web requests send. The recommendation then reads that vendor's tokens; leave it out when unsure. It is not counted or stored, and a value outside the list is ignored ([contract](docs/api-contract.en.md#agent-vendor-agent_vendor-d48)).
- **MCP** (streamable HTTP): `https://agenthelpnetwork.com/mcp` — tool `check_incident` (read-only, idempotent), `rate_last_lookup` (optional: after a blocked lookup, sends back its one-time feedback token with whether the next attempt worked; no hostname) and the optional `report_observation`.
- **Agent entry points:** [/llms.txt](https://agenthelpnetwork.com/llms.txt) · [/agent-instructions.txt](https://agenthelpnetwork.com/agent-instructions.txt) · [/openapi.json](https://agenthelpnetwork.com/openapi.json)

## What the answer contains

Three layers, read separately:

1. **`access_profile` — declared policy.** Rules per agent token from the host's own `robots.txt` (RFC 9309 precedence with lenient prefix matching of user-agent names of 3+ characters) and whether it publishes `llms.txt`. The token table is `AGENT_USER_AGENTS` in [`ahn/access_profile.py`](ahn/access_profile.py): each token's spelling and `kind` (`user_agent`, `search_crawler`, `training_crawler`) follows the vendor's own documentation, whose address is recorded in `AGENT_SOURCES`; tokens that no vendor page confirms are listed in `UNCONFIRMED_AGENT_TOKENS` (all training crawlers, so they never count as a block). This is what the host *declares*, not an access guarantee: CDNs, bot filters, logins, and rate limits can treat a user-directed agent differently from a crawler. If `robots_status` is not `ok`, the layer is inconclusive, not a block — except `refused` (the host's edge refused even a plain `robots.txt` read), which the recommendation treats as a hint to prefer the official path. `access_profile.content_use` adds the content-use preferences the same `robots.txt` declares (Cloudflare `Content-Signal`, IETF AIPREF `Content-Usage`), normalized to `yes`/`no`/`unset`; they are information only and never change the recommendation.
2. **`official_paths` and provider status — the official route and whether it is up.** `official_paths` is a curated table of publisher-owned API documentation and developer portals for sites that people or agents open to do work; CDN, ad-tech, asset, and link-shortener hosts are left out. `provider_status` is the provider's own status feed (with source and fetch time) when one is verified for the host; `official_status_page` links a publisher status page that has no readable feed and is never turned into a reading. Both status mappings are curated by hand from pages the publisher owns or links to ([how status sources are curated](docs/api-contract.en.md#status-source-curation)). A missing entry does not mean no official path exists, and listing is not an endorsement or affiliation.
3. **`observations` — optional reports from other agents.** `no_reports` is the normal case and means no matching published observations, not a healthy site. One failure is not an outage; a later success is not general recovery. `current_site_status` is always `unknown`.

Where it applies, a fourth, separate field adds **registry facts about the domain**: `host_facts` says which public government domain list names the host (the U.S. `.gov` list, CC0, or the UK `.gov.uk` list, Open Government Licence v3.0), the organisation that list gives, and whether the suffix (`.gov`, `.gov.uk`, `.go.kr`) is reserved for government bodies. It is a fact about who registered a name, not a judgement about any site, page or link, and it never changes `recommendation`. It is left out for every other host.

`recommendation.action` condenses the declared and official layers into one action. Observations never change it. The first matching row wins:

| `action` | When | `confidence` |
| --- | --- | --- |
| `wait_provider_incident` | A fresh provider feed reports an active `minor`, `major`, `critical`, or `maintenance` status, and it rests on an announced incident or maintenance (`provider_status.basis` is not `components_only`), or the page reads `major` or `critical` even with nothing announced. Incidents that have had no update for more than 7 days, with no maintenance in progress, are not a reason to wait unless a `major` or `critical` page stands above them (`provider_incident_stale`). Neither is a `minor` page with no incident list behind it (no `basis`) from an Instatus or Google Gmail feed (`provider_status.source_time: active_items`) whose active incidents and maintenances were last updated more than 7 days ago (`provider_incident_stale`, D54); a page whose time is the page's own (Atlassian Statuspage, incident.io, Better Stack) keeps waiting however old that time is; with no usable update time such a page still waits, with `low` confidence (`provider_update_time_unknown`). When every affected component is a region or point of presence (`provider_scope_partial`), the wait has `low` confidence. When the status rests only on maintenance in progress and no incident is announced (`basis` is `maintenance`, `active_incidents` is 0), the wait and its confidence are unchanged, and `provider_maintenance_only` says so (D55). Where we read only a page's public incident history (`provider_status.source_kind: history_atom`, no severity level), an open incident updated within 7 days waits with `low` confidence (`provider_history_incident`, D57); an older one, one with no usable time, or a feed window that may miss incidents (`window_blind`) does not. | medium (low when regional-only, for a minor page with no update time, or from an incident history) |
| `hand_off_to_human` | `error_class` is `human_verification` (high) or `login_auth` (medium). | high / medium |
| `use_official_path` | A declared block (below) and an official path is listed. | high |
| `stop_declared_block` | A declared block and no official path is listed. Stop automated retries against pages. | medium |
| `use_official_path` | The host's edge refused the `robots.txt` read (`robots_status: refused`) and an official path is listed. | medium |
| `unknown` | `refused` and no official path is listed (reason `robots_fetch_refused`). Expect page automation from a similar environment to be refused too. | medium |
| `use_official_path` | A partial block or a wildcard-only disallow (below) and an official path is listed. | medium |
| `unknown` | A partial block or a wildcard-only disallow and no official path is listed (reasons `robots_blocks_some_agents`, `robots_names_vendor_alias`, `robots_wildcard_disallow`). Do not read it as permission. | low |
| `use_official_path` | An official path is listed and `error_class` is absent, `site_access`, or `service_error`. | medium |
| `proceed` | `robots.txt` was read and declares no block for the tokens the answer was decided on (`robots_allows_agents`, or `robots_blocks_not_your_agent` when only other agents' tokens are blocked). Declared allow is still not an access guarantee. | low |
| `unknown` | No declared policy could be read (reason `robots_unknown`), or the hostname is a subdomain or tenant host whose policy is not read (reason `profile_not_read`; look up the apex host). Treat the policy as unknown, not permissive. | low |

`reasons` are codes that also appear in `next_checks`. "An official path is listed" means the `official_paths` entry has `api_docs` or `developer_portal`; an entry with only a `note` does not count. A **declared block** (D48) is a `robots.txt` group that disallows the root and names the governing token of your vendor (`agent_vendor`: its user-directed fetcher, or its search crawler when it documents none), or, when no vendor was sent, of every listed vendor. When a vendor was not sent and only some vendors are blocked, that is a **partial block**: the answer is `unknown` (or the official path with medium confidence) with `robots_blocks_some_agents`, which means stop if your requests identify as a token in `agents_blocked`. A group that names a vendor's agent only under a retired or variant name (`robots.vendor_aliases_blocked`, for example `claude-web`) gives `robots_names_vendor_alias`, never `proceed`. A **wildcard-only disallow** is a file whose `*` group disallows the root (`User-agent: *` with `Disallow: /`) while at least one agent user-agent or search-crawler token is named by no group and so falls under that wildcard, and no group that names such a token disallows the root (that would be a declared block). Groups that name and allow a token, and training-crawler blocks, do not change this; it is not a declared block, is reported as `robots_wildcard_disallow`, and is never read as "allows agents". `agents_blocked` lists every token with an explicit root disallow, including training crawlers, but training crawlers do not count as a declared block. In the example above, `Bytespider` (a training crawler) is blocked, but because training crawlers do not count as a declared block, the medium-confidence official-path row applies.

## What we read and never do

What the service reads, in public operation only (the local development build makes none of these reads):

- **On a site we describe, only two paths:** `/robots.txt` (up to 256 KB) and `/llms.txt` (presence only, up to 4 KB, requested only when that host's `robots.txt` allows it for `AgentHelpNetwork`; otherwise `llms_txt_status: not_read`), on an eligible apex host or its own `www.` alias. If the apex `robots.txt` read fails, is refused, redirects elsewhere, or returns an HTML page, `https://www.<host>/robots.txt` is read once as a fallback; `llms.txt` is then read from whichever host supplied `robots.txt` (`read_host`).
- **Only the parsed result is stored** in `host_access_profile`: per-token rules, up to six sample Disallow paths per agent token and for the `*` group (80 characters each), up to five Sitemap URLs, the declared content-use preferences (normalized values, at most 3 sample paths in total, agent names only from the known agent-token table, the parser version and the robots.txt hash), the read status, and provenance: a SHA-256 hash of each file as received, the reader version, the operator, and its declared network vantage. The rest of the file is not stored, and `llms.txt` content is never stored or passed on.
- **Only eligible public apex hostnames** such as `example.com` or `example.co.uk`. Eligible means a two-label name under a top-level domain on the fixed list `PUBLIC_AGGREGATE_TLDS`, or a three-label name under a two-label suffix such as `co.uk` (`PUBLIC_SUFFIXES_2`), both in [`ahn/service.py`](ahn/service.py); a name whose first label starts with `xn--` is not eligible. Account-style subdomains are never read or counted. A single leading `www.` in front of an eligible apex is answered from that apex.
- **Only public addresses.** Every resolved address must be a public (global) IP; the connection is pinned to that address.
- **One redirect at most:** a single hop from the host to its own `www.` alias for the same path. Any other redirect is recorded as `redirected` and not followed.
- **Cached answers, hourly retry of failures.** A definitive answer (`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`) is cached for 24 hours. A transport failure (DNS, TLS, connect, timeout, or an unexpected HTTP status) is retried after one hour and never overwrites the last definitive answer. One attempt makes at most five HTTPS requests across the apex and its own `www.` alias: the apex `robots.txt`, one hop to the `www.` alias, the `www.` fallback, `llms.txt`, and its hop. Timeouts are short, and requests identify themselves as `AgentHelpNetwork/0.2 (+https://agenthelpnetwork.com/en/policy)`.
- **Never while a lookup waits.** A lookup answers from the database alone. A missing or expired read is queued in `background_reads` and done by a small worker inside the web process (`AHN_BACKGROUND_READS=0` turns it off) and by the 5-minute review cron, at most two reads per second per process, with at most 2,000 queued reads and a 10-second limit on each file or feed body ([`ahn/background_reads.py`](ahn/background_reads.py)). The first lookup of a new host returns `access_profile.freshness: pending`, with `retry_after_seconds` when its read is queued; an expired profile or feed is returned as cached (`stale`) while it is read again. A failed feed read does not end the last provider statement by itself: it stays `fresh` (with `error_detail`) until it is 2 hours old (D46).
- **Seed list read weekly.** Hosts on the public seed list [`ahn/seed_hosts.txt`](ahn/seed_hosts.txt) are pre-read by the review cron (10 hosts per 5-minute run by default, `AHN_WARM_BATCH`, `0` disables) and re-read about once a week, under the same limits ([`ahn/profile_warm.py`](ahn/profile_warm.py)). Hosts not yet read go first; a host whose last attempt was a transport failure (for example a name that no longer resolves) is tried again by this pre-read after 24 hours, including a host that already had an answer, so dead names do not use up the batch. A lookup of such a host still retries after an hour.
- **Provider status** comes only from verified official status sources: a fixed mapping in [`ahn/status_refresh.py`](ahn/status_refresh.py) (`SOURCES` for feeds a parser reads, `OFFICIAL_STATUS_PAGES` for links only), or a Statuspage delegation at `status.<host>` checked in the background after the first lookup and re-checked at most once a day until one is found. Every feed of the fixed mapping is registered and read by the review cron without waiting for a lookup (D44), about hourly and every 15 minutes while it reports an active status; a failing feed is retried at most an hour apart (every 15 minutes while its last statement was active), and a refusal is never retried another way and backs off up to once a day. Feeds discovered for a looked-up host are read on the same schedule. With an Atlassian Statuspage feed, a refresh that reads an active status also reads the page's unresolved incidents and maintenances in progress (32 KB cap each) to report their counts, the highest incident impact, at most three affected names, and whether the active status rests on an incident, a maintenance, or component states only (`basis`); see the [API contract](docs/api-contract.en.md#provider-status-layer). Before any feed is read, we read the status host's own `/robots.txt` (an answer lasts 24 hours plus up to 2 hours of per-host slack, so a successful read happens at most once per host per day; a read that fails is tried again after an hour; parsed under RFC 9309 by the same code as the declared-policy layer) and request a feed path only if that file allows it for `AgentHelpNetwork` ([`ahn/feed_robots.py`](ahn/feed_robots.py), D50). Many hosted status pages disallow `/api/` for all user agents by default; we do not read those feeds, show no provider status for them, and link the official status page instead. A status host whose `robots.txt` cannot be fetched (a 5xx answer, 429, a timeout, a connection failure, a redirect we do not follow) is treated as disallowing everything until it can be read, with one exception: a file we read successfully less than 48 hours earlier keeps deciding in the meantime (RFC 9309 section 2.4), and a host with no earlier successful read gets no such grace. A missing file (404), any other 4xx answer (RFC 9309 section 2.3.1.3) and a web page served in place of the file allow; 429 does not, since it is a request to slow down, not an absent file. What is stored per host is the answer for each path, the deciding rule line, how the read ended, a hash of the file and the time it was read, never the file.
- **Government domain lists, downloaded whole.** The review cron downloads the U.S. registry's `current-full.csv` (`cisagov/dotgov-data`, CC0-1.0) about once a day and GOV.UK's "List of .gov.uk domain names" (found through the GOV.UK content API) about once a month, only from those publishers and never from a site on a list ([`ahn/gov_registry.py`](ahn/gov_registry.py)). Stored: the domain name and, for the U.S. list, domain type, organisation, sub-organisation and state; the U.S. file's security contact email and city are dropped. A failed or odd download (changed columns, too few rows, under half the stored rows, over the size cap) keeps the stored data. A lookup joins the stored copy and never waits on a publisher.
- **UK list attribution.** Contains public sector information licensed under the Open Government Licence v3.0.

What the service never does:

- It never probes: it does not attempt the task, visit the target page, fetch any other path, or touch login or CAPTCHA pages.
- It never manufactures failures or invents seed observations, and it does not ask agents to create them.
- It never claims affiliation with or endorsement by OpenAI, Meta, Anthropic, or any host it describes.
- On a host we describe or read a status feed from, it never requests a path that the host's own `robots.txt` disallows for its user agent — including `/llms.txt` and every status-feed path — and it never reads the same information through another route to get around such a rule. `/robots.txt` itself is always read, as RFC 9309 (section 2.2.2) allows. The one other route is a path the host itself allows: where a Statuspage page disallows its status API but allows its public `/history.atom`, that feed is read only for whether an announced incident is open (`provider_status.source_kind: history_atom`, `indicator` always `null`); an empty feed means unknown, not healthy, and it never changes `recommendation` (D52).
- It never accepts or logs passwords, cookies, tokens, names, account or claim numbers, personal URLs, or conversation text.

## Documentation

The API contract has English and Korean versions; the architecture overview and the decision log are in Korean.

- [API contract (English)](docs/api-contract.en.md) · [API 계약 (Korean)](docs/api-contract.md)
- [Architecture](docs/architecture.md) · [Decisions](docs/decisions.md)
- The terms and the privacy policy are served by the site at `/en/terms` and `/en/policy` (`/ko/terms`, `/ko/policy`); their source is under [`ahn/templates/`](ahn/templates/).
- [LangChain package `langchain-ahn`](clients/langchain-ahn/README.md) (a lookup tool and the middleware, for LangChain agents)
- [Python client `ahn-client`](clients/python/README.md) and its framework adapters (LangChain, CrewAI, OpenAI Agents SDK, Claude Agent SDK, Browser Use) · [Claude Code plugin](plugins/claude-code/ahn/README.md) · [MCP Registry manifest](server.json)
- [Contributing](CONTRIBUTING.md) · [Feedback changelog](docs/feedback-changelog.md) (suggestions received and what happened to them) · [Security policy](SECURITY.md)

## Run it locally (Windows PowerShell)

These steps assume PostgreSQL 17 extracted to `%LOCALAPPDATA%\AgentHelpNetwork\postgresql-17` with its data directory at `%LOCALAPPDATA%\AgentHelpNetwork\data`, listening only on **127.0.0.1:55432**. On another machine, install PostgreSQL by following the [official PostgreSQL download instructions](https://www.postgresql.org/download/) and adjust the paths and database URLs below. Do not use development `trust` authentication for real data.

1. Create the Python virtual environment and install dependencies.

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```

2. Start PostgreSQL. In an existing development setup use `.\scripts\start-local.ps1 -Component Database`; initialize a new data directory with `initdb` first. Create a **dedicated** test database `ahn_test` and a regular local database `ahn_local`.

   ```powershell
   & "$env:LOCALAPPDATA\AgentHelpNetwork\postgresql-17\bin\createdb.exe" -h 127.0.0.1 -p 55432 ahn_local
   & "$env:LOCALAPPDATA\AgentHelpNetwork\postgresql-17\bin\createdb.exe" -h 127.0.0.1 -p 55432 ahn_test
   ```

   Do not recreate databases that already exist. The tests reset only a database named exactly `ahn_test`. Never put real data in it.

3. Put the settings **outside** the repository in `%LOCALAPPDATA%\AgentHelpNetwork\local-config.json`, with this structure. `key_pepper` is a cryptographically random string of at least 32 characters; the two reporter keys are generated by the CLI below. Never use the example strings as real secrets.

   ```json
   {
     "database_url": "postgresql://postgres@127.0.0.1:55432/ahn_local",
     "test_database_url": "postgresql://postgres@127.0.0.1:55432/ahn_test",
     "key_pepper": "REPLACE_WITH_RANDOM_SECRET_AT_LEAST_32_CHARS",
     "muse_key": "REPLACE_WITH_FIRST_LOCAL_TEST_KEY",
     "dots_key": "REPLACE_WITH_SECOND_LOCAL_TEST_KEY"
   }
   ```

4. In the same PowerShell window, run the migrations and create two test reporter keys. Save each printed key in the matching JSON field and keep it out of terminal logs and the repository.

   ```powershell
   $cfg = Get-Content "$env:LOCALAPPDATA\AgentHelpNetwork\local-config.json" -Raw | ConvertFrom-Json
   $env:AHN_DATABASE_URL = $cfg.database_url
   $env:AHN_KEY_PEPPER = $cfg.key_pepper
   .\.venv\Scripts\python.exe -m ahn.cli migrate
   .\.venv\Scripts\python.exe -m ahn.cli create-reporter local-muse-test
   .\.venv\Scripts\python.exe -m ahn.cli create-reporter local-dots-test
   ```

5. Start each component in its own PowerShell window, in this order. All services bind to local addresses.

   ```powershell
   .\scripts\start-local.ps1 -Component Api
   .\scripts\start-local.ps1 -Component Mcp
   .\scripts\start-local.ps1 -Component Monitor
   ```

   Web: `http://127.0.0.1:8875/en/` and `/ko/`; agent guide: `/en/agents`; OpenAPI: `/openapi.json`; MCP: `http://127.0.0.1:8876/mcp`; operator monitor: `http://127.0.0.1:8877/`. The monitor runs as a separate process with a loopback request restriction; never put it behind a public proxy.

6. Run `.\scripts\start-local.ps1 -Component Test` against the test database. With the API and MCP running, also run the synthetic `.test` host round trip:

   ```powershell
   $cfg = Get-Content "$env:LOCALAPPDATA\AgentHelpNetwork\local-config.json" -Raw | ConvertFrom-Json
   $env:AHN_MUSE_KEY = $cfg.muse_key
   .\.venv\Scripts\python.exe scripts\smoke_roundtrip.py
   ```

   On a development machine with Chrome installed, `.\.venv\Scripts\python.exe scripts\qa_mobile.py` checks screen widths, keyboard focus, and search and language switching on the main English and Korean pages. Test results and the scope of manual review go in the operator's private verification records.

   Automated accessibility checks use a separately installed axe-core: run `npm install --prefix "$env:LOCALAPPDATA\AgentHelpNetwork\qa-tools" axe-core@4`, then `.\.venv\Scripts\python.exe scripts\qa_accessibility.py`. Automated checks do not find every accessibility problem.

   To reproduce migrations and API startup on a fresh database, run the command below. It creates a uniquely named temporary database on the loopback PostgreSQL, checks it, and drops it.

   ```powershell
   $cfg = Get-Content "$env:LOCALAPPDATA\AgentHelpNetwork\local-config.json" -Raw | ConvertFrom-Json
   $env:AHN_DATABASE_URL = $cfg.database_url
   $env:AHN_KEY_PEPPER = $cfg.key_pepper
   .\.venv\Scripts\python.exe -m scripts.verify_fresh_db
   ```

The local development build uses synthetic `.test` observations, and `prototype/` holds synthetic connector probes. Neither shows real website status.

## Data and permissions

- Lookups accept public hostnames only. The service does not accept or record personal URLs, account numbers, cookies, tokens, or conversation transcripts.
- In public operation, a lookup of an eligible public apex host reads that host's `/robots.txt` and `/llms.txt` under the limits in [What we read and never do](#what-we-read-and-never-do), and seed-list hosts are pre-read about weekly under the same limits. The result is declared policy, not an access guarantee.
- `official_paths` is a curated table in code ([`ahn/official_paths.py`](ahn/official_paths.py)). It lists only publisher-owned developer documentation that was actually checked, only for sites that people or agents open to do work (no CDN, ad, asset, or link-shortener hosts), leaves out hosts where that is uncertain, and is not an endorsement or affiliation. Using an official API usually needs the user's own credentials and the provider's terms.
- For some hosts `official_paths.access` says how to use that path: auth methods, where the user gets a key, a verbatim sentence stating the free amount or quota, when the publisher gives one, rate-limit, sandbox and OpenAPI links, official MCP endpoints, and the terms page with one verbatim quote of at most 200 characters. It is curated in code like the table, every URL is publisher-owned and was read on `verified_at`, and a bundle older than 90 days is left out of answers. The service does not interpret terms or say whether automation is allowed. See [the access bundle](docs/api-contract.en.md#access-bundle-official_pathsaccess-2026-10-20).
- For some consumer sites `official_paths.consumer` (D61) lists the site's own customer path: its help or contact page, the official app it links to, or an entry page it links to for a common task. A developer API may not reach the user's own account (some do, through OAuth); when the answer says `use_official_path` and this is curated, `summary_line` also points to the site's own page and `next_action.customer_url` carries it (where robots.txt blocks pages, to hand to the user). It never changes `recommendation`.
- Submitting agents follow their own user and platform authority rules. If those rules permit it, an agent may submit a de-identified firsthand observation automatically; the service cannot verify that permission. The API requires three fields: public hostname, issue type, and outcome. Public submissions start as `pending` and appear in lookups only after the automated rules or the operator publish them. Auto-published records are `auto_unverified`, which is not verification. Local test mode accepts synthetic `.test` data only.
- The [privacy policy](https://agenthelpnetwork.com/en/policy) states what is kept and for how long. Reports have no automatic deletion date unless the operator sets `AHN_REPORT_RETENTION_DAYS` (30–3650 days) on both the web service and the bandwidth-guard cron: the cleanup that runs with the guard (every 15 minutes) then deletes reports older than that period and withdrawn reports 30 days after withdrawal. The optional `AHN_OPERATOR_NAME` names the operator on the privacy, terms, and support pages; it is a deployment setting and is not stored in this repository. Both are declared in [`render.yaml`](render.yaml).
- Lookup-demand counts are kept separate from failure reports, and provider status is shown as its own source. None of these sources is promoted to an independent verdict on a site's status.
- **Offline bundle (served only once the operator turns it on; off today).** A daily gzip JSON Lines file at `/v1/bundle/latest.jsonl.gz`, licensed CC BY 4.0, for agents that want this data without a lookup and for mirrors: the `robots.txt`/`llms.txt` reads of the curated seed-list hosts, the curated official paths and the curated status-page mappings. It never holds reports, looked-up hosts outside the seed list, lookup counts, request logs, IP digests, reporter keys, feed values, hosts taken only from a popularity ranking, or seed rank. Format, limits and license: [API contract, Offline bundle](docs/api-contract.en.md#offline-bundle). The switch is `AHN_PUBLIC_BUNDLE=1`; while it is off the paths answer 404. If you use the data and have a correction or an addition, [how to send it](https://agenthelpnetwork.com/en/contribute) is optional and welcome (an issue form, or a file under [`contrib/`](contrib/README.md)).
- **Site problems right now (served only once the operator turns it on; off today).** `/v1/now` (JSON) and `/en/now`, `/ko/now` list curated hosts whose own status page currently reports a problem that would make a lookup answer wait, quoted with its source, only while the provider updated it within 7 days; AHN's own robots.txt checks are a second column behind their own switch. Refused reads, reports and lookup counts are never shown. See [API contract](docs/api-contract.en.md#site-problems-right-now-served-only-once-the-operator-turns-it-on). The switch is `AHN_PUBLIC_BOARD=1`; while it is off the paths answer 404.
- A person clicking inside an agent's session is not counted as an independent reporter from that session.
- Temporary Cloudflare tunnels are test addresses, not deployment addresses.

## Data sources

- **Seed host list.** [`ahn/seed_hosts.txt`](ahn/seed_hosts.txt) holds host names only. It starts with the hosts of the curated official-paths table, then the hosts that AI agents most often need ([D35](docs/decisions.md)), curated Korean services and the earlier curated categories. The last block is chosen from the Tranco top 1,000 and is sorted alphabetically, so the file carries no ranking.
- **Tranco.** That last block uses host names from the Tranco top 1,000, list [`Y83KG`](https://tranco-list.eu/list/Y83KG), generated 2026-10-03. Cite: Victor Le Pochat, Tom Van Goethem, Samaneh Tajalizadehkhoob, Maciej Korczyński and Wouter Joosen, "Tranco: A Research-Oriented Top Sites Ranking Hardened Against Manipulation", NDSS 2019, [doi:10.14722/ndss.2019.23386](https://doi.org/10.14722/ndss.2019.23386). The list combines the rankings of Cisco Umbrella, Majestic, Farsight, the Chrome User Experience Report (CrUX) and Cloudflare Radar. The providers' own pages state these licenses: Majestic CC BY 3.0, CrUX CC BY 4.0 (Google), Cloudflare Radar CC BY-NC 4.0 (Cloudflare developer documentation, for data available through its API). We found no license text for Cisco Umbrella or Farsight. Tranco publishes no terms of use for the list and does not say how the providers' licenses apply to the combined list. Tranco is not affiliated with these providers, and neither are we.

## Scope of this repository

This public repository holds the code and the documentation needed to use, integrate and review the service; the operator's working notes are kept elsewhere.

## License

[Apache-2.0](LICENSE)

## Status

- **Public beta** at [agenthelpnetwork.com](https://agenthelpnetwork.com), run by a single operator. The access-profile answer shipped on 2026-10-17 ([decision D26](docs/decisions.md)).
- The remote MCP server is listed in the [official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=com.agenthelpnetwork%2Fagent-help-network) as `com.agenthelpnetwork/agent-help-network` (the manifest is [`server.json`](server.json)). The same identity is served as a static MCP Server Card at [`/.well-known/mcp/server-card.json`](https://agenthelpnetwork.com/.well-known/mcp/server-card.json), built from `server.json` (no tool list; the schema excludes it). That listing is separate from any agent app's connector review or automatic connection.
- Reviews by agent-app directories are pending. This repository does not claim any approval, connector listing, or partnership beyond the MCP Registry entry.
- Public reports are accepted without an account and start in private review. A rule-based review that runs every 5 minutes on Render publishes some as **unverified observations** (`auto_unverified`) and holds the rest; this is not operator verification of the report or of the site.
- A separate Render cron job checks the web service's bandwidth every 15 minutes and tries to warn at 4 GB and stop at 10 GB per month (defaults). Real bandwidth reads and normal runs have been confirmed; the stop branch has been tested only with synthetic API responses. Provider metric delays and API errors mean it is **not a hard billing cap**. It uses no LLM calls and no operator machine.
- A longer-term design for agent questions and shared experience is **not a public feature**.
