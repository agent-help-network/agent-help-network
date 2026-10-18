-- Discovered public status sources. Rows are created only after a host is queried.
CREATE TABLE status_sources (
    site_host varchar(253) PRIMARY KEY,
    source_name varchar(80) NOT NULL,
    status_page_url text NOT NULL,
    api_url text NOT NULL,
    enabled boolean NOT NULL DEFAULT true
);

CREATE TABLE status_cache (
    site_host varchar(253) PRIMARY KEY REFERENCES status_sources(site_host),
    indicator varchar(16) NOT NULL CHECK (indicator IN ('none','minor','major','critical','unknown')),
    source_updated_at timestamptz,
    fetched_at timestamptz,
    last_error_at timestamptz
);
