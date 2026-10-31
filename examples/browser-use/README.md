# Browser Use + Agent Help Network

A [Browser Use](https://github.com/browser-use/browser-use) agent that meets a 403, a CAPTCHA, a login wall, a 429 or a 5xx tends to do the same thing again, or to look for a way around the page. This example gives the agent one custom tool, `check_blocked_site`. When the page is blocked, the model calls it, and the tool asks [Agent Help Network](https://agenthelpnetwork.com/en/agents) (AHN) what the host declares for agents, where its official developer path is, and what its provider reports. The answer's `summary_line` and `next_action` go back to the model as the tool result, so its next step is "use the official path", "ask the user", "wait", "look up once more" or "stop".

AHN is an independent project, not an official service of Browser Use or of any site it describes.

## Install and run

Python 3.11 or newer (Browser Use's requirement).

```
pip install -r requirements.txt
python run_example.py https://example.com/
```

`requirements.txt` is for this example only; the repository's tests do not install Browser Use. Tested against `browser-use` 0.13.14 and `ahn-client` 0.6.0. `run_example.py` uses `ChatBrowserUse`; build the model for your own provider as the [Browser Use documentation](https://docs.browser-use.com/open-source/customize/tools/add) shows. The tool is registered with `@tools.action(...)`, as described on that page; check it against the version you have installed.

## What the tool does

1. The model calls `check_blocked_site(url, reason)`, where `reason` is `access_denied`, `verification_challenge`, `login_required` or `server_error`.
2. The tool calls `ahn_client.check(...)` with the page's hostname, an error class derived from `reason`, and `via="browser"`. Nothing else is sent: no path, query, page text or title. Local and private hosts are never looked up.
3. It reads `Result.summary_line` and `Result.next_action` (ahn-client 0.6.0 checks each field) and returns the sentence plus one instruction for the model.

The lookup is tagged `+browser-use` in the User-Agent (`ahn-client/<version> (+browser-use; +https://agenthelpnetwork.com)`), which is how the service counts this path. No new value is involved: it is the adapter label `ahn-client` already supports. The `agent_vendor` parameter is not set by default; it names the vendor whose `robots.txt` token your own web requests send, and Browser Use drives a normal browser, so there is nothing to claim. Pass `BlockGuide(agent_vendor="...")` only if you know it: use the vendor token that matches your own fetcher, and `"other"` when it matches none of the known vendors (the same rule as in [`ahn/llms.txt`](../../ahn/llms.txt) and the MCP tool description).

## What each `next_action.do` leads to

| `do` | The model is told to |
| --- | --- |
| `wait_for_provider` | stop working on the site and tell the user (status page link when given); no reload loop |
| `hand_off_to_human` | stop and ask the user to do the step in their own browser; never ask for or type credentials |
| `use_official_path` | use the site's official API or developer path (link when given) instead of its pages |
| `stop_automation` | stop automating the site and tell the user |
| `proceed` | retry the page at most once, slowly; a declared policy is not a guarantee |
| `retry_lookup_once` | wait `retry_after_seconds` (5 to 300 s; the `wait` action takes at most 30 s per call, so repeat it), then call the tool once more. A call before the wait is over makes no lookup and only reports the time left, because ahn-client answers it from its cache; it does not use up the one repeat lookup. A second `retry_lookup_once` after the wait is read as `no_clear_action` |
| `stop_if_listed` | stop retrying and ask the user whether their setup identifies as one of the listed agent names |
| `lookup_apex_host` | call the tool once with the site's main domain (`lookup_host`) |
| `no_clear_action` (and any other value) | treat the policy as unknown, not as permission; stop retrying and ask the user |

The values and their meaning are those of the [API contract](../../docs/api-contract.en.md#summary-line-and-next-action). The branching lives in `instruction()` in `ahn_browser_use.py`, a plain function that is tested without Browser Use (`tests/test_example_browser_use.py`).

## What this example does not do

- It does not solve or bypass CAPTCHAs or other human-verification checks.
- It does not log in for the user, and never asks for or handles credentials.
- It does not change the browser's identity, rotate proxies or retry in a loop to get past a block.
- It does not visit the blocked site on its own, and it does not decide whether you may access a site. AHN reports what the host declares and what is published; a declared policy is not an access guarantee, and the answer never grants permission.
- It does not report anything to AHN. The tool only reads.

When the answer says to stop or to ask the user, the example stops and asks.
