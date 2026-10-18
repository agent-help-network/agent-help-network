-- Avoid probing the same unrecognized first-party status subdomain on every read.
ALTER TABLE queried_hosts ADD COLUMN last_discovery_at timestamptz;
