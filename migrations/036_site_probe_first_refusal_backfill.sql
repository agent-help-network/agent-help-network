-- D86: one-time move of the stored first home-page refusals that still wait the old 7 days onto the D83 schedule.
-- D83 gives a host's first counted refusal (outcome bot_challenge or refused, consecutive_refusals = 1) a re-check after
-- 24 hours instead of 7 days, but only for refusals stored after it was deployed; a row stored earlier keeps the flat
-- 7-day next_due_at. This brings those rows to what the D83 rule would have given them:
--   next_due_at = LEAST(old next_due_at, GREATEST(probed_at + 24 hours, now()))
-- i.e. probed_at + 24 hours, but not in the past (a row measured more than 24 hours ago is simply due now), and never later
-- than before. Only the one column next_due_at of existing rows is updated (no table or index change); the rows touched are
-- the first-refusal rows of the check list.
-- Selected rows: outcome IN ('bot_challenge','refused') AND consecutive_refusals = 1 AND not yet due AND next_due_at is the
-- flat 7 days after the check (what the old rule wrote; a D83 row waits less than 7 days). Not touched: rate limits (429),
-- login walls, 503 and every other outcome, rows with consecutive_refusals >= 2, rows already due, and rows D83 already
-- scheduled (so running it twice changes nothing: after the first run no selected row waits 7 days any more).
-- Limits: (1) the Retry-After of the check is not stored, so a stored first refusal whose Retry-After was 7 days or more
-- (capped to the same 7 days) cannot be told from an old row and moves to the 24-hour check too; the check that follows is
-- one ordinary home-page request inside the existing caps, and a refusal that persists is again scheduled by the Retry-After
-- the new answer carries. (2) A migration cannot read AHN_REFUSAL_CONFIRM: with that switch set to 0 the rows are still
-- moved once, at the first deploy of this migration; later refusals follow the switch as before.
-- Load: each selected host gets at most one home-page request, through the existing caps: the review cron's list-driven check
-- for hosts on today's check list (hourly cap 60, daily cap 800, 6-hour minimum gap), and a lookup of the host, which also
-- starts a check once the row is due (lookup caps 40 per hour, 300 per day); robots.txt for "/" is checked first in both. A host
-- that is neither on the list nor looked up is not read; its row only becomes due.
-- Rollback (data only, optional): the rows cannot be told from rows D83 scheduled afterwards, so restoring the old state for
-- all of them is
--           UPDATE site_probe SET next_due_at = probed_at + interval '168 hours' WHERE outcome IN ('bot_challenge','refused')
--             AND consecutive_refusals = 1 AND next_due_at <= probed_at + interval '168 hours' AND next_due_at > now();
-- (which also returns D83's own not-yet-due first refusals to 7 days). Not needed to roll back the code: nothing reads
-- anything but next_due_at.
-- Deploy note: schema_migrations records version 36 once this has run; DELETE FROM schema_migrations WHERE version=36 to re-run.
-- The 7 days are written as 168 hours so the comparison does not depend on the session time zone (a day is not always 24 hours
-- in a zone with daylight saving time); the deploy path runs in UTC.
--
-- Safety gate: the number of selected rows is counted first, with the same conditions. At most 50 rows (max_rows below):
-- they are all updated. More than 50: NO row is changed (all or nothing, never a part), because the number was not measured
-- before the deploy and a larger set means more home-page requests than this change was sized for. Either way a NOTICE
-- records the count and whether it was applied. A skipped run is not retried by itself (the migration is recorded as
-- applied); to apply it later run the DO block below by hand with a larger max_rows after counting.

DO $d86$
DECLARE
    max_rows constant integer := 50;
    target_rows integer;
BEGIN
    SELECT count(*) INTO target_rows
      FROM site_probe
     WHERE outcome IN ('bot_challenge', 'refused')
       AND consecutive_refusals = 1
       AND next_due_at > now()
       AND next_due_at >= probed_at + interval '168 hours';
    IF target_rows > max_rows THEN
        RAISE NOTICE 'D86 backfill: % target rows, more than % - not applied, no row changed', target_rows, max_rows;
        RETURN;
    END IF;
    UPDATE site_probe
       SET next_due_at = LEAST(next_due_at, GREATEST(probed_at + interval '24 hours', now()))
     WHERE outcome IN ('bot_challenge', 'refused')
       AND consecutive_refusals = 1
       AND next_due_at > now()
       AND next_due_at >= probed_at + interval '168 hours';
    RAISE NOTICE 'D86 backfill: % target rows (limit %) - applied', target_rows, max_rows;
END
$d86$;
