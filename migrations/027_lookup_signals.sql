-- Lookup signals, shadow mode (ahn/lookup_signals.py, docs/decisions.md D47).
-- A lookup may carry optional fixed-list codes (HTTP status class, challenge header, Retry-After range, fetch or
-- browser). They are counted per public host and hour. Nothing reads these tables back into an answer yet.
-- Two new tables, no change to an existing one: creating them takes no lock on a table that is in use.
-- Retention is enforced by platform_metrics.prune_analytics: hourly counts 35 days, source digests 2 days.
-- Rollback: DROP TABLE lookup_sources_daily; DROP TABLE lookup_signals_hourly;
--           DELETE FROM schema_migrations WHERE version=27;
-- The application keeps working with the tables missing: the write is a savepoint that logs and carries on.

-- One row per combination of codes in an hour. Every column holds a value from a fixed list, 'unspecified' when the
-- client did not send that code. action is the recommendation the lookup was answered with; after_action is the
-- recommendation the same source got on its previous lookup of this host when this lookup came 10 to 60 minutes
-- later and both were blocked lookups ('none' otherwise), so rows with after_action<>'none' are re-lookups.
-- sources counts lookups that were the first from their source for this host in that hour (a source is counted
-- once per host and hour, in the row of its first lookup); it never exceeds lookups.
CREATE TABLE IF NOT EXISTS lookup_signals_hourly (
    hour timestamptz NOT NULL,
    site_host varchar(253) NOT NULL,
    error_class varchar(32) NOT NULL,
    failure_stage varchar(32) NOT NULL,
    http_status_class varchar(16) NOT NULL,
    challenge varchar(16) NOT NULL,
    retry_after varchar(16) NOT NULL,
    via varchar(16) NOT NULL,
    client_kind varchar(16) NOT NULL,
    action varchar(32) NOT NULL,
    after_action varchar(32) NOT NULL,
    lookups bigint NOT NULL DEFAULT 0 CHECK (lookups >= 0),
    sources bigint NOT NULL DEFAULT 0 CHECK (sources >= 0 AND sources <= lookups),
    PRIMARY KEY (hour, site_host, error_class, failure_stage, http_status_class, challenge, retry_after, via, client_kind, action, after_action)
);
CREATE INDEX IF NOT EXISTS lookup_signals_hourly_host ON lookup_signals_hourly (site_host, hour);

-- To tell separate clients apart without keeping an address: source_digest is a keyed digest of the IP address that
-- is specific to this host and this UTC day, so the same address gives a different value for another host or day.
-- last_at is cut to the minute (the 10 to 60 minute re-lookup window cannot be told apart by hour). Kept 2 days.
CREATE TABLE IF NOT EXISTS lookup_sources_daily (
    day date NOT NULL,
    site_host varchar(253) NOT NULL,
    source_digest char(64) NOT NULL,
    first_hour timestamptz NOT NULL,
    last_at timestamptz NOT NULL,
    last_group varchar(16) NOT NULL,
    last_action varchar(32) NOT NULL,
    PRIMARY KEY (day, site_host, source_digest)
);
