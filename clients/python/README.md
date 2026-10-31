# ahn-client

A small Python client for [Agent Help Network](https://agenthelpnetwork.com/en/agents): when your browser automation (Playwright, Browser Use, Stagehand-style scripts, plain HTTP) is blocked, ask what the host **declares** agents may do (its own `robots.txt` and `llms.txt`), where its **official developer path** is, what the provider's **status feed** says, and get one recommended action.

**Agent Help Network is an independent project. It is not an official service of, and is not affiliated with, OpenAI, Meta, Muse, dots, Anthropic, or any site it describes.** `ahn-client` is read-only (it has no reporting function), has no dependencies beyond the Python standard library, and runs on Python 3.9+. The optional [framework adapters](#framework-adapters) need the framework they plug into, and with it that framework's Python version (at the versions tested, all four need Python 3.10 or newer, and Browser Use 3.11).

## Install

```bash
pip install ahn-client                       # no dependencies
pip install "ahn-client[langchain]"          # or [crewai], [openai-agents], [claude-agent-sdk]: see Framework adapters
```

## Use

```python
import ahn_client as ahn
answer = ahn.check("https://example.com/some/page", *ahn.classify("403 Forbidden"))
print(answer.summary() if answer else "no answer")
```

In an `except` block:

```python
try:
    page.goto(url)
except Exception as exc:
    error_class, stage = ahn.classify(exc)
    if error_class:  # None: a 404 or a bad request, not a block or an outage
        answer = ahn.check(url, error_class, stage, **ahn.signals(exc))   # a hostname and enum codes leave your machine
        if answer:
            print(answer.summary())            # the service's own advice first (0.6.0), e.g. "Wait instead of retrying ..."
            wait = answer.wait_seconds()       # seconds before one more lookup (provider wait or profile being read), or None
            if answer.next_action and answer.next_action.do == "use_official_path":
                print(answer.next_action.url)
```

Since 0.6.0 the answer keeps what the service already says about what to do now (D51), instead of rebuilding it from the six `recommendation` values:

```python
answer.summary_line        # the service's one sentence, or None (an older service)
answer.next_action         # NextAction(do, url, retry_after_seconds, stop_if_you_are, lookup_host), or None
answer.links               # {"feedback": ..., "changelog": ...}, or None
answer.remember            # one fixed sentence for the next task on this host, made from next_action alone, or None
answer.wait_seconds(site_retry_after)   # max(site's Retry-After, service's wait), computed here, at most 3600
```

`next_action.do` is one of `wait_for_provider`, `hand_off_to_human`, `use_official_path`, `stop_automation`, `proceed`, `retry_lookup_once`, `stop_if_listed`, `lookup_apex_host`, `no_clear_action`. `stop_if_listed` is not a plain `unknown`: if your requests send one of `next_action.stop_if_you_are`, stop retrying pages. `retry_after_seconds` is when to repeat the same lookup **once**, not a polling interval. After a stop, a hand-off or an official path (`stop_automation`, `stop_if_listed`, `hand_off_to_human`, `use_official_path`) `retry_after_seconds` is `None` and `wait_seconds()` returns `None`: there is nothing to wait for. `recommendation` is always the service's verdict. The site's own Retry-After is merged on your machine only, and only for the "come back later" answers; it is not sent to the service and does not change its answer. Against an older service, or with `AHN_RESPONSE_GUIDE=0`, the three keys are `None` and `summary()` is the line earlier versions gave.

Examples with a real `except` block and a decision per recommendation: [`examples/playwright_example.py`](https://github.com/agent-help-network/agent-help-network/blob/main/clients/python/examples/playwright_example.py), [`examples/browser_use_example.py`](https://github.com/agent-help-network/agent-help-network/blob/main/clients/python/examples/browser_use_example.py). Neither tries to get around a site's controls: they use the official path, hand off to the user, wait, or stop.

## Framework adapters

When a web tool fails inside an agent framework with a block or an outage (403, 429, a challenge, a login wall, 5xx, a timeout, a refused connection), an adapter reads the hostname of the call's URL, looks it up with `classify()` and `check()`, and adds one line (`Result.summary()`, at most 700 characters) to what the agent sees. Each adapter is an extra: the base install has no dependencies and imports no framework.

All four follow the same rules:

- They use only the extension point the framework's own documentation gives for a failed tool call.
- The original error is not hidden or changed (LangChain's optional `convert_exceptions=True` is the one stated exception), nothing is retried, and no workaround is tried. A failure that is not a block or an outage (404, 422, a typo in an address), a call without an http(s) URL, an excluded host, a failed lookup or one slower than the time limit (2 seconds) adds nothing, and the run goes on exactly as it would have.
- Only the hostname and enum codes are sent (see [What it sends](#what-it-sends)). The error text, URL path and query stay on your machine.
- The hostname comes from an http(s) URL in the call's arguments, or from the request the exception carries (httpx, requests, aiohttp, urllib). **The text of an error is never searched for URLs**, so an exception that only names a URL in its message, with no URL argument and no request, is not looked up (a message also links to help pages such as `errors.pydantic.dev` or `docs.mistral.ai`). The Claude Agent SDK hook is the exception: it reads its payloads by the Claude Code plugin's rules, which include the URL of a failed WebFetch. Tool-argument validation errors and errors with nothing web-like in them (a `PermissionError`, "Task blocked on mutex") are not looked up. Cluster-internal names (`.svc`, `.consul`, `.cluster`), address-embedding names (`10-0-0-1.sslip.io`), internationalized names that IDNA 2003 and 2008 read differently (`faß.de`) and URLs with a backslash are never looked up.
- A tool has to fail for the adapter to see it. A tool that returns the text of a 403 page as an ordinary result is not a failure to the framework; make it raise (`response.raise_for_status()`) or report an error.
- The same host is answered from the in-memory cache for 10 minutes, and each failing call gets the line again.

### LangChain v1: `wrap_tool_call` middleware

```python
from langchain.agents import create_agent
from ahn_client.langchain import ahn_tool_failure_middleware   # pip install "ahn-client[langchain]"

agent = create_agent(model, tools=[fetch_page], middleware=[ahn_tool_failure_middleware()])
```

Implements the documented [`wrap_tool_call` / `awrap_tool_call`](https://docs.langchain.com/oss/python/langchain/middleware/custom) hooks.

- A tool call that comes back as a `ToolMessage` with `status="error"` (a `ToolNode` with `handle_tool_errors`, `ToolRetryMiddleware` after its last attempt, or a tool that returns one) gets the line appended; the original content stays in front.
- A tool that **raises** is left alone: the exception is re-raised unchanged, so the run behaves exactly as it does without the middleware whether or not Agent Help Network answers. An exception cannot carry text to the model.
- `ahn_tool_failure_middleware(convert_exceptions=True)` also handles raised blocks and outages, always the same way and whatever the lookup returned: the exception becomes a `ToolMessage(status="error")` whose content is `Error: <exception class> (<error class>)` and the line when there is one. The exception's own text, URL, path and query are not put in the message. The run then goes on instead of stopping, which is why it is off by default.
- **If you already use `ToolRetryMiddleware`, list this middleware before it** (the first middleware listed is the outermost): `middleware=[ahn_tool_failure_middleware(), ToolRetryMiddleware()]`. Retries then run first and this middleware sees the final failure once. After it, with `convert_exceptions=True`, the first exception would become a message and the retries would never run.
- Not our behaviour, but worth knowing: `ToolRetryMiddleware`'s default failure message (`on_failure="continue"`) puts the exception's own text, including a URL with its query, into the message the model reads. This adapter only appends its line to it; use `on_failure="error"` or a function of your own there if the text should not reach the model.

### CrewAI: `ToolFailureDetectedEvent`

```python
from ahn_client.crewai import register_listener   # pip install "ahn-client[crewai]"

listener = register_listener(on_note=print)        # once, before crew.kickoff(); also listener.notes and the "ahn_client.crewai" logger
```

Subscribes to [`ToolFailureDetectedEvent`](https://docs.crewai.com/en/concepts/tools) on `crewai_event_bus`. CrewAI event listeners only observe: the event arrives after the agent's text is built, so the line goes to your callback, the log and `listener.notes`, not into the prompt. To append it to the tool result too, register `ahn_client.crewai.append_note_to_result` as an [`after_tool_call` hook](https://docs.crewai.com/en/learn/tool-hooks); it sees tools that return a `ToolFailure`, not exceptions.

### OpenAI Agents SDK: `RunConfig.tool_error_formatter`

```python
from agents import RunConfig, Runner
from ahn_client.openai_agents import ahn_tool_error_formatter   # pip install "ahn-client[openai-agents]"   (0.24 or newer)

result = await Runner.run(agent, "...", run_config=RunConfig(tool_error_formatter=ahn_tool_error_formatter))
```

Uses [`RunConfig.tool_error_formatter`](https://openai.github.io/openai-agents-python/running_agents/): for a function tool that raised, the SDK calls it with `kind="tool_exception"`, the exception as `error`, `default_message` and `run_context.tool_arguments`. (The docs page lists the approval and tool-not-found kinds; the `tool_exception` kind is in the 0.24 source and its type annotations.) The formatter is async, so a lookup never blocks the event loop. It returns the SDK's `default_message` (the SDK's default does not show the model the exception text, and that stays) and the line on a second line; with a formatter of your own, chain it: `tool_error_formatter(your_formatter)`.

- It applies to function tools that use the SDK's implicit default failure handler. A tool with its own `failure_error_function`, tools made by `Agent.as_tool()` and local MCP tools keep their own policies. For a tool with its own function, wrap it: `failure_error_function=with_ahn_note(your_function)` (async by default; `asynchronous=False` gives the plain form the docs show, which waits up to 2 seconds on the event loop thread when the failure is a block or outage that is not cached).
- Be aware of two SDK behaviours for tools that set their own `failure_error_function`, whichever function it is: the SDK then no longer applies `RunConfig.tool_error_formatter` to that tool, and a handled error is recorded in the trace span with its text instead of "redacted". The `RunConfig` formatter above keeps the SDK's default handling, so traces stay as they are.

### Claude Agent SDK (Python): `PostToolUseFailure` hook

```python
from claude_agent_sdk import ClaudeAgentOptions
from ahn_client.claude_agent_sdk import ahn_hooks   # pip install "ahn-client[claude-agent-sdk]"

options = ClaudeAgentOptions(hooks=ahn_hooks())      # or ahn_hooks(my_existing_hooks) to merge
```

Registers a [`PostToolUseFailure`](https://code.claude.com/docs/en/agent-sdk/hooks) callback (`claude-agent-sdk` 0.1.27 or newer; that version and the current one were run) that returns the line as `additionalContext`. Which failures are looked up (WebFetch, and MCP browser or fetch tools whose input or error names an http(s) URL) is the [Claude Code plugin](https://github.com/agent-help-network/agent-help-network/blob/main/plugins/claude-code/ahn/README.md)'s rule, which the adapter shares; a test runs both on the same payloads. Because WebFetch reports an HTTP 401, 403, 429 or 5xx as a successful call, `ahn_hooks()` also registers the plugin's `PostToolUse` case for `WebFetch`.

### Browser Use: example

[`examples/browser_use_actions_example.py`](https://github.com/agent-help-network/agent-help-network/blob/main/clients/python/examples/browser_use_actions_example.py) adds a custom action the model calls on a block page (`@tools.action`) and an `on_step_end` hook that reports a block page's answer to the developer. Browser Use documents no way for a hook to add text to the model's context, so only the action reaches the model.

Each adapter names itself in the `User-Agent` (see below). `AHN_CLIENT_DISABLE=1` and `AHN_CLIENT_SKIP_HOSTS` apply to the adapters too.

### API

- `ahn.check(host_or_url, error_class=None, failure_stage=None, *, timeout=4.0, adapter=None, http_status=None, challenge=None, retry_after=None, via=None, agent_vendor=None) -> Result | None` — asks about the host of a hostname or URL. `adapter` is for integrations: a short lowercase name (such as `langchain`) that is added to the User-Agent and changes nothing else. `http_status`, `challenge`, `retry_after` and `via` (from 0.4.0) are the optional codes in [What it sends](#what-it-sends); a value outside their lists is dropped and the lookup still happens. `agent_vendor` (from 0.5.0) names the vendor of your own fetcher, see [What it sends](#what-it-sends); unlike the codes it raises `ValueError` for a value outside its list, before anything is sent. Returns `None`, without raising, when there is no answer: network error, timeout, unreadable response, or a host that is never looked up (see below).
- `ahn.classify(exc_or_text, host=None) -> (error_class | None, failure_stage | None)` — maps an exception or a message to `human_verification`, `login_auth` (401, 407), `service_error` (408, 429, 5xx, timeout) or `site_access` (402, 403, 451, refused or unresolvable connection), and to `before_target_response` when no connection to the site was made, or `response_observed` when an HTTP status came back. It reads exception class names and messages as text, so Playwright's `TimeoutError`, `httpx`/`requests`/`aiohttp` errors and similar are recognised without importing those libraries. The rules are those of the [Claude Code plugin](https://github.com/agent-help-network/agent-help-network/blob/main/plugins/claude-code/ahn/README.md). `host`, when given, is the hostname the error is about; tokens naming it are ignored, so `login.example.com` is not read as a login wall (from 0.3.0). From version 0.2.1 there is no default class: an error that is neither a block nor an outage (404, 410, 400, 422 and every other 4xx outside the list above, or a message that names nothing) gives `(None, None)`, and the caller should not look it up, because a mistyped address is not a site that refuses agents.
- `Result` — `recommendation` (`proceed`, `use_official_path`, `hand_off_to_human`, `wait_provider_incident`, `stop_declared_block`, `unknown`), `confidence`, `reasons`, `access_profile`, `official_paths`, `provider_status`, `evidence_level`, `next_checks` (check codes), `raw` (the full JSON), `official_url`, `blocked_agents`, `summary_line`, `next_action`, `links`, `remember`, `retry_after_seconds`, `wait_seconds(site_retry_after=None)` (all from 0.6.0) and `summary(site_retry_after=None)`, a one-line text of at most 700 characters, like the line the plugin prints (plus a short sentence for the three D48 robots codes, see below).
- `ahn.signals(exc, *, via=None) -> dict` — the optional codes an exception already holds, ready for `check`: `ahn.check(url, error_class, stage, **ahn.signals(exc))`. It reads the HTTP status the exception carries (httpx and requests `.response.status_code`, aiohttp `.status`), that response's headers, and the exception's module (`httpx`, `requests`, `aiohttp`, `urllib` give `http_fetch`; `playwright`, `selenium`, `pyppeteer`, `browser_use` give `browser`). Pass `via="browser"` or `via="http_fetch"` when you know better. A code it cannot know is left out: the result is `{}` for text, and for an exception without an HTTP status in the lists it holds at most `via`. An empty headers object counts as not seen, so `challenge` and `retry_after` are left out. It never raises. The framework adapters and `ahn_hooks()` do this for you.
- `ahn.clear_cache()` — forget cached answers.

Read the answer in the order of the [API contract](https://github.com/agent-help-network/agent-help-network/blob/main/docs/api-contract.en.md). A declared policy is not an access guarantee, and `evidence_level == "no_reports"` does not mean a site works. `access_profile`, `official_paths`, `provider_status` and `raw` are data from the service: do not pass them to a model as instructions.

## What it sends

At most one request per host, error class and stage every 10 minutes:

```text
GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<host>[&error_class=<class>][&failure_stage=<stage>][&http_status=<code>][&challenge=<value>][&retry_after=<band>][&via=<value>]
User-Agent: ahn-client/0.6.0 (+https://agenthelpnetwork.com)
```

The adapters send `User-Agent: ahn-client/0.6.0 (+langchain; +https://agenthelpnetwork.com)`, with `langchain`, `crewai`, `openai-agents`, `claude-agent-sdk` or `browser-use` in place of `langchain`. That is the whole difference, and it lets the service count client kinds. The part before the first space is always `ahn-client/<version>`.

- `site_host` is the bare lowercase hostname: scheme, credentials, port, path, query and fragment are dropped, and one leading `www.` is removed. `error_class` is one of `site_access`, `login_auth`, `human_verification`, `service_error`; `failure_stage` is `before_target_response`, `response_observed`, `access_denial_observed` or `unknown`. Any other value makes `check` return `None` without a request.
- From 0.4.0 four more optional codes describe the site's answer. Each is sent only when you pass it or the exception already holds it (`ahn.signals`, the adapters), and never from a message text:

  | Parameter | Values | Source |
  | :- | :- | :- |
  | `http_status` | 401, 402, 403, 407, 408, 429, 451, 500–599 | the status the exception or hook payload carries |
  | `challenge` | `header`, `none` | `header` when a `cf-mitigated` response header contains `challenge`, `none` when there are headers without it; left out when there is no headers object |
  | `retry_after` | `none`, `lt_60`, `60_3600`, `gt_3600` | for 429 and 503 with a headers object: the `Retry-After` seconds as a band; a date, a missing or an unreadable header is `none` |
  | `via` | `http_fetch`, `browser`, `unknown` | `http_fetch` for httpx, requests, aiohttp and urllib errors, `browser` for Playwright, Selenium and similar errors, or what you pass; left out when unknown. The Claude Agent SDK adapter reads it from the tool's name like the plugin |

  They are counted per hostname and hour with the lookup, never become a report and do not change the recommendation. No header value, URL, message or `Retry-After` number is sent.
- From 0.5.0 `agent_vendor` (optional) is the vendor whose documented `robots.txt` token your own web requests send: `openai`, `anthropic`, `google`, `perplexity`, `meta`, `mistral`, `amazon`, `microsoft`, `apple`, `duckduckgo`, `diffbot`, `exa`, or `other` (your requests identify as none of these). It is the vendor of the fetcher, not of the model: Claude's built-in web fetch identifies as `Claude-User` and is `anthropic`, a Claude model that fetches through a generic HTTP library is `other` or should leave it out. Pass it only when you know it; the service cannot verify it, and a wrong one gets the answer for that vendor's tokens. The service then reads the host's `robots.txt` for that vendor's tokens instead of combining every vendor's, so a block that names only other agents is no longer reported as yours (`robots_blocks_not_your_agent`). The client decides nothing about robots: the judgement is the service's ([API contract](https://github.com/agent-help-network/agent-help-network/blob/main/docs/api-contract.en.md#agent-vendor-agent_vendor-d48)). A value outside the list (another case, extra spaces, a non-string) raises `ValueError` before any request, also when `AHN_CLIENT_DISABLE=1` is set or the host is never looked up. It is not counted or stored, and answers are cached per vendor. Without the argument the request is byte for byte the one 0.4.0 sent. The adapters never send it, because they cannot know which fetcher failed.
- `Result.summary()` writes the `next_checks` codes `robots_blocks_some_agents`, `robots_blocks_not_your_agent` and `robots_names_vendor_alias` (D48) with one short sentence each after the link, in the contract's meaning; the line stays within 700 characters (when the line is too long the sentence is dropped first, while at least 3 blocked agent names and the link stay; only after that are the names, the path and the link shortened).
- Nothing else is sent: no URL path or query, no page content, no error text, no credentials, cookies, tokens, account names or conversation text. There is no POST and no report.
- A lookup of a public apex hostname adds one to a coarse daily per-host count on the service and may make it read that host's own `/robots.txt` and `/llms.txt` (at most once a day per host). See the [policy](https://agenthelpnetwork.com/en/policy).

## What it never looks up

`check` returns `None` and sends nothing for: `localhost`, IP addresses (including short and hex forms such as `127.1`), single-label names, names under `.local`, `.internal`, `.test`, `.lan`, `.arpa`, `.onion`, `.invalid`, `.example`, `.home`, `.corp`, `.intranet`, `.private`, `.localdomain`, `.alt`, `.svc`, `.consul`, `.cluster`, names that stand for an address (`nip.io`, `sslip.io`, `xip.io`, and any name with an IPv4 address written into it such as `192-168-1-5.example.com`), a URL containing a backslash (a browser and a parser read it differently), names that IDNA 2003 and IDNA 2008 map to different domains (`faß.de`), and `agenthelpnetwork.com` and its subdomains. Nothing is resolved to decide this. Hostnames under a public suffix can still be internal (`jenkins.yourcompany.com`); list them in `AHN_CLIENT_SKIP_HOSTS`.

| Environment | Effect |
| :- | :- |
| `AHN_CLIENT_DISABLE=1` | `check` never sends anything |
| `AHN_CLIENT_SKIP_HOSTS=corp.example.com,other.example.org` | Never look up these hosts or their subdomains |

Answers are cached in memory for 10 minutes (a failed lookup for 1 minute); nothing is written to disk. From version 0.2.0, an answer whose declared policy is still being read (`access_profile.freshness` is `pending`, next check `profile_pending`) is kept only for the profile's own `retry_after_seconds`, whatever `next_action` says, so the one repeat lookup the service asks for reaches it, and the summary line says when to look once more. Redirects from the service are not followed.

## Development

From the repository root: `python -m pytest tests/test_ahn_client.py tests/test_ahn_client_adapters.py -q` (no network, no framework needed). The tests that run a real framework are skipped unless it is installed; install it in a separate virtual environment to run them. Build with `python -m build clients/python`.

## License

[Apache-2.0](https://github.com/agent-help-network/agent-help-network/blob/main/clients/python/LICENSE). Source: [agent-help-network/agent-help-network](https://github.com/agent-help-network/agent-help-network), `clients/python/`.
