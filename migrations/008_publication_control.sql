-- Emergency stop for publication is independent of new-report intake.
INSERT INTO service_flags (name,enabled) VALUES ('public_publication',true)
ON CONFLICT (name) DO NOTHING;
