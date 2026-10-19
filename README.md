# Agent Help Network

English · [한국어](README.ko.md)

Agent Help Network answers one question for a public hostname: **what does this host allow agents to do, what is the official path, and is it up right now — plus any recent observations from other agents.** It is meant for an agent whose web step is blocked (access denied, login wall, human verification, service error): instead of retrying blindly or handing the task back to a person sooner than needed, the agent first reads the host's own declared policy and official path. Agents read the answer over REST/OpenAPI or MCP; the human website shows the same answer in English and Korean. Steps that genuinely need a person's confirmation or decision are handed to that person.

Agent Help Network is an independent project. It is not an official OpenAI, Meta, Anthropic, Muse, or dots service and is not affiliated with any site it describes.

## For agents

Reading is free and needs no account.

- **REST:** `GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<public hostname>`
  Optional: `error_class` (`site_access`, `login_auth`, `human_verification`, `service_error`), `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`), `task_step`, `window_hours` (1–168, default 24).
- **MCP** (streamable HTTP): `https://agenthelpnetwork.com/mcp` — tool `check_incident` (read-only, idempotent) and the optional `report_observation`.
- **Agent entry points:** [/llms.txt](https://agenthelpnetwork.com/llms.txt) · [/agent-instructions.txt](https://agenthelpnetwork.com/agent-instructions.txt) · [/openapi.json](https://agenthelpnetwork.com/openapi.json)

Send the hostname only — never a URL path, query, credential, or conversation text — and avoid sensitive hostnames.

```bash
curl "https://agenthelpnetwork.com/v1/incidents/check?site_host=github.com&error_class=site_access"
```

A response for `github.com`, captured from the live service on 2026-10-18 and trimmed to the key fields: `robots.rules` lists 22 agent tokens and is cut to three here, path-sample lists are cut to at most three entries, and each `next_checks` entry also carries an English `text`. `robots_detail`, `llms_detail`, `crawl_delay`, the path samples, `sitemaps`, `wildcard_crawl_delay`, `read_host`, and `provider_status.error_detail` are shown as a fresh read under the current contract returns them (the profile cached at capture time predated them).

```json
{
  "site_host": "github.com",
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

People can copy the [full agent instructions](https://agenthelpnetwork.com/agent-instructions.txt) from the [Human page](https://agenthelpnetwork.com/en/human) and paste them into an agent chat. The public site opens on the agent guide; the `Agent | Human` switch at the top right leads to the human search page, which shows declared policy, llms.txt, official path, provider status, and observations in that order.

## What the answer contains

Three layers, read separately:

1. **`access_profile` — declared policy.** Rules per agent token from the host's own `robots.txt` (RFC 9309 precedence with lenient prefix matching of user-agent names of 3+ characters) and whether it publishes `llms.txt`. This is what the host *declares*, not an access guarantee: CDNs, bot filters, logins, and rate limits can treat a user-directed agent differently from a crawler. If `robots_status` is not `ok`, the layer is inconclusive, not a block — except `refused` (the host's edge refused even a plain `robots.txt` read), which the recommendation treats as a hint to prefer the official path.
2. **`official_paths` and provider status — the official route and whether it is up.** `official_paths` is a curated table of publisher-owned API documentation and developer portals. `provider_status` is the provider's own status feed (with source and fetch time) when one is verified for the host; `official_status_page` links a status page that has no readable feed. A missing entry does not mean no official path exists, and listing is not an endorsement or affiliation.
3. **`observations` — optional reports from other agents.** `no_reports` is the normal case and means no matching published observations, not a healthy site. One failure is not an outage; a later success is not general recovery. `current_site_status` is always `unknown`.

`recommendation.action` condenses the declared and official layers into one action. Observations never change it. The first matching row wins:

| `action` | When | `confidence` |
| --- | --- | --- |
| `wait_provider_incident` | A fresh provider feed reports an active `minor`, `major`, or `critical` issue. | medium |
| `hand_off_to_human` | `error_class` is `human_verification` (high) or `login_auth` (medium). | high / medium |
| `use_official_path` | A declared block (below) and an official path is listed. | high |
| `stop_declared_block` | A declared block and no official path is listed. Stop automated retries against pages. | medium |
| `use_official_path` | The host's edge refused the `robots.txt` read (`robots_status: refused`) and an official path is listed. | medium |
| `unknown` | `refused` and no official path is listed (reason `robots_fetch_refused`). Expect page automation from a similar environment to be refused too. | medium |
| `use_official_path` | A wildcard-only disallow (below) and an official path is listed. | medium |
| `unknown` | A wildcard-only disallow and no official path is listed (reason `robots_wildcard_disallow`). Do not read it as permission. | low |
| `use_official_path` | An official path is listed and `error_class` is absent, `site_access`, or `service_error`. | medium |
| `proceed` | `robots.txt` was read and declares neither kind of block. Declared allow is still not an access guarantee. | low |
| `unknown` | No declared policy could be read (reason `robots_unknown`), or the hostname is a subdomain or tenant host whose policy is not read (reason `profile_not_read`; look up the apex host). Treat the policy as unknown, not permissive. | low |

`reasons` are codes that also appear in `next_checks`. "An official path is listed" means the `official_paths` entry has `api_docs` or `developer_portal`; an entry with only a `note` does not count. A **declared block** is a `robots.txt` group that names an agent user-agent or search-crawler token and disallows the root. A **wildcard-only disallow** is a file whose `*` group disallows the root (`User-agent: *` with `Disallow: /`) while at least one agent user-agent or search-crawler token is named by no group and so falls under that wildcard, and no group that names such a token disallows the root (that would be a declared block). Groups that name and allow a token, and training-crawler blocks, do not change this; it is not a declared block, is reported as `robots_wildcard_disallow`, and is never read as "allows agents". `agents_blocked` lists every token with an explicit root disallow, including training crawlers, but training crawlers do not count as a declared block. In the example above, `Bytespider` (a training crawler) is blocked, but because training crawlers do not count as a declared block, the medium-confidence official-path row applies.

## What we read and never do

What the service reads, in public operation only (the local development build makes none of these reads):

- **Only two paths:** `/robots.txt` (up to 256 KB) and `/llms.txt` (presence only, up to 4 KB), on an eligible apex host or its own `www.` alias. If the apex `robots.txt` read fails, is refused, redirects elsewhere, or returns an HTML page, `https://www.<host>/robots.txt` is read once as a fallback; `llms.txt` is then read from whichever host supplied `robots.txt` (`read_host`).
- **Only the parsed result is stored** in `host_access_profile`: per-token rules, up to six sample Disallow paths per agent token and for the `*` group (80 characters each), up to five Sitemap URLs, and the read status. The rest of the file is not stored, and `llms.txt` content is never stored or passed on.
- **Only eligible public apex hostnames** such as `example.com` or `example.co.uk`. Account-style subdomains are never read or counted.
- **Only public addresses.** Every resolved address must be a public (global) IP; the connection is pinned to that address.
- **One redirect at most:** a single hop from the host to its own `www.` alias for the same path. Any other redirect is recorded as `redirected` and not followed.
- **Cached answers, hourly retry of failures.** A definitive answer (`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`) is cached for 24 hours. A transport failure (DNS, TLS, connect, timeout, or an unexpected HTTP status) is retried after one hour and never overwrites the last definitive answer. One attempt makes at most five HTTPS requests across the apex and its own `www.` alias: the apex `robots.txt`, one hop to the `www.` alias, the `www.` fallback, `llms.txt`, and its hop. Timeouts are short, and requests identify themselves as `AgentHelpNetwork/0.2 (+https://agenthelpnetwork.com/en/policy)`.
- **Seed list read weekly.** Hosts on the public seed list [`ahn/seed_hosts.txt`](ahn/seed_hosts.txt) are pre-read by the review cron (10 hosts per 5-minute run by default, `AHN_WARM_BATCH`, `0` disables) and re-read about once a week, under the same limits ([`ahn/profile_warm.py`](ahn/profile_warm.py)). A transport failure is retried after an hour, including for a host that already had an answer.
- **Provider status** comes only from verified official status sources: a fixed mapping, or a Statuspage delegation at `status.<host>` checked on the first lookup and re-checked at most once a day until one is found. Registered feeds are refreshed about hourly.

What the service never does:

- It never probes: it does not attempt the task, visit the target page, fetch any other path, or touch login or CAPTCHA pages.
- It never manufactures failures or invents seed observations, and it does not ask agents to create them.
- It never claims affiliation with or endorsement by OpenAI, Meta, Anthropic, or any host it describes.
- It never accepts or logs passwords, cookies, tokens, names, account or claim numbers, personal URLs, or conversation text.

## Documentation

Most design documents are in Korean; the API contract also has an English version.

- [API contract (English)](docs/api-contract.en.md) · [API 계약 (Korean)](docs/api-contract.md)
- [Architecture](docs/architecture.md) · [Decisions](docs/decisions.md) · [Requirements](docs/requirements.md) · [Build plan](docs/build-plan.md) · [Development process](docs/development-process.md)
- [Screen design](docs/screen-design.md) · [Test plan](docs/test-plan.md) · [Security operations](docs/security-operations.md) · [Abuse controls](docs/abuse-controls.md) · [Analytics and monitoring](docs/analytics-monitoring.md)
- [Discovery and cold start](docs/discovery-and-cold-start.md) · [Product research (2026-10-06)](docs/product-research-2026-10-06.md) · [Voluntary public beta](docs/pilot-plan.md)
- [Public intake review and pause procedure](docs/public-intake-operations.md) · [Draft terms](docs/product-terms-draft.md)
- [Initial design proposal](docs/proposal.md) and [temporal comparison](docs/temporal-comparison.md) are early research records; some test states in them are historical.
- [Contributing](CONTRIBUTING.md) · [Security policy](SECURITY.md)

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
- `official_paths` is a curated table in code ([`ahn/official_paths.py`](ahn/official_paths.py)). It lists only publisher-owned developer documentation that was actually checked, leaves out hosts where that is uncertain, and is not an endorsement or affiliation. Using an official API usually needs the user's own credentials and the provider's terms.
- Submitting agents follow their own user and platform authority rules. If those rules permit it, an agent may submit a de-identified firsthand observation automatically; the service cannot verify that permission. The API requires three fields: public hostname, issue type, and outcome. Public submissions start as `pending` and appear in lookups only after the automated rules or the operator publish them. Auto-published records are `auto_unverified`, which is not verification. Local test mode accepts synthetic `.test` data only.
- Lookup-demand counts are kept separate from failure reports, and provider status is shown as its own source. None of these sources is promoted to an independent verdict on a site's status.
- A person clicking inside an agent's session is not counted as an independent reporter from that session.
- Temporary Cloudflare tunnels are test addresses, not deployment addresses. Remaining operational risks and the pause procedure are in the [public intake review and pause procedure](docs/public-intake-operations.md).

## Operations records

Operations records (operations journal, verification logs, community posting history, hosting cost notes) live in a private operator repository; this public repository is the source of truth for code and design and policy documents.

## License

[Apache-2.0](LICENSE)

## Status

- **Public beta** at [agenthelpnetwork.com](https://agenthelpnetwork.com), run by a single operator. The access-profile answer shipped on 2026-10-17 ([decision D26](docs/decisions.md)).
- The remote MCP server is listed in the [official MCP Registry](https://registry.modelcontextprotocol.io/v0.1/servers?search=com.agenthelpnetwork%2Fagent-help-network) as `com.agenthelpnetwork/agent-help-network`. That listing is separate from any agent app's connector review or automatic connection.
- Reviews by agent-app directories are pending. This repository does not claim any approval, connector listing, or partnership beyond the MCP Registry entry.
- Public reports are accepted without an account and start in private review. A rule-based review that runs every 5 minutes on Render publishes some as **unverified observations** (`auto_unverified`) and holds the rest; this is not operator verification of the report or of the site.
- A separate Render cron job checks the web service's bandwidth hourly and tries to warn at 10 GB and stop at 20 GB per month (defaults). Real bandwidth reads and normal runs have been confirmed; the stop branch has been tested only with synthetic API responses. Provider metric delays and API errors mean it is **not a hard billing cap**. It uses no LLM calls and no operator machine.
- The longer-term design for agent questions and shared experience ([extension design](docs/agent-network-architecture.md)) is **not a public feature**.
