-- Why the last status-feed read failed (http_503, timeout, dns_failed, ...), so a
-- "stale" provider value says whether the feed refused us or the network dropped.
ALTER TABLE status_cache ADD COLUMN last_error_detail varchar(32);
-- Rows whose latest attempt failed before this column existed get the generic label,
-- so "stale" always comes with a reason until the next refresh records the real one.
UPDATE status_cache SET last_error_detail='error'
 WHERE last_error_at IS NOT NULL AND (fetched_at IS NULL OR last_error_at >= fetched_at);
