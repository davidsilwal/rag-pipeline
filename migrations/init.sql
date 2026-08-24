-- ============================================================================
-- rag-pipeline initial schema (Phase 0)
-- PostgreSQL 16 + pgvector. Run via docker-entrypoint-initdb.d (01-init.sql).
-- HNSW vector index is intentionally deferred (migrations/versions/0002_hnsw_after_load.sql)
-- and built AFTER bulk load.
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";       -- pgvector for dense embeddings
CREATE EXTENSION IF NOT EXISTS "pg_trgm";       -- Trigram indexing for fuzzy search
CREATE EXTENSION IF NOT EXISTS "btree_gin";     -- Multi-column GIN indexing

-- 1. Sync State (Delta tokens & cursors)
CREATE TABLE sync_state (
    sync_key VARCHAR(64) PRIMARY KEY,
    delta_token TEXT,
    last_sync_started_at TIMESTAMPTZ,
    last_sync_completed_at TIMESTAMPTZ,
    total_files_discovered INT DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 2. Sources (Raw Evidence Registry)
CREATE TABLE sources (
    source_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    drive_item_id VARCHAR(255) UNIQUE NOT NULL,
    drive_id VARCHAR(255) NOT NULL,
    file_path TEXT NOT NULL,
    file_name TEXT NOT NULL,
    mime_type VARCHAR(128) NOT NULL,
    size_bytes BIGINT NOT NULL,
    sha256_hash CHAR(64) NOT NULL,
    etag VARCHAR(128),
    security_classification VARCHAR(32) DEFAULT 'internal',
    status VARCHAR(32) NOT NULL DEFAULT 'discovered',
    error_message TEXT,
    source_metadata JSONB DEFAULT '{}'::jsonb,
    lang VARCHAR(8) DEFAULT 'simple',
    leased_by TEXT,
    lease_token UUID,
    heartbeat_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_sources_sha256 ON sources(sha256_hash);
CREATE INDEX idx_sources_status ON sources(status);
CREATE INDEX idx_sources_path ON sources(file_path);

-- 3. Canonical Units (Atomic extracted blocks with exact provenance)
CREATE TABLE units (
    unit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_id UUID NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    doc_id VARCHAR(255) NOT NULL,
    unit_index INT NOT NULL,
    parent_unit_id UUID REFERENCES units(unit_id),
    heading_path TEXT[] NOT NULL DEFAULT '{}',
    unit_type VARCHAR(32) NOT NULL,
    raw_text TEXT NOT NULL,
    clean_text TEXT NOT NULL,
    char_start INT,
    char_end INT,
    page_number INT,
    bbox_coords JSONB,
    content_hash CHAR(64) NOT NULL,
    disposition VARCHAR(32) NOT NULL DEFAULT 'authoritative',
    quality_score FLOAT DEFAULT 1.0,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, unit_index)
);
CREATE INDEX idx_units_source_id ON units(source_id);
CREATE INDEX idx_units_content_hash ON units(content_hash);
CREATE INDEX idx_units_disposition ON units(disposition);
CREATE INDEX idx_units_heading_path ON units USING GIN(heading_path);

-- 4. Embedding Cache (BGE-M3 1024d Dense + Sparse Lexical Weights)
CREATE TABLE embed_cache (
    content_hash CHAR(64) PRIMARY KEY,
    model_id VARCHAR(64) NOT NULL DEFAULT 'BAAI/bge-m3',
    dense_vector vector(1024) NOT NULL,
    sparse_weights JSONB,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 5. Topic Clusters (HDBSCAN Outputs)
CREATE TABLE topic_clusters (
    cluster_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    cluster_label INT NOT NULL,
    topic_name VARCHAR(255) NOT NULL,
    centroid vector(1024),
    top_keywords TEXT[] NOT NULL,
    unit_count INT DEFAULT 0,
    exemplar_unit_ids UUID[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 6. GraphRAG Knowledge Graph Tables
CREATE TABLE graphrag_entities (
    entity_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name VARCHAR(255) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    description TEXT NOT NULL,
    source_unit_ids UUID[] NOT NULL DEFAULT '{}',
    frequency INT DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, entity_type)
);
CREATE INDEX idx_entities_name ON graphrag_entities(name);

CREATE TABLE graphrag_relationships (
    rel_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_entity_id UUID NOT NULL REFERENCES graphrag_entities(entity_id) ON DELETE CASCADE,
    target_entity_id UUID NOT NULL REFERENCES graphrag_entities(entity_id) ON DELETE CASCADE,
    relationship_type VARCHAR(64) NOT NULL,
    description TEXT NOT NULL,
    weight FLOAT DEFAULT 1.0,
    source_unit_ids UUID[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_entity_id, target_entity_id, relationship_type)
);

CREATE TABLE graphrag_communities (
    community_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    level INT NOT NULL DEFAULT 0,
    title VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    findings JSONB NOT NULL DEFAULT '[]'::jsonb,
    member_entities TEXT[] NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 7. Cluster Consensus
CREATE TABLE cluster_consensus (
    consensus_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    wiki_topic_path TEXT NOT NULL UNIQUE,
    hdbscan_cluster_id UUID REFERENCES topic_clusters(cluster_id),
    community_id UUID REFERENCES graphrag_communities(community_id),
    heading_pattern TEXT NOT NULL,
    confidence_score FLOAT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'auto_approved',
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 8. Fact Claims & Conflict Ledger
CREATE TABLE claims (
    claim_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject TEXT NOT NULL,
    predicate TEXT NOT NULL,
    object TEXT NOT NULL,
    statement_text TEXT NOT NULL,
    authority_score INT NOT NULL DEFAULT 50,
    source_unit_id UUID NOT NULL REFERENCES units(unit_id),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_claims_subject ON claims(subject);

CREATE TABLE conflicts (
    conflict_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    topic_path TEXT NOT NULL,
    claim_a_id UUID NOT NULL REFERENCES claims(claim_id),
    claim_b_id UUID NOT NULL REFERENCES claims(claim_id),
    conflict_type VARCHAR(64) NOT NULL,
    description TEXT NOT NULL,
    severity VARCHAR(32) NOT NULL DEFAULT 'high',
    resolution_status VARCHAR(32) NOT NULL DEFAULT 'open',
    resolution_notes TEXT,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);

-- 8b. Deduplication Pairs & Tombstones
CREATE TABLE dedup_pairs (
    pair_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    kept_unit_id UUID NOT NULL REFERENCES units(unit_id),
    suppressed_unit_id UUID NOT NULL REFERENCES units(unit_id),
    similarity_score FLOAT NOT NULL,
    method VARCHAR(32) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(kept_unit_id, suppressed_unit_id),
    CHECK (kept_unit_id < suppressed_unit_id)
);
CREATE INDEX idx_dedup_suppressed ON dedup_pairs(suppressed_unit_id);

-- 9. Authoritative Wiki Pages (Git Reflection)
CREATE TABLE wiki_pages (
    page_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    file_path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    page_type VARCHAR(64) NOT NULL,
    domain VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    frontmatter JSONB NOT NULL,
    markdown_body TEXT NOT NULL,
    git_commit_sha VARCHAR(40),
    source_unit_ids UUID[] NOT NULL DEFAULT '{}',
    last_verified_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_wiki_pages_path ON wiki_pages(file_path);

-- 10. Retrieval Chunks
CREATE TABLE wiki_chunks (
    chunk_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    heading_path TEXT[] NOT NULL,
    chunk_index INT NOT NULL,
    content TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    dense_vector vector(1024),
    sparse_weights JSONB,
    fts_vector tsvector GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED,
    chunk_metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(page_id, chunk_index)
);
CREATE INDEX idx_wiki_chunks_page_id ON wiki_chunks(page_id);
CREATE INDEX idx_wiki_chunks_fts ON wiki_chunks USING GIN(fts_vector);
CREATE INDEX idx_wiki_chunks_trgm ON wiki_chunks USING GIN (content gin_trgm_ops);

-- 11. Pipeline Job Run Ledger
CREATE TABLE pipeline_jobs (
    job_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prefect_flow_run_id VARCHAR(255),
    job_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    fingerprint VARCHAR(128) NOT NULL,
    items_processed INT DEFAULT 0,
    items_failed INT DEFAULT 0,
    log_summary TEXT,
    started_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMPTZ
);

-- 12. Coverage Reports
CREATE TABLE coverage_reports (
    report_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    page_id UUID NOT NULL REFERENCES wiki_pages(page_id) ON DELETE CASCADE,
    total_units INT NOT NULL,
    covered_units INT NOT NULL,
    coverage_score FLOAT NOT NULL,
    uncovered_unit_ids UUID[] DEFAULT '{}',
    job_id UUID REFERENCES pipeline_jobs(job_id),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_coverage_page ON coverage_reports(page_id);

-- ============================================================================
-- Roles
-- ============================================================================
-- gpu_worker: R/W on all pipeline tables
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='gpu_worker') THEN
        CREATE ROLE gpu_worker WITH LOGIN PASSWORD '__GENERATE__';
    END IF;
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='readonly') THEN
        CREATE ROLE readonly WITH LOGIN PASSWORD '__GENERATE__';
    END IF;
END$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO gpu_worker;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO gpu_worker;
GRANT SELECT ON wiki_pages, wiki_chunks, graphrag_entities, graphrag_relationships,
    graphrag_communities, claims, conflicts, topic_clusters TO readonly;
