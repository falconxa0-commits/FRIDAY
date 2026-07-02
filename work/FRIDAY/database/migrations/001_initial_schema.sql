-- ============================================================================
-- FRIDAY Initial Schema Migration
-- ============================================================================
-- Creates all core tables, indexes, and the pgvector extension for
-- semantic similarity search.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. Enable pgvector extension
-- ---------------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

-- ---------------------------------------------------------------------------
-- 2. conversations table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content     TEXT NOT NULL,
    timestamp   TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata    JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX idx_conversations_role       ON conversations (role);
CREATE INDEX idx_conversations_timestamp  ON conversations (timestamp DESC);

-- ---------------------------------------------------------------------------
-- 3. memories table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content     TEXT NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    embedding   vector(384),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    category    TEXT
);

CREATE INDEX idx_memories_category    ON memories (category);
CREATE INDEX idx_memories_created_at  ON memories (created_at DESC);

-- HNSW index for fast approximate nearest-neighbour search
CREATE INDEX idx_memories_embedding ON memories
    USING hnsw (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- 4. action_logs table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS action_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action      TEXT NOT NULL,
    service     TEXT NOT NULL,
    params      JSONB DEFAULT '{}'::jsonb,
    risk_level  TEXT NOT NULL DEFAULT 'low'
                CHECK (risk_level IN ('low', 'medium', 'high', 'critical')),
    status      TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'approved', 'rejected', 'completed', 'failed')),
    user_id     TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
);

CREATE INDEX idx_action_logs_service    ON action_logs (service);
CREATE INDEX idx_action_logs_risk_level ON action_logs (risk_level);
CREATE INDEX idx_action_logs_status     ON action_logs (status);
CREATE INDEX idx_action_logs_created_at ON action_logs (created_at DESC);

-- ---------------------------------------------------------------------------
-- 5. scheduled_tasks table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name   TEXT NOT NULL,
    task_type   TEXT NOT NULL DEFAULT 'interval'
                CHECK (task_type IN ('interval', 'cron', 'once')),
    params      JSONB DEFAULT '{}'::jsonb,
    schedule    TEXT,            -- cron expression or interval string
    next_run    TIMESTAMPTZ,
    is_active   BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_scheduled_tasks_next_run  ON scheduled_tasks (next_run)
    WHERE is_active = true;
CREATE INDEX idx_scheduled_tasks_is_active ON scheduled_tasks (is_active);

-- ---------------------------------------------------------------------------
-- 6. evolution_history table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evolution_history (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    variant     TEXT NOT NULL,
    metrics     JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_evolution_history_variant    ON evolution_history (variant);
CREATE INDEX idx_evolution_history_created_at ON evolution_history (created_at DESC);

-- ---------------------------------------------------------------------------
-- 7. match_memories RPC function (pgvector similarity search)
-- ---------------------------------------------------------------------------
-- Usage:
--   SELECT * FROM match_memories('your query embedding', 0.5, 10);
-- Returns rows from `memories` ordered by cosine distance, filtered by
-- a minimum similarity threshold.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION match_memories(
    query_embedding  vector(384),
    match_threshold  float DEFAULT 0.5,
    match_count      int  DEFAULT 10,
    filter_category  text DEFAULT NULL
)
RETURNS TABLE (
    id        UUID,
    content   TEXT,
    metadata  JSONB,
    category  TEXT,
    similarity float
)
LANGUAGE plpgsql STABLE
AS $$
BEGIN
    RETURN QUERY
        SELECT
            m.id,
            m.content,
            m.metadata,
            m.category,
            1 - (m.embedding <=> query_embedding) AS similarity
        FROM memories m
        WHERE
            (1 - (m.embedding <=> query_embedding)) >= match_threshold
            AND (filter_category IS NULL OR m.category = filter_category)
        ORDER BY m.embedding <=> query_embedding
        LIMIT match_count;
END;
$$;
