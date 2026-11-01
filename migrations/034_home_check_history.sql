-- D68: the "Site problems right now" history also records AHN's home-page checks (ahn/site_probe.py, D67), not only its
-- robots.txt reads. A home-page check is one GET of "/" that already happens; recording it adds no request.
-- Codes only, as before: the page, a header value, a URL path and an IP address are never kept.
-- Nullable columns with no default: no table rewrite, only a short catalog lock.
--   robots_read_events.source  'home' for a home-page check; NULL for a robots.txt read (every row written before this).
--   robots_read_events.rt_band response-time range of a home-page answer: 'lt1s', '1to3s' or '3s_plus'; NULL otherwise
--                              (no exact time is kept here).
--   robots_read_daily.home_reads home-page checks recorded for the host that day (NULL = none, or a day before this).
-- Rollback: first deploy the code without this change, then run:
--           ALTER TABLE robots_read_events DROP COLUMN IF EXISTS source, DROP COLUMN IF EXISTS rt_band;
--           ALTER TABLE robots_read_daily DROP COLUMN IF EXISTS home_reads;
--           DELETE FROM schema_migrations WHERE version=34;
-- Rows already written by home-page checks stay valid robots.txt-shaped rows (robots_status 'home') and age out with the
-- 14-day raw retention; delete them first if they should not count after a rollback:
--           DELETE FROM robots_read_events WHERE robots_status='home';

ALTER TABLE robots_read_events ADD COLUMN IF NOT EXISTS source text, ADD COLUMN IF NOT EXISTS rt_band text;
ALTER TABLE robots_read_daily ADD COLUMN IF NOT EXISTS home_reads integer;
