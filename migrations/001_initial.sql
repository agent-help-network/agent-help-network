CREATE TABLE IF NOT EXISTS schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE reporters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    key_hash char(64) NOT NULL UNIQUE,
    label varchar(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    disabled_at timestamptz
);

CREATE TABLE incidents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    site_host varchar(253) NOT NULL,
    task_step varchar(64) NOT NULL,
    error_class varchar(32) NOT NULL CHECK (error_class IN ('site_access','login_auth','human_verification','service_error')),
    visibility varchar(16) NOT NULL DEFAULT 'pending' CHECK (visibility IN ('pending','published','hidden')),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (site_host, task_step, error_class)
);
CREATE INDEX incidents_host_error_idx ON incidents (site_host, error_class);

CREATE TABLE observations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    incident_id uuid NOT NULL REFERENCES incidents(id),
    reporter_id uuid NOT NULL REFERENCES reporters(id),
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    agent_family varchar(16) NOT NULL CHECK (agent_family IN ('dots','muse','other')),
    attempt_actor varchar(8) NOT NULL CHECK (attempt_actor IN ('agent','human')),
    attempt_env varchar(32) NOT NULL CHECK (attempt_env IN ('agent_cloud_browser','human_in_agent_browser','human_own_browser','unknown')),
    outcome varchar(16) NOT NULL CHECK (outcome IN ('passed','blocked','error','abandoned')),
    challenge_vendor varchar(16) NOT NULL CHECK (challenge_vendor IN ('cloudflare','recaptcha','hcaptcha','unknown','none')),
    challenge_kind varchar(20) NOT NULL CHECK (challenge_kind IN ('interactive_check','loop','block_page','unknown','none')),
    synthetic boolean NOT NULL DEFAULT false,
    review_state varchar(16) NOT NULL DEFAULT 'pending' CHECK (review_state IN ('pending','published','hidden')),
    supersedes_id uuid REFERENCES observations(id),
    withdrawn_at timestamptz
);
CREATE INDEX observations_incident_time_idx ON observations (incident_id, observed_at DESC) WHERE withdrawn_at IS NULL;
CREATE INDEX observations_reporter_idx ON observations (reporter_id, observed_at DESC);

CREATE TABLE report_submissions (
    reporter_id uuid NOT NULL REFERENCES reporters(id),
    idempotency_key uuid NOT NULL,
    request_hash char(64) NOT NULL,
    observation_id uuid NOT NULL REFERENCES observations(id),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (reporter_id, idempotency_key)
);

CREATE TABLE audit_events (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_type varchar(40) NOT NULL,
    record_id uuid,
    created_at timestamptz NOT NULL DEFAULT now()
);
