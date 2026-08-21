-- ============================================================================
-- 0004_source_types.sql — GitHub/local source ingestion support.
-- Additive only. Safe to re-run with IF NOT EXISTS.
-- ============================================================================

ALTER TABLE IF EXISTS sources ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'local';
ALTER TABLE IF EXISTS sources ADD COLUMN IF NOT EXISTS source_url TEXT;
