-- ============================================================================
-- 0003_multi_worker.sql — Multi-worker orchestration (plan §3–§5).
-- Additive only. Safe to re-run (IF NOT EXISTS).
-- Apply with: psql "$DATABASE_URL" -f migrations/versions/0003_multi_worker.sql
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Worker registry (plan §3.1). Capabilities advertised at register, refreshed
-- on heartbeat. `worker_token` is the per-worker secret for auth (§13).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workers (
    worker_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL UNIQUE,
    platform          TEXT NOT NULL DEFAULT 'bare',
    hostname          TEXT,
    ip                INET,
    version           TEXT,
    status            TEXT NOT NULL DEFAULT 'online',     -- online | draining | offline
    capabilities      JSONB NOT NULL DEFAULT '{}'::jsonb,
    stages_enabled    TEXT[] NOT NULL DEFAULT '{}',
    concurrency_max   INTEGER NOT NULL DEFAULT 1,
    worker_token      UUID NOT NULL DEFAULT gen_random_uuid(),
    registered_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_heartbeat    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_workers_status ON workers(status);
CREATE INDEX IF NOT EXISTS idx_workers_heartbeat ON workers(last_heartbeat);

-- ---------------------------------------------------------------------------
-- Durable task queue (plan §4.2). Claimed race-free with FOR UPDATE SKIP LOCKED.
-- `next_run_at` backs the exponential-backoff re-queue path (§4.4).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_queue (
    task_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stage             TEXT NOT NULL,      -- extract|chunk|embed|dedup|cluster|consensus|graphrag|compile|discover
    scope_type        TEXT NOT NULL,      -- source | unit_batch | corpus | topic
    scope_id          TEXT NOT NULL,      -- source_id / corpus marker / topic path
    priority          INTEGER NOT NULL DEFAULT 100,
    status            TEXT NOT NULL DEFAULT 'queued',  -- queued|claimed|running|succeeded|failed|dead_letter
    attempts          INTEGER NOT NULL DEFAULT 0,
    max_attempts      INTEGER NOT NULL DEFAULT 3,
    payload           JSONB NOT NULL DEFAULT '{}'::jsonb,
    next_run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    leased_by         UUID REFERENCES workers(worker_id),
    lease_token       UUID,
    lease_expires_at  TIMESTAMPTZ,
    result_meta       JSONB,
    error_message     TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ
);
-- Claim hot path: find queued/reclaimable work for a stage, ordered by priority.
CREATE INDEX IF NOT EXISTS idx_task_queue_claim
    ON task_queue (stage, status, priority, created_at)
    WHERE status IN ('queued','claimed');
CREATE INDEX IF NOT EXISTS idx_task_queue_scope ON task_queue (stage, scope_type, scope_id);
CREATE INDEX IF NOT EXISTS idx_task_queue_lease ON task_queue (lease_expires_at) WHERE status = 'claimed';
-- Idempotent enqueue: at most one *active* task per (stage, scope).
CREATE UNIQUE INDEX IF NOT EXISTS uq_task_queue_active_scope
    ON task_queue (stage, scope_type, scope_id)
    WHERE status IN ('queued','claimed','running');

-- ---------------------------------------------------------------------------
-- Source binary blobs & extracted text (plan §8 / thin-client extensions).
-- Kept out of `sources` to avoid bloating the hot row; idempotent by hash.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS source_blobs (
    source_id     UUID NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    sha256_hash   CHAR(64) NOT NULL,
    content_type  TEXT NOT NULL DEFAULT 'application/octet-stream',
    data          BYTEA NOT NULL,
    size_bytes    BIGINT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id)
);
CREATE INDEX IF NOT EXISTS idx_source_blobs_sha ON source_blobs(sha256_hash);

CREATE TABLE IF NOT EXISTS source_text (
    source_id     UUID NOT NULL REFERENCES sources(source_id) ON DELETE CASCADE,
    content_hash  CHAR(64) NOT NULL,
    text          TEXT NOT NULL,
    char_count    INTEGER NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id)
);
CREATE INDEX IF NOT EXISTS idx_source_text_hash ON source_text(content_hash);

-- ---------------------------------------------------------------------------
-- Extend pipeline_jobs with per-task checkpoint columns (plan §10). Additive.
-- ---------------------------------------------------------------------------
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS worker_id UUID REFERENCES workers(worker_id);
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS stage TEXT;
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS lease_token UUID;
ALTER TABLE pipeline_jobs ADD COLUMN IF NOT EXISTS task_id UUID REFERENCES task_queue(task_id);
