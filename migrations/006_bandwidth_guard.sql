-- Hourly provider samples are retained locally because Render Hobby metrics
-- are available for only seven days. Values are service metrics, not invoices.
CREATE TABLE bandwidth_guard_samples (
    resource_id varchar(64) NOT NULL,
    sample_at timestamptz NOT NULL,
    gigabytes numeric(18,9) NOT NULL CHECK (gigabytes >= 0),
    PRIMARY KEY (resource_id, sample_at)
);
CREATE TABLE bandwidth_guard_state (
    name varchar(40) PRIMARY KEY,
    period_start timestamptz NOT NULL,
    checked_at timestamptz NOT NULL,
    latest_sample_at timestamptz,
    usage_gb numeric(18,6) NOT NULL CHECK (usage_gb >= 0),
    state varchar(16) NOT NULL CHECK (state IN ('normal','warning','suspended')),
    warning_sent_at timestamptz,
    suspended_at timestamptz
);
