-- ============================================================================
-- 0005_dedup_review_incremental.sql — Human dedup review + incremental updates.
-- Additive only. Safe to re-run with IF NOT EXISTS / IF NOT EXISTS.
-- ============================================================================

-- Sources: incremental processing state
ALTER TABLE IF EXISTS sources ADD COLUMN IF NOT EXISTS source_version INTEGER NOT NULL DEFAULT 1;
ALTER TABLE IF EXISTS sources ADD COLUMN IF NOT EXISTS last_processed_at TIMESTAMPTZ;
ALTER TABLE IF EXISTS sources ADD COLUMN IF NOT EXISTS last_processed_by VARCHAR(64);
ALTER TABLE IF EXISTS sources ADD COLUMN IF NOT EXISTS processing_notes TEXT;

-- Units: lifecycle status used by dedup review + incremental planning
ALTER TABLE IF EXISTS units ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active';
CREATE INDEX IF NOT EXISTS idx_units_status ON units(status);

-- Dedup review decisions
CREATE TABLE IF NOT EXISTS dedup_reviews (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pair_id UUID NOT NULL UNIQUE REFERENCES dedup_pairs(pair_id) ON DELETE CASCADE,
    reviewer TEXT NOT NULL,
    decision VARCHAR(32) NOT NULL,
    notes TEXT DEFAULT '',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dedup_reviews_decision ON dedup_reviews(decision);
