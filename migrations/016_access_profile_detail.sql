-- Why a robots.txt/llms.txt read ended the way it did (http_403, dns_timeout, ...),
-- so a refusal by the host's edge is reported as such instead of as an unknown.
ALTER TABLE host_access_profile ADD COLUMN robots_detail varchar(32);
ALTER TABLE host_access_profile ADD COLUMN llms_detail varchar(32);
