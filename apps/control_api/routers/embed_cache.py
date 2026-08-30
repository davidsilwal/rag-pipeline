#!/usr/bin/env python3
"""apps/control_api/routers/embed_cache.py — Embedding cache write path (plan §13).

POST /embed_cache  — idempotent upsert keyed by content_hash.
The notebook and fat worker post BGE-M3 dense vectors here; pgvector stores
them as 1024-d. Sparse BM25-style weights ride along as JSON.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text

from database import get_engine
from deps import optional_worker_token

router = APIRouter(prefix="/embed_cache", tags=["embed_cache"])


class EmbeddingUpsertRequest(BaseModel):
    content_hash: str = Field(..., min_length=64, max_length=64)
    model_id: str = "BAAI/bge-m3"
    dense_vector: list[float]
    sparse_weights: dict | None = None


@router.post("/", summary="Upsert an embedding by content_hash (idempotent)")
async def upsert_embedding(payload: EmbeddingUpsertRequest, _token=None):
    engine = get_engine()
    vec = "[" + ",".join(repr(float(x)) for x in payload.dense_vector) + "]"
    async with engine.begin() as conn:
        result = await conn.execute(
            text("""
                INSERT INTO embed_cache (content_hash, model_id, dense_vector, sparse_weights)
                VALUES (:hash, :model, CAST(:vec AS vector), CAST(:sparse AS jsonb))
                ON CONFLICT (content_hash) DO UPDATE SET
                    model_id = EXCLUDED.model_id,
                    dense_vector = EXCLUDED.dense_vector,
                    sparse_weights = EXCLUDED.sparse_weights,
                    created_at = now()
            """),
            {
                "hash": payload.content_hash,
                "model": payload.model_id,
                "vec": vec,
                "sparse": _json_dumps(payload.sparse_weights or {}),
            },
        )
    return {"status": "upserted", "content_hash": payload.content_hash}


class BatchEmbeddingUpsertRequest(BaseModel):
    rows: list[EmbeddingUpsertRequest]


@router.post("/batch", summary="Bulk upsert embeddings (200+ per call)")
async def batch_upsert_embeddings(payload: BatchEmbeddingUpsertRequest, _token=None):
    engine = get_engine()
    async with engine.begin() as conn:
        for row in payload.rows:
            vec = "[" + ",".join(repr(float(x)) for x in row.dense_vector) + "]"
            await conn.execute(
                text("""
                    INSERT INTO embed_cache (content_hash, model_id, dense_vector, sparse_weights)
                    VALUES (:hash, :model, CAST(:vec AS vector), CAST(:sparse AS jsonb))
                    ON CONFLICT (content_hash) DO UPDATE SET
                        model_id = EXCLUDED.model_id,
                        dense_vector = EXCLUDED.dense_vector,
                        sparse_weights = EXCLUDED.sparse_weights,
                        created_at = now()
                """),
                {
                    "hash": row.content_hash,
                    "model": row.model_id,
                    "vec": vec,
                    "sparse": _json_dumps(row.sparse_weights or {}),
                },
            )
    return {"status": "upserted", "count": len(payload.rows)}


@router.get("/check", summary="Check which content hashes are already cached")
async def check_cached_hashes(hashes: list[str] = Query(...), _token=None):
    """Return the subset of hashes that exist in embed_cache."""
    engine = get_engine()
    if not hashes:
        return []
    async with engine.connect() as conn:
        rows = (await conn.execute(
            text("SELECT content_hash FROM embed_cache WHERE content_hash = ANY(:hashes)"),
            {"hashes": hashes},
        )).fetchall()
    return [r[0] for r in rows]


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, default=str)
