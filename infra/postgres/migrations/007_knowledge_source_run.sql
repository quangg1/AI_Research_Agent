-- Link a knowledge cache record back to the run that produced it, so
-- deleting a run can deactivate its cached answer instead of leaving it
-- reusable forever (soft-delete on research_runs never touched
-- knowledge_records -- the two tables had no relationship at all).

BEGIN;

ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS source_thread_id TEXT;

CREATE INDEX IF NOT EXISTS knowledge_records_source_thread_idx
    ON knowledge_records (source_thread_id)
    WHERE source_thread_id IS NOT NULL;

COMMIT;
