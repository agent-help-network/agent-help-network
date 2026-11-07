-- D109: two optional codes on a report: the cause the reporting agent says it established (cause) and the basis it used
-- (cause_basis), each from a fixed list. Shown only as "reported by the submitting agent, not verified by AHN"; neither
-- reaches the recommendation, next_action, the D73 cause or any count. Nullable columns, no default: no table rewrite.
-- The CHECK constraints are NOT VALID: existing rows are not scanned (they are all NULL), new rows are checked, so the
-- lock is short.
-- Rollback: roll the code back first (it selects both columns), then
--   ALTER TABLE observations DROP CONSTRAINT observations_cause_codes, DROP CONSTRAINT observations_cause_basis_codes,
--     DROP COLUMN cause, DROP COLUMN cause_basis;
--   DELETE FROM schema_migrations WHERE version=40;
ALTER TABLE observations
    ADD COLUMN cause varchar(40),
    ADD COLUMN cause_basis varchar(32),
    ADD CONSTRAINT observations_cause_codes CHECK (cause IS NULL OR cause IN (
        'site_said_no_agents', 'site_bot_check', 'site_unavailable', 'platform_refused', 'platform_needs_person',
        'verification_on_person_device', 'verification_not_received', 'signed_out', 'agent_browser_differs')) NOT VALID,
    ADD CONSTRAINT observations_cause_basis_codes CHECK (cause_basis IS NULL OR cause_basis IN (
        'own_platform_message', 'site_message', 'person_confirmed', 'compared_with_own_browser')) NOT VALID;
