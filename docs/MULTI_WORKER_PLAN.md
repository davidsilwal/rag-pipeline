# Multi-Worker / Multi-Device Distributed Execution Plan

**Status:** Proposed · **Scope:** Scale the RAG pipeline from 1 worker → N heterogeneous workers (GPU boxes, CPU boxes, ephemeral Colab/Deepnote runtimes) · **Owner:** pipeline team

---

## 0. Executive Summary

The pipeline currently runs as a **single monolithic worker** (`notebooks/deepnote_worker.py` / `docker/worker`) that polls the Control API for up to 50 `discovered` sources and processes them serially through every stage (extract → embed → dedup → cluster → consensus → GraphRAG → wiki).

To support **multiple worker nodes on multiple devices (GPU and CPU machines) effectively**, we need five things:

1. **A worker registry** — every node announces itself, its capabilities (GPU/CPU/RAM/disk), and its liveness.
2. **A durable task queue with lease-based claiming** — small units of work, claimed atomically by exactly one worker, re-queued when a worker dies (the `leased_by` / `lease_token` / `heartbeat_at` columns already exist on `sources` but are **unused**).
3. **Capability-aware scheduling** — GPU-only stages (BGE-M3 embeddings) go to GPU workers; CPU/memory-heavy stages (UMAP+HDBSCAN clustering) go to fat-memory workers; LLM stages go to workers near the LLM gateway.
4. **Idempotent stage execution** — every task can be safely re-run after a crash (already true for `embed_cache` via `content_hash` keys; must be made true for all stages).
5. **Coordination for global stages** — consensus / cluster rebuild / GraphRAG must not run concurrently with conflicting writes (Postgres advisory locks).

The pragmatic recommendation: **use Postgres as the durable queue** (no new infrastructure, `SELECT … FOR UPDATE SKIP LOCKED` for race-free claims) and **extend the existing Control API** into the orchestration layer. Prefect Server is already deployed in `docker-compose.yml` but is **not used by workers today**; it remains a later upgrade path, not a prerequisite.

---

## 1. Current Architecture & Gap Analysis

### 1.1 What exists today

```
                     ┌─────────────────────────────────────────────┐
                     │  VPS Control Plane (docker-compose)        │
                     │  Caddy ──▶ control-api (FastAPI :8000)     │
                     │  Postgres/pgvector :5432  Redis :6379      │
                     │  Prefect Server :4200 (deployed, unused)   │
                     └───────────────┬─────────────────────────────┘
                                     │ REST /api/v1 (+ direct asyncpg)
              ┌──────────────────────┼───────────────────────┐
              ▼                      ▼                       ▼
   docker worker (1 replica)   Colab notebook          Deepnote notebook
   runs deepnote_worker.py     run_pipeline()          run_pipeline()
```

Data model (relevant tables): `sources` (with **unused** `leased_by`, `lease_token`, `heartbeat_at`), `units`, `embed_cache` (keyed by `content_hash` — already idempotent), `topic_clusters`, `pipeline_jobs`, `claims`, `conflicts`, `wiki_pages`.

### 1.2 Gaps that block multi-worker today

| # | Gap | Evidence | Consequence |
|---|-----|----------|-------------|
| G1 | **No exclusive lease** | Worker does `GET /sources/?status=discovered&limit=50`; `sources.leased_by/lease_token/heartbeat_at` never set anywhere | Two workers claim the **same 50 sources** → duplicate work, conflicting status writes |
| G2 | **No task queue** | Worker pulls raw rows by status, not jobs | No prioritization, no retry policy, no per-stage scheduling, no visibility timeout |
| G3 | **No worker registry/heartbeat** | No `workers` table; health endpoint returns hard-coded values (`main.py`) | Control plane cannot see who is online, what device they have, or how loaded they are |
| G4 | **Worker assumes it does everything** | `process_batch_job()` runs all 5 stages serially | A CPU-only node wastes time on embeddings; a GPU node wastes GPU idle time during clustering |
| G5 | **Missing API endpoints** | Worker posts to `/embed_cache` and `/sources/{id}/status`, but those routes **do not exist** in `routers/` | Worker's API path is partially broken today; must be completed as part of orchestration |
| G6 | **Global stages uncoordinated** | consensus / GraphRAG / wiki compile have no lock | Two workers producing wiki pages/clusters concurrently → lost updates |
| G7 | **No retry/poison-ticket handling** | `run_pipeline()` retries the whole batch 3× then gives up | One bad source fails an entire batch; no dead-letter visibility |
| G8 | **Colab/Deepnote hard-code VPS IP & secrets in notebook** | `_colab_setup()` embeds `169.58.94.123`, Azure IDs | Every new worker needs a copy-paste setup; no way to onboard at scale |
| G9 | **Health check is fake** | `main.py` returns static `postgres: True, redis: True, disk_space_gb: 100.0` | Ops cannot trust it; workers treat it as reachability only |

