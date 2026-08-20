#!/usr/bin/env python3
"""
workers/gpu_worker/embedder.py
BAAI/bge-m3 dense + sparse embedding pipeline.

Uses FlagEmbedding (CPU/GPU). Embeddings are cached by content_hash in `embed_cache`
(table defined in migrations/init.sql). Only missing hashes are embedded.
"""

import hashlib
import os
import asyncio
from typing import Iterable, Optional
import asyncpg

from . import logger

# Lazy import: FlagEmbedding is heavy & GPU-only; import after the GPU env is ready.
try:
    from FlagEmbedding import BGEM3EmbeddingModel
except ImportError:  # pragma: no cover
    BGEM3EmbeddingModel = None  # type: ignore[assignment]


def sha256_text(text: str) -> str:
    """SHA-256 hex of clean_text — matches units.content_hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BGEM3Embedder:
    """Wraps BAAI/bge-m3 with batching + Postgres cache lookups by content_hash."""

    MODEL_NAME: str = "BAAI/bge-m3"

    def __init__(self, model_name: str = MODEL_NAME, batch_size: int = 32, use_gpu: bool = True):
        if BGEM3EmbeddingModel is None:
            raise RuntimeError(
                "FlagEmbedding not installed in this venv. Install with "
                "`pip install FlagEmbedding==2.*`."
            )
        self.model_name = model_name
        self.batch_size = batch_size
        self.model = BGEM3EmbeddingModel(model_name, use_gpu=use_gpu)
        self.use_gpu = use_gpu

    async def _hash_set_exists(self, pool: asyncpg.pool.Pool, hashes: Iterable[str]) -> set[str]:
        rows = await pool.fetch(
            "SELECT content_hash FROM embed_cache WHERE content_hash = ANY($1)",
            list(hashes),
        )
        return {r["content_hash"] for r in rows}

    async def embed_batch(self, texts: list[str]) -> tuple[list, list]:
        """Return (dense_vectors, sparse_weights) for each input text."""
        results = self.model.encode(
            texts,
            batch_size=self.batch_size,
            return_dense=True,
            return_sparse=True,
            return_colberi=False,  # multi-vector via colbert; keep dense+sparse only
        )
        dense = results["dense_vecs"]         # list[np.ndarray] of 1024-dim
        sparse = results["lexical_weights"]   # list[dict{token_id: weight}]
        return dense, sparse

    async def upsert_cache(
        self, pool: asyncpg.pool.Pool, texts: list[str], dense, sparse
    ) -> int:
        rows = [
            (
                sha256_text(t),
                self.model_name,
                d.tolist(),
                json_dumps(s),
            )
            for t, d, s in zip(texts, dense, sparse)
        ]
        inserted = await pool.executemany(
            """
            INSERT INTO embed_cache (content_hash, model_id, dense_vector, sparse_weights)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (content_hash) DO UPDATE SET
                model_id = EXCLUDED.model_id,
                dense_vector = EXCLUDED.dense_vector,
                sparse_weights = EXCLUDED.sparse_weights;
            """,
            rows,
        )
        return len(rows)

    async def embed_and_cache(
        self, pool: asyncpg.pool.Pool, texts: list[str]
    ) -> dict[str, "list[float]"]:
        """Embed only MISSING hashes (by content_hash); return {content_hash: dense_vector}."""
        hashes = [sha256_text(t) for t in texts]
        existing = await self._hash_set_exists(pool, hashes)
        missing = [(t, h) for t, h in zip(texts, hashes) if h not in existing]

        out: dict[str, list[float]] = {}
        if existing:
            for h in existing:
                row = await pool.fetchrow(
                    "SELECT dense_vector FROM embed_cache WHERE content_hash = $1", h
                )
                if row:
                    out[h] = row["dense_vector"]

        if missing:
            batch_texts = [t for t, _ in missing]
            dense, sparse = await self.embed_batch(batch_texts)
            await self.upsert_cache(pool, batch_texts, dense, sparse)
            for (_, h), d in zip(missing, dense):
                out[h] = d.tolist()

        return out


def json_dumps(obj) -> str:
    import json
    return json.dumps(obj)


# ---------------------------------------------------------------------------
# CLI entry used by Prefect flow / Colab runner
# ---------------------------------------------------------------------------
async def embed_units_by_source(source_id: str) -> int:
    """Embed all units belonging to a source, caching by content_hash. Idempotent."""
    from ..db import get_pool

    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT content_hash, clean_text FROM units
        WHERE source_id = $1 AND (clean_text IS NOT NULL OR clean_text <> '')
        ORDER BY unit_index
        """,
        source_id,
    )
    if not rows:
        return 0
    texts = [r["clean_text"] for r in rows]
    embedder = BGEM3Embedder(use_gpu=bool(os.getenv("GPU_WORKER_CUDA", "1")))
    await embedder.embed_and_cache(pool, texts)
    return len(texts)


import sys

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        logger.error("source_id required; usage: python -m workers.gpu_worker.embedder <source_id>")
        raise SystemExit(2)
    asyncio.run(embed_units_by_source(src))
