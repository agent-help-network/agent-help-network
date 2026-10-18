-- A pending report can be held for human review without being published.
ALTER TABLE observations ADD COLUMN review_reason varchar(40);
ALTER TABLE observations ADD COLUMN reviewed_at timestamptz;
CREATE INDEX observations_auto_review_queue_idx
    ON observations (received_at)
    WHERE review_state='pending' AND review_reason IS NULL AND withdrawn_at IS NULL AND synthetic=false;