---

## 2. Target Architecture

### 2.1 Principles

- **Control plane owns orchestration; workers are stateless executors.**
- **Tasks are small** (per-source or per-N-units), not "process the whole corpus".
- **At-least-once delivery + idempotent executors** (duplicates are safe, not errors).
- **Capabilities are explicit** — workers advertise what they can run; the scheduler never guesses.
- **Postgres is the single source of truth** for both data and queue state.
- **Fail fast, retry, then dead-letter** — a stuck task must be visible, not silent.

### 2.2 Target topology

```
                     ┌────────────────────────────────────────────────┐
                     │  Control Plane (VPS)                          │
                     │  control-api :8000                            │
                     │    ├─ /workers     (registry + heartbeats)    │
                     │    ├─ /tasks       (claim/complete/retry)     │
                     │    ├─ /sources     (leases on rows)           │
                     │    └─ /stages      (stage metadata)           │
                     │  Postgres (queue + data + advisory locks)     │
                     │  Redis (optional: fan-out/notify, LRU cache)  │
                     └───────┬───────────────┬───────────────┬───────┘
            ┌────────────────┘               │               └────────────────┐
            ▼                                ▼                                ▼
   GPU worker (docker --gpus)       CPU worker (docker, no GPU)     Ephemeral (Colab/Deepnote)
   caps: {gpu: cuda, vram, ...}     caps: {cpu_cores, mem, ...}     caps: {gpu or cpu, short-lived}
   stages: embed, extract,          stages: extract, dedup,          same task loop, lease TTL
   dedup, consensus                 cluster, consensus, graphrag     shortened; rejoin on boot
                                    (big-RAM box)
```

### 2.3 Worker loop (same code on every device)

```
boot:
  load config (WORKER_ID, STAGES_ENABLED, EMBED_DEVICE, MAX_CONCURRENT)
  register with control API (capabilities, version)     → worker_id + token
loop:
  for each enabled stage:
    POST /tasks/claim {stage, worker_id, max_tasks}     → tasks leased to me
    for each task: run stage handler (idempotent), then:
      POST /tasks/{id}/complete {lease_token, ok, result_meta}
      or POST /tasks/{id}/fail {lease_token, error, will_retry}
  POST /workers/{id}/heartbeat {load: {running, queue_len}}
  sleep(POLL_INTERVAL)
shutdown (SIGTERM):
  stop claiming; finish ≤1 in-flight task; POST /workers/{id}/deregister
```

---

## 3. Worker Registry & Capability Advertisement

### 3.1 New table `workers`

```sql
CREATE TABLE workers (
  worker_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name             TEXT NOT NULL UNIQUE,            -- e.g. "gpu-box-01", "colab-session-7f"
  platform         TEXT NOT NULL,                   -- docker | colab | deepnote | bare
  hostname         TEXT,
  ip               INET,
  version          TEXT,                            -- worker code version (for rollout checks)
  status           TEXT NOT NULL DEFAULT 'online',  -- online | draining | offline
  capabilities     JSONB NOT NULL,                  -- see below
  concurrency_max  INTEGER NOT NULL DEFAULT 1,      -- tasks this worker runs in parallel
  registered_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_heartbeat   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_workers_status ON workers(status);
```

Capabilities payload (advertised at register, refreshed on heartbeat):

```json
{
  "gpu":      {"present": true,  "vendor": "nvidia", "cuda": "12.1",
               "vram_mb_per_device": 24576, "device_count": 1},
  "cpu":      {"cores": 16, "model": "EPYC-7B13"},
  "memory":   {"total_mb": 131072, "free_mb": 90000},
  "disk":     {"free_mb": 512000},
  "models":   ["BAAI/bge-m3"],                      // pre-downloaded HF models
  "llm":      {"endpoint": "http://llm-gateway:4000/v1", "models": ["Qwen/Qwen2.5-14B-Instruct-AWQ"]},
  "net":      {"to_control_plane_ms": 12}           // affinity hint
}
```

### 3.2 API endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/workers/register` | Register; returns `worker_id` + `worker_token` (secret for later calls) |
| POST | `/api/v1/workers/{id}/heartbeat` | Refresh liveness + current load; returns updated config/feature flags |
| POST | `/api/v1/workers/{id}/deregister` | Graceful shutdown |
| GET | `/api/v1/workers` | Dashboard: list with `status`, `capabilities`, `last_heartbeat`, `running_tasks` |
| GET | `/api/v1/workers/{id}` | Detail view |

Heartbeat TTL (e.g. 30 s) determines offline detection; a sweeper marks `offline` after 3 missed beats and releases that worker's leases (see §5).

---

## 4. Durable Task Queue (the core)

### 4.1 Recommendation: Postgres as the queue

Rationale: Postgres is already the system of record, the Control API already touches it, and `pgvector` data lives beside the queue. No new broker to operate, no new failure domain, transactional guarantees for free. Prefect Server (already in compose) can wrap this later, but is **not required** to start scaling.

