# Changelog

## 0.6.4

- `ahn.local_digest(hosts, *, pad_to=16, cache_dir=None, public_key=None, ...)` compares regular sites with the bundle's thin shards on your machine: shard ids are computed locally, random padding shards keep the number of requests fixed, shards are verified against the manifest's SHA-256 (and its Ed25519 signature when you give the key), `If-None-Match` caching, a seven-day changes marker, and `status == "not_in_bundle"` for hosts to check live. The service receives no hostname.
- `ahn.digest_markdown(digest)` renders a card with no text copied from sites.
- `ahn.FailureLog` and `ahn.ADVICE`: an optional local file of the last 14 days of a site's failures and the advice codes a pattern calls for. Nothing is sent.

## 0.6.3

- `ahn.check(..., task_step=...)`: `"status_check"` or `"preflight"` tells the service that the lookup only checks whether a site is up
  (or ready) and nothing was refused. The service now answers a lookup that sends neither `error_class` nor `failure_stage` as
  if the step had been refused, so a pure status check should send one of these words; after a failure keep sending `error_class`
  and `failure_stage` (they win). Any other value is dropped. The word is part of the cache key. No other behavior changes.

## 0.6.2

- `ahn.signals(exc)` now reads AWS WAF's `x-amzn-waf-action` header, as `diagnose` already did: a 403 or 451 with `challenge` or
  `captcha` gives `challenge="header"`. The Claude Code plugin hook (plugin 0.3.8) and the Claude Agent SDK adapter read it the
  same way, so one response gets the same codes whichever path reads it. The header counts only on a 202 (`challenge`),
  a 405 (`captcha`), a 403 or a 451; on a 429, a 503 or any other status it is not a challenge (`challenge="none"`).
- `ahn.diagnose` follows that rule too: a 429 or 5xx with the header no longer reports `challenge="header"`.

## 0.6.1 (not published to PyPI; released as part of 0.6.2)

- `ahn.diagnose(response, body=None)`: a Playwright, httpx or requests response (any object with `status_code` or `status`,
  `headers` and optionally `text` or `content`) gives `(error_class, failure_stage, signals)` for `ahn.check`, by the same status
  and header rules as `classify` and `signals`. The first 4,000 characters of a body are tested on your machine for a few
  human-check markers and the body is never sent, stored or returned. No library is imported or required.
- AWS WAF: a 202 or 405 with `x-amzn-waf-action: challenge|captcha` is `human_verification` with `challenge=header`
  (https://docs.aws.amazon.com/waf/latest/developerguide/waf-captcha-and-challenge-actions.html). A 403 or 451 is
  `human_verification` when a header or the body points to a human check.
- A 200 whose page `<title>` looks like a human check gives `error_class` None with `.candidate` True and `.hint` `"human_verification"`: a hint, never looked up by code that just unpacks the result.
- `Result.summary()` ends with "robots.txt asks N s between requests." when the host's robots.txt asks for a delay
  (`Result.crawl_delay_seconds`, rounded to one decimal; a delay that rounds to 0.0 is ignored).

## 0.6.0

- `Result` keeps the service's own guidance (D51) instead of rebuilding it from the six `recommendation` values:
  `summary_line`, `next_action` (a `NextAction` with `do`, `url`, `retry_after_seconds`, `stop_if_you_are`, `lookup_host`)
  and `links`. Each is `None` when the service does not send it (an older service, or `AHN_RESPONSE_GUIDE=0`); every field
  is validated and a bad one is dropped.
- `Result.summary()` starts with the service's `summary_line`, then the `next_action` values it points at (URL, tokens to
  stop on, host to look up) and, if it fits in 700 characters, the detail line of earlier versions. Without a
  `summary_line` the line is the one 0.5.0 gave.
- `Result.retry_after_seconds` is now the service's `next_action.retry_after_seconds`, so it is also set while a provider
  incident is being waited out (900), not only while a profile is pending; after a stop, a hand-off or an official path
  (`stop_automation`, `stop_if_listed`, `hand_off_to_human`, `use_official_path`) it is `None` and the site's value is not
  merged. The cache still keeps a pending-profile answer only for the profile's own `retry_after_seconds`. `Result.wait_seconds(site_retry_after)` returns
  the larger of that and the site's own Retry-After (seconds or a band code), at most 3600; the framework adapters pass the
  Retry-After band they already hold. The merge is local: nothing new is sent and the service's answer is not changed.
- `Result.lookup_again_seconds`: the pending profile's own "look up once more in N s" value (0.5.0 gave it as
  `retry_after_seconds` in every case; now `retry_after_seconds` is `None` after a stop, a hand-off or an official path, so
  this keeps it). The name follows the service's wording ("look up once more") and says it is not a site wait; it is never
  combined with the site's Retry-After. langchain-ahn passes it on in its JSON.
- When to merge the site's Retry-After: with a readable `next_action`, by its `do`; a `next_action` whose `do` this version
  does not know never merges. Without a `next_action` (an older service, `AHN_RESPONSE_GUIDE=0`) the verdict as the service
  sent it decides, and only `proceed`, `wait_provider_incident` or an explicit `unknown` merge; `stop_declared_block`,
  `hand_off_to_human`, `use_official_path` and any verdict this version does not know (shown as `unknown` in
  `recommendation`) do not.
- `Result.remember`: one fixed sentence for the next task on the host, made from `next_action` alone (for example
  "For example.com, start with the official path: ..."). No new judgement; `None` for states that pass (a provider wait, a hand-off).
- `NextAction` is exported.

## 0.5.0

- `ahn.check(..., agent_vendor=None)`: optional vendor of your own fetcher (`openai`, `anthropic`, `google`, `perplexity`,
  `meta`, `mistral`, `amazon`, `microsoft`, `apple`, `duckduckgo`, `diffbot`, `exa`, `other`), sent to the service so it
  judges `robots.txt` for that vendor's tokens (D48). Any other value raises `ValueError` before a request. Not counted
  or stored by the service. Answers are cached per vendor. Without the argument the request is unchanged.
- `Result.summary()` explains the next_checks codes `robots_blocks_some_agents`, `robots_blocks_not_your_agent` and
  `robots_names_vendor_alias` in one short sentence each.
- The client makes no robots judgement of its own.

## 0.4.0

- Optional lookup codes `http_status`, `challenge`, `retry_after` and `via`, `ahn.signals(exc)`, and the framework adapters.
