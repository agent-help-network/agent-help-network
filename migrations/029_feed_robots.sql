-- Robots.txt decisions for status-feed hosts (docs/decisions.md D50, ahn/feed_robots.py). One row per feed host: what
-- the host's own /robots.txt says, for AgentHelpNetwork, about each path the service would request (the feed, the two
-- Statuspage scope lists, the page's Atom history). The file itself is never stored, only its SHA-256, the rule line
-- that decided each path (at most 80 characters) and the answer. The row is replaced whenever the file is read again
-- (about once a day per host; an hour after a failed read). error_at and error_detail record a failed read that left an
-- earlier `ok` answer in force (at most 48 hours, RFC 9309 section 2.4). No request-derived data: a host name from the
-- status-mapping table, or status.<host> of a looked-up host whose page was found by its DNS delegation.
-- Rollback: DROP TABLE feed_robots; then DELETE FROM schema_migrations WHERE version=29. Nothing else reads it: the
-- feeds are then read as before the table existed (the code in this change must be rolled back first).
CREATE TABLE feed_robots (
    feed_host      text PRIMARY KEY CHECK (length(feed_host) BETWEEN 1 AND 253),
    checked_at     timestamptz NOT NULL,
    robots_status  text NOT NULL CHECK (robots_status IN ('ok','absent','unavailable','error')),
    robots_detail  varchar(32),
    robots_sha256  char(64),
    parser_version varchar(100) NOT NULL,
    decisions      jsonb NOT NULL,
    error_at       timestamptz,
    error_detail   varchar(32)
);
