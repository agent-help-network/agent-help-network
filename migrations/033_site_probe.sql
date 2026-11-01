-- D67: the AHN server check of a host's home page (ahn/site_probe.py). One GET of "/" from our server for a limited set
-- of public hosts, unless the host's robots.txt disallows "/" for us. Codes only are kept: never the page, a header
-- value, a cookie value or a redirect path.
-- New tables plus one nullable column and a wider CHECK on the small work-queue table: nothing here rewrites a large table.
-- Rollback: DROP TABLE site_probe, site_probe_daily, probe_breaker_daily, site_probe_set, probe_state;
--           ALTER TABLE host_access_profile DROP COLUMN home_gate;
--           (and, with no site_probe row left in background_reads, restore the three-kind CHECK on background_reads.kind)
--           DELETE FROM schema_migrations WHERE version=33;
-- The application keeps working with these objects missing: every use is in a savepoint that logs and carries on.

-- The robots.txt verdict for "/", decided in memory while the profile is read (no extra request) and kept with the
-- profile row: allowed, why not, the deciding rule line (at most 80 characters), the host read and the read time.
ALTER TABLE host_access_profile ADD COLUMN home_gate jsonb;

-- The work queue gets a fourth kind of read.
ALTER TABLE background_reads DROP CONSTRAINT IF EXISTS background_reads_kind_check,
    ADD CONSTRAINT background_reads_kind_check CHECK (kind IN ('access_profile','status_feed','status_discovery','site_probe'));

-- The latest check of each host: one current row, deleted 30 days after the check (platform_metrics.prune_analytics).
CREATE TABLE IF NOT EXISTS site_probe (
    site_host varchar(253) PRIMARY KEY,
    read_host varchar(253) NOT NULL,
    probed_at timestamptz NOT NULL,
    outcome varchar(24) NOT NULL,
    http_status smallint,
    bot_vendor varchar(24),
    bot_signal varchar(8),
    bot_confidence varchar(8),
    edge_vendors text[] NOT NULL DEFAULT '{}',
    redirect jsonb,
    login_signal varchar(12),
    response_ms integer,
    bytes_read integer,
    not_measured_reason varchar(24),
    fingerprint_version varchar(16) NOT NULL,
    next_due_at timestamptz NOT NULL,
    consecutive_refusals integer NOT NULL DEFAULT 0,
    consecutive_failures integer NOT NULL DEFAULT 0,
    trigger_kind varchar(8) NOT NULL,
    origin_operator varchar(64),
    vantage jsonb,
    user_agent varchar(200)
);

-- Daily counts of checks by outcome, bot-defense provider and what started them, per host (35 days).
CREATE TABLE IF NOT EXISTS site_probe_daily (
    day date NOT NULL,
    site_host varchar(253) NOT NULL,
    outcome varchar(24) NOT NULL,
    bot_vendor varchar(24) NOT NULL DEFAULT '',
    trigger_kind varchar(8) NOT NULL,
    probes integer NOT NULL DEFAULT 0 CHECK (probes >= 0),
    PRIMARY KEY (day, site_host, outcome, bot_vendor, trigger_kind)
);

-- The automatic stop (35 days): per day and group (control = seed hosts that are not checked, probe_set = hosts that are),
-- robots.txt reads and how many were refused (401/403), and failed status-feed reads with 403 or 429. No hostname.
CREATE TABLE IF NOT EXISTS probe_breaker_daily (
    day date NOT NULL,
    grp varchar(16) NOT NULL,
    robots_reads integer NOT NULL DEFAULT 0 CHECK (robots_reads >= 0),
    robots_refused integer NOT NULL DEFAULT 0 CHECK (robots_refused >= 0),
    feed_403 integer NOT NULL DEFAULT 0 CHECK (feed_403 >= 0),
    feed_429 integer NOT NULL DEFAULT 0 CHECK (feed_429 >= 0),
    PRIMARY KEY (day, grp)
);

-- Today's list of hosts to check ahead of any lookup (hostname, which list, rank), replaced once a day.
CREATE TABLE IF NOT EXISTS site_probe_set (
    site_host varchar(253) PRIMARY KEY,
    set_code varchar(1) NOT NULL,
    rank integer NOT NULL,
    set_day date NOT NULL
);

-- Operator state of the check: the baseline refusal rate taken before the first check, and the automatic stop.
CREATE TABLE IF NOT EXISTS probe_state (
    name varchar(32) PRIMARY KEY,
    value jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
