#!/usr/bin/env python3
"""
workers/gpu_worker/embedder.py
BAAI/bge-m3 dense + sparse embedding pipeline with runtime compatibility.

This wrapper prefers a working local path and falls back gracefully when
the installed FlagEmbedding package exposes a different API than older
versions.
"""

from __future__ import annotations

import hashlib
import os
import asyncio
from typing import Iterable, Optional
import asyncpg

from . import logger

# Lazy import: FlagEmbedding is heavy; import after the runtime is ready.
try:
    from FlagEmbedding import BGEM3FlagModel
except ImportError:  # pragma: no cover
    BGEM3FlagModel = None  # type: ignore[assignment]

try:
    from FlagEmbedding import M3Embedder  # type: ignore[import]
except ImportError:  # pragma: no cover
    M3Embedder = None  # type: ignore[assignment]

try:
    from FlagEmbedding import BGEM3EmbeddingModel  # type: ignore[import]
except ImportError:  # pragma: no cover
    BGEM3EmbeddingModel = None  # type: ignore[assignment]

# Some installs only provide one name; normalize it.
if BGEM3EmbeddingModel is None and BGEM3FlagModel is not None:
    BGEM3EmbeddingModel = BGEM3FlagModel  # type: ignore[assignment,misc]


def sha256_text(text: str) -> str:
    """SHA-256 hex of clean_text — matches units.content_hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class BGEM3Embedder:
    """Wraps BAAI/bge-m3 with batching + Postgres cache lookups by content_hash."""

    MODEL_NAME: str = "BAAI/bge-m3"

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        batch_size: int = 32,
        use_gpu: bool = True,
        normalize_embeddings: bool = True,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self.use_gpu = use_gpu
        self.normalize_embeddings = normalize_embeddings
        self._inner = self._build_inner(model_name, use_gpu, normalize_embeddings)

    def _build_inner(self, model_name: str, use_gpu: bool, normalize_embeddings: bool):
        preferred = [BGEM3EmbeddingModel, BGEM3FlagModel, M3Embedder]
        for candidate in preferred:
            if candidate is None:
                continue
            try:
                if candidate in (BGEM3EmbeddingModel, BGEM3FlagModel):
                    try:
                        return candidate(model_name, use_gpu=use_gpu)
                    except TypeError:
                        return candidate(model_name_or_path=model_name, devices="cpu" if not use_gpu else None)
                if candidate is M3Embedder:
                    try:
                        return candidate(
                            model_name_or_path=model_name,
                            devices="cpu" if not use_gpu else None,
                            use_fp16=False,
                            return_dense=True,
                            return_sparse=True,
                            return_colbert_vecs=False,
                        )
                    except TypeError:
                        return candidate(
                            model_name_or_path=model_name,
                            devices="cpu" if not use_gpu else None,
                            return_dense=True,
                            return_sparse=True,
                            return_colbert_vecs=False,
                        )
            except Exception as e:  # pragma: no cover
                logger().warning("FlagEmbedding candidate init failed: %s", e)

        try:
            from sentence_transformers import SentenceTransformer  # type: ignore[import]
            from sentence_transformers.util import cos_sim  # type: ignore[import]

            class _SentenceTransformerWrapper:
                def __init__(self, name: str):
                    self.model = SentenceTransformer(name, device="cpu" if not use_gpu else "cuda")
                    self.model_name = name

                def encode(
                    self,
                    sentences,
                    batch_size=32,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False,
                    **kwargs,
                ):
                    embeddings = self.model.encode(
                        sentences,
                        batch_size=batch_size,
                        show_progress_bar=False,
                        convert_to_numpy=True,
                        normalize_embeddings=normalize_embeddings,
                    )
                    dense_vecs = [emb for emb in embeddings]
                    lexical_weights = [{int(i): float(v) for i, v in enumerate([])} for _ in sentences]
                    return {"dense_vecs": dense_vecs, "lexical_weights": lexical_weights}

            return _SentenceTransformerWrapper(model_name)
        except Exception as e:
            raise RuntimeError(
                "No usable embedding backend found. Install FlagEmbedding or sentence-transformers."
            ) from e

    def encode(self, texts: list[str], **kwargs):
        batch_size = kwargs.get("batch_size", self.batch_size)
        return_dense = kwargs.get("return_dense", True)
        return_sparse = kwargs.get("return_sparse", False)
        return_colbert_vecs = kwargs.get("return_colbert_vecs", False)

        out = self._inner.encode(
            texts,
            batch_size=batch_size,
            return_dense=return_dense,
            return_sparse=return_sparse,
            return_colbert_vecs=return_colbert_vecs,
        )
        dense = out.get("dense_vecs", [])
        sparse = out.get("lexical_weights", [])
        if len(sparse) != len(texts):
            sparse = [{} for _ in texts]
        return dense, sparse

    async def embed_batch(self, texts: list[str]) -> tuple[list, list]:
        results = self.encode(
            texts,
            batch_size=self.batch_size,
            return_dense=True,
            return_sparse=True,
            return_colbert_vecs=False,
        )
        return results

    async def _hash_set_exists(self, pool: asyncpg.pool.Pool, hashes: Iterable[str]) -> set[str]:
        rows = await pool.fetch(
            "SELECT content_hash FROM embed_cache WHERE content_hash = ANY($1)",
            list(hashes),
        )
        return {r["content_hash"] for r in rows}

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
    use_gpu = bool(os.getenv("GPU_WORKER_CUDA", "1") not in {"0", "false", "False", ""})
    embedder = BGEM3Embedder(use_gpu=use_gpu)
    await embedder.embed_and_cache(pool, texts)
    return len(texts)


import sys

if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if not src:
        logger.error("source_id required; usage: python -m workers.gpu_worker.embedder <source_id>")
        raise SystemExit(2)
    asyncio.run(embed_units_by_source(src))
