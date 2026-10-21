-- Signal history for the "Site problems right now" board (ahn/signal_history.py, docs/decisions.md D41).
-- Facts about curated public hostnames only: the outcome of AHN's own robots.txt reads and changes in a
-- provider's published status. No lookup, report, IP address, client or file content is stored here.
-- Nothing is public yet; the operator view (/owner) reads these tables. Retention is enforced by
-- platform_metrics.prune_analytics: raw read events 14 days, periods, provider changes and daily totals 90 days.
-- Rollback: DROP the four tables, then DELETE FROM schema_migrations WHERE version=24.

-- One row per robots.txt read of a curated host, written where the read result is stored (get_profile).
-- a_outcome is the board's classification (responding, no_response:*, tls_error, server_error, refused, other).
-- detail is the reader's label: a failure label, or for an answer its HTTP status or redirect target host.
-- applied_at, held and hold_reason are set by the rollup once the read's 15-minute window has closed.
-- Whether a read followed a lookup is not kept here; robots_read_daily counts it per host and day only.
CREATE TABLE IF NOT EXISTS robots_read_events (
    id bigserial PRIMARY KEY,
    site_host text NOT NULL,
    read_at timestamptz NOT NULL,
    read_host text NOT NULL,
    robots_status text NOT NULL,
    detail text,
    a_outcome text NOT NULL,
    origin_operator text NOT NULL,
    vantage jsonb NOT NULL,
    applied_at timestamptz,
    held boolean NOT NULL DEFAULT false,
    hold_reason text
);
CREATE INDEX IF NOT EXISTS robots_read_events_read_at ON robots_read_events (read_at);
CREATE INDEX IF NOT EXISTS robots_read_events_pending ON robots_read_events (read_at) WHERE applied_at IS NULL;
CREATE INDEX IF NOT EXISTS robots_read_events_host ON robots_read_events (site_host, read_at);

-- One row per change of a connected provider feed's indicator for a curated host (written by refresh_one).
CREATE TABLE IF NOT EXISTS provider_status_events (
    id bigserial PRIMARY KEY,
    site_host text NOT NULL,
    at timestamptz NOT NULL,
    from_indicator text,
    to_indicator text NOT NULL CHECK (to_indicator IN ('none','minor','major','critical','maintenance','unknown')),
    source_updated_at timestamptz,
    applied_at timestamptz
);
CREATE INDEX IF NOT EXISTS provider_status_events_host ON provider_status_events (site_host, at DESC);
CREATE INDEX IF NOT EXISTS provider_status_events_pending ON provider_status_events (at) WHERE applied_at IS NULL;

-- Problem periods per host and signal, derived by the rollup. Signal A (AHN check): opened by the first
-- counted read failure (a suspect while confirmed_at is NULL), confirmed after repeated failures over a
-- minimum time, ended by an answer (ended_reason answered, or answered_refused / answered_other when a
-- refusal or another non-failure ends a no-response or TLS period). Signal P (provider): one period per
-- published problem indicator, ended_reason provider_changed.
CREATE TABLE IF NOT EXISTS outage_episodes (
    id bigserial PRIMARY KEY,
    site_host text NOT NULL,
    signal char(1) NOT NULL CHECK (signal IN ('A','P')),
    kind text NOT NULL,
    first_fail_at timestamptz NOT NULL,
    confirmed_at timestamptz,
    ended_at timestamptz,
    ended_reason text,
    last_event_at timestamptz NOT NULL,
    reads integer NOT NULL DEFAULT 0 CHECK (reads >= 0),
    ok_streak integer NOT NULL DEFAULT 0 CHECK (ok_streak >= 0),
    held_overlap integer NOT NULL DEFAULT 0 CHECK (held_overlap >= 0),
    feed_connected boolean NOT NULL DEFAULT false,
    matched_episode_id bigint
);
CREATE UNIQUE INDEX IF NOT EXISTS outage_episodes_one_open ON outage_episodes (site_host, signal) WHERE ended_at IS NULL;
CREATE INDEX IF NOT EXISTS outage_episodes_first_fail ON outage_episodes (first_fail_at);

-- Daily per-host totals of robots.txt reads, kept longer than the raw events for the 30-day baseline.
-- lookup_reads and seed_reads are added when a read is recorded; the other counts by the rollup.
CREATE TABLE IF NOT EXISTS robots_read_daily (
    site_host text NOT NULL,
    day date NOT NULL,
    reads integer NOT NULL DEFAULT 0 CHECK (reads >= 0),
    responding integer NOT NULL DEFAULT 0 CHECK (responding >= 0),
    failed integer NOT NULL DEFAULT 0 CHECK (failed >= 0),
    refused integer NOT NULL DEFAULT 0 CHECK (refused >= 0),
    held integer NOT NULL DEFAULT 0 CHECK (held >= 0),
    lookup_reads integer NOT NULL DEFAULT 0 CHECK (lookup_reads >= 0),
    seed_reads integer NOT NULL DEFAULT 0 CHECK (seed_reads >= 0),
    PRIMARY KEY (site_host, day)
);
CREATE INDEX IF NOT EXISTS robots_read_daily_day ON robots_read_daily (day);
