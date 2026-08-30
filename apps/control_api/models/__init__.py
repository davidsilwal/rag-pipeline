#!/usr/bin/env python3
"""apps/control_api/models/ — SQLAlchemy ORM models."""

from sqlalchemy import Column, String, Integer, Boolean, Float, JSON, Text, ForeignKey, Index, UniqueConstraint, CheckConstraint, DateTime, LargeBinary
from sqlalchemy import ARRAY
from sqlalchemy.dialects.postgresql import UUID, TSVECTOR, INET
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func, text

from database import Base


class Source(Base):
    __tablename__ = "sources"
    source_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    drive_item_id = Column(String(255), unique=True, nullable=False)
    drive_id = Column(String(255), nullable=True)
    source_type = Column(String(32), default="local", nullable=False)
    source_url = Column(Text)
    file_path = Column(Text, nullable=False)
    file_name = Column(String, nullable=False)
    mime_type = Column(String(128), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256_hash = Column(String(64), nullable=False)
    etag = Column(String(128))
    security_classification = Column(String(32), default="internal")
    status = Column(String(32), default="discovered", nullable=False)
    error_message = Column(Text)
    source_metadata = Column(JSON, default={})
    lang = Column(String(8), default="simple")
    leased_by = Column(String)
    lease_token = Column(UUID)
    heartbeat_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=func.now())
    source_version = Column(Integer, default=1, nullable=False)
    last_processed_at = Column(DateTime(timezone=True))
    last_processed_by = Column(String(64))
    processing_notes = Column(Text)

    __table_args__ = (
        Index("idx_sources_sha256", "sha256_hash"),
        Index("idx_sources_status", "status"),
        Index("idx_sources_path", "file_path"),
    )


class Unit(Base):
    __tablename__ = "units"
    unit_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    source_id = Column(UUID, ForeignKey("sources.source_id", ondelete="CASCADE"), nullable=False)
    doc_id = Column(String(255), nullable=False)
    unit_index = Column(Integer, nullable=False)
    parent_unit_id = Column(UUID, ForeignKey("units.unit_id"))
    heading_path = Column(ARRAY(String), default=list, nullable=False)
    unit_type = Column(String(32), nullable=False)
    raw_text = Column(Text, nullable=False)
    clean_text = Column(Text, nullable=False)
    char_start = Column(Integer)
    char_end = Column(Integer)
    page_number = Column(Integer)
    bbox_coords = Column(JSON)
    content_hash = Column(String(64), nullable=False)
    disposition = Column(String(32), default="authoritative", nullable=False)
    quality_score = Column(Float, default=1.0)
    status = Column(String(32), default="active", nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        UniqueConstraint("source_id", "unit_index", name="uq_units_source_idx"),
        Index("idx_units_source_id", "source_id"),
        Index("idx_units_content_hash", "content_hash"),
        Index("idx_units_disposition", "disposition"),
        Index("idx_units_status", "status"),
        Index("idx_units_heading_path", "heading_path", postgresql_using="gin"),
    )


class EmbedCache(Base):
    __tablename__ = "embed_cache"
    content_hash = Column(String(64), primary_key=True)
    model_id = Column(String(64), default="BAAI/bge-m3", nullable=False)
    dense_vector = Column(Vector(1024), nullable=False)
    sparse_weights = Column(JSON)
    created_at = Column(DateTime(timezone=True), default=func.now())


class TopicCluster(Base):
    __tablename__ = "topic_clusters"
    cluster_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    cluster_label = Column(Integer, nullable=False)
    topic_name = Column(String(255), nullable=False)
    centroid = Column(Vector(1024))
    top_keywords = Column(ARRAY(String), nullable=False)
    unit_count = Column(Integer, default=0)
    exemplar_unit_ids = Column(ARRAY(UUID), default=list)
    created_at = Column(DateTime(timezone=True), default=func.now())


class GraphRAGEntity(Base):
    __tablename__ = "graphrag_entities"
    entity_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    name = Column(String(255), nullable=False)
    entity_type = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    source_unit_ids = Column(ARRAY(UUID), default=list, nullable=False)
    frequency = Column(Integer, default=1)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        UniqueConstraint("name", "entity_type", name="uq_entity_natural"),
        Index("idx_entities_name", "name"),
    )


