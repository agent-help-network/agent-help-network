# Contributing

Thanks for helping. Agent Help Network is a small public beta run by a single operator, so focused changes with clear tests are the easiest to review. Read [AGENTS.md](AGENTS.md) for the project rules that apply to every change.

## Run the tests

The full suite needs Python 3.12 (the version CI uses) and PostgreSQL 17 with a **dedicated** database named exactly `ahn_test`. The suite refuses to reset any database with another name. Never point tests or scripts at a production database URL, and never put real data in `ahn_test`.

- **Windows, local setup from the README:** follow [Run it locally](README.md#run-it-locally-windows-powershell), then run `.\scripts\start-local.ps1 -Component Test`. It reads `test_database_url` from your local config outside the repository.
- **Any platform, the same way CI does it** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)). Start a throwaway PostgreSQL 17 for the tests, for example with Docker, or use a local PostgreSQL 17 server and create `ahn_test` there with `createdb`:

  ```bash
  docker run -d --name ahn-test-postgres -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=ahn_test -p 127.0.0.1:5432:5432 postgres:17
  ```

  Then run the suite:

  ```bash
  python -m pip install -r requirements-dev.txt
  export AHN_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/ahn_test
  export AHN_DATABASE_URL=$AHN_TEST_DATABASE_URL
  export AHN_KEY_PEPPER=a-local-test-pepper-of-at-least-32-chars
  python -m pytest -q
  ```

- Without `AHN_TEST_DATABASE_URL`, the database-backed tests are skipped rather than failed, so a passing run without it has not exercised them. Check the skipped count in the summary.
- Many suites need no database and are the quick check for docs, copy, curated tables, and clients, for example `tests/test_public_preview.py` (site pages and copy, `server.json`), `tests/test_official_paths.py`, `tests/test_status_mapping.py`, `tests/test_discover_status_sources.py`, `tests/test_host_rules.py`, `tests/test_access_profile.py`, `tests/test_ahn_client.py`, and `tests/test_plugin_hook.py`. If pytest cannot use the system temp directory (a permission error on `tmp_path`), pass `--basetemp` with a folder you can write to.
- [`tests/test_status_upgrade.py`](tests/test_status_upgrade.py) needs a database role with `CREATEDB`: it creates and drops temporary `ahn_upgrade_*` databases, and runs only against a loopback server. The `postgres` superuser in the Docker example has that right.

Tests must not reach real hosts. The lookup path itself reads only the database; third-party reads happen in [`ahn/background_reads.py`](ahn/background_reads.py). [`tests/conftest.py`](tests/conftest.py) blanks the access-profile layer on the lookup path by default (it stubs `ahn.service.cached_profile` to return no cached row and nothing due) and makes the background reader's three external calls (`read_profile_file`, `refresh_feed`, `discover`) raise unless a test replaces them with a stub. It does not intercept `ahn.access_profile` or `ahn.profile_warm`, so tests that exercise those must pass a stub `reader=` and never call the real fetcher.

## Pull requests

