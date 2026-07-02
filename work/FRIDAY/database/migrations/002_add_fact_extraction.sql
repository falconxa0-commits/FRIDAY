-- ============================================================================
-- FRIDAY Migration 002: Add fact extraction table
-- ============================================================================
-- Stores structured facts extracted from user conversations for
-- richer personalization and recall.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- 1. facts table
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS facts (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_text   TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT 'general',
    fact_type   TEXT NOT NULL DEFAULT 'statement',
    source      TEXT,                    -- conversation ID or origin
    confidence  float DEFAULT 1.0,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- 2. Indexes
-- ---------------------------------------------------------------------------
CREATE INDEX idx_facts_category   ON facts (category);
CREATE INDEX idx_facts_fact_type  ON facts (fact_type);
CREATE INDEX idx_facts_created_at ON facts (created_at DESC);
