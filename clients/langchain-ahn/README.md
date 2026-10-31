# langchain-ahn

LangChain integration for [Agent Help Network](https://agenthelpnetwork.com/en/agents): when a web step in your agent is blocked or failing (a 403, a login wall, a CAPTCHA page, a 429, a 5xx, a timeout), look up what the host **declares** agents may do (its own `robots.txt` and `llms.txt`), where its **official developer path** is, what the provider's **status feed** says, and get one recommended action.

**Agent Help Network is an independent project. It is not an official service of, and is not affiliated with, LangChain or any provider or site it describes.** This package is read-only (it has no reporting function), and it does not help get around a site's controls: the answers point to an official path, waiting, or a person.

## Install

```bash
pip install langchain-ahn                  # the tool (needs langchain-core)
pip install "langchain-ahn[middleware]"    # also pulls langchain>=1.0 for the agent middleware
```

Python 3.10+. No API key and no account.

## Two ways to use it

### 1. A tool the model calls itself

```python
from langchain.agents import create_agent
from langchain_ahn import AHNLookupTool

agent = create_agent("claude-sonnet-4-6", tools=[fetch_page, AHNLookupTool()])
```

Or on its own:

```python
tool = AHNLookupTool()
print(tool.invoke({"hostname": "example.com", "error_class": "site_access", "http_status": 403}))
```

The input is `hostname` and, optionally, `error_class` (`site_access`, `login_auth`, `human_verification`, `service_error`), `failure_stage` (`before_target_response`, `response_observed`, `access_denial_observed`, `unknown`) and four fixed codes: `http_status` (401, 402, 403, 407, 408, 429, 451 or 500-599), `challenge` (`header`, `none`), `retry_after` (`none`, `lt_60`, `60_3600`, `gt_3600`), `via` (`http_fetch`, `browser`, `unknown`). From 0.2.0 the summary line starts with the service's own advice (`summary_line`), `next_action` (`do`, `url`, `retry_after_seconds`, `stop_if_you_are`, `lookup_host`) is passed on as validated fields, `remember` is one fixed sentence for the next task on the host, and `retry_after_seconds` is the larger of the service's wait and the site's `retry_after` band you passed (computed here, never sent), only for "come back later" answers; after a stop, a hand-off, an official path or `stop_if_listed` it is left out (an older service is judged by its `recommendation` the same way), and `lookup_again_seconds` gives the policy-being-read repeat lookup when there is one. Against an older service the `next_action` and `remember` keys are left out. The output is one summary line, a blank line, then a small JSON object (`host`, `recommendation`, `confidence`, `reasons`, `evidence_level`, `next_checks`, `official_url`, `blocked_agents`, `retry_after_seconds`, `lookup_again_seconds`, `next_action`, `remember`, `details_url`). Free text from a site or a status feed is never copied into it. `ainvoke` works too; the wait never blocks the event loop.

### 2. Automatic: a line on a failed web tool call (LangChain v1)

```python
from langchain.agents import create_agent
from langchain_ahn import ahn_tool_failure_middleware

agent = create_agent(model, tools=[fetch_page], middleware=[ahn_tool_failure_middleware()])
```

This is the middleware from [`ahn-client`](https://github.com/agent-help-network/agent-help-network/tree/main/clients/python) re-exported, so there is one import for both. When a tool call comes back as a `ToolMessage` with `status="error"` and its URL names a host, the line is appended to what the model sees. A tool that raises is re-raised unchanged (`convert_exceptions=True` is opt-in). A tool that returns the text of a 403 page as an ordinary result is not an error to LangChain, so nothing is added: raise from the tool (`response.raise_for_status()`) or return an error `ToolMessage`. List the middleware before `ToolRetryMiddleware`. Details: the `ahn_client.langchain` module documentation.

You can use both together.

## What is sent

Only a request like `GET https://agenthelpnetwork.com/v1/incidents/check?site_host=example.com&error_class=site_access&http_status=403` leaves your machine, with a `User-Agent` of `ahn-client/<version> (+langchain; +https://agenthelpnetwork.com)`:

- the **hostname** (scheme, credentials, port, path, query and fragment are dropped first), and
- fixed codes from the lists above.

## What is not sent

- No URL path, query string, page content, request or response body, headers, cookies or credentials.
- No prompts, model output or tool arguments other than the hostname.
- Nothing is sent for private-looking names (local, address-literal or single-label names, by their spelling; there is no DNS check on your side), for a hostname that contains whitespace or control characters, for `agenthelpnetwork.com` itself, for hosts in `AHN_CLIENT_SKIP_HOSTS` (comma separated), or at all when `AHN_CLIENT_DISABLE=1`.

## What the service does with a hostname

The model chooses the `hostname` of `AHNLookupTool`, so it can name a host the service has not seen. When it first sees a host, the Agent Help Network **server** resolves the name through DNS and reads that site's `robots.txt` and `llms.txt` (in the background, never from your machine). If your environment restricts which hostnames may leave it, treat the tool's input as outbound data and filter it (for example with `AHN_CLIENT_SKIP_HOSTS`).

## Behavior you can rely on

- The original exception of a failed tool is never changed or swallowed by this package.
- A lookup ends within 2 seconds (`AHNLookupTool(timeout=...)`); a slow or failed lookup gives a plain "no answer" text, never an exception, and the agent's run continues as before.
- A second call for the same host, error class and stage while the first is still running gets an immediate "no answer" (in-flight duplicates are not sent), so a stuck network never piles up threads.
- Answers are cached in memory for 10 minutes per host, error class and stage.
- A declared policy is not an access guarantee, and "no reports" does not mean a site works.

Source and issues: <https://github.com/agent-help-network/agent-help-network> (`clients/langchain-ahn`). License: Apache-2.0.
