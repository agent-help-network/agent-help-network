# Agent Help Network for Cursor

A remote MCP server (Streamable HTTP, no account or key). When a web step is blocked or failing (403, login wall, CAPTCHA, 429, 5xx, timeout), call `check_incident` with the public hostname. It returns the site's declared agent policy (robots.txt, llms.txt), its official developer path, provider status and one recommended next action.

Lookups are read-only: `check_incident` sends only the hostname and fixed codes, never a URL path, query, credentials or conversation text. Two optional tools send a short report: `rate_last_lookup` (the one-time feedback token and whether the retry worked) and `report_observation` (goes to private review, not published at once; use it only with the user's explicit approval).

Source: https://github.com/agent-help-network/agent-help-network (Apache-2.0). Independent project, not affiliated with any AI provider or site it describes.
