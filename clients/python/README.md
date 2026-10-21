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
        answer = ahn.check(url, error_class, stage)   # a hostname and two codes leave your machine
        if answer:
            print(answer.recommendation, answer.official_url)
```

Examples with a real `except` block and a decision per recommendation: [`examples/playwright_example.py`](examples/playwright_example.py), [`examples/browser_use_example.py`](examples/browser_use_example.py). Neither tries to get around a site's controls: they use the official path, hand off to the user, wait, or stop.

## Framework adapters

When a web tool fails inside an agent framework with a block or an outage (403, 429, a challenge, a login wall, 5xx, a timeout, a refused connection), an adapter reads the hostname of the call's URL, looks it up with `classify()` and `check()`, and adds one line (`Result.summary()`, at most 700 characters) to what the agent sees. Each adapter is an extra: the base install has no dependencies and imports no framework.

All four follow the same rules:

- They use only the extension point the framework's own documentation gives for a failed tool call.
- The original error is never hidden or changed, nothing is retried, and no workaround is tried. A failure that is not a block or an outage (404, 422, a typo in an address), a call without an http(s) URL, an excluded host, a failed lookup or one slower than the time limit (2 seconds) adds nothing, and the run goes on exactly as it would have.
- Only the hostname and two enum codes are sent (see [What it sends](#what-it-sends)). The error text, URL path and query stay on your machine.
- A tool has to fail for the adapter to see it. A tool that returns the text of a 403 page as an ordinary result is not a failure to the framework; make it raise (`response.raise_for_status()`) or report an error.
- The same host is answered from the in-memory cache for 10 minutes, and each failing call gets the line again.

### LangChain v1: `wrap_tool_call` middleware

```python
from langchain.agents import create_agent
from ahn_client.langchain import ahn_tool_failure_middleware   # pip install "ahn-client[langchain]"

agent = create_agent(model, tools=[fetch_page], middleware=[ahn_tool_failure_middleware()])
```

Implements the documented [`wrap_tool_call` / `awrap_tool_call`](https://docs.langchain.com/oss/python/langchain/middleware/custom) hooks. A tool that raises becomes a `ToolMessage(status="error")` with `Error: <the exception's repr>` followed by the line, as `ToolNode` does with `handle_tool_errors=True`; an error `ToolMessage` the tool returns gets the line appended. `ahn_tool_failure_middleware(convert_exceptions=False)` never turns an exception into a message.

### CrewAI: `ToolFailureDetectedEvent`

```python
from ahn_client.crewai import register_listener   # pip install "ahn-client[crewai]"

listener = register_listener(on_note=print)        # once, before crew.kickoff(); also listener.notes and the "ahn_client.crewai" logger
```