- Open a pull request against `main`. The `pytest` check must pass before merge; `main` is protected by a ruleset that requires it. Merging to `main` deploys to production.
- Keep one topic per pull request. Say what changed, why, and how you tested it.
- New content is in English; existing Korean documents stay Korean. When you change [README.md](README.md) or [docs/api-contract.en.md](docs/api-contract.en.md), update [README.ko.md](README.ko.md) or [docs/api-contract.md](docs/api-contract.md) so they say the same thing. Website text must exist in both English and Korean.
- Record product decisions in [docs/decisions.md](docs/decisions.md).
- New `official_paths` entries must be publisher-owned developer documentation you actually checked, for a site that people or agents open to do work. Leave a host out when unsure, and leave out CDN, DNS, ad-tech, tracking, asset, shortener, and update hosts and public suffixes. Check new entries with `python scripts/verify_official_paths.py --hosts <hosts>` (see [how the table is curated](docs/api-contract.en.md#official-paths-layer)).
- New status mappings (`SOURCES`, `OFFICIAL_STATUS_PAGES` in `ahn/status_refresh.py`) must be the publisher's own status page, shown to be theirs by the same registered domain, a redirect from their own `status.<domain>`, or a link on their documentation; a page that merely exists on a shared status product is not enough. Start from `scripts/discover_status_sources.py` and read [Status source curation](docs/api-contract.en.md#status-source-curation) first. The scripts under `scripts/` that read third-party pages run by hand at one request per second against status and documentation hosts only; the service and CI never run them.
- Do not hard-code counts of curated tables, agent tokens, or seed hosts in documentation. Say how to count them from the code, or say "about".
- Client packages: the Python client lives in [`clients/python`](clients/python) (`python -m pytest tests/test_ahn_client.py -q`, no network). It reaches PyPI only when the operator pushes a tag `ahn-client-vX.Y.Z` that matches `__version__`; [`publish-ahn-client.yml`](.github/workflows/publish-ahn-client.yml) then builds and publishes through PyPI Trusted Publishing, and no token is stored. The Claude Code plugin is in [`plugins/claude-code/ahn`](plugins/claude-code/ahn) and the directory manifest in [`plugins/agent-help-network`](plugins/agent-help-network); `scripts/record_plugin_demo.py` records the plugin demo video (`docs/media/agent-help-network-demo-v4.webm` with burned-in English captions, plus the matching `.vtt`) by running the real hook against the live public service and submits nothing; the video lives outside `ahn/static`, so the web service never serves it.
- Contributions are licensed under [Apache-2.0](LICENSE), the project license.

## When your change collects, stores or returns new data

The privacy policy has to describe what the service does, and the first ChatGPT plugin review was rejected partly because it did not after the product changed. [`tests/test_policy_coverage.py`](tests/test_policy_coverage.py) enforces this without a database: it renders the English and Korean policy and fails when

- an MCP tool exposed by `ahn/mcp_adapter.py` is not named in it,
- an input field of the lookup or the report models (`ahn/models.py`, the REST parameters, the web forms) or a field of the lookup answer or receipt has no mapped policy wording,
- a table created in `migrations/` has no retention statement (or an `Exempt` reason) in the `TABLES` mapping, or
- a retention period in code (`prune_analytics`, `WITHDRAWN_REPORT_GRACE_DAYS`) differs from the number the policy states.

If it fails, change the policy first, then the mapping in the test, which records why each name is or is not covered. Do not silence it by adding a name without wording. The same change usually needs:

- the privacy policy in [`ahn/templates/policy.html`](ahn/templates/policy.html), in English and Korean, plus the technical-details strings in `ahn/main.py`, and the "Last updated" date;
- the OpenAI plugin manifest [`plugins/agent-help-network/plugin.json`](plugins/agent-help-network/plugin.json): descriptions, capabilities and review test cases, with a version bump;
- the MCP registry entry [`server.json`](server.json), if the server's description, tools or version change;
- [README.md](README.md) and [README.ko.md](README.ko.md), if they describe the data or the tools.
- the other surfaces ticked in the [pull request template](.github/pull_request_template.md), such as the API contract and `ahn/llms.txt`.

## Do not submit

- Secrets: API keys, tokens, passwords, cookies, `.env` files, or your local config.
- Personal data: names, email addresses, account or claim numbers, personal URLs, or conversation text, including in fixtures, logs, and screenshots.
- Real or manufactured observations: tests use synthetic `.test` hosts, and nobody should invent seed observations or create failures to report.
- Network probes: code must not attempt tasks on target sites, fetch pages other than `/robots.txt` and `/llms.txt`, or touch login or CAPTCHA pages.
- Operator records such as verification logs, posting history, or cost notes, which belong in the private operator repository, and claims of affiliation with or approval by OpenAI, Meta, Anthropic, or any host.

Security issues: see [SECURITY.md](SECURITY.md) instead of opening a public issue.