### 4.2 New table `task_queue`

```sql
CREATE TABLE task_queue (
  task_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  stage             TEXT NOT NULL,          -- extract|chunk|embed|dedup|cluster|consensus|graphrag|compile
  scope_type        TEXT NOT NULL,          -- source | unit_batch | corpus | topic
  scope_id          TEXT NOT NULL,          -- source_id / hash range / corpus marker
  priority          INT  NOT NULL DEFAULT 100,   -- lower = sooner (P0=0, P3=300)
  status            TEXT NOT NULL DEFAULT 'queued',  -- queued|claimed|running|succeeded|failed|dead_letter
  attempts          INT  NOT NULL DEFAULT 0,
  max_attempts      INT  NOT NULL DEFAULT 3,
  payload           JSONB,                  -- stage-specific parameters (no blobs)
  leased_by         UUID REFERENCES workers(worker_id),
  lease_token       UUID,
  lease_expires_at  TIMESTAMPTZ,
  result_meta       JSONB,                  -- e.g. items_processed, vectors written
  error_message     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  started_at        TIMESTAMPTZ,
  completed_at      TIMESTAMPTZ
);
CREATE INDEX idx_task_queue_claim
  ON task_queue (stage, status, priority, created_at)
  WHERE status IN ('queued','claimed');
```

### 4.3 Race-free claiming with `SKIP LOCKED`

The single most important query — safe under any number of concurrent workers:

```sql
-- POST /tasks/claim {stage, worker_id, max_tasks}
WITH candidate AS (
    SELECT task_id FROM task_queue
    WHERE stage = $1
      AND status = 'queued'
      AND (lease_expires_at IS NULL OR lease_expires_at < now())   -- reclaim expired
    ORDER BY priority, created_at
    LIMIT $3
    FOR UPDATE SKIP LOCKED
)
UPDATE task_queue t
   SET status = 'claimed', leased_by = $2, lease_token = gen_random_uuid(),
       lease_expires_at = now() + make_interval(secs => $4),        -- TTL
       attempts = attempts + 1, started_at = now()
  FROM candidate c
 WHERE t.task_id = c.task_id
RETURNING t.task_id, t.stage, t.scope_type, t.scope_id, t.payload, t.lease_token, t.lease_expires_at;
```

- `FOR UPDATE SKIP LOCKED` → concurrent claims never block each other and never double-assign.
- `lease_expires_at < now()` → lazy **reclaim**: crashed workers' tasks are picked up by the next claim without a sweeper (though a periodic sweeper is still useful for visibility).
- The returned `lease_token` must be presented on `complete`/`fail`/`heartbeat` — stale completions from a lost lease are rejected (compare-and-set on token).

### 4.4 Task lifecycle

```
queued ──claim──▶ claimed ──complete──▶ succeeded
   ▲                  │
   │                  ├──fail──▶ failed ──(attempts < max)──▶ queued (backoff)
   │                  │                     └─(attempts ≥ max)──▶ dead_letter
   └──lazy reclaim────┘ (lease expired / worker offline)
```

- **Complete** = `UPDATE … SET status='succeeded', result_meta=$x WHERE task_id=$1 AND lease_token=$2` (no-op if token mismatch).
- **Fail** = record `error_message`; if `attempts >= max_attempts` → `dead_letter` (alert), else re-queue with exponential backoff `next_run_at = now() + 2^attempts`.
- **Heartbeat** = `UPDATE task_queue SET lease_expires_at = now()+TTL WHERE task_id=$1 AND lease_token=$2`.

### 4.5 Task granularity & fan-out

| Stage | Granularity | Fan-out rationale |
|-------|-------------|-------------------|
| extract/chunk | per `source_id` | parallelize across sources; failure blast radius = 1 file |
| embed | per source (or per 200 units) | BGE-M3 batches; parallelize across GPU workers |
| dedup | per source pair bucket | MinHash LSH bucket = independent |
| cluster | 1 corpus-wide task | UMAP+HDBSCAN on full embedding set — **single worker, advisory-locked** |
| consensus/claims | per topic batch | LLM-bound → parallelize up to LLM gateway throughput |
| graphrag/compile | 1 task each | single-writer, advisory-locked |

Producer rules: `sources.register` enqueues `extract`; `extract` completion enqueues `chunk` → `embed`; `embed` completion for a source enqueues `dedup`; a per-corpus scheduler task enqueues `cluster` → `consensus` → `graphrag` → `compile` on a cron/timer. All enqueues are idempotent (`ON CONFLICT` on `(stage, scope_type, scope_id)` for queued tasks).

---

## 5. Lease & Heartbeat Protocol

