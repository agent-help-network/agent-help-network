-- D75: changes in a public host's declared access policy, kept as short codes (ahn/host_access_changes.py).
-- One row when a re-read of a host's robots.txt/llms.txt/standard files, or one of AHN's home-page checks, differs from the
-- previous stored value of the same field. The first reading of a host writes nothing. Facts about hosts, not people:
-- no lookup, client, address, file text, path or header value is stored here; from_code and to_code are short codes only.
-- Retention: 90 days, deleted by platform_metrics.prune_analytics.
-- A new table and one index: no existing table is touched or rewritten, so the lock is a short catalog lock.
-- Rollback: first deploy the code without this change (nothing else reads or writes the table), then run:
--           DROP TABLE host_access_changes;
--           DELETE FROM schema_migrations WHERE version=35;
-- With the table missing, the writers log a warning and the readers leave their optional field out; reads and answers continue.

CREATE TABLE IF NOT EXISTS host_access_changes (
    id bigserial PRIMARY KEY,
    site_host varchar(253) NOT NULL,
    at timestamptz NOT NULL,
    -- A fixed list. robots_verdict:<token> carries one agent token of AHN's own token table (ahn/access_profile.py).
    field varchar(64) NOT NULL CHECK (
        field IN ('robots_status','llms_presence','tdm_root','site_declared_api','site_declared_reservation',
                  'home_outcome','bot_vendor')
        OR field ~ '^robots_verdict:[A-Za-z0-9._-]{1,40}$'),
    from_code varchar(24) NOT NULL,
    to_code varchar(24) NOT NULL,
    source varchar(12) NOT NULL CHECK (source IN ('read','home_check')),
    CHECK (from_code <> to_code)
);
CREATE INDEX IF NOT EXISTS host_access_changes_host_at ON host_access_changes (site_host, at);
