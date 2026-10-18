CREATE TABLE host_access_profile (
    site_host varchar(253) PRIMARY KEY,
    fetched_at timestamptz,
    robots_status varchar(16) NOT NULL DEFAULT 'pending',
    robots jsonb,
    llms_txt_status varchar(16) NOT NULL DEFAULT 'pending',
    last_error_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX report_submissions_observation_idx ON report_submissions (observation_id);
