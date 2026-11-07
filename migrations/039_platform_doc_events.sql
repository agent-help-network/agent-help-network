-- D107: one row each time a vendor help page listed in ahn/platform_policies.py is seen to have changed
-- (scripts/check_platform_docs.py, at most weekly). Only the page address and two SHA-256 hashes of its normalised
-- text are stored, never the page. prev_hash IS NULL marks the first reading of a page (a baseline, not a change).
-- No request data. New table, no change to an existing one.
-- Rollback: roll the code back first, then DROP TABLE platform_doc_events; DELETE FROM schema_migrations WHERE version=39.
CREATE TABLE IF NOT EXISTS platform_doc_events (
    id         bigserial PRIMARY KEY,
    platform   varchar(40) NOT NULL,
    url        text NOT NULL CHECK (char_length(url) <= 300),
    changed_at timestamptz NOT NULL DEFAULT now(),
    prev_hash  char(64),
    new_hash   char(64) NOT NULL
);
CREATE INDEX IF NOT EXISTS platform_doc_events_url_idx ON platform_doc_events (url, changed_at DESC);
