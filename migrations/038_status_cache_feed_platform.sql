-- Which status-page platform a stored reading was taken from, where it matters (docs/decisions.md D100).
-- NULL = not recorded (every reading before D100, and every platform but one). 'incident_io' = the page's status.json
-- carries the incident.io shape (a 26-character upper-case page id and no time_zone). Such a page keeps its overall
-- indicator at 'none' while a minor incident without components is open, so the cron also reads its summary.json;
-- the marker lets an answer tell "the incident list was not read" from "this platform has no such list".
-- Nullable column, no default: no table rewrite.
-- Rollback: ALTER TABLE status_cache DROP COLUMN feed_platform; then DELETE FROM schema_migrations WHERE version=38.
-- Roll the code back first: it selects the column.
ALTER TABLE status_cache
    ADD COLUMN feed_platform varchar(16) CHECK (feed_platform IN ('incident_io'));
