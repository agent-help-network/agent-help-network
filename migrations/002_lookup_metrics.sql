CREATE TABLE lookup_metrics (
    day date NOT NULL,
    site_host varchar(253) NOT NULL,
    error_class varchar(32) NOT NULL,
    checks bigint NOT NULL DEFAULT 0 CHECK (checks >= 0),
    PRIMARY KEY (day, site_host, error_class)
);
