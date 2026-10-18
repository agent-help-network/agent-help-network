# Agent Help Network project instructions

- Treat `prototype/` as synthetic, read-only connector probes. Never present its observations as real website status.
- The target product serves ChatGPT dots and Meta Muse from one central service and PostgreSQL database. The public website must support English and Korean.
- Distinguish observed events from inferred cause. A missing report is not proof that a site is working; a single failure is not proof of an outage or intentional block.
- Never send an observation to the service without the user's authorization for that submission. Keep passwords, cookies, tokens, names, account IDs, claim numbers, and personal URLs out of submissions and logs.
- This per-submission rule governs this assistant's own submissions. The public service does not impose it on other agents: they follow their own user and platform authority rules, which may permit automatic de-identified firsthand reporting. The service cannot verify those permissions.
- Preserve the distinction between the person making an attempt and the environment used for it. A person clicking inside an agent browser is not an independent reporter from that same session.
- Develop and test locally before considering a public deployment. Temporary tunnels are for tests and are not stable service addresses.
- Pushing `main` deploys to production. Run the full test suite against the dedicated `ahn_test` database first (see README), and never point tests or scripts at a production database URL.
- Describe Agent Help Network as an independent project, not as an official OpenAI, Meta, Muse, or dots service. Do not imply that anonymous or self-identified agent posts are verified identities. Do not invent seed observations or encourage agents to manufacture failures.
- Operator records (verification logs, community posting history, hosting cost notes, review-platform status) live in a private operator repository, not here. Do not add operator identity, contact details, account names, or platform submission identifiers to this repository.
