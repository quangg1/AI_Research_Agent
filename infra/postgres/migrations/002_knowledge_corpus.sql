BEGIN;

CREATE TABLE IF NOT EXISTS knowledge_records (
    id UUID PRIMARY KEY,
    run_id UUID REFERENCES research_runs(id) ON DELETE SET NULL,
    goal TEXT NOT NULL,
    answer JSONB NOT NULL DEFAULT '{}'::jsonb,
    citations JSONB NOT NULL DEFAULT '[]'::jsonb,
    embedding JSONB,
    embedding_model TEXT,
    version INT NOT NULL DEFAULT 1,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS fingerprint TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS queries TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS depth_score INT NOT NULL DEFAULT 0;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS reuse_count BIGINT NOT NULL DEFAULT 0;
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE knowledge_records ADD COLUMN IF NOT EXISTS last_reused_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS knowledge_records_active_idx
    ON knowledge_records (active, updated_at DESC);
CREATE INDEX IF NOT EXISTS knowledge_records_status_idx
    ON knowledge_records (status, updated_at DESC);

CREATE TABLE IF NOT EXISTS corpus_documents (
    id TEXT PRIMARY KEY,
    url TEXT,
    title TEXT,
    host TEXT,
    tier TEXT,
    snippet TEXT,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS source_path TEXT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS chunk_index INT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS content_hash TEXT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS quote TEXT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS credibility DOUBLE PRECISION;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS published TEXT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS source_kind TEXT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS generation BIGINT NOT NULL DEFAULT 1;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS active BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ;
ALTER TABLE corpus_documents ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE UNIQUE INDEX IF NOT EXISTS corpus_documents_source_chunk_idx
    ON corpus_documents (source_path, chunk_index)
    WHERE source_path IS NOT NULL;
CREATE INDEX IF NOT EXISTS corpus_documents_active_generation_idx
    ON corpus_documents (active, generation);

CREATE TABLE IF NOT EXISTS corpus_sync_runs (
    id UUID PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'running',
    generation BIGINT NOT NULL,
    source_hash TEXT,
    documents_seen INT NOT NULL DEFAULT 0,
    documents_indexed INT NOT NULL DEFAULT 0,
    embedding_model TEXT,
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

COMMIT;
