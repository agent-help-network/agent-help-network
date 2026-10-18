-- A coverage gap means the stored month total is only a lower bound. The flag
-- stays set for the rest of the billing period so the owner is notified once.
-- The thresholds in force are recorded next to the state they produced.
ALTER TABLE bandwidth_guard_state ADD COLUMN coverage_gap_since timestamptz;
ALTER TABLE bandwidth_guard_state ADD COLUMN warning_gb numeric(12,3);
ALTER TABLE bandwidth_guard_state ADD COLUMN stop_gb numeric(12,3);
