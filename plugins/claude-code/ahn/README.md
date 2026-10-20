# Agent Help Network plugin for Claude Code

When a web step fails in Claude Code — a `WebFetch` that returns 403, a login wall, a CAPTCHA, a 5xx, a failed browser tool — this plugin looks the target **hostname** up on [Agent Help Network](https://agenthelpnetwork.com/en/agents) and adds one short line to Claude's context:

```text
AHN example.com: recommendation=use_official_path (high). Declared policy: ok; blocked agents: ChatGPT-User, Claude-User (+1 training crawler); llms.txt: absent. Official path: https://developer.example.com/apis. Provider status: not connected. Reports: no_reports. Next: response_observed, robots_blocks_agents. Full JSON: https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com&error_class=site_access&failure_stage=response_observed. Declared policy is not an access guarantee; bypassing site controls is not advised.
```

That line is illustrative: it was produced from a made-up answer with `AHN_HOOK_FAKE_RESPONSE`, not from any real site's files.

The answer says what the host declares agents may do (its own robots.txt and llms.txt), where the official developer path is, what the provider's status feed says, how many other agents reported problems, and one recommended action: `proceed`, `use_official_path`, `hand_off_to_human`, `wait_provider_incident`, `stop_declared_block`, or `unknown`.

Agent Help Network is an independent project. It is not an official service of Anthropic or of any site it describes, declared policy is not an access guarantee, and the plugin never helps bypass a site's controls.

The hook is read-only: it sends a hostname and enum codes and reports nothing. The bundled MCP server is not read-only: besides `check_incident` it also offers `report_observation`, a write tool that submits an observation to Agent Help Network. Claude should call it only with the user's explicit approval for that submission, never automatically; the hook and the skill never call it.

## Contents

| File | Purpose |
| :- | :- |
| `.claude-plugin/plugin.json` | Manifest: plugin `ahn`, version 0.2.1 |
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
| `PostToolUse` | `WebFetch` | The fetch completed but the server answered with a status that reports a block or an outage: 401, 403, 407, 429, 451 or 5xx (see [Which errors are looked up](#which-errors-are-looked-up)). WebFetch reports these as a successful call with `tool_response.code`, so `PostToolUseFailure` never sees them |

That covers, for example, `mcp__Claude_Browser__navigate` (desktop browser pane), `mcp__remote-devices__Claude_Browser__navigate` (the browser pane reached from a cloud session), `mcp__claude-in-chrome__navigate`, `mcp__playwright__browser_navigate`, `mcp__plugin_playwright_playwright__browser_navigate` and `mcp__fetch__fetch`. It does not cover a browser step that "succeeds" while showing a block or CAPTCHA page, or `curl` run through Bash; use the `ahn-lookup` skill or the MCP tool by hand there.

## What it sends

At most one request per failure:

```text
GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<host>&error_class=<class>[&failure_stage=<stage>]
User-Agent: ahn-claude-code-plugin/0.2
```

- `site_host` is the bare lowercase hostname taken from `tool_input.url` (for MCP tools, also `uri`, `href`, `link`, `page_url` or `target_url`; only `http(s)://` or scheme-less host values), or else from the first `http(s)://` URL in the error text, or else, for `WebFetch` only, from Claude Code's own refusal `is unable to fetch from <host>`. No other bare dotted word in an error is read as a hostname (`document.querySelector`, `locator.click`, `urllib3.exceptions...`, Windows path segments). Scheme, userinfo, port, path, query and fragment are dropped, and a leading `www.` is removed.
- `error_class` is one of `human_verification`, `login_auth`, `service_error`, `site_access`; `failure_stage` is `before_target_response`, `response_observed`, or omitted. Both are derived locally from the HTTP status or from the error text with URLs reduced to hostnames, so a path or query never influences them. There is no default class: an error that is neither a block nor an outage sends nothing (next section).
- Nothing else leaves your machine: no URL paths or queries, no page content, no error text, no conversation text, no credentials, cookies or account names, no session or user identifiers.

### Which errors are looked up

A mistyped address is not a site that refuses agents, so only statuses that report a block or an outage are looked up (RFC 9110 sections 15.5 and 15.6, RFC 6585, RFC 7725). The status table applies to `WebFetch` results; the same numbers and wording in an error text classify the same way.

| HTTP status | `error_class` sent | Note |
| :- | :- | :- |
| 401, 407 | `login_auth` | credentials required |
| 403, 451 | `site_access` | `human_verification` instead when a `cf-mitigated: challenge` header or the page text (CAPTCHA, "verify you are human", "checking your browser") points to a challenge |
| 429 | `service_error` | the contract has no rate-limit class; a `Retry-After` of whole seconds, when the tool result carries one, is added to the line as `HTTP 429, the site asks to retry after N s.` and is not sent anywhere |
| 500–599 | `service_error` | the server failed |
| every other 4xx (400, 404, 405, 409, 410, 411, 413, 414, 415, 422 ...) | none: no lookup, nothing added | the address or the request was wrong, not blocked |

For thrown errors (`PostToolUseFailure`), text that names none of a block, a login, a challenge, a timeout or a 5xx, such as `Not Found`, `404` or `422 Unprocessable`, is not looked up either. Nothing received from the site at all (DNS failure, refused connection, Claude Code's own `is unable to fetch from` refusal) is still sent as `site_access` with `before_target_response`.

It sends nothing when:


- the host is local, private or special-use by name: `localhost`, IP literals including short and hex forms such as `127.1` or `0x7f.1`, single-label names, and names under `.local`, `.internal`, `.test`, `.lan`, `.arpa`, `.onion`, `.invalid`, `.example`, `.home`, `.corp`, `.intranet`, `.private`, `.localdomain`, `.alt` (nothing is resolved);
- the host is `agenthelpnetwork.com` or one of its subdomains;
- the host or a parent domain is listed in `AHN_HOOK_SKIP_HOSTS` (compared with and without a leading `www.` on both sides);
- an MCP tool failed without any URL in its input or error;
- the same host was looked up in the last 10 minutes;
- the response is a 4xx other than 401, 403, 407, 429 and 451, or the error text names no block and no outage;
- the tool call was interrupted (`is_interrupt`), or the tool was `WebSearch`;
- `AHN_HOOK_DISABLE=1` is set.

The lookup gets about 4 seconds total, DNS included: it runs in a background thread, and if no answer has arrived by then the hook prints nothing and exits 0 (each socket operation also has a 4-second timeout; Claude Code itself cancels the hook after 10 seconds). TLS certificates and hostnames are always verified, and redirects are never followed: any 3xx counts as no answer. Any error — network, TLS, timeout, redirect, a response over 256 KB, bad JSON — produces no output. The hook always exits 0, so it can never block or fail the tool call.

The answer is checked field by field against the [API contract](../../../docs/api-contract.en.md): every code must be one of the contract's enum values (`recommendation.action` and `confidence`, `robots_status`, `llms_txt_status`, `provider_status.indicator` and `freshness`, `evidence_level`, `next_checks[].code`), agent tokens must match `^[A-Za-z][A-Za-z0-9-]{1,31}$`, and an official path is shown only as an `https://` URL rebuilt from a public hostname and a path of at most 80 characters, without userinfo, port, query or fragment. Anything else becomes `unknown` or is left out, and the line is cut to 700 characters. From version 0.2.0 the hook accepts `pending` with `retry_after_seconds` and the `profile_pending` check: the service answers at once and reads the host's files in the background, and the line then says `pending (being read now; look up once more in N s, do not poll)`.

The hook never reports observations. It makes no POST requests and never calls `report_observation`.

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

`tests/test_plugin_hook.py` covers host extraction, the skip list, classification, the cache and its location, the exact request URL, response validation against the contract enums, the 256 KB cap, the total deadline, refused redirects (against a local server on 127.0.0.1), TLS verification, output length, the launcher, and the silent-failure paths, without network access beyond 127.0.0.1. Run it with `python -m pytest tests/test_plugin_hook.py -q`.

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
