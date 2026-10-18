-- Which host actually supplied robots.txt/llms.txt: the apex, or its www alias after the fallback,
-- so source_urls point at the file that was read instead of a host that refused.
ALTER TABLE host_access_profile ADD COLUMN read_host varchar(253);
