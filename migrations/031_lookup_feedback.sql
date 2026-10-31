-- Lookup feedback (docs/decisions.md D58, ahn/feedback.py). A blocked lookup's answer may carry a one-time feedback
-- token; the caller may send it back with an outcome code (POST /v1/feedback, MCP rate_last_lookup). Nothing is stored
-- when a token is issued: it is a signed, self-contained value. Two new tables, no change to an existing one.
-- Retention is enforced by platform_metrics.prune_analytics: hourly counts 35 days, used-token fingerprints until the
-- token expires (one hour after the lookup; the cleanup runs every 15 minutes, and each new feedback also removes
-- expired ones).
-- Rollback: DROP TABLE lookup_feedback_used; DROP TABLE lookup_feedback_hourly;
--           DELETE FROM schema_migrations WHERE version=31.
-- With the tables missing, POST /v1/feedback answers 503 and lookups are unaffected (a token stores nothing).

-- One row per (issue hour of the lookup, public hostname, recommendation action, client kind, outcome, path). Every
-- column but hour holds a value from a fixed list or the eligible apex hostname the lookup was counted under. At most
-- 200 combinations per hour; more fold into one '(overflow)' row per outcome and path.
CREATE TABLE IF NOT EXISTS lookup_feedback_hourly (
    hour timestamptz NOT NULL,
    site_host varchar(253) NOT NULL,
    action varchar(32) NOT NULL,
    client_kind varchar(16) NOT NULL,
    outcome varchar(16) NOT NULL CHECK (outcome IN ('succeeded','still_blocked','handed_off','gave_up')),
    path varchar(16) NOT NULL CHECK (path IN ('official_path','same_site','unknown')),
    responses bigint NOT NULL DEFAULT 0 CHECK (responses >= 0),
    PRIMARY KEY (hour, site_host, action, client_kind, outcome, path)
);

-- A token that has been used: the SHA-256 of the token text and when the token expires. No hostname, address or
-- outcome. Only there so a token cannot be used twice; deleted once expires_at has passed.
CREATE TABLE IF NOT EXISTS lookup_feedback_used (
    token_digest char(64) PRIMARY KEY,
    expires_at timestamptz NOT NULL
);
