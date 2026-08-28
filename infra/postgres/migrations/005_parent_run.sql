-- Follow-up thread: link child runs to a parent research run.
ALTER TABLE research_runs ADD COLUMN IF NOT EXISTS parent_run_id UUID REFERENCES research_runs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS research_runs_parent_idx
    ON research_runs (parent_run_id, created_at DESC)
    WHERE parent_run_id IS NOT NULL AND deleted_at IS NULL;
