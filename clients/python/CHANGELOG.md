# Changelog

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
