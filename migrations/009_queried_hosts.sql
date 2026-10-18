-- Register only eligible public apex hosts that were actually looked up.
CREATE TABLE queried_hosts (
    site_host varchar(253) PRIMARY KEY,
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX queried_hosts_last_seen_idx ON queried_hosts (last_seen_at DESC);
ALTER TABLE status_cache ALTER COLUMN fetched_at DROP NOT NULL;

-- Remove the old eager seed rows on databases that already ran migration 007.
-- A later lookup recreates the verified mapping and fetches the official API.
DELETE FROM status_cache WHERE site_host IN
    ('github.com','openai.com','chatgpt.com','cloudflare.com');
DELETE FROM status_sources WHERE site_host IN
    ('github.com','openai.com','chatgpt.com','cloudflare.com');