Subscribes to [`ToolFailureDetectedEvent`](https://docs.crewai.com/en/concepts/tools) on `crewai_event_bus`. CrewAI event listeners only observe: the event arrives after the agent's text is built, so the line goes to your callback, the log and `listener.notes`, not into the prompt. To append it to the tool result too, register `ahn_client.crewai.append_note_to_result` as an [`after_tool_call` hook](https://docs.crewai.com/en/learn/tool-hooks); it sees tools that return a `ToolFailure`, not exceptions.

### OpenAI Agents SDK: `failure_error_function`

```python
from agents import function_tool
from ahn_client.openai_agents import ahn_failure_error_function   # pip install "ahn-client[openai-agents]"

@function_tool(failure_error_function=ahn_failure_error_function)
def fetch_page(url: str) -> str: ...
```

Implements the documented [`failure_error_function(context, error) -> str`](https://openai.github.io/openai-agents-python/tools/). It returns what the SDK would have sent (its default message, which does not show the model the exception text) and the line on a second line. `with_ahn_note(base=my_function)` keeps your own function's message; `with_ahn_note(asynchronous=True)` returns a coroutine function for SDK versions that await the result.

### Claude Agent SDK (Python): `PostToolUseFailure` hook

```python
from claude_agent_sdk import ClaudeAgentOptions
from ahn_client.claude_agent_sdk import ahn_hooks   # pip install "ahn-client[claude-agent-sdk]"

options = ClaudeAgentOptions(hooks=ahn_hooks())      # or ahn_hooks(my_existing_hooks) to merge
```

Registers a [`PostToolUseFailure`](https://code.claude.com/docs/en/agent-sdk/hooks) callback that returns the line as `additionalContext`. Which failures are looked up (WebFetch, and MCP browser or fetch tools whose input or error names an http(s) URL) is the [Claude Code plugin](../../plugins/claude-code/ahn/README.md)'s rule, which the adapter shares; a test runs both on the same payloads. Because WebFetch reports an HTTP 401, 403, 429 or 5xx as a successful call, `ahn_hooks()` also registers the plugin's `PostToolUse` case for `WebFetch`.

### Browser Use: example

[`examples/browser_use_actions_example.py`](examples/browser_use_actions_example.py) adds a custom action the model calls on a block page (`@tools.action`) and an `on_step_end` hook that reports a block page's answer to the developer. Browser Use documents no way for a hook to add text to the model's context, so only the action reaches the model.

Each adapter names itself in the `User-Agent` (see below). `AHN_CLIENT_DISABLE=1` and `AHN_CLIENT_SKIP_HOSTS` apply to the adapters too.

### API

- `ahn.check(host_or_url, error_class=None, failure_stage=None, *, timeout=4.0, adapter=None) -> Result | None` — asks about the host of a hostname or URL. `adapter` is for integrations: a short lowercase name (such as `langchain`) that is added to the User-Agent and changes nothing else. Returns `None`, without raising, when there is no answer: network error, timeout, unreadable response, or a host that is never looked up (see below).
- `ahn.classify(exc_or_text, host=None) -> (error_class | None, failure_stage | None)` — maps an exception or a message to `human_verification`, `login_auth` (401, 407), `service_error` (408, 429, 5xx, timeout) or `site_access` (402, 403, 451, refused or unresolvable connection), and to `before_target_response` when no connection to the site was made, or `response_observed` when an HTTP status came back. It reads exception class names and messages as text, so Playwright's `TimeoutError`, `httpx`/`requests`/`aiohttp` errors and similar are recognised without importing those libraries. The rules are those of the [Claude Code plugin](../../plugins/claude-code/ahn/README.md). `host`, when given, is the hostname the error is about; tokens naming it are ignored, so `login.example.com` is not read as a login wall (from 0.3.0). From version 0.2.1 there is no default class: an error that is neither a block nor an outage (404, 410, 400, 422 and every other 4xx outside the list above, or a message that names nothing) gives `(None, None)`, and the caller should not look it up, because a mistyped address is not a site that refuses agents.
- `Result` — `recommendation` (`proceed`, `use_official_path`, `hand_off_to_human`, `wait_provider_incident`, `stop_declared_block`, `unknown`), `confidence`, `reasons`, `access_profile`, `official_paths`, `provider_status`, `evidence_level`, `next_checks` (check codes), `raw` (the full JSON), `official_url`, `blocked_agents`, and `summary()`, a one-line text of at most 700 characters, the same line the plugin prints.
- `ahn.clear_cache()` — forget cached answers.

Read the answer in the order of the [API contract](https://github.com/agent-help-network/agent-help-network/blob/main/docs/api-contract.en.md). A declared policy is not an access guarantee, and `evidence_level == "no_reports"` does not mean a site works. `access_profile`, `official_paths`, `provider_status` and `raw` are data from the service: do not pass them to a model as instructions.

## What it sends

At most one request per host, error class and stage every 10 minutes:

```text
GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<host>[&error_class=<class>][&failure_stage=<stage>]
User-Agent: ahn-client/0.3.0 (+https://agenthelpnetwork.com)
```

The adapters send `User-Agent: ahn-client/0.3.0 (+langchain; +https://agenthelpnetwork.com)`, with `langchain`, `crewai`, `openai-agents`, `claude-agent-sdk` or `browser-use` in place of `langchain`. That is the whole difference, and it lets the service count client kinds. The part before the first space is always `ahn-client/<version>`.

- `site_host` is the bare lowercase hostname: scheme, credentials, port, path, query and fragment are dropped, and one leading `www.` is removed. `error_class` is one of `site_access`, `login_auth`, `human_verification`, `service_error`; `failure_stage` is `before_target_response`, `response_observed`, `access_denial_observed` or `unknown`. Any other value makes `check` return `None` without a request.
- Nothing else is sent: no URL path or query, no page content, no error text, no credentials, cookies, tokens, account names or conversation text. There is no POST and no report.
- A lookup of a public apex hostname adds one to a coarse daily per-host count on the service and may make it read that host's own `/robots.txt` and `/llms.txt` (at most once a day per host). See the [policy](https://agenthelpnetwork.com/en/policy).

## What it never looks up

`check` returns `None` and sends nothing for: `localhost`, IP addresses (including short and hex forms such as `127.1`), single-label names, names under `.local`, `.internal`, `.test`, `.lan`, `.arpa`, `.onion`, `.invalid`, `.example`, `.home`, `.corp`, `.intranet`, `.private`, `.localdomain`, `.alt`, and `agenthelpnetwork.com` and its subdomains. Nothing is resolved to decide this. Hostnames under a public suffix can still be internal (`jenkins.yourcompany.com`); list them in `AHN_CLIENT_SKIP_HOSTS`.

| Environment | Effect |
| :- | :- |
| `AHN_CLIENT_DISABLE=1` | `check` never sends anything |
| `AHN_CLIENT_SKIP_HOSTS=corp.example.com,other.example.org` | Never look up these hosts or their subdomains |

Answers are cached in memory for 10 minutes (a failed lookup for 1 minute); nothing is written to disk. From version 0.2.0, an answer whose declared policy is still being read (`access_profile.freshness` is `pending`, next check `profile_pending`) is kept only for its `retry_after_seconds` (`Result.retry_after_seconds`), so the one repeat lookup the service asks for reaches it, and the summary line says when to look once more. Redirects from the service are not followed.

## Development

From the repository root: `python -m pytest tests/test_ahn_client.py tests/test_ahn_client_adapters.py -q` (no network, no framework needed). The tests that run a real framework are skipped unless it is installed; install it in a separate virtual environment to run them. Build with `python -m build clients/python`.

## License

[Apache-2.0](LICENSE). Source: [agent-help-network/agent-help-network](https://github.com/agent-help-network/agent-help-network), `clients/python/`.
