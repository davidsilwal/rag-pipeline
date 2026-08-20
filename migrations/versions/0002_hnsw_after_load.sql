-- ============================================================================
-- 0002_hnsw_after_load.sql
-- Run AFTER bulk COPY of embed_cache and wiki_chunks on the VPS.
-- Raises maintenance_work_mem temporarily, then builds HNSW index.
-- ============================================================================

-- 1. Temporarily raise maintenance_work_mem to speed the index build:
SET maintenance_work_mem = '1GB';

-- 2. Build HNSW index on wiki_chunks (dense_vector).
--    If table has zero rows, CREATE INDEX CONCURRENTLY is a no-op; skip in that case.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM wiki_chunks LIMIT 1) THEN
        EXECUTE '
            CREATE INDEX CONCURRENTLY idx_wiki_chunks_vector_hnsw
            ON wiki_chunks USING hnsw (dense_vector vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
        ';
    END IF;
END$$;

-- 3. Reset maintenance_work_mem:
RESET maintenance_work_mem;

-- 4. ANALYZE for the planner (now that the index exists):
ANALYZE wiki_chunks;