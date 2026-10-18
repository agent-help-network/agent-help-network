-- Why the last status-feed read failed (http_503, timeout, dns_failed, ...), so a
-- "stale" provider value says whether the feed refused us or the network dropped.
ALTER TABLE status_cache ADD COLUMN last_error_detail varchar(32);