class GraphRAGRelationship(Base):
    __tablename__ = "graphrag_relationships"
    rel_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    source_entity_id = Column(UUID, ForeignKey("graphrag_entities.entity_id", ondelete="CASCADE"), nullable=False)
    target_entity_id = Column(UUID, ForeignKey("graphrag_entities.entity_id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    weight = Column(Float, default=1.0)
    source_unit_ids = Column(ARRAY(UUID), default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        UniqueConstraint("source_entity_id", "target_entity_id", "relationship_type"),
    )


class GraphRAGCommunity(Base):
    __tablename__ = "graphrag_communities"
    community_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    level = Column(Integer, default=0, nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    findings = Column(JSON, default=list, nullable=False)
    member_entities = Column(ARRAY(String), default=list, nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())


class ClusterConsensus(Base):
    __tablename__ = "cluster_consensus"
    consensus_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    wiki_topic_path = Column(Text, unique=True, nullable=False)
    hdbscan_cluster_id = Column(UUID, ForeignKey("topic_clusters.cluster_id"))
    community_id = Column(UUID, ForeignKey("graphrag_communities.community_id"))
    heading_pattern = Column(Text, nullable=False)
    confidence_score = Column(Float, nullable=False)
    status = Column(String(32), default="auto_approved", nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())


class Claim(Base):
    __tablename__ = "claims"
    claim_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    subject = Column(Text, nullable=False)
    predicate = Column(Text, nullable=False)
    object = Column(Text, nullable=False)
    statement_text = Column(Text, nullable=False)
    authority_score = Column(Integer, default=50, nullable=False)
    source_unit_id = Column(UUID, ForeignKey("units.unit_id"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (Index("idx_claims_subject", "subject"),)


class Conflict(Base):
    __tablename__ = "conflicts"
    conflict_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    topic_path = Column(Text, nullable=False)
    claim_a_id = Column(UUID, ForeignKey("claims.claim_id"), nullable=False)
    claim_b_id = Column(UUID, ForeignKey("claims.claim_id"), nullable=False)
    conflict_type = Column(String(64), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(32), default="high", nullable=False)
    resolution_status = Column(String(32), default="open", nullable=False)
    resolution_notes = Column(Text)
    created_at = Column(DateTime(timezone=True), default=func.now())


class DedupPair(Base):
    __tablename__ = "dedup_pairs"
    pair_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    kept_unit_id = Column(UUID, ForeignKey("units.unit_id"), nullable=False)
    suppressed_unit_id = Column(UUID, ForeignKey("units.unit_id"), nullable=False)
    similarity_score = Column(Float, nullable=False)
    method = Column(String(32), nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        UniqueConstraint("kept_unit_id", "suppressed_unit_id"),
        CheckConstraint("kept_unit_id < suppressed_unit_id", name="chk_pair_order"),
        Index("idx_dedup_suppressed", "suppressed_unit_id"),
    )


class DedupReview(Base):
    __tablename__ = "dedup_reviews"
    review_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    pair_id = Column(UUID, ForeignKey("dedup_pairs.pair_id", ondelete="CASCADE"), unique=True, nullable=False)
    reviewer = Column(Text, nullable=False)
    decision = Column(String(32), nullable=False)
    notes = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_dedup_reviews_decision", "decision"),)


class WikiPage(Base):
    __tablename__ = "wiki_pages"
    page_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    file_path = Column(Text, unique=True, nullable=False)
    title = Column(Text, nullable=False)
    page_type = Column(String(64), nullable=False)
    domain = Column(String(64), nullable=False)
    status = Column(String(32), default="active", nullable=False)
    frontmatter = Column(JSON, nullable=False)
    markdown_body = Column(Text, nullable=False)
    git_commit_sha = Column(String(40))
    source_unit_ids = Column(ARRAY(UUID), default=list, nullable=False)
    last_verified_at = Column(DateTime(timezone=True), default=func.now())
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())

    __table_args__ = (Index("idx_wiki_pages_path", "file_path"),)


class WikiChunk(Base):
    __tablename__ = "wiki_chunks"
    chunk_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    page_id = Column(UUID, ForeignKey("wiki_pages.page_id", ondelete="CASCADE"), nullable=False)
    file_path = Column(Text, nullable=False)
    heading_path = Column(ARRAY(String), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    dense_vector = Column(Vector(1024))
    sparse_weights = Column(JSON)
    fts_vector = Column(TSVECTOR)
    chunk_metadata = Column(JSON, default={})
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        UniqueConstraint("page_id", "chunk_index"),
        Index("idx_wiki_chunks_page_id", "page_id"),
        Index("idx_wiki_chunks_fts", "fts_vector", postgresql_using="gin"),
        Index("idx_wiki_chunks_trgm", "content", postgresql_using="gin", postgresql_ops="gin_trgm_ops"),
    )


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"
    job_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    prefect_flow_run_id = Column(String(255))
    job_type = Column(String(64), nullable=False)
    status = Column(String(32), default="running", nullable=False)
    fingerprint = Column(String(128), nullable=False)
    items_processed = Column(Integer, default=0)
    items_failed = Column(Integer, default=0)
    log_summary = Column(Text)
    worker_id = Column(UUID, ForeignKey("workers.worker_id"))
    stage = Column(String(32))
    lease_token = Column(UUID)
    task_id = Column(UUID, ForeignKey("task_queue.task_id"))
    started_at = Column(DateTime(timezone=True), default=func.now())
    completed_at = Column(DateTime(timezone=True))


class CoverageReport(Base):
    __tablename__ = "coverage_reports"
    report_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    page_id = Column(UUID, ForeignKey("wiki_pages.page_id", ondelete="CASCADE"), nullable=False)
    total_units = Column(Integer, nullable=False)
    covered_units = Column(Integer, nullable=False)
    coverage_score = Column(Float, nullable=False)
    uncovered_unit_ids = Column(ARRAY(UUID), default=list)
    job_id = Column(UUID, ForeignKey("pipeline_jobs.job_id"))
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (Index("idx_coverage_page", "page_id"),)


class SyncState(Base):
    __tablename__ = "sync_state"
    sync_key = Column(String(64), primary_key=True)
    delta_token = Column(Text)
    last_sync_started_at = Column(DateTime(timezone=True))
    last_sync_completed_at = Column(DateTime(timezone=True))
    total_files_discovered = Column(Integer, default=0)
    metadata_ = Column("metadata", JSON, default={})


# ---------------------------------------------------------------------------
# Multi-worker orchestration (plan §3–§5)
# ---------------------------------------------------------------------------

class Worker(Base):
    __tablename__ = "workers"
    worker_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    name = Column(String(255), unique=True, nullable=False)
    platform = Column(String(255), default="bare", nullable=False)
    hostname = Column(String(255))
    ip = Column(INET)
    version = Column(String(255))
    status = Column(String(32), default="online", nullable=False)
    capabilities = Column(JSON, default=dict, nullable=False)
    stages_enabled = Column(ARRAY(String), default=list, nullable=False)
    concurrency_max = Column(Integer, default=1, nullable=False)
    worker_token = Column(UUID, server_default=text("gen_random_uuid()"), nullable=False)
    registered_at = Column(DateTime(timezone=True), default=func.now())
    last_heartbeat = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (
        Index("idx_workers_status", "status"),
        Index("idx_workers_heartbeat", "last_heartbeat"),
    )


class TaskQueue(Base):
    __tablename__ = "task_queue"
    task_id = Column(UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    stage = Column(String(32), nullable=False)
    scope_type = Column(String(32), nullable=False)
    scope_id = Column(Text, nullable=False)
    priority = Column(Integer, default=100, nullable=False)
    status = Column(String(32), default="queued", nullable=False)
    attempts = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=3, nullable=False)
    payload = Column(JSON, default=dict, nullable=False)
    next_run_at = Column(DateTime(timezone=True), default=func.now())
    leased_by = Column(UUID, ForeignKey("workers.worker_id"))
    lease_token = Column(UUID)
    lease_expires_at = Column(DateTime(timezone=True))
    result_meta = Column(JSON)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), default=func.now(), onupdate=func.now())
    started_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_task_queue_claim", "stage", "status", "priority", "created_at",
              postgresql_where=text("status IN ('queued','claimed')")),
        Index("idx_task_queue_scope", "stage", "scope_type", "scope_id"),
        Index("idx_task_queue_lease", "lease_expires_at", postgresql_where=text("status = 'claimed'")),
        Index("uq_task_queue_active_scope", "stage", "scope_type", "scope_id", unique=True,
              postgresql_where=text("status IN ('queued','claimed','running')")),
    )


class SourceBlob(Base):
    __tablename__ = "source_blobs"
    source_id = Column(UUID, ForeignKey("sources.source_id", ondelete="CASCADE"), primary_key=True)
    sha256_hash = Column(String(64), nullable=False)
    content_type = Column(String(255), default="application/octet-stream", nullable=False)
    data = Column(LargeBinary, nullable=False)
    size_bytes = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (Index("idx_source_blobs_sha", "sha256_hash"),)


class SourceText(Base):
    __tablename__ = "source_text"
    source_id = Column(UUID, ForeignKey("sources.source_id", ondelete="CASCADE"), primary_key=True)
    content_hash = Column(String(64), nullable=False)
    text = Column(Text, nullable=False)
    char_count = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime(timezone=True), default=func.now())

    __table_args__ = (Index("idx_source_text_hash", "content_hash"),)