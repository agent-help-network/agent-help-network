# Agent Help Network plugin for Claude Code

When a web step fails in Claude Code — a `WebFetch` that returns 403, a login wall, a CAPTCHA, a 5xx, a failed browser tool — this plugin looks the target **hostname** up on [Agent Help Network](https://agenthelpnetwork.com/en/agents) and adds one short line to Claude's context:

```text
AHN example.com: recommendation=use_official_path (high). Declared policy: ok; blocked agents: ChatGPT-User, Claude-User (+1 training crawler); llms.txt: absent. Official path: https://developer.example.com/apis. Provider status: not connected. Reports: no_reports. Next: response_observed, robots_blocks_agents. Full JSON: https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com&error_class=site_access&failure_stage=response_observed. Declared policy is not an access guarantee; bypassing site controls is not advised.
```

That line is illustrative: it was produced from a made-up answer with `AHN_HOOK_FAKE_RESPONSE`, not from any real site's files.

The answer says what the host declares agents may do (its own robots.txt and llms.txt), where the official developer path is, what the provider's status feed says, how many other agents reported problems, and one recommended action: `proceed`, `use_official_path`, `hand_off_to_human`, `wait_provider_incident`, `stop_declared_block`, or `unknown`. `proceed` means no block is declared, not that nothing blocked you. The hook only looks a host up after a failure and sends `error_class`; for a manual "is this site down?" check where nothing was refused, send `task_step=status_check`.

Agent Help Network is an independent project. It is not an official service of Anthropic or of any site it describes, declared policy is not an access guarantee, and the plugin never helps bypass a site's controls.

The hook is read-only: it sends a hostname and enum codes and reports nothing. The bundled MCP server is not read-only: besides `check_incident` it also offers `report_observation`, a write tool that submits an observation to Agent Help Network. Claude should call it only with the user's explicit approval for that submission, never automatically; the hook and the skill never call it.

## When a line is added

The plugin acts only after a web step has already failed, and only for a failure that reports a block or an outage. It never runs before a request and never changes a request.

| What happened | Line added? | What is sent to Agent Help Network |
| :- | :- | :- |
| `WebFetch` answered 401 or 407 | yes | hostname; `error_class=login_auth`; `http_status`; `via` |
| `WebFetch` answered 402, 403 or 451 | yes | hostname; `error_class=site_access` (`human_verification` when a `cf-mitigated: challenge` or `x-amzn-waf-action: challenge|captcha` header or the page text shows a challenge); `http_status`; `via` |
| `WebFetch` answered 408, 429 or a 5xx | yes | hostname; `error_class=service_error`; `http_status`; `via`; a `retry_after` band when the result carries a `Retry-After` header |
| A fetch or browser tool threw an error naming a block, a login, a challenge, a timeout or a 5xx | yes | hostname; the matching `error_class`; `failure_stage` |
| DNS failure, refused connection, or Claude Code's own `is unable to fetch from <host>` refusal | yes | hostname; `error_class=site_access`; `failure_stage=before_target_response` |
| Any other 4xx (400, 404, 405, 410, 422 ...), or an error text that names no block and no outage | no | nothing |
| The tool call was interrupted, the tool was `WebSearch`, the host is local or private, the same host was looked up in the last 10 minutes, or `AHN_HOOK_DISABLE=1` is set | no | nothing |
| A browser step that "succeeds" but shows a block or CAPTCHA page, or `curl` run through Bash | no (use the `ahn-lookup` skill or the MCP tool by hand) | nothing |

What is never sent: URL paths or queries, page content, error text, conversation text, credentials, cookies, account names, session or user identifiers, tool names. The hook makes no POST request and never calls `report_observation`. The precise rules are under [Triggers](#triggers), [What it sends](#what-it-sends) and [Which errors are looked up](#which-errors-are-looked-up).

## Privacy and data handling

- A lookup sends a public hostname and fixed enum codes to `https://agenthelpnetwork.com/v1/incidents/check`. There is no account, sign-in or API key.
- The service keeps coarse per-host counts and, for a short time, a keyed digest of the caller's IP address that is specific to one host and one day. It does not keep URLs, page content or conversation text. Retention periods are listed in the [privacy policy](https://agenthelpnetwork.com/en/policy).
- The plugin keeps a small cache of hostnames and lookup times from the last 10 minutes on your machine (see [On your machine](#on-your-machine)) and writes no logs.
- Privacy policy: <https://agenthelpnetwork.com/en/policy>. Terms: <https://agenthelpnetwork.com/en/terms>. Security reports: <https://github.com/agent-help-network/agent-help-network/blob/main/SECURITY.md>.
- Support, correction and deletion requests: <support@agenthelpnetwork.com> (also <https://agenthelpnetwork.com/en/support>).
- Source code and license: <https://github.com/agent-help-network/agent-help-network>, Apache-2.0.

## Contents

| File | Purpose |
| :- | :- |
| `.claude-plugin/plugin.json` | Manifest: plugin `ahn`, version 0.3.12 |
| `.claude-plugin/icon.png` | Listing icon (512 x 512 PNG, same design as the site icon) |
| `hooks/hooks.json` | Runs the lookup after failed web tools (see [Triggers](#triggers)) |
| `hooks/run-hook.sh` | Finds Python 3.7 or newer (`py -3` first on Windows, `python3` first elsewhere) and runs the hook; prints nothing and exits 0 without one |
| `hooks/ahn_lookup.py` | The hook. Python 3.7+ standard library only |
| `.mcp.json` | Remote MCP server `agent-help-network` at `https://agenthelpnetwork.com/mcp` (streamable HTTP, no auth). It provides the read-only `check_incident` tool and the `report_observation` write tool; use the latter only with the user's explicit approval, never automatically |
| `skills/ahn-lookup/SKILL.md` | When to look a host up by hand and how to read every field of the answer |

## Install

From your shell:

```bash
claude plugin marketplace add agent-help-network/agent-help-network
```

```bash
claude plugin install ahn@agent-help-network
```

Or inside a session: `/plugin marketplace add agent-help-network/agent-help-network`, then `/plugin install ahn@agent-help-network`. Start a new session (or run `/reload-plugins`) afterwards. To try it from a checkout without installing, start Claude Code with `claude --plugin-dir plugins/claude-code/ahn`.

Requirements: Python 3.7 or newer on `PATH` (`py`, `python3` or `python`) and a POSIX `sh`. On Windows that means Git for Windows (Git Bash), which Claude Code uses for hook commands; without Git Bash, Claude Code runs hook commands in PowerShell, `sh` is not found, and the hook shows a non-blocking hook error instead of a lookup.

## Triggers

| Event | Matcher | When it fires |
| :- | :- | :- |
| `PostToolUseFailure` | `^WebFetch$` and MCP tools with `browser`, `chrome`, `playwright`, `puppeteer` or `fetch` (either case of the first letter) in any segment of the name before the tool name, except the plugin's own `mcp__plugin_ahn_*` tools | The tool threw an error or an MCP tool returned an error result, for example `Claude Code is unable to fetch from <host>`, DNS failures, connection resets, browser navigation errors. An MCP failure is looked up only when its input has a URL field (`url`, `uri`, `href`, `link`, `page_url`, `target_url`) or its error text contains an `http(s)://` URL |
| `PostToolUse` | `WebFetch` | The fetch completed but the server answered with a status that reports a block or an outage: 401, 402, 403, 407, 408, 429, 451 or 5xx (see [Which errors are looked up](#which-errors-are-looked-up)). WebFetch reports these as a successful call with `tool_response.code`, so `PostToolUseFailure` never sees them |

That covers, for example, `mcp__Claude_Browser__navigate` (desktop browser pane), `mcp__remote-devices__Claude_Browser__navigate` (the browser pane reached from a cloud session), `mcp__claude-in-chrome__navigate`, `mcp__playwright__browser_navigate`, `mcp__plugin_playwright_playwright__browser_navigate` and `mcp__fetch__fetch`. It does not cover a browser step that "succeeds" while showing a block or CAPTCHA page, or `curl` run through Bash; use the `ahn-lookup` skill or the MCP tool by hand there.

## What it sends

At most one request per failure:

```text
GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<host>&error_class=<class>[&failure_stage=<stage>][&http_status=<code>][&challenge=<value>][&retry_after=<band>][&via=<value>]
User-Agent: ahn-claude-code-plugin/0.2
```

- `site_host` is the bare lowercase hostname taken from `tool_input.url` (for MCP tools, also `uri`, `href`, `link`, `page_url` or `target_url`; only `http(s)://` or scheme-less host values), or else from the first `http(s)://` URL in the error text, or else, for `WebFetch` only, from Claude Code's own refusal `is unable to fetch from <host>`. No other bare dotted word in an error is read as a hostname (`document.querySelector`, `locator.click`, `urllib3.exceptions...`, Windows path segments). Scheme, userinfo, port, path, query and fragment are dropped, and a leading `www.` is removed.
- `error_class` is one of `human_verification`, `login_auth`, `service_error`, `site_access`; `failure_stage` is `before_target_response`, `response_observed`, or omitted. Both are derived locally from the HTTP status or from the error text with URLs reduced to hostnames, so a path or query never influences them. There is no default class: an error that is neither a block nor an outage sends nothing (next section).
- From version 0.3.0 a lookup can also carry up to four more codes that describe the site's answer. The hook adds one only when the tool result it was handed already holds the value, and leaves it out otherwise:

  | Parameter | Values | Added when | How the hook decides |
  | :- | :- | :- | :- |
  | `http_status` | 401, 402, 403, 407, 408, 429, 451, 500–599 | the result is a `WebFetch` answer (`PostToolUse`) | `tool_response.code`. A status mentioned in an error text is never sent |
  | `challenge` | `header`, `none` | the result carries a non-empty `headers` object | `header` when a `cf-mitigated` header (a list value is read element by element, bytes are decoded) contains `challenge`, or, from version 0.3.8, when an `x-amzn-waf-action` header (AWS WAF) is `challenge` or `captcha` on a 403 or 451; else `none`. The AWS header on any other status is not a challenge. The page text is not used for this code |
  | `retry_after` | `none`, `lt_60`, `60_3600`, `gt_3600` | a 429 or 503 whose result carries a `headers` object | the `Retry-After` seconds as a band (under 60, 60 to 3600, over 3600); a date, a missing or an unreadable header is `none` |
  | `via` | `http_fetch`, `browser` | the tool's name says it | `WebFetch` and MCP tools whose server name has `fetch` in it: `http_fetch`; MCP tools whose server name has `browser`, `chrome`, `playwright`, `puppeteer`, `selenium`, `webdriver`, `devtools`, `firefox`, `camoufox` or `stagehand` in it: `browser`; any other tool: left out |

  Claude Code's `WebFetch` result today has no `headers` (it carries `code`, `codeText`, `bytes` and similar), so what the plugin actually sends for a WebFetch is `http_status` and `via`; `challenge` and `retry_after` are sent only for a tool whose result does carry headers. An empty or unreadable `headers` object counts as not seen and adds neither. For an MCP tool the name of the server (`mcp__<server>__<tool>`) is what `via` reads, so a `fetch_issue` tool on a GitHub server is not a fetch.

  These codes are counted per hostname and hour next to the error class. They never become a report and do not change the recommendation. The hook still sends nothing from the request or the page: no URL path or query, no page text, no header value (a header is only tested for the marker above or reduced to the band), no `Retry-After` seconds, no tool name.
- From version 0.3.2 a lookup that comes from Claude Code's own `WebFetch` also carries `agent_vendor=anthropic` (`WebFetch` identifies itself as `Claude-User`), so the service judges `robots.txt` for Anthropic's token instead of combining every vendor's. The hook sends it only when the tool is exactly `WebFetch`: an MCP browser or fetch tool may send any user agent, so for those the hook sends no vendor and the answer stays vendor-neutral (the service never reads a missing vendor as `anthropic`). `agent_vendor` is not one of the signal codes above: the service does not count or store it, it only chooses the robots part of the answer, and it stays in the line's `Full JSON` link so the link gives the same answer. The line then says `Answer for agent_vendor=anthropic (WebFetch).` before the `Full JSON` link, and the 10-minute "already looked up" memory is kept per host and vendor, so a failure of another tool on the same host is still looked up. The hook decides nothing about robots itself.
- Nothing else leaves your machine: no URL paths or queries, no page content, no error text, no conversation text, no credentials, cookies or account names, no session or user identifiers.

### Which errors are looked up

A mistyped address is not a site that refuses agents, so only statuses that report a block or an outage are looked up (RFC 9110 sections 15.5 and 15.6, RFC 6585, RFC 7725). The status table applies to `WebFetch` results; the same numbers and wording in an error text classify the same way.

| HTTP status | `error_class` sent | Note |
| :- | :- | :- |
| 401, 407 | `login_auth` | credentials required |
| 402 | `site_access` | payment required: a site that asks for payment to be crawled (for example [Cloudflare Pay per Crawl](https://developers.cloudflare.com/ai-crawl-control/features/pay-per-crawl/what-is-pay-per-crawl/)) has declared an access policy, and the lookup reports what the host declares. The plugin never suggests a way around a paywall |
| 403, 451 | `site_access` | `human_verification` instead when a `cf-mitigated: challenge` or `x-amzn-waf-action: challenge|captcha` header or the page text (CAPTCHA, "verify you are human", "checking your browser") points to a challenge |
| 408 | `service_error` | request timeout |
| 429 | `service_error` | the contract has no rate-limit class; a `Retry-After` of whole seconds, when the tool result carries one, is added to the line as `HTTP 429, the site asks to retry after N s.` Only its band (`retry_after`, see above) is sent, never the number of seconds |
| 500–599 | `service_error` | the server failed; a 503 with a `Retry-After` of whole seconds is added to the line the same way (`HTTP 503, the site asks to retry after N s.`) |
| every other 4xx (400, 404, 405, 409, 410, 411, 413, 414, 415, 422 ...) | none: no lookup, nothing added | the address or the request was wrong, not blocked |

For thrown errors (`PostToolUseFailure`), text that names none of a block, a login, a challenge, a timeout or a 5xx, such as `Not Found`, `404` or `422 Unprocessable`, is not looked up either. Nothing received from the site at all (DNS failure, refused connection, Claude Code's own `is unable to fetch from` refusal) is still sent as `site_access` with `before_target_response`.

It sends nothing when:


- the host is local, private or special-use by name: `localhost`, IP literals including short and hex forms such as `127.1` or `0x7f.1`, single-label names, and names under `.local`, `.internal`, `.test`, `.lan`, `.arpa`, `.onion`, `.invalid`, `.example`, `.home`, `.corp`, `.intranet`, `.private`, `.localdomain`, `.alt` and the cluster-internal `.svc`, `.consul`, `.cluster` (nothing is resolved);
- the host name carries an IPv4 address, since it stands for an address: a name under `nip.io`, `sslip.io` or `xip.io`, or any name with four numbers written into it with dots or dashes (`10-0-0-1.example.com`, `ec2-10-0-0-1.compute.example.com`). `v1.2.3.example.com` and `1-2-3.example.com` have fewer than four numbers and are still looked up;
- the URL contains a backslash (`https://evil.com\@good.com`): a browser reads the backslash as a slash and goes to `evil.com`, while a URL parser reads `good.com`. With two readings possible, no host is looked up;
- the host is `agenthelpnetwork.com` or one of its subdomains;
- the host or a parent domain is listed in `AHN_HOOK_SKIP_HOSTS` (compared with and without a leading `www.` on both sides);
- an MCP tool failed without any URL in its input or error;
- the same host was looked up in the last 10 minutes;
- the response is a 4xx other than 401, 402, 403, 407, 408, 429 and 451, or the error text names no block and no outage;
- the tool call was interrupted (`is_interrupt`), or the tool was `WebSearch`;
- `AHN_HOOK_DISABLE=1` is set.

The lookup gets about 4 seconds total, DNS included: it runs in a background thread, and if no answer has arrived by then the hook prints nothing and exits 0 (each socket operation also has a 4-second timeout; Claude Code itself cancels the hook after 10 seconds). TLS certificates and hostnames are always verified, and redirects are never followed: any 3xx counts as no answer. Any error — network, TLS, timeout, redirect, a response over 256 KB, bad JSON — produces no output. The hook always exits 0, so it can never block or fail the tool call.

The answer is checked field by field against the [API contract](https://github.com/agent-help-network/agent-help-network/blob/main/docs/api-contract.en.md): every code must be one of the contract's enum values (`recommendation.action` and `confidence`, `robots_status`, `llms_txt_status`, `provider_status.indicator` and `freshness`, `evidence_level`, `next_checks[].code`), agent tokens must match `^[A-Za-z][A-Za-z0-9-]{1,31}$`, and an official path is shown only as an `https://` URL rebuilt from a public hostname and a path of at most 80 characters, without userinfo, port, query or fragment. Anything else becomes `unknown` or is left out, and the line is cut to 700 characters. From version 0.2.0 the hook accepts `pending` with `retry_after_seconds` and the `profile_pending` check: the service answers at once and reads the host's files in the background, and the line then says `pending (being read now; look up once more in N s, do not poll)`.

The hook never reports observations. It makes no POST requests and never calls `report_observation`.

### Next codes the line can carry

The `Next:` part of the line shows up to two `next_checks` codes, as the service sent them. The hook accepts every code of the contract's `next_checks` list. The first five were added to the list after 0.2.1 and are accepted from version 0.2.2; the two provider codes were added after 0.2.2 and are accepted from version 0.2.3; `provider_incident_stale` was added after 0.2.4 and is accepted from version 0.2.5; `robots_blocks_some_agents`, `robots_blocks_not_your_agent` and `robots_names_vendor_alias` came with the vendor-aware robots rule (D48) and are accepted from version 0.3.1 (their wording below is the contract's); `provider_update_time_unknown` came with D54 and is accepted from version 0.3.3; `provider_maintenance_only` came with D55 and is accepted from version 0.3.4. `llms_txt_status: not_read` (the host's `robots.txt` disallows `/llms.txt` for us or could not be read, so it was not requested) came with D56 and is accepted from version 0.3.5; an older hook shows it as `unknown`. `provider_history_incident` came with D57 and is accepted from version 0.3.6. An older hook drops a code it does not know from the line.

| Code | Meaning |
| :- | :- |
| `robots_grouping_differs_by_parser` | Information only. Parsers disagree on this `robots.txt`: Google-style parsers merge groups that the service keeps apart and usually read it more strictly, or read only the first name of a comma-separated User-agent line, which the service splits into every listed name. The recommendation uses the service's reading |
| `robots_blocks_some_agents` | This host's `robots.txt` explicitly disallows the root for some agent tokens (`access_profile.robots.agents_blocked`), and this answer does not know which agent you are. If your requests identify as one of those tokens, treat it as a declared block: stop retrying pages and prefer an official path. Otherwise the block is not about you. Send `agent_vendor` to get an answer for your agent |
| `robots_blocks_not_your_agent` | Some agent tokens are explicitly disallowed (`agents_blocked`), but not the tokens this answer was decided on (your vendor's governing tokens, or, without `agent_vendor`, every vendor's). Those blocks do not decide this answer. The host may not welcome some automation, so keep requests modest; this is not an access guarantee. If your fetcher sends a token the service does not list, check `robots.txt` for a group that names it |
| `robots_names_vendor_alias` | A group that disallows the root names your vendor's agent under a name your documented token does not match (for example a retired name, `robots.vendor_aliases_blocked`). It is not read as a declared block on your token, and not as permission either: prefer an official path and tell the user |
| `content_use_ai_input_declared_no` | The host's `robots.txt` declares `ai-input=no` (or AIPREF `ai-use=n`) for user-directed agents. This is a content-use preference, not an access rule; follow your operator's policy and tell the user if it matters |
| `government_registry_listed` | The domain appears in a public government domain list (U.S. `.gov`, UK `.gov.uk`). The list says who registered the domain; it does not vouch for every page or link on it |
| `government_suffix_restricted` | The registry limits registration under this suffix to government bodies (for example `.gov`, `.gov.uk`, `.go.kr`). That says who may register a domain, not that any page or link is vouched for |
| `government_list_version_missing` | The domain is not in the loaded version of the government list. A newly registered name can be missing; this is a fact about the list version, not a finding about the site |
| `provider_components_only` | The provider's page-level status shows an issue, but it has announced no incident or maintenance (`provider_status.basis` is `components_only`); only some components are marked. Check on the provider's status page whether a component you use is one of them. With a `minor` or `maintenance` page the recommendation is then not `wait_provider_incident`; with `major` or `critical` it still is |
| `provider_incident_stale` | The provider's active incidents have had no update for more than 7 days and no maintenance is in progress. An incident left open without updates is then not a reason for `wait_provider_incident`: if `provider_status.affected` names a service you use, wait for the provider; otherwise continue. A `major` or `critical` page above the worst incident's impact still waits. The same code is used for a `minor` page with no incident list from an Instatus or Google Gmail feed (`provider_status.source_time: active_items`) whose active incidents and maintenances were last updated (`provider_status.source_updated_at`) more than 7 days ago (D54) |
| `provider_update_time_unknown` | The provider's page shows `minor`, lists no incidents the service can read, and carries no update time it can use, so a current issue cannot be told from one left on the page. The answer still waits, with `low` confidence: check the date on the provider's status page, and continue if the issue is old or names no service you use (D54) |
| `provider_maintenance_only` | The provider has announced no incident: its active status rests only on maintenance in progress (`provider_status.basis` is `maintenance`, `active_incidents` is 0). The page-level indicator may still read `minor`, `major` or `critical`, and the service cannot tell from it whether anything besides the maintenance is wrong, so the answer still waits with unchanged confidence. Compare the maintenance window and affected services with your blocked step (D55) |
| `provider_history_incident` | The provider's status API is not read (its `robots.txt` disallows it, or it has not been read again yet); its public incident-history feed (`provider_status.source_kind: history_atom`) lists an open incident updated within the last 7 days. The feed gives no page status, severity or affected services, so the answer waits with low confidence: check the provider's status page for what is affected, and continue if it names no service you use (D57) |
| `provider_scope_partial` | The announced issue may reach only part of the provider: its affected components are all regions or points of presence, or the worst incident is less severe than the page-level indicator. Check whether `provider_status.affected` names a service you use. When every affected component is regional, a `wait_provider_incident` answer has `low` confidence |

### On the service side

A lookup of an eligible public apex hostname adds one to a coarse daily per-host lookup count and may cause Agent Help Network to read that host's own `/robots.txt` and `/llms.txt`. A definitive answer (`ok`, `absent`, `refused`, `redirected`, `invalid`, `too_large`, `not_public`) is cached for 24 hours; a transport failure (DNS, TLS, connect, timeout, unexpected HTTP status) is retried after one hour. One attempt makes at most five HTTPS requests across the apex and its own `www.` alias. See the [policy](https://agenthelpnetwork.com/en/policy). Hostnames under public suffixes can still be internal (for example `jenkins.yourcompany.com`); put such domains in `AHN_HOOK_SKIP_HOSTS`.

### On your machine

The hook keeps `ahn-hook-cache.json` in the plugin's data directory (`$CLAUDE_PLUGIN_DATA`, set by Claude Code), or else in a per-user directory: `%LOCALAPPDATA%\ahn` on Windows, `~/.cache/ahn` (created with mode 0700) elsewhere. It holds hostnames and lookup times from the last 10 minutes, at most 200 entries, pruned on every run, and is replaced atomically through a private temporary file in the same directory; a failed write removes that file, and a missing or corrupt cache never stops a lookup. It writes no logs.

## Configure and disable

| Setting | Effect |
| :- | :- |
| `AHN_HOOK_DISABLE=1` | The hook does nothing. The skill and MCP tool stay available |
| `AHN_HOOK_SKIP_HOSTS=corp.example.com,intranet.example.org` | Never look up these hosts or their subdomains. Entries may be written with or without `www.`, as URLs, or as `*.example.com` |
| `AHN_HOOK_DEBUG=1` | One line on stderr per run saying what the hook did (hostname and reason only). Claude does not see stderr |
| `AHN_HOOK_FAKE_RESPONSE=<path to JSON>` | Use that file as the answer instead of calling the service, for testing |

Set them in your shell or in the `env` block of Claude Code settings. To turn the whole plugin off, run `claude plugin disable ahn@agent-help-network` (or use `/plugin`); `claude plugin uninstall ahn@agent-help-network` removes it.

## Simulate the hook

From the repository root, without network access to the service:

```bash
echo '{"hook_event_name":"PostToolUse","tool_name":"WebFetch","tool_input":{"url":"https://www.nytimes.com/section/world"},"tool_response":{"code":403,"codeText":"Forbidden"}}' | AHN_HOOK_FAKE_RESPONSE=response.json python3 plugins/claude-code/ahn/hooks/ahn_lookup.py
```

`tests/test_plugin_hook.py` covers host extraction, the skip list, classification, the cache and its location, the exact request URL and the four optional codes (that no path, query, text or header value reaches it), response validation against the contract enums, the 256 KB cap, the total deadline, refused redirects (against a local server on 127.0.0.1), TLS verification, output length, the launcher, and the silent-failure paths, without network access beyond 127.0.0.1. Run it with `python -m pytest tests/test_plugin_hook.py -q`.

## Differences from the design contract

The plugin was specified in an internal design note on 2026-10-18 and adapted to the Claude Code documentation (hooks, plugins, plugin marketplaces) and to Claude Code 2.1.296 behaviour on that date:

1. **Events.** `PostToolUseFailure` exists and is used for thrown errors (top-level `error` and `is_interrupt` fields). A second hook on `PostToolUse` for `WebFetch` was added because WebFetch returns HTTP 4xx/5xx as a successful result (`tool_response` = `{code, codeText, url, ...}`), observed in this version; without it the most common case, a 403, never triggers the lookup. Since 0.2.1 only the block and outage statuses of the table above are looked up; before, every 400–599 was, and a 404 or 422 was sent as `site_access`.
2. **Matcher.** Claude Code matchers are case-sensitive, unanchored JavaScript regular expressions, and real tool names are `mcp__Claude_Browser__*`, `mcp__remote-devices__Claude_Browser__*`, `mcp__claude-in-chrome__*`, `mcp__playwright__*` and `mcp__plugin_<plugin>_<server>__*`. The matcher therefore looks for a keyword in any segment of an MCP name before the tool name (so `mcp__github__fetch_issue` is not matched), with both cases of each keyword's first letter, and excludes the plugin's own `mcp__plugin_ahn_*` tools. `WebSearch` was left out: it has no target host, its input is conversation text, and its failures are search-service failures, not site blocks. The script also ignores `WebSearch` payloads.
3. **Launcher.** Hook commands run through a shell (Git Bash on Windows), and `python3` does not reliably exist on Windows (it can be the Microsoft Store alias), so `hooks/run-hook.sh` picks the interpreter: `py -3` first on Windows, `python3` first elsewhere, each used only after it proves to be Python 3.7 or newer (an older Python would fail on the script's syntax and exit non-zero). `.gitattributes` keeps it LF on Windows checkouts.
4. **`www.`** A leading `www.` label is removed, because the service reads declared policy and lookup counts only for apex hostnames; `www.nytimes.com` would otherwise return no declared policy.
5. **`failure_stage`.** Text-based detection follows the contract, plus Claude Code's own refusal (`is unable to fetch from`, no request reaches the site) and `getaddrinfo` as `before_target_response`. On the `PostToolUse` path an HTTP status was received, so it sends `response_observed`.
6. **Classification.** Runs on the HTTP status (`PostToolUse`) or on the error text with every URL reduced to its hostname (`PostToolUseFailure`); text that matches no class gets none, there is no `site_access` default. Patterns accept common variants (`log in`/`log-in`, `sign-in`, `authentication`, `are you a robot`, `bad gateway`, `ETIMEDOUT`). Interrupted calls are skipped.
7. **Wording.** The closing sentence is a statement ("bypassing site controls is not advised") rather than an imperative, because the hooks documentation warns that imperative, system-style text in `additionalContext` can trigger prompt-injection defenses.
8. **Validation.** Only values from the contract's closed enums, agent tokens of at most 32 letters, digits and hyphens, and official-path URLs rebuilt from validated parts reach Claude's context; anything else becomes `unknown` or is dropped. A regular expression for "code-like" text was not enough: a spoofed or compromised server could fill every field with valid-looking instruction words. Blocked agents are listed only when `robots_status` is `ok`, `(stale)` marks a stale profile, and training crawlers are counted separately because the contract does not treat them as declared blocks.
9. **MCP type.** `.mcp.json` uses `"type": "http"`, Claude Code's name for streamable HTTP.
10. **Cache.** The host is recorded before the request, so an unreachable service is not retried for that host within 10 minutes. The cache lives in `$CLAUDE_PLUGIN_DATA` or a per-user directory, never in a shared temp directory, and is written through `mkstemp` and `os.replace`.
11. **Additions.** `AHN_HOOK_SKIP_HOSTS` for internal domains, compared with and without `www.` because the lookup host drops it.
12. **Host extraction.** A bare hostname is read from error text only in Claude Code's own refusal phrase. MCP failures use URL fields of the tool input and `http(s)://` URLs in the error, and are skipped when there is none, because browser and fetch errors are full of dotted code and path tokens.
13. **Deadline, redirects, TLS.** `urllib`'s timeout applies to each socket operation and not to DNS, so the whole lookup runs in a daemon thread with a 4-second join. The opener refuses every redirect, so an answer can never come from another URL or over plain HTTP, and uses its own verifying TLS context even if the process default was weakened.
14. **MCP write tool.** The bundled server is the production MCP endpoint, which also exposes `report_observation`. The plugin does not change the server; its descriptions say that only the hook is read-only and that `report_observation` needs the user's explicit approval and is never called automatically.

## Disclaimer

Lookups describe declared policy, official links and unverified reports. They are not proof of a site's current status, and `no_reports` does not mean a site works. See the [terms](https://agenthelpnetwork.com/en/terms).
