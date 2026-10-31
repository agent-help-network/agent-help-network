-- D59: what the host publishes for agents at standard locations, read next to its robots.txt where that file allows it:
-- the RFC 9727 API catalog, the W3C TDMRep file and TDM response headers, and (experimental) the AI Catalog's MCP
-- Server Card entries. Parsed fields only (ahn/site_declared.py), never the files. NULL until the host is read again.
ALTER TABLE host_access_profile ADD COLUMN site_declared jsonb;
