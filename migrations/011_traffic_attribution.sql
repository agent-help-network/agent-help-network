CREATE TABLE traffic_attribution_daily (
    day date NOT NULL,
    route_code varchar(80) NOT NULL,
    method varchar(8) NOT NULL,
    status_class varchar(3) NOT NULL,
    source varchar(24) NOT NULL,
    medium varchar(16) NOT NULL,
    campaign varchar(24) NOT NULL,
    referrer varchar(24) NOT NULL,
    client_type varchar(24) NOT NULL,
    requests bigint NOT NULL DEFAULT 0 CHECK (requests >= 0),
    PRIMARY KEY (day, route_code, method, status_class, source, medium, campaign, referrer, client_type)
);

CREATE TABLE traffic_client_daily (
    day date NOT NULL,
    client_digest char(64) NOT NULL,
    client_type varchar(24) NOT NULL,
    requests bigint NOT NULL DEFAULT 0 CHECK (requests >= 0),
    PRIMARY KEY (day, client_digest, client_type)
);
