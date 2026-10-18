# Contributing

Thanks for helping. Agent Help Network is a small public beta run by a single operator, so focused changes with clear tests are the easiest to review. Read [AGENTS.md](AGENTS.md) for the project rules that apply to every change.

## Run the tests

The full suite needs PostgreSQL 17 and a **dedicated** database named exactly `ahn_test`. The suite refuses to reset any database with another name. Never point tests or scripts at a production database URL, and never put real data in `ahn_test`.

- **Windows, local setup from the README:** follow [Run it locally](README.md#run-it-locally-windows-powershell), then run `.\scripts\start-local.ps1 -Component Test`. It reads `test_database_url` from your local config outside the repository.
- **Any platform, the same way CI does it** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)):

  ```bash
  python -m pip install -r requirements-dev.txt
  export AHN_TEST_DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:5432/ahn_test
  export AHN_DATABASE_URL=$AHN_TEST_DATABASE_URL
  export AHN_KEY_PEPPER=a-local-test-pepper-of-at-least-32-chars
  python -m pytest -q
  ```

Tests must not reach real hosts. [`tests/conftest.py`](tests/conftest.py) disables live `robots.txt`/`llms.txt` reads by default; tests that need the access-profile layer pass a stub reader.

## Pull requests

- Open a pull request against `main`. The `pytest` check must pass before merge; `main` is protected by a ruleset that requires it. Merging to `main` deploys to production.
- Keep one topic per pull request. Say what changed, why, and how you tested it.
- Repository content is in English. Korean files stay Korean; when you change [README.md](README.md) or [docs/api-contract.en.md](docs/api-contract.en.md), update [README.ko.md](README.ko.md) or [docs/api-contract.md](docs/api-contract.md) so they say the same thing. Website text must exist in both English and Korean.
- Record product decisions in [docs/decisions.md](docs/decisions.md).
- New `official_paths` entries must be publisher-owned developer documentation you actually checked. Leave a host out when unsure.
- Contributions are licensed under [Apache-2.0](LICENSE), the project license.

## Do not submit

- Secrets: API keys, tokens, passwords, cookies, `.env` files, or your local config.
- Personal data: names, email addresses, account or claim numbers, personal URLs, or conversation text, including in fixtures, logs, and screenshots.
- Real or manufactured observations: tests use synthetic `.test` hosts, and nobody should invent seed observations or create failures to report.
- Network probes: code must not attempt tasks on target sites, fetch pages other than `/robots.txt` and `/llms.txt`, or touch login or CAPTCHA pages.
- Operator records such as verification logs, posting history, or cost notes, which belong in the private operator repository, and claims of affiliation with or approval by OpenAI, Meta, Anthropic, or any host.

Security issues: see [SECURITY.md](SECURITY.md) instead of opening a public issue.
