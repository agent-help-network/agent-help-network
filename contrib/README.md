# contrib/

An inbox for data you would like to send back. If you use the Agent Help Network data and have a corrected or missing official developer path, status-page mapping or access bundle, you can add a small JSON Lines file here in a pull request. Nothing is expected of you, and the data stays under CC BY 4.0 either way. The page [agenthelpnetwork.com/en/contribute](https://agenthelpnetwork.com/en/contribute) says the same in plainer words, and an [issue form](https://github.com/agent-help-network/agent-help-network/issues/new?template=data-contribution.yml) works too if you would rather not write a file.

## What happens to a file

We read the public source again ourselves and apply what matches it under the usual curation rules (publisher-owned pages, short verbatim quotes, no guesses). **A contribution pull request is not merged into `main` as it stands.** The operator applies the confirmed content in a separate curation pull request of their own and closes yours with thanks, so a commit's author details and anything personal in the file do not end up in the project's history. The pull request page itself still shows your git author name, as GitHub always does. If you want a name or handle in the change record, put it in `credit`; without it we leave names out. What we cannot confirm we leave out and say why on the pull request.

## The file

A name like `2026-11-03-my-handle.jsonl` (lowercase letters, digits, dots, dashes, underscores; ending in `.jsonl`), UTF-8, one JSON object per line, at most 200 lines and 256 KB.

A line is a bundle record ([API contract, Offline bundle](../docs/api-contract.en.md#offline-bundle)) trimmed to what you know. Leave out the other envelope fields (`record_id`, `received_at`, `signature` and so on); we fill them in.

| Field | Meaning |
|---|---|
| `schema_version` | `1` |
| `kind` | `official_path` or `status_source` (an access bundle goes inside an `official_path` record, see below) |
| `observed_at` | When you looked at the source, UTC, like `2026-10-31T09:30:00Z` |
| `source_url` | The public page where you confirmed it: a plain `https://` address on a public host. Optional only for an `official_path` record that says the site has no public API |
| `payload` | The data, below |
| `credit` | Optional. A name or handle for the change record (60 characters or fewer) |

`official_path` payload: `site_host` (lowercase hostname like `example.com`), `name`, and `api_docs` or `developer_portal` (or both). A site with no public API is welcome too: leave both out (or `null`) and say so in `note`; `source_url` is then optional. `access` is optional.
`status_source` payload: `site_host`, `source_name`, `status_page_url`; optional `api_url` and `feed_kind` (they go together; leave both out for a plain link to the status page). `feed_kind` is one of `statuspage`, `instatus`, `betterstack`, `google_gmail`.
`access` (inside an `official_path` payload) follows [the access bundle](../docs/api-contract.en.md#access-bundle-official_pathsaccess-2026-10-20): `auth`, `key_signup_url`, `free_tier`, `rate_limits_url`, `sandbox_url`, `openapi_url`, `official_mcp`, `terms` and `verified_at` (a date). Quotes are verbatim, one line, 200 characters or fewer. Leave out a field you did not see rather than writing `null`.

Examples (made-up host; each line stands alone):

```jsonl
{"schema_version":1,"kind":"official_path","observed_at":"2026-10-31T09:30:00Z","source_url":"https://docs.example.com/api","payload":{"site_host":"example.com","name":"Example API","api_docs":"https://docs.example.com/api","developer_portal":null,"note":null},"credit":"a-handle-if-you-like"}
{"schema_version":1,"kind":"status_source","observed_at":"2026-10-31T09:35:00Z","source_url":"https://status.example.com/","payload":{"site_host":"example.com","source_name":"Example Status","status_page_url":"https://status.example.com/","api_url":"https://status.example.com/api/v2/summary.json","feed_kind":"statuspage"}}
{"schema_version":1,"kind":"official_path","observed_at":"2026-10-31T09:40:00Z","source_url":"https://docs.example.com/api/auth","payload":{"site_host":"example.com","name":"Example API","api_docs":"https://docs.example.com/api","developer_portal":null,"note":null,"access":{"auth":[{"method":"api_key","source_url":"https://docs.example.com/api/auth"}],"key_signup_url":"https://docs.example.com/keys","verified_at":"2026-10-31"}}}
```

## What we cannot take

Public hostnames and public pages only. Please leave out personal URLs, anything behind a login, private or personal information, keys, tokens, cookies, email addresses and other people's conversations. The check turns away addresses that carry a port or credentials or are not plain `https://`, email addresses (also written `(at)`, `[at]` or with a full-width `@`), anything shaped like a key or token (common provider formats, JWTs, webhook URLs), text over 2,000 characters in one value, direction-control characters, symbolic links and JSON nested very deeply. A query string or a fragment is allowed and a fragment is fine as a page anchor; a query string only produces a warning for the reviewer, unless it carries a name such as `key`, `token` or `sig`, which is turned away. The check can miss things, so a person still reads every contribution. If it is not on a public page that we can read as well, we cannot check it.

## Checking the shape

```
python scripts/check_contrib.py            # every contrib/*.jsonl
python scripts/check_contrib.py my.jsonl   # one file
```

It checks the shape only (allowed fields, hostname grammar, plain https URLs, nothing private or secret-looking) and makes no network request. CI runs the same check over everything in this folder. A warning, such as a URL outside the host's own domain or a query string, does not fail the check; it just tells the reviewer where to look.
