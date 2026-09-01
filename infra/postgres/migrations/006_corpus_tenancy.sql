-- Per-organization corpus isolation + upload tracking

BEGIN;

ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS org_id TEXT REFERENCES organizations(id);
ALTER TABLE corpus_sync_runs ADD COLUMN IF NOT EXISTS org_id TEXT REFERENCES organizations(id);

CREATE INDEX IF NOT EXISTS corpus_documents_org_active_idx
    ON corpus_documents (org_id, active, generation);

DROP INDEX IF EXISTS corpus_documents_source_chunk_idx;
CREATE UNIQUE INDEX IF NOT EXISTS corpus_documents_org_source_chunk_idx
    ON corpus_documents (org_id, source_path, chunk_index)
    WHERE source_path IS NOT NULL;

CREATE TABLE IF NOT EXISTS corpus_uploads (
    id UUID PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    original_name TEXT NOT NULL,
    content_type TEXT,
    size_bytes BIGINT NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ready',
    error TEXT,
    created_by TEXT REFERENCES users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS corpus_uploads_org_created_idx
    ON corpus_uploads (org_id, created_at DESC);

COMMIT;
