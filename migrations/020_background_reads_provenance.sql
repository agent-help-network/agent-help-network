-- Lookups never wait on a third-party host: robots.txt/llms.txt reads, status-feed reads and
-- Statuspage discovery are queued here and done by the web process's background worker and the
-- 5-minute review cron. One row per (kind, host), so repeated lookups collapse into one read;
-- a row is deleted as soon as it is processed, or after five failed processing attempts.
CREATE TABLE background_reads (
    kind varchar(32) NOT NULL CHECK (kind IN ('access_profile','status_feed','status_discovery')),
    site_host varchar(253) NOT NULL,
    requested_at timestamptz NOT NULL DEFAULT now(),
    not_before timestamptz NOT NULL DEFAULT now(),
    attempts integer NOT NULL DEFAULT 0,
    claimed_at timestamptz,
    PRIMARY KEY (kind, site_host)
);
CREATE INDEX background_reads_due_idx ON background_reads (not_before, requested_at);

-- Provenance of each stored fact: which operator read it, from where (vantage), with which reader,
-- and a SHA-256 of the bytes received (the file itself is not stored). Existing rows stay NULL:
-- a hash of a file that was never kept cannot be reconstructed honestly.
ALTER TABLE host_access_profile
    ADD COLUMN origin_operator text,
    ADD COLUMN vantage jsonb,
    ADD COLUMN reader_version text,
    ADD COLUMN robots_sha256 char(64),
    ADD COLUMN llms_sha256 char(64);

ALTER TABLE status_cache
    ADD COLUMN origin_operator text,
    ADD COLUMN vantage jsonb,
    ADD COLUMN reader_version text,
    ADD COLUMN body_sha256 char(64);

-- Why a host is mapped to a status source. Filled for automatic discovery (evidence
-- 'discovered_cname'); the hand-curated mappings in code are left NULL for now.
ALTER TABLE status_sources
    ADD COLUMN curated_by text,
    ADD COLUMN evidence text,
    ADD COLUMN evidence_url text;

-- Which operator received a report, its id there, and the interface it arrived through.
ALTER TABLE observations
    ADD COLUMN origin_operator text,
    ADD COLUMN origin_observation_id uuid,
    ADD COLUMN received_channel varchar(8) CHECK (received_channel IN ('rest','mcp','web'));
