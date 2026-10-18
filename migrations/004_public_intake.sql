-- All no-account reports share one unverified source. This avoids claiming
-- that multiple anonymous submissions came from independent people.
INSERT INTO reporters (id, key_hash, label)
VALUES ('b2d045dc-9791-4a27-9db4-3e469c63f57f', repeat('0', 64), 'public-unverified')
ON CONFLICT (id) DO NOTHING;

CREATE TABLE public_intake_daily (
    day date PRIMARY KEY,
    reports integer NOT NULL CHECK (reports >= 0)
);

CREATE TABLE public_intake_host_daily (
    day date NOT NULL,
    site_host varchar(253) NOT NULL,
    reports integer NOT NULL CHECK (reports >= 0),
    PRIMARY KEY (day, site_host)
);

CREATE TABLE service_flags (
    name varchar(40) PRIMARY KEY,
    enabled boolean NOT NULL
);
INSERT INTO service_flags (name, enabled) VALUES ('public_intake', true)
ON CONFLICT (name) DO NOTHING;
