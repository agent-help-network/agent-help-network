-- The offline data bundle (ahn/bundle.py): the latest gzip JSON Lines file, rebuilt about once a day by the
-- review cron while AHN_PUBLIC_BUNDLE=1 and served by the web service. One row, replaced on each build and
-- deleted by the cron when the switch is off. It holds only curated seed-host robots.txt/llms.txt reads and the
-- curated tables from the code. served_on/served_bytes count the bytes of full downloads sent on one UTC day
-- (the daily budget survives restarts); no client data is stored.
CREATE TABLE IF NOT EXISTS public_bundle (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    generated_at timestamptz NOT NULL,
    sha256 char(64) NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    record_count integer NOT NULL CHECK (record_count >= 0),
    byte_size integer NOT NULL CHECK (byte_size > 0),
    body bytea NOT NULL,
    served_on date,
    served_bytes bigint NOT NULL DEFAULT 0 CHECK (served_bytes >= 0)
);
