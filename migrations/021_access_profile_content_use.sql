-- Content-use preferences declared in robots.txt (Cloudflare Content-Signal, IETF AIPREF Content-Usage), parsed from
-- the same bytes as the robots rules and stored as normalized yes/no/unset values with at most three sample paths;
-- the file itself is still not stored. NULL for rows read before this column, until the host is read again.
ALTER TABLE host_access_profile ADD COLUMN content_use jsonb;
