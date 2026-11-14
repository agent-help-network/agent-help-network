-- D118: the task route write path. Three new tables and nothing else: no existing table is altered or locked.
--   task_routes             one record per row: closed values only (the lists of ahn/task_routes.py), never free text, a person,
--                           an account, a time finer than the reporting period, or (for an agent's report) any link.
--   task_route_submissions  the idempotency key and request hash of a public report, as report_submissions is for observations.
--   task_route_intake_daily the daily intake counters behind the caps (all reports, per site, per site and task kind); 8 days.
-- Every enum column has a CHECK with exactly the values the code may write; tests/test_task_routes_write_db.py compares each list with
-- the model enum. basis official_doc, outcome not_applicable and source_kind curated_official belong to the curated table in code
-- and are not stored here.
-- Rollback: roll the code back first (the lookup and the write path read these tables only while AHN_TASK_ROUTE_REPORTS is on), then
--   DROP TABLE task_route_submissions; DROP TABLE task_route_intake_daily; DROP TABLE task_routes;
--   DELETE FROM schema_migrations WHERE version=42;
CREATE TABLE task_routes (
    id uuid PRIMARY KEY,
    site_host varchar(253) NOT NULL,
    task_kind varchar(32) NOT NULL,
    obstacle varchar(32) NOT NULL,
    verification_kind varchar(32),
    site_requires varchar(32),
    document_kind varchar(32),
    entry_point_kind varchar(32),
    routes_tried text[] NOT NULL DEFAULT '{}',
    route_worked varchar(32),
    basis varchar(32) NOT NULL,
    outcome varchar(32) NOT NULL,
    duration varchar(32),
    period varchar(7) NOT NULL,
    source_kind varchar(32) NOT NULL,
    sensitive boolean NOT NULL,
    official_url varchar(200),
    review_state varchar(16) NOT NULL DEFAULT 'pending',
    review_reason varchar(32),
    received_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz,
    withdrawn_at timestamptz,
    CONSTRAINT task_routes_task_kind_values CHECK (task_kind IN (
        'claim_unclaimed_property', 'warranty_claim', 'warranty_dispute', 'refund_request', 'return_or_exchange',
        'cancel_subscription', 'account_closure', 'account_recovery', 'portal_login', 'address_or_profile_change',
        'appointment_booking', 'appointment_change_or_cancel', 'records_request', 'complaint_filing',
        'billing_dispute', 'fee_waiver_or_adjustment', 'insurance_claim', 'government_benefit_application',
        'tax_or_government_filing', 'id_or_license_renewal', 'accommodation_request', 'prescription_refill',
        'document_submission', 'other')),
    CONSTRAINT task_routes_obstacle_values CHECK (obstacle IN (
        'document_missing', 'document_rejected', 'substitute_document_needed', 'form_unclear', 'wrong_entry_point',
        'wrong_channel', 'wrong_department_or_jurisdiction', 'eligibility_unclear', 'verification_step',
        'code_expired', 'deadline_or_window', 'fee_required', 'waiting_reply', 'no_reply', 'request_denied',
        'site_requires_info_from_person', 'technical_block', 'none')),
    CONSTRAINT task_routes_verification_kind_values CHECK (verification_kind IS NULL OR verification_kind IN (
        'sms_code', 'email_code', 'app_approval', 'new_device', 'security_question', 'account_locked',
        'consent_screen', 'id_document_upload')),
    CONSTRAINT task_routes_site_requires_values CHECK (site_requires IS NULL OR site_requires IN (
        'referral_or_prescription', 'prior_approval_record', 'account_holder_step', 'original_document',
        'preference_only')),
    CONSTRAINT task_routes_document_kind_values CHECK (document_kind IS NULL OR document_kind IN (
        'proof_of_identity', 'proof_of_address', 'proof_of_ownership', 'proof_of_purchase', 'proof_of_authority',
        'business_registration_or_closure', 'sworn_statement', 'approval_or_authorization_letter',
        'professional_letter', 'prior_correspondence', 'account_statement', 'official_form', 'other')),
    CONSTRAINT task_routes_entry_point_kind_values CHECK (entry_point_kind IS NULL OR entry_point_kind IN (
        'role_specific_portal', 'regional_site', 'separate_subdomain', 'mobile_app_only',
        'partner_or_third_party_portal')),
    CONSTRAINT task_routes_routes_tried_values CHECK (cardinality(routes_tried) <= 6 AND routes_tried <@ ARRAY[
        'support_chat', 'phone', 'email', 'web_form', 'office_visit', 'mail_or_fax', 'consumer_agency', 'regulator',
        'designated_dispute_program', 'official_app', 'different_entry_point', 'escalation_within_site', 'other']::text[]),
    CONSTRAINT task_routes_route_worked_values CHECK (route_worked IS NULL OR route_worked IN (
        'support_chat', 'phone', 'email', 'web_form', 'office_visit', 'mail_or_fax', 'consumer_agency', 'regulator',
        'designated_dispute_program', 'official_app', 'different_entry_point', 'escalation_within_site', 'other',
        'none')),
    CONSTRAINT task_routes_basis_values CHECK (basis IN (
        'agency_reply', 'outcome_observed', 'own_attempt')),
    CONSTRAINT task_routes_outcome_values CHECK (outcome IN (
        'resolved', 'progressed', 'no_progress', 'gave_up')),
    CONSTRAINT task_routes_duration_values CHECK (duration IS NULL OR duration IN (
        'under_1w', 'from_1w_to_4w', 'over_4w')),
    CONSTRAINT task_routes_source_kind_values CHECK (source_kind IN (
        'operator_panel', 'external_agent')),
    CONSTRAINT task_routes_review_state_values CHECK (review_state IN ('pending', 'published', 'held')),
    CONSTRAINT task_routes_period_shape CHECK (period ~ '^[0-9]{4}-Q[1-4]$' OR period ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    -- A link only on an operator record; a public report has no field for one.
    CONSTRAINT task_routes_official_url_source CHECK (official_url IS NULL OR source_kind = 'operator_panel')
);
CREATE INDEX task_routes_published_host ON task_routes (site_host) WHERE review_state = 'published' AND withdrawn_at IS NULL;
CREATE INDEX task_routes_review_queue ON task_routes (received_at) WHERE review_state = 'pending' AND withdrawn_at IS NULL;

CREATE TABLE task_route_submissions (
    idempotency_key uuid PRIMARY KEY,
    request_hash char(64) NOT NULL,
    route_id uuid NOT NULL UNIQUE REFERENCES task_routes (id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- One row per day and scope: site_host '' and task_kind '' is the day's total, site_host alone the per-site count, both the
-- per-site-and-task-kind count. site_host is the site's root domain (www and the apex count together).
CREATE TABLE task_route_intake_daily (
    day date NOT NULL,
    site_host varchar(253) NOT NULL,
    task_kind varchar(32) NOT NULL,
    reports integer NOT NULL CHECK (reports >= 0),
    PRIMARY KEY (day, site_host, task_kind),
    CONSTRAINT task_route_intake_daily_task_kind_values CHECK (task_kind = '' OR task_kind IN (
        'claim_unclaimed_property', 'warranty_claim', 'warranty_dispute', 'refund_request', 'return_or_exchange',
        'cancel_subscription', 'account_closure', 'account_recovery', 'portal_login', 'address_or_profile_change',
        'appointment_booking', 'appointment_change_or_cancel', 'records_request', 'complaint_filing',
        'billing_dispute', 'fee_waiver_or_adjustment', 'insurance_claim', 'government_benefit_application',
        'tax_or_government_filing', 'id_or_license_renewal', 'accommodation_request', 'prescription_refill',
        'document_submission', 'other')),
    CONSTRAINT task_route_intake_daily_scope CHECK (site_host <> '' OR task_kind = '')
);
