# rag-pipeline

RAG pipeline for corpus ingestion, wiki generation, and coverage-aware markdown recompilation. Designed for multi-worker orchestration with a FastAPI control plane, a Next.js dashboard, and GPU-accelerated embedding/GraphRAG stages.

## What it does

- Ingests sources (files, URLs, OneDrive) into structured units
- Embeds and deduplicates chunks with a local or remote embedding backend
- Clusters units, builds consensus summaries, and extracts entities/relationships via GraphRAG
- Compiles wiki pages as Markdown with inline citations and coverage verification
- Exposes a FastAPI control plane for job/task management and a web dashboard for monitoring
- Runs headless workers (CPU or GPU) that claim tasks via a lease queue backed by PostgreSQL

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│  Dashboard  │────▶│  Control API │────▶│  PostgreSQL  │
│  (Next.js)  │     │  (FastAPI)   │     │  + pgvector   │
└─────────────┘     └──────┬───────┘     └──────┬───────┘
                          │                    │
                    ┌─────▼─────┐       ┌──────▼──────┐
                    │   Redis   │       │  Prefect    │
                    └──────────┘       └─────────────┘
                          │
                    ┌─────▼─────┐
                    │  Worker   │
                    │ (runner)  │
                    └─────┬─────┘
                          │
                    ┌─────▼─────┐
                    │  GPU/CPU  │
                    │  stages   │
                    └───────────┘
```

### Stages

`discover → extract → chunk → embed → dedup → cluster → consensus → graphrag → compile`

## Stack

- **API / Control plane**: FastAPI, SQLAlchemy, asyncpg, Prefect
- **Workers**: Python asyncio loop, lazy-loaded heavy deps (`torch`, `litellm`, `FlagEmbedding`, `umap`, `hdbscan`)
- **LLM**: LiteLLM proxy (OpenRouter, NVIDIA, OpenCode Zen, free models)
- **Embedding**: `BAAI/bge-m3` (CPU allowed)
- **Frontend**: Next.js dashboard with TanStack Query
- **Infra**: Docker Compose, PostgreSQL w/ pgvector, Redis

## Prerequisites

- Docker & Docker Compose
- Python 3.12+
- Node.js 18+ (dashboard only)

## Quick start

```bash
cp .env.example .env
docker compose up -d
```

### Worker-only run (no Docker)

```bash
python -m workers.runner
```

## Configuration

Core environment variables:

- `DATABASE_URL` — asyncpg DSN (worker) or SQLAlchemy DSN (API)
- `REDIS_URL`
- `API_TOKEN` — control-plane bearer token
- `LITELLM_API_KEY`
- `LOCAL_LLM_API_BASE` — LLM provider base URL
- `LOCAL_LLM_MODEL` — default model alias
- `GPU_WORKER_CUDA` — set `1` to enable GPU worker
- `EMBEDDING_MODEL_NAME` — embedding model id
- `EMBED_BACKEND` / `EMBED_ALLOW_CPU` — embedding runner toggles

## API

All endpoints require `Authorization: Bearer $API_TOKEN`.

| Namespace | Purpose |
|-----------|---------|
| `/sources` | Upload / list / delete sources |
| `/units` | Chunked unit listings |
| `/wiki` | Wiki pages + refresh |
| `/search` | Semantic search |
| `/jobs` | Long-running ingestion jobs |
| `/tasks` | Task queue inspections |
| `/workers` | Worker registration / lease |

## Dashboard

Access `http://localhost:3000` after `docker compose up` for:
- Source catalog and import status
- Unit / page browsers
- Wiki search and refresh
- Worker health and task queues

## Workers

The worker loop in `workers/runner.py` handles:
- Registration + heartbeat to control API
- Long-poll task claims with lease expiry
- Idempotent stage handlers with retry + fallback
- Graceful SIGTERM drain

## GraphRAG

Entity, relationship, and community extraction live in `workers/gpu_worker/graphrag_engine.py`. Results are persisted to Postgres and used during page compilation.

## Secrets hygiene

- Do not commit `.env`, `.env.*`, keys, tokens, or credentials.
- Rotate any key that was ever in a tracked file.
- Use `.env.example` for non-sensitive defaults.