-- Where a stored provider reading came from (docs/decisions.md D52). NULL = the page's status API (status.json and its
-- two scope lists, as before). 'history_atom' = the page's public Atom history, read only where the host's robots.txt
-- disallows the API but allows that path: the row then carries open-announcement counts and no indicator (indicator
-- stays 'unknown'). window_blind is true when that feed cannot be trusted for a recent or an older open incident
-- (scheduled maintenance filling it, or a window shorter than 7 days); NULL otherwise. Nullable columns without
-- defaults: no table rewrite.
-- Rollback: ALTER TABLE status_cache DROP COLUMN feed_source, DROP COLUMN window_blind;
-- then DELETE FROM schema_migrations WHERE version=30. Rows written from the Atom feed then read as 'unknown'
-- readings of the API (indicator 'unknown'): roll the code back first.
ALTER TABLE status_cache
    ADD COLUMN feed_source varchar(16) CHECK (feed_source IN ('history_atom')),
    ADD COLUMN window_blind boolean;
