-- Providers in a maintenance window report indicator "maintenance"; the cache check from 007 did not allow it.
ALTER TABLE status_cache DROP CONSTRAINT IF EXISTS status_cache_indicator_check;
ALTER TABLE status_cache ADD CONSTRAINT status_cache_indicator_check
    CHECK (indicator IN ('none','minor','major','critical','maintenance','unknown'));
