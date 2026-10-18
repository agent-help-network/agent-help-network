CREATE TABLE render_metric_samples (
    metric varchar(24) NOT NULL,
    sample_at timestamptz NOT NULL,
    value numeric NOT NULL CHECK (value >= 0),
    unit varchar(20) NOT NULL,
    collected_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (metric, sample_at)
);
