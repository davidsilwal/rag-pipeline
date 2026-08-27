# rag-pipeline

RAG pipeline: ingestion, wiki generation, and markdown recompilation with coverage-aware citation wiring.

## Prerequisites

- Docker
- Docker Compose
- Python 3.12+
- Node.js 18+ (dashboard)

## Quick start

```bash
cp .env.example .env
docker compose up -d
```

## Configuration

Key environment variables:

- `DATABASE_URL`
- `REDIS_URL`
- `API_TOKEN`
- `LITELLM_API_KEY`
- `LOCAL_LLM_API_BASE`
- `LOCAL_LLM_MODEL`



## Secrets hygiene

- Do not commit `.env`, `.env.*`, keys, tokens, or credentials.
- Rotate any key that was ever in a tracked file.
- Use `.env.example` for non-sensitive defaults.