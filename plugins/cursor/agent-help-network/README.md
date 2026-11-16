# Agent Help Network for Cursor

A remote MCP server (Streamable HTTP, no account or key). When a web step is blocked or failing (403, login wall, CAPTCHA, 429, 5xx, timeout), call `check_incident` with the public hostname. It returns the site's declared agent policy (robots.txt, llms.txt), its official developer path, provider status and one recommended next action.

Lookups are read-only: `check_incident` sends only the hostname and fixed codes, never a URL path, query, credentials or conversation text. Two optional tools send a short report: `rate_last_lookup` (the one-time feedback token and whether the retry worked) and `report_observation` (goes to private review, not published at once; use it only with the user's explicit approval).

If the server also lists `find_task_route`, call it once when you start a customer task on a site (a return or exchange, a refund, a cancellation, a warranty claim, an account recovery, a document submission), before you open its help pages, even when nothing is blocked. It returns recorded task routes for the hostname: records, not a guarantee and not advice, and an empty list does not mean there is no route. `find_task_route` does not change when to call `check_incident` or what `check_incident` recommends. If it is not listed, carry on as usual.

Source: https://github.com/agent-help-network/agent-help-network (Apache-2.0). Independent project, not affiliated with any AI provider or site it describes.
