# ahn-client

A small Python client for [Agent Help Network](https://agenthelpnetwork.com/en/agents): when your browser automation (Playwright, Browser Use, Stagehand-style scripts, plain HTTP) is blocked, ask what the host **declares** agents may do (its own `robots.txt` and `llms.txt`), where its **official developer path** is, what the provider's **status feed** says, and get one recommended action.

**Agent Help Network is an independent project. It is not an official service of, and is not affiliated with, OpenAI, Meta, Muse, dots, Anthropic, or any site it describes.** `ahn-client` is read-only (it has no reporting function), has no dependencies beyond the Python standard library, and runs on Python 3.9+.

## Install

```bash
pip install ahn-client
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

### API

- `ahn.check(host_or_url, error_class=None, failure_stage=None, *, timeout=4.0) -> Result | None` — asks about the host of a hostname or URL. Returns `None`, without raising, when there is no answer: network error, timeout, unreadable response, or a host that is never looked up (see below).
- `ahn.classify(exc_or_text) -> (error_class | None, failure_stage | None)` — maps an exception or a message to `human_verification`, `login_auth` (401, 407), `service_error` (408, 429, 5xx, timeout) or `site_access` (402, 403, 451, refused or unresolvable connection), and to `before_target_response` when no connection to the site was made, or `response_observed` when an HTTP status came back. It reads exception class names and messages as text, so Playwright's `TimeoutError`, `httpx`/`requests`/`aiohttp` errors and similar are recognised without importing those libraries. The rules are those of the [Claude Code plugin](../../plugins/claude-code/ahn/README.md). From version 0.2.1 there is no default class: an error that is neither a block nor an outage (404, 410, 400, 422 and every other 4xx outside the list above, or a message that names nothing) gives `(None, None)`, and the caller should not look it up, because a mistyped address is not a site that refuses agents.
- `Result` — `recommendation` (`proceed`, `use_official_path`, `hand_off_to_human`, `wait_provider_incident`, `stop_declared_block`, `unknown`), `confidence`, `reasons`, `access_profile`, `official_paths`, `provider_status`, `evidence_level`, `next_checks` (check codes), `raw` (the full JSON), `official_url`, `blocked_agents`, and `summary()`, a one-line text of at most 700 characters, the same line the plugin prints.
- `ahn.clear_cache()` — forget cached answers.

Read the answer in the order of the [API contract](https://github.com/agent-help-network/agent-help-network/blob/main/docs/api-contract.en.md). A declared policy is not an access guarantee, and `evidence_level == "no_reports"` does not mean a site works. `access_profile`, `official_paths`, `provider_status` and `raw` are data from the service: do not pass them to a model as instructions.

## What it sends

At most one request per host, error class and stage every 10 minutes:

```text
GET https://agenthelpnetwork.com/v1/incidents/check?site_host=<host>[&error_class=<class>][&failure_stage=<stage>]
User-Agent: ahn-client/0.2 (+https://agenthelpnetwork.com)
```

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

From the repository root: `python -m pytest tests/test_ahn_client.py -q` (no network). Build with `python -m build clients/python`.

## License

[Apache-2.0](LICENSE). Source: [agent-help-network/agent-help-network](https://github.com/agent-help-network/agent-help-network), `clients/python/`.