- **Claim TTL** per stage: embed 10 min, extract 15 min, cluster 60 min, consensus 20 min (a stage that legitimately runs longer must heartbeat to extend).
- **Heartbeat cadence**: every 10–15 s (`lease_expires_at = now() + TTL`).
- **Lost lease handling**: worker that notices `lease_token` mismatch on complete/fail treats the task as abandoned (another worker may have reclaimed it) and **stops** touching its side effects — this is the at-least-once contract.
- **Worker-side concurrency**: `MAX_CONCURRENT_TASKS` per worker (e.g. 1 on Colab, 2–4 on a big GPU box, 4–8 on CPU boxes for I/O-bound stages). All claims of one worker share one heartbeat.
- **Offline worker**: sweeper (`cron`-ish task in control API or a lightweight background task) marks `status='offline'` after 3 missed beats and force-expires `lease_expires_at` on its claimed tasks → next claim reclaims them.
- **Sources-level leases** (for download/extract where row-level status matters): reuse the existing `leased_by/lease_token/heartbeat_at` columns exactly as above; status transitions become `discovered → claimed → extracted → indexed → error` and are guarded by `WHERE lease_token = $x`.

---

## 6. Capability-Aware Scheduling

### 6.1 Stage → capability matrix

| Stage | Device | Min capability | Notes |
|-------|--------|----------------|-------|
| extract / chunk | CPU | 1 core, 1 GB | trivial; runs anywhere |
| **embed (BGE-M3)** | **GPU** | `gpu.present=true`, ≥8 GB VRAM | CPU fallback only if `EMBED_ALLOW_CPU=1` on that worker (slow, one-time thanks to `embed_cache`) |
| dedup (MinHash LSH) | CPU | 2 cores, 4 GB | I/O + hashing bound |
| **cluster (UMAP+HDBSCAN)** | **CPU fat** | ≥8 cores, ≥32 GB RAM | must fit full corpus vectors; pin via `capabilities.memory` + advisory lock |
| consensus / claims | CPU+LLM | `llm.endpoint` set | throughput bound by LLM gateway |
| graphrag / wiki compile | CPU | any | single-writer (advisory lock) |

### 6.2 Eligibility filter (SQL-side)

```sql
WHERE task.stage = $stage
  AND worker.enabled_stages @> $stage          -- worker opted in
  AND worker.status = 'online'
  AND stage_capability_satisfied(worker.capabilities, $stage)  -- e.g. GPU check
  AND worker.running_tasks < worker.concurrency_max
```

### 6.3 Ranking (score = capability × load × affinity)

```
score = 100 * cap_ok + 30 * (1 - running/concurrency_max) + 10 * affinity
```

- `cap_ok` = 1 if device requirement met (hard gate), else 0.
- Load term → naturally spreads work; weighted round-robin prevents one busy worker from hoarding.
- Affinity term → worker that already downloaded the file (`worker_id` on the source row) or is on the same LAN as the LLM gateway.

### 6.4 Priority & fairness

- `priority` from discovery (`extraction_priority`: P0 wiki knowledge → 0, P3 assets → 300); queue orders by `priority, created_at`.
- Anti-starvation: every Nth claim cycle, a **starved-task pass** promotes tasks queued > threshold regardless of priority.

---

## 7. GPU / CPU Heterogeneity Handling

1. **Embeddings**: GPU workers claim `embed` tasks; `BGEM3Embedder` already branches on `torch.cuda.is_available()`. Add `EMBED_DEVICE=auto|cuda|cpu` and `EMBED_BATCH_SIZE` (32 default; 8 on CPU). The `embed_cache` content-hash key means a CPU fallback embedding is computed **once per unique text** — cheap insurance for bursty corpora.
2. **Model provisioning**: bake BGE-M3 into the worker image **or** pre-pull via `hf_hub_download` at register time (report `models` in capabilities so the scheduler only sends embed tasks to workers that have it). Avoid download stampede across 10 workers.
3. **LLM inference is a separate service**: run vLLM/Ollama/LiteLLM on dedicated nodes; pipeline workers call the gateway URL (already supported via `LOCAL_LLM_API_BASE` / LiteLLM). Pipeline workers never load a 14B model themselves.
4. **One machine, multiple workers**: on a GPU box, run `device_count` embed workers (`CUDA_VISIBLE_DEVICES=i` per process) **plus** CPU-bound workers (dedup/cluster) — keeps the GPU box busy while the GPU idles during CPU stages.
5. **Device detection at boot**: worker probes `torch.cuda.is_available()`, `pynvml` VRAM, `os.cpu_count()`, free RAM/disk → builds its capabilities JSON. No manual config beyond a `WORKER_PROFILE` override.

---

## 8. Data & Shared-State Safety

