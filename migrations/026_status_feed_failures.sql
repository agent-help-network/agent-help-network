-- Curated status feeds are read on a schedule without a lookup (docs/decisions.md D44, ahn/status_refresh.py
-- refresh_batch). A feed that keeps failing backs off exponentially, so the cache counts consecutive failed reads:
-- set to 1 by the first failure after a success, raised by each further failure, cleared (NULL) by a success.
-- Curated rows of status_sources are marked with evidence 'curated_mapping' by the cron's sync; that column exists
-- since migration 020 and needs no change here. Nullable, no default: no table rewrite.
-- Rollback: ALTER TABLE status_cache DROP COLUMN failures; then DELETE FROM schema_migrations WHERE version=26.
ALTER TABLE status_cache
    ADD COLUMN failures smallint CHECK (failures > 0);
