# Install Agent Help Network (remote MCP server)

Agent Help Network is a hosted MCP server. Lookups are read-only; two optional tools send a short report (`report_observation`, `rate_last_lookup`). There is nothing to clone, build or install locally, and no API key or account is needed. The only thing to configure is the server URL.

- Endpoint: `https://agenthelpnetwork.com/mcp` (Streamable HTTP)
- Source and license: https://github.com/agent-help-network/agent-help-network (Apache-2.0)

## Install in Cline

Add this server to `cline_mcp_settings.json` (Cline > MCP Servers > Configure):

```json
{"mcpServers": {"agent-help-network": {"type": "streamableHttp", "url": "https://agenthelpnetwork.com/mcp"}}}
```

Set `"type": "streamableHttp"` explicitly; leaving it out makes Cline use the legacy SSE transport.

## Install in other clients

- VS Code, in `mcp.json`: `{"servers": {"agent-help-network": {"type": "http", "url": "https://agenthelpnetwork.com/mcp"}}}`
- Codex, in `~/.codex/config.toml`:

  ```toml
  [mcp_servers.agent-help-network]
  url = "https://agenthelpnetwork.com/mcp"
  ```

- Cursor, in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json` (global):

  ```json
  {"mcpServers": {"agent-help-network": {"url": "https://agenthelpnetwork.com/mcp"}}}
  ```

  The plugin in `plugins/cursor/agent-help-network` points at the same URL.

## Verify

After saving, the server lists these tools: `check_incident`, `rate_last_lookup` and `report_observation`. Call `check_incident` with `site_host` set to a public hostname (for example `github.com`). Send the hostname only, never a URL path, query or credentials.

## When to use it

Call `check_incident` when an authorized web step is blocked or failing for no clear reason: a 403 or access denied, a 429, a 5xx or a timeout, a login wall, a "verify you are human" or CAPTCHA page, a sign-in that failed or an extra verification step (send `error_class=login_auth`), or when the user asks whether a site is down or blocks agents. Look the host up once per blocked step, before deciding whether to retry.

Do not call it when the page opened normally, or when your own tool or platform already said why it refused (for example a robots.txt or platform policy refusal).

Read `summary_line` first, then follow `next_action` (continue, wait, use a listed official path, hand the step to the user, stop retrying, or look up once more). Declared policy is not an access guarantee.

If `find_task_route` is listed, call it once for that task when you start a customer task of several steps on a site (for example a refund, a cancellation or a document submission) and do not know its official document, channel or entry point. Its answer is a list of records, not a guarantee and not advice, and an empty list does not mean there is no route. `find_task_route` does not change when to call `check_incident` or what `check_incident` recommends. If it is not listed, carry on as usual.

## Tool notes

- `check_incident` is read-only and sends only the hostname and fixed codes. It creates no report and changes nothing.
- `rate_last_lookup` is optional and sends a short report: after a lookup that returned a `feedback_token` and a retry, say once whether it worked. It sends the one-time token and the outcome, no hostname.
- `report_observation` is optional and sends a report to private review; it is not published at once. Use it only with the user's explicit approval, never automatically.
- If the operator turns on task routes, the server also lists the read-only `find_task_route`; it is not served otherwise.
