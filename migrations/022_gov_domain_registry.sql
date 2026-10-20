-- Public government domain lists (U.S. .gov registry, UK .gov.uk list), downloaded whole by the review cron
-- (ahn/gov_registry.py) and joined by a lookup. A lookup never contacts the list publishers.
-- One row per list says which version is loaded and how it got here; the domain rows are replaced
-- (changed rows updated, vanished rows deleted) in one transaction when the publisher posts a new
-- version, and left as they are when a download fails or looks wrong.
CREATE TABLE gov_registry_versions (
    registry varchar(16) PRIMARY KEY CHECK (registry IN ('us_dotgov','uk_govuk')),
    list_version text,                 -- UK: the list's "as of" date; U.S.: the UTC date this service first received these contents
    source_url text,                   -- the file that was read
    etag text,
    content_sha256 char(64),           -- of the file as received (a public file, so the hash may be kept)
    row_count integer,
    loaded_at timestamptz,             -- when the stored rows last changed
    fetched_at timestamptz,            -- last check that succeeded (rows loaded, or confirmed unchanged)
    last_attempt_at timestamptz,
    last_error text,                   -- short label of the last failed or rejected attempt; NULL after a success
    origin_operator text,
    vantage jsonb,
    reader_version text
);

-- Only the fields the answer can show. The U.S. file's security contact email and city are read and
-- dropped; the UK file has the domain name only.
CREATE TABLE gov_domain_registry (
    registry varchar(16) NOT NULL REFERENCES gov_registry_versions (registry),
    domain varchar(253) NOT NULL,
    domain_type text,
    organization text,
    suborganization text,
    state text,
    PRIMARY KEY (registry, domain)
);