- **Single Postgres** remains the source of truth; workers write through the Control API (ephemeral runtimes) or direct asyncpg pool (trusted docker workers). All writes are idempotent upserts keyed by `content_hash` / `(source_id, unit_index)` / natural keys (already the pattern in `embedder.upsert_cache`).
- **Embedding race**: two workers embedding the same missing hash → both compute, `ON CONFLICT DO UPDATE` makes it safe. Optional optimization: `pg_advisory_xact_lock(hashtext('embed:'||content_hash))` around `embed_and_cache`.
- **Global-stage locks**: cluster / consensus / graphrag / wiki compile run under `pg_advisory_xact_lock('cluster')`-style keys; a second claimant skips (or waits briefly) and records `skipped_by` in `result_meta` so dashboards show it.
- **Content access**: most stages only need `units.raw_text` / `clean_text` (already in Postgres). For binary extraction (PDF/DOCX), choose per fleet: (a) **API download** — control plane stores blobs (e.g. in object storage) and serves `GET /sources/{id}/blob` with a short-lived signed URL (works for Colab/Deepnote, no shared FS needed); (b) **shared volume/NFS/S3** mounted on trusted workers. Recommend (a) for the ephemeral fleet.
- **Migrations** run only from the control plane; workers must never migrate.
- **Dead-letter retention**: `dead_letter` tasks kept with full `payload` + `error_message` for manual replay via `POST /tasks/{id}/requeue`.

---

## 9. Worker Runtime & Packaging

- **One image, two profiles**: `rag-pipeline-worker:latest` (same code); GPU nodes run `docker compose --profile gpu` with `--gpus all` + `NVIDIA_VISIBLE_DEVICES`; CPU nodes run the plain profile. Entrypoint becomes `python -m workers.runner` (new module), replacing the notebook's `run_pipeline()` as the canonical loop; the notebook path delegates to the same module for Colab/Deepnote compatibility.
- **Two worker images, side by side**: the fat Python worker above is **unchanged**, and a second image `rag-pipeline-worker-thin` (Rust, `workers/rust_thin/`, T2 relay, ~10 MB) is added alongside via `docker-compose.thin.yml` (`--scale worker-thin=N` for N thin nodes; each replica gets a unique registry name from its container HOSTNAME). Thin nodes run only `discover/extract(text)/chunk` and answer everything else `stage_not_supported` (see §9A).
- **New env vars**:

| Var | Default | Meaning |
|-----|---------|---------|
| `WORKER_ID` | auto | stable id (hostname) for registry |
| `WORKER_PROFILE` | auto | gpu \| cpu \| colab \| deepnote — overrides detection |
| `STAGES_ENABLED` | all | comma list, e.g. `extract,embed,dedup` |
| `EMBED_DEVICE` | auto | auto \| cuda \| cpu |
| `EMBED_ALLOW_CPU` | 0 | permit CPU embedding fallback |
| `EMBED_BATCH_SIZE` | 32 | encode batch |
| `MAX_CONCURRENT_TASKS` | 1 | per-worker parallelism |
| `TASK_POLL_INTERVAL` | 15 | claim loop sleep (s) |
| `TASK_LEASE_TTL` | 600 | claim TTL (s); ephemeral workers use 120 |
| `CONTROL_API_URL` / `API_TOKEN` | — | existing |

- **Supervision**: docker `restart: unless-stopped` or systemd unit; `SIGTERM` → graceful drain (stop claiming, finish ≤1 task, deregister, unset leases on in-flight tasks).
- **Scaling a machine**: `docker-compose.workers.yml` with `deploy.replicas` + per-replica `WORKER_ID`/`CUDA_VISIBLE_DEVICES`; or systemd `worker@.service` template.
- **Onboarding Colab/Deepnote**: replace hard-coded VPS IP/secrets in `_colab_setup()` with a single onboarding URL `https://vps/onboard` that returns env overrides + worker token, and set short TTLs so ephemeral sessions can vanish safely.

---

## 9A. Thin & Ultra-Lightweight Worker Clients (low-end devices)

**Core principle: a low-end node runs a *client*, never a pipeline.** It never imports torch/FlagEmbedding/transformers, never downloads models, never touches the DB. The capability gate (§6) guarantees the scheduler only ever sends it stages it can run; everything else is executed server-side or on fat workers. The empty `workers/vps_thin/` package is the home for this code.

### 9A.1 Client profile tiers (lightest → heaviest)

