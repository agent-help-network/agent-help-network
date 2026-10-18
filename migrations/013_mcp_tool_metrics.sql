CREATE TABLE mcp_check_metrics (
    day date NOT NULL,
    evidence_level varchar(32) NOT NULL CHECK (evidence_level IN ('no_reports', 'single_observation', 'multiple_observations', 'later_success_observed', 'stale_reports')),
    provider_signal varchar(12) NOT NULL CHECK (provider_signal IN ('active', 'clear', 'unavailable')),
    calls integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
    PRIMARY KEY (day, evidence_level, provider_signal)
);
