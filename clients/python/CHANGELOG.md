# Changelog

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