| Tier | What runs on the device | Footprint | Best for |
|------|------------------------|-----------|----------|
| **T3 — Agentless** | Nothing. Device exposes a folder (SFTP/WebDAV/SMB) or runs a ~50-line **uploader**: watch folder → hash locally (`sha256`) → `POST /sources/register` + `POST /sources/{id}/blob` only for **new hashes** → control plane does everything | ~0 deps, ~30 MB (Python) or ~10 MB (Go) | NAS, SMB share, edge box sitting next to the data |
| **T2 — Relay client** | Claims `discover` / `extract` / `chunk` tasks and executes them; any other stage is answered `stage_not_supported` and the control plane re-routes it to an eligible worker | stdlib-only Python, or one static Go/Rust binary (~10–15 MB disk, ~15–25 MB RAM) — **reference implementation: `workers/rust_thin/`** (Rust, containerized, ~10 MB image) | Raspberry Pi Zero 2 (512 MB), 256 MB VPS, battery devices |
| **T1 — Thin executor** | T2 + text extraction (markdown/txt/html, PDF via `pypdf`) + optional small-bucket MinHash dedup | small venv (~60–120 MB), deps 3–6 (`httpx`, `pypdf`, `mmh3`, …) | 1–2 GB RAM SBCs, small VPS |
| T0 — Fat worker (today) | everything incl. embed/cluster/consensus | >5 GB (torch+models), 2–16 GB+ RAM | GPU boxes, big-RAM CPU boxes |

### 9A.2 Lightweighting techniques (concrete)

1. **Lazy, capability-gated imports.** `torch`, `FlagEmbedding`, `transformers`, `sklearn`, `umap`, `hdbscan` must never be imported on a thin node — not even for `import` side effects (the current notebook does `import torch` at module top: banned in the thin runner). Put heavy imports inside stage handlers only (already the pattern in `embedder.py`), and short-circuit before the handler if `profile=thin` and the stage is not in `STAGES_ENABLED`.
2. **No DB driver.** Thin clients talk **only** to the HTTPS Control API (`httpx` or stdlib `urllib`). No asyncpg/psycopg2, no `DATABASE_URL` — smaller install and no DB credentials on edge devices.
3. **No model files, no HF cache.** `models: []` in capabilities → scheduler never assigns `embed`. Optionally report `models: ["none"]` and set `EMBED_ALLOW_CPU=0`.
4. **No dependency auto-install.** The notebook's `pip install FlagEmbedding torch ...` bootstrap is the inverse of lightweight. Thin runner: `WORKER_PROFILE=thin` + missing optional dep → fail fast with a clear message (`error: optional stage dependency missing, run --profile thin-full`), never silent pip.
5. **Content-hash upload dedup.** The device computes `sha256` locally during discovery (already in the manifest) and uploads blobs **only when the hash is new** (`GET /sources/by-hash/{sha256}` or a `HEAD /sources/{id}/blob`). Unchanged files cost one API round-trip, not a transfer — critical on metered/edge links.
6. **Long-poll instead of busy-poll.** `POST /tasks/claim?long_poll=true` — the server holds the HTTP connection up to ~25–30 s and returns as soon as a task is eligible → ~1 wakeup per 30 s (battery-friendly) instead of a poll every 15 s. Optional WebSocket channel for real-time push later; both are drop-in behind the same `claim` API.
7. **Power-aware leases.** Thin nodes may sleep. `TASK_LEASE_TTL=120` (short), heartbeat **only while a task is in flight**; idle nodes need no heartbeats beyond their claim cadence. If the device sleeps mid-task, the lease expires and the idempotent stage is re-executed by another worker — at-least-once makes sleep safe.
8. **Tiny claim sizes & backoff.** `MAX_CONCURRENT_TASKS=1`, claim `max_tasks=1`; on network failure, exponential backoff (2 s → 4 → 8 … cap 60 s). No tight loops.
9. **Compressed transport.** gzip request/response bodies for blob uploads and vector-sized payloads; JSON for control messages. `httpx` handles this natively; stdlib client uses `urllib` + manual `gzip`.
10. **Static-binary option (T2/T3).** The client loop (claim → run light stage → complete + heartbeat) is small enough to re-implement in Go/Rust as a single static binary — no Python runtime, ~10–15 MB disk, ~15–25 MB RAM, trivial to deploy with systemd on a 256 MB VPS.
11. **Minimal secrets.** One short-lived per-worker token (from `/workers/register`); TLS with pinned CA or mTLS; nothing else stored. Smaller attack surface = fewer deps = fewer CVEs.

### 9A.3 Thin profile config (add to §9 env table)

| Var | Thin default | Meaning |
|-----|--------------|---------|
| `WORKER_PROFILE` | `thin` | disables heavy stages, bans heavy imports |
| `STAGES_ENABLED` | `discover,extract,chunk` | relay/executor scope |
| `LONG_POLL` | `1` | hold claim connection up to 30 s |
| `TASK_LEASE_TTL` | `120` | short — power-aware |
| `MAX_CONCURRENT_TASKS` | `1` | one task at a time |
| `UPLOAD_ONLY` | `0` | `1` = T3 uploader mode (no task loop) |

### 9A.4 Safety net

The capability gate is the first line of defense (thin worker is never *eligible* for `embed`/`cluster`/`consensus`). If a thin client ever receives an unsupported stage (e.g. after a config change), it responds `fail(error="stage_not_supported", will_retry=false)`; the control plane's scheduler re-enqueues the task for an eligible worker and records the mismatch as a config alert. A thin node must **never** silently drop a task.

### 9A.5 Ops notes for edge devices

