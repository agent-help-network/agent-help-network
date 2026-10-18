CREATE TABLE request_metrics (
    day date NOT NULL,
    route_code varchar(80) NOT NULL,
    method varchar(8) NOT NULL,
    status_class varchar(3) NOT NULL,
    latency_bucket_ms integer NOT NULL,
    requests bigint NOT NULL DEFAULT 0 CHECK (requests >= 0),
    PRIMARY KEY (day, route_code, method, status_class, latency_bucket_ms)
);
