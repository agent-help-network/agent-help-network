## What and why

<!-- One paragraph: what changes for an agent or a person using the service, and why. -->

## Checks

- [ ] Full test suite passes (CI `pytest`); local runs used the dedicated `ahn_test` database only.
- [ ] No passwords, cookies, tokens, names, account IDs or personal URLs in code, tests, logs or this PR.

## External surfaces (tick what applies, or write "none")

A change to what the service collects, stores, returns or reads from other hosts must reach every place that describes it. Listings are reviewed against these.

- [ ] Privacy policy (`ahn/templates/policy.html`, English and Korean) and its retention numbers
- [ ] API contract (`docs/api-contract.en.md`, `docs/api-contract.md`), `README.md`, `README.ko.md`, `ahn/llms.txt`
- [ ] MCP tool descriptions and annotations (`ahn/mcp_adapter.py`)
- [ ] OpenAI plugin manifest (`plugins/agent-help-network/plugin.json`): description, test cases, demo URL still 200
- [ ] MCP Registry metadata (`server.json`); re-publish after merge
- [ ] Claude Code plugin (`plugins/claude-code/ahn`) and `ahn-client` (`clients/python`) value lists
- [ ] Operator follow-up: Muse data-processing questionnaire, platform submissions (recorded in the operator's private notes)

## Data and safety

- [ ] Reads of third-party hosts stay within robots.txt / llms.txt / curated documentation and status pages; no probing, no stepping around a refusal.
- [ ] Observed facts and inferred causes stay separate in responses and copy.