- Flaky networks → lease expiry is the *expected* path, not an anomaly; rely on idempotent re-execution and keep `attempts` budget server-side.
- Mark `platform=thin`, `net.to_control_plane_ms` high → scheduler weights affinity against them for big payload tasks.
- Battery/power-managed devices: prefer T3 uploader bursts over a persistent task loop; heartbeats are the only steady-state traffic.

---

## 10. Observability & Operations

- **Dashboards** (control API JSON is enough to start; add Prometheus later):
  - Workers: `status`, `last_heartbeat_age`, `capabilities`, `running_tasks`, `tasks_done/failed` per worker.
  - Queue: depth per stage, `claimed` age, `dead_letter` count, reclaim rate.
  - Throughput: tasks/s per stage, avg duration, retry rate.
- **Checkpointing**: extend `pipeline_jobs` with `worker_id`, `stage`, `lease_token`; workers call `POST /jobs/checkpoint` per task (existing endpoint, add fields) — gives a full audit trail.
- **Logging**: JSON structured logs with `worker_id`, `task_id`, `stage`; ship to `POST /logs` (new) or a sidecar (Loki/ELK) if present.
- **Health**: make `GET /health` real (async PG `SELECT 1`, Redis ping, queue depths, disk).
- **Alerts** (control plane background task):
  - worker offline > 3× heartbeat TTL,
  - task queued > 30 min (starvation / scheduler bug),
  - task retries > 2 (poison),
  - dead_letter count > 0 (manual replay needed),
  - queue depth > X (scale signal).

---

## 11. Failure Handling & Recovery

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Worker crash (kill -9, Colab session dies) | missed heartbeats → offline; leases expire | lazy reclaim on next claim → re-queue → idempotent re-execution |
| Task exception | `fail` with `error_message` | retry w/ backoff up to `max_attempts` → dead_letter |
| Stale completion (lease reclaimed by another worker) | `lease_token` mismatch → no-op | worker aborts side effects for that task |
| Two workers race a global stage | advisory lock | loser skips and records `skipped_by` |
| Control API restart | — | Postgres durability; boot sweeper reclaims expired leases; queue intact |
| Network partition | heartbeats fail → lease expiry | same as worker crash |
| Model download stall | register timeout | pre-pull in image; report `models` in capabilities |

---

## 12. Migration Roadmap (phased)

### Phase 0 — Foundations (week 0)
- [ ] Write `workers` table + register/heartbeat/deregister/list API.
- [ ] Real `/health` (PG + Redis + queue checks).
- [ ] Implement missing worker-facing endpoints: `POST /embed_cache`, `POST /sources/{id}/status` (idempotent, token-guarded).
- **Acceptance:** 2 docker workers register, heartbeat, and show online in `/workers`.

### Phase 1 — Queue & leases (week 1)
- [ ] `task_queue` table + `claim/complete/fail/heartbeat/requeue` API with `SKIP LOCKED`.
- [ ] New `workers/runner.py` task loop; notebooks delegate to it (keep old batch path behind `LEGACY_BATCH=1` for rollback).
- [ ] Source-level leases on `sources` (status guard + token).
- **Acceptance:** 2 workers claim disjoint tasks (no overlap in audit log); kill a worker mid-task → task re-queued and finished by the other.

### Phase 2 — Capability-aware scheduling (week 2)
- [ ] Stage→capability matrix + eligibility filter + load-aware ranking.
- [ ] Priority ordering + anti-starvation pass.
- [ ] `EMBED_DEVICE`/`EMBED_ALLOW_CPU` gating; CPU-only workers skip embed unless allowed.
- **Acceptance:** GPU worker runs embeddings; CPU-only worker never claims embed tasks; P0 sources finish before P3.

### Phase 3 — Coordination & content access (week 3)
- [ ] Advisory locks for cluster/consensus/graphrag/compile; skip + `skipped_by` bookkeeping.
- [ ] Blob download API (or S3/NFS option) for binary extraction.
- [ ] Colab/Deepnote onboarding endpoint; short TTLs for ephemeral workers.
- **Acceptance:** 1 GPU + 2 CPU + 1 Colab worker complete a full corpus run end-to-end with zero duplicate wiki pages and no lost updates.

### Phase 4 — Ops & hardening (week 4)
- [ ] Metrics + dashboards + alerts.
- [ ] Chaos tests: kill workers randomly, restart control API, partition network → assert no duplicate/lost tasks.
- [ ] Scale test 2 → 10 workers; tune TTLs, batch sizes, concurrency.
- [ ] Optional: Prefect work-pool integration (reuse deployed Prefect Server) as an alternative orchestrator — evaluated against PG-queue baseline.
- **Acceptance:** sustained throughput ~N× single worker with ≤2% duplicate work (absorbed by idempotency), zero silent data loss.

---

## 13. Concrete API Surface (new/changed)

