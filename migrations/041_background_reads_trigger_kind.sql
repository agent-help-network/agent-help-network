-- D111: a home-page check queued by a lookup that was marked as a test (X-AHN-Test: 1, ahn_test=1) is recorded with trigger_kind 'test'
-- (site_probe.trigger_kind is varchar(8) with no CHECK, so that table needs nothing). The queue row has to carry the mark from the
-- lookup to the worker that runs the check later: one nullable column on the small work-queue table, no default, no table rewrite.
-- NULL means a lookup (or anything else) queued it. The value 'test' is a fixed code; no host, address or text is added.
-- Rollback: roll the code back first (it only writes and reads the column inside a try), then
--   ALTER TABLE background_reads DROP COLUMN trigger_kind;
--   DELETE FROM schema_migrations WHERE version=41;
ALTER TABLE background_reads ADD COLUMN trigger_kind varchar(8);
