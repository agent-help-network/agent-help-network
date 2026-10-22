-- Adoption measurement (ahn/adoption.py, docs/decisions.md D49): one row per UTC day and combination of fixed-list
-- values, counting answered lookups. No hostname, no address, no digest.
-- One new table, no change to an existing one: creating it takes no lock on a table that is in use.
-- Retention is enforced by platform_metrics.prune_analytics: 90 days.
-- Rollback: DROP TABLE lookup_outcome_daily; DELETE FROM schema_migrations WHERE version=28;
-- The application keeps working with the table missing: the write is a savepoint that logs and carries on.
-- Size is bounded by the fixed lists, not by what clients send: 8 client categories x 6 adapter labels x 6 MCP host
-- labels x 7 actions x 4 profile states is the theoretical ceiling per day (a few dozen rows in practice).

-- client_category: plugin, ahn_client, mcp_host, agent_or_script, browser, unknown (adoption), crawler and web (stored,
--   not counted as adoption). adapter: none, langchain, crewai, openai_agents, claude_agent_sdk, other (only ahn_client
--   rows carry anything but none). mcp_host: none, claude, chatgpt, cursor, vscode, other_mcp (only mcp_host rows).
-- action is the recommendation the lookup was answered with. profile_state is the state of the host's declared-policy
--   layer in that answer: ready (read present and fresh), stale, pending (first lookup, read queued), none (no such
--   layer for this host).
-- sources counts lookups that were the first of a source for a host on that UTC day (the same notion as
--   lookup_sources_daily, which is deleted after 2 days); summed over days it is an upper bound, because a source
--   is counted again each day and once per host.
CREATE TABLE IF NOT EXISTS lookup_outcome_daily (
    day date NOT NULL,
    client_category varchar(16) NOT NULL,
    adapter varchar(16) NOT NULL,
    mcp_host varchar(16) NOT NULL,
    action varchar(32) NOT NULL,
    profile_state varchar(8) NOT NULL,
    lookups bigint NOT NULL DEFAULT 0 CHECK (lookups >= 0),
    sources bigint NOT NULL DEFAULT 0 CHECK (sources >= 0 AND sources <= lookups),
    PRIMARY KEY (day, client_category, adapter, mcp_host, action, profile_state)
);