| Method | Path | Notes |
|--------|------|-------|
| POST | `/api/v1/workers/register` | returns worker_id + token |
| POST | `/api/v1/workers/{id}/heartbeat` | load + capabilities refresh |
| POST | `/api/v1/workers/{id}/deregister` | graceful |
| GET | `/api/v1/workers` | dashboard |
| POST | `/api/v1/tasks/claim` | `{stage, worker_id, max_tasks}` → leased tasks |
| POST | `/api/v1/tasks/{id}/heartbeat` | extends TTL (token-guarded) |
| POST | `/api/v1/tasks/{id}/complete` | `{lease_token, result_meta}` |
| POST | `/api/v1/tasks/{id}/fail` | `{lease_token, error_message, will_retry}` |
| POST | `/api/v1/tasks/{id}/requeue` | admin: replay from dead_letter |
| GET | `/api/v1/tasks` | filters: stage, status, worker |
| POST | `/api/v1/embed_cache` | **new** — worker write path (idempotent upsert) |
| POST | `/api/v1/sources/{id}/status` | **new** — guarded status transition |
| GET | `/api/v1/sources/{id}/blob` | short-lived content access (Phase 3) |
| GET | `/api/v1/health` | real checks |

All mutating endpoints require the caller's worker token (or API_TOKEN for admin).

---

## 14. Effectiveness / Sizing Guidance

- **Bottleneck reality**: embeddings are GPU-bound; clustering is RAM-bound; consensus is LLM-gateway-bound. Match worker mix to the dominant bottleneck:
  - Embedding-heavy (large corpus): 1 GPU worker ≈ 4–8× CPU worker throughput → add GPU workers first.
  - Clustering-heavy: one fat-memory CPU box (≥32 GB) for `cluster`; GPU workers idle there — so **co-locate** CPU stages on GPU boxes.
- **Batch sizes**: embed batch 32 GPU / 8 CPU; dedup buckets sized so each task ≤ ~30 s; consensus tasks ≤ ~200 claims per topic batch.
- **DB load**: workers on untrusted networks go through the API (connection pool bounded); trusted docker workers may use direct asyncpg. Keep `pg_hba` scoped per worker.
- **Vector payloads**: 1024-dim dense vectors via JSON are fine at this scale; if transfer becomes a bottleneck, switch embed_cache writes to binary COPY over a trusted channel.
- **Cost**: use ephemeral Colab/Deepnote for burst capacity (short TTLs, rejoin on boot), steady-state fleet on owned GPU/CPU boxes.

---

## 15. Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| Duplicate work from at-least-once delivery | Idempotent writers keyed by content_hash/natural keys; token-guarded transitions |
| Two workers overwrite wiki_pages | Advisory lock + `updated_at` last-write-wins + `skipped_by` audit |
| Ephemeral workers vanish mid-task | Short TTL + lazy reclaim + idempotent re-execution |
| Embedding compute duplication | `embed_cache` content-hash dedup; optional per-hash advisory lock |
| Model download stampede on new nodes | Pre-pulled models in image / HF cache volume; `models` capability gate |
| Control API becomes a bottleneck at 10+ workers | Stateless FastAPI behind Caddy; claim query is one indexed UPDATE; scale horizontally if needed |
| Prefect upgrade path conflicts with PG queue | Keep queue abstraction in `workers/runner.py`; swap transport behind the same task API |
| Secrets in Colab/Deepnote notebooks | Onboarding endpoint issues short-lived worker tokens; remove hard-coded IP/IDs |

---

## Appendix A — Worker `runner.py` pseudocode (skeleton)

```python
async def main():
    cfg = load_config()                       # WORKER_* env vars
    caps = detect_capabilities(cfg.profile)   # torch.cuda, pynvml, os.cpu_count, shutil.disk_usage
    reg = await api.post("/workers/register", {"name": hostname, "capabilities": caps,
                                                "stages_enabled": cfg.stages})
    while not shutdown.is_set():
        for stage in cfg.stages:
            tasks = await api.post("/tasks/claim", {"stage": stage, "worker_id": reg.id,
                                                     "max_tasks": slot_for(stage)})
            for t in tasks:
                asyncio.create_task(run_task(t, reg.token))   # bounded by concurrency
        await api.post(f"/workers/{reg.id}/heartbeat", {"load": load()})
        await asyncio.sleep(cfg.poll_interval)

async def run_task(t, token):
    hb = asyncio.create_task(heartbeat_loop(t, token))
    try:
        meta = await STAGE_HANDLERS[t.stage](t.payload)       # idempotent
        await api.post(f"/tasks/{t.task_id}/complete", {"lease_token": t.lease_token, "result_meta": meta})
    except Exception as e:
        await api.post(f"/tasks/{t.task_id}/fail", {"lease_token": t.lease_token,
                                                     "error_message": str(e), "will_retry": True})
    finally:
        hb.cancel()
```
