# rust-thin-worker

Ultra-lightweight **T2 relay worker** for the RAG pipeline — a single static
Rust binary (~10–15 MB) in a ~10 MB Alpine container, designed to run
**side-by-side** with the existing fat Python worker (`rag-pipeline-worker`,
which keeps torch/FlagEmbedding/models and does the heavy stages).

Follows **§9A "Thin & Ultra-Lightweight Worker Clients"** in
`docs/MULTI_WORKER_PLAN.md`. It is a *client*, not a pipeline: no DB driver,
no models, no torch, no async runtime. Single-threaded, blocking, ~20 MB RSS.

```
┌────────────────────────────┐         ┌─────────────────────────────┐
│ rag-pipeline-worker        │         │ rag-pipeline-worker-thin    │
│ Python, GPU/CPU, all stages│  +  N× │ Rust static, discover/extract│
│ (unchanged)                │         │ /chunk only, long-poll       │
└────────────────────────────┘         └─────────────────────────────┘
                     │                          │
                     └──────── Control API ─────┘
                        (workers + task queue, plan §3–§5)
```

## Quick start

```bash
# build + run merged with the existing stack (side-by-side)
docker compose -f docker-compose.yml -f docker-compose.thin.yml up -d --build worker-thin

# scale out thin nodes
docker compose -f docker-compose.yml -f docker-compose.thin.yml up -d --scale worker-thin=3 worker-thin

# build the image alone
docker build -t rag-pipeline-worker-thin ./workers/rust_thin

# run bare (no docker) — needs the Control API + token
CONTROL_API_URL=https://vps/api/v1 API_TOKEN=... STAGES_ENABLED=discover,chunk \
  DISCOVER_ROOT=/srv/docs cargo run --release
```

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `CONTROL_API_URL` | `http://control-api:8000/api/v1` | Control API base |
| `API_TOKEN` | — | bearer token (worker token from `/workers/register`) |
| `WORKER_NAME` | container `HOSTNAME` | unique per replica automatically |
| `STAGES_ENABLED` | `discover,extract,chunk` | light stages this node runs |
| `DISCOVER_ROOT` | `/workspace` | root scanned by the `discover` stage |
| `LONG_POLL` | `1` | hold claim connection up to ~30 s (battery-friendly) |
| `TASK_LEASE_TTL_SECS` | `120` | short lease — power-aware |
| `MAX_CLAIM` | `1` | one task at a time |
| `POLL_INTERVAL_SECS` | `30` | idle tick cadence |
| `RUST_LOG` | `info` | log level |

## Stage support matrix

| Stage | Runs on thin node? | Notes |
|-------|--------------------|-------|
| `discover` | ✅ | walk `DISCOVER_ROOT`, sha256 each file, register + upload **only new hashes** (one HEAD per known file) |
| `extract` | ✅ text-only | text mimes → `POST /sources/{id}/text`; binaries → completes `{deferred:true}` for the server-side fat worker |
| `chunk` | ✅ | pure string work: headings + 2000-char cap → `POST /units` |
| `embed`, `cluster`, `consensus`, `graphrag`, `compile` | ❌ | answered `stage_not_supported`; scheduler routes to capable workers (capability gate, plan §6) |

## API contract this client expects

Server-side endpoints from plan §13 (`/workers/register`, `/workers/{id}/heartbeat`,
`/workers/{id}/deregister`, `/tasks/claim`, `/tasks/{id}/complete|fail`) **plus** the
thin-client extensions below — these are part of the Phase 0/1 control-plane work:

| Method | Path | Purpose |
|--------|------|---------|
| HEAD | `/sources/by-hash/{sha256}` | 200 = known, 404 = new (upload dedup) |
| POST | `/sources/register` | existing endpoint; `drive_item_id="local:{sha}"` for local scans |
| POST | `/sources/{source_id}/blob` | store raw bytes |
| GET | `/sources/{source_id}/blob` | fetch raw bytes |
| GET | `/sources/by-id/{source_id}` | source metadata (mime/status) |
| POST | `/sources/{source_id}/text` | store extracted plain text |
| GET | `/sources/{source_id}/text` | fetch extracted plain text |
| POST | `/units` | register chunked units (dedup by `content_hash`) |
| POST | `/tasks/claim` with `stages: [...]` | array form keeps one long-poll socket (server may also accept single `stage`) |

`lease_token` is mandatory on `complete`/`fail`; stale tokens are no-ops —
at-least-once + idempotent stages make flaky-edge-network crashes safe.

## Design decisions

- **ureq (blocking) instead of tokio/reqwest** — one dep, no async runtime,
  ~20 MB RSS; the claim loop is I/O-bound so blocking is fine.
- **rustls TLS, musl static** — no openssl/glibc, fully static binary; runs on
  `distroless`-style runtimes and 256 MB VPSes.
- **`panic=abort` + `lto` + `strip`** — ~2–4 MB binary; an unrecoverable panic
  kills the process and the lease-expiry path recovers the task (safe by design).
- **No DB driver, no `DATABASE_URL`** — only the HTTPS API; no DB credentials
  on edge devices.
- **Heartbeat once per tick** (work time + poll interval ≤ TTL) — idle nodes
  send ~1 request per 30 s; short leases let sleeping/powered-down devices
  recover transparently.
- **Content-hash dedup on upload** — unchanged files cost one HEAD round-trip,
  critical on metered links.

## Limitations (accepted for T2)

- Single-threaded: one task at a time (`MAX_CLAIM=1`); long tasks extend the
  lease via the next tick's heartbeat only — for very long stages, raise
  `TASK_LEASE_TTL_SECS` or run the stage server-side.
- No binary (PDF/DOCX/OCR) extraction on-device — deliberately deferred to the
  fat worker.
- No `UPLOAD_ONLY` (T3 agentless) mode yet — that profile is a ~50-line
  watch-folder script; tracked as follow-up in plan §9A.

## Validate

```bash
cargo build --release          # then commit the generated Cargo.lock
docker build -t rag-pipeline-worker-thin ./workers/rust_thin
docker image inspect rag-pipeline-worker-thin | jq '.[0].Size'
```
