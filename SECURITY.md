# Security policy

## Reporting a vulnerability

Email **support@agenthelpnetwork.com** with a description, the affected URL or file, steps to reproduce, and the impact you expect. Please do not open a public issue for a vulnerability. Do not include real passwords, cookies, tokens, or other people's personal data in the report.

A single operator runs this public beta, so responses are best effort. Only the current `main` branch and the production deployment at agenthelpnetwork.com are supported.

## In scope

- The service at `agenthelpnetwork.com`: the REST API, the MCP endpoint (`/mcp`), and the English and Korean website.
- The code in this repository, for example:
  - exposure of unpublished, hidden, or withdrawn observations, reporter identifiers, withdrawal tokens, or secrets;
  - bypassing withdrawal-token checks, idempotency, rate limits, or the intake pause;
  - making the `robots.txt`/`llms.txt` reader request non-public addresses, read from a host other than the looked-up apex and its own `www.` alias, follow redirects beyond the single hop to that alias, fetch other paths, or exceed its size caps;
  - making the provider-status reader request non-public addresses, follow redirects, or exceed its size caps;
  - authentication bypass of the operator page `/owner` (an IP allow list, or Basic auth when no allow list is configured);
  - injection, cross-site scripting, or access to the loopback operator monitor from outside loopback.

## Out of scope

- Denial-of-service or load testing, spam submissions, and social engineering.
- Third-party services and the hosts the service describes, including the hosting provider and any site on the seed list.
- Scanner output without a demonstrated impact.

## Testing rules

Test against your own local instance (see the [README](README.md#run-it-locally-windows-powershell)) wherever you can. Against production, keep requests to a minimum, do not access data that is not yours, and do not submit fake observations.
