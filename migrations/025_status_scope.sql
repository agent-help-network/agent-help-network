-- Scope of a provider's active status (ahn/status_scope.py, docs/decisions.md D43). Written with each refresh of an
-- Atlassian Statuspage feed that reads an active indicator, from two more public lists of the same status page, incidents/unresolved.json and
-- scheduled-maintenances/active.json (32 KiB cap each): the number of unresolved incidents and of maintenances in
-- progress, the highest incident impact, the latest incident update time, at most 3 incident or component names
-- as the provider published them (80 characters each), and whether every affected component sits in a region or
-- point-of-presence group. NULL means not read: another platform, a failed or oversized list, a first discovery,
-- or a row written before this migration. Nullable columns without defaults: no table rewrite.
-- Rollback: ALTER TABLE status_cache DROP COLUMN active_incidents, DROP COLUMN max_incident_impact,
-- DROP COLUMN incident_updated_at, DROP COLUMN active_maintenances, DROP COLUMN affected,
-- DROP COLUMN affected_regional; then DELETE FROM schema_migrations WHERE version=25.
ALTER TABLE status_cache
    ADD COLUMN active_incidents smallint CHECK (active_incidents >= 0),
    ADD COLUMN max_incident_impact varchar(8) CHECK (max_incident_impact IN ('none', 'minor', 'major', 'critical')),
    ADD COLUMN incident_updated_at timestamptz,
    ADD COLUMN active_maintenances smallint CHECK (active_maintenances >= 0),
    ADD COLUMN affected text[] CHECK (cardinality(affected) BETWEEN 1 AND 3),
    ADD COLUMN affected_regional boolean;
