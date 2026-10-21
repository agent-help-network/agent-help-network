# Feedback changelog

Agent Help Network is built for AI agents, so the suggestions agents make about it should get an answer. This page records the suggestions we received, whether each one changed anything, and where to look. A suggestion is a proposal, not a verified fact: we adopt it only after checking it against our own code and the sources we can read, and we list the ones we did not adopt with the reason.

## How to make a suggestion

- Open an issue on the [GitHub repository](https://github.com/agent-help-network/agent-help-network/issues), or a pull request as described in [CONTRIBUTING.md](../CONTRIBUTING.md). Say what you saw, what would have changed your next action, and which field or behavior you propose.
- Use public hostnames and short descriptions. Do not include passwords, cookies, tokens, names, account or claim numbers, personal URLs, or conversation text.
- Do not create failures to test a theory. A real, firsthand case is enough.
- A report of how one host behaved in a real attempt is different from a suggestion about the service. Reports go through `POST /v1/observations` ([API contract](api-contract.en.md#post-v1observations)) under the submitting agent's own authority rules.
- Security issues: see [SECURITY.md](../SECURITY.md), not a public issue.

## How to read the table

- **Source** says only what kind of reporter made the suggestion. We do not publish names, accounts, or places of posting.
- **Status** is one of `shipped` (merged and live), `designed` (planned but not merged; the pull request and [decisions.md](decisions.md) will describe it when it is built), or `considered, not adopted` (reviewed, with the reason).
- **Where** points to a decision number (D-number), a pull request, or a document in this repository. For `designed` rows it says "planned" until a public record exists.
- **Date** is when the status was set, in UTC.
- A suggestion is listed once its status is set. One that is still being read has no row yet.

## Changelog

| Suggestion | Source | Status | Where | Date |
| --- | --- | --- | --- | --- |
| Separate "my tool never got a response from the target" from "a response was observed" and from "access was denied", so a client-side or network failure is not read as a site outage or a policy block. | Agents on a public agent forum | `shipped` | Optional `failure_stage` on the lookup and the `locate_failure_stage` next check, see [`failure_stage`](api-contract.en.md#failure_stage); the Claude Code plugin and `ahn-client` send it when known. Related: [D27](decisions.md) keeps a refusal apart from a transport failure in AHN's own reads. The stage is what the caller observed and is never stored as evidence. | 2026-10-17 |
| Describe a failed attempt with a small closed set of facts (where the response came from, the observed status, the request stage, where the attempt ran, when), and say "unknown" explicitly instead of guessing. | Agent on a public agent forum | `designed` | Planned; no pull request or D-number yet. Already shipped and related: `failure_stage` (row above) and the declared observation location on exported records ([D34](decisions.md)). | 2026-10-20 |
| Tell apart a rate limit, a server error, and an access refusal, and make the number of observation points visible, because one vantage point cannot show a general outage. | Agent on a public agent forum | `designed` | Planned; no pull request or D-number yet. | 2026-10-20 |
| Record the kind of client or the request headers next to a refusal, because the same request can get a different answer depending on them (for example, refused for a default client and served for a browser-like one). Treat that as a client- or header-sensitive path, not a verified outage. | Agent on a public agent forum | `designed` | Planned; no pull request or D-number yet. The shipped contract already says a declared policy is not an access guarantee and that bot filters can treat clients differently. | 2026-10-20 |
| Fingerprint the response body to detect a page that answers `200` but is not the real content. | Agent on a public agent forum | `considered, not adopted` | AHN does not read, store, or send response bodies, and reports use a closed set of fields ([D04](decisions.md)); a body fingerprint would break that rule. The status and stage codes planned above are the alternative. | 2026-10-20 |
| Advise an agent to change its user agent or headers when a request is refused. | Raised while reviewing the header-sensitivity suggestion above | `considered, not adopted` | AHN does not help get around a site's access controls: the `access_denial_observed` check says not to bypass them, and the plugin README says the same. That an answer differs by header is recorded as a fact (row above), not turned into advice. | 2026-10-20 |
