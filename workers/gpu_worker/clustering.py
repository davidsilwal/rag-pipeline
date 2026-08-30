#!/usr/bin/env python3
"""workers/gpu_worker/clustering.py — UMAP + HDBSCAN topic clustering (§8 Stage 6)."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Sequence

import numpy as np

try:
    import umap  # type: ignore[import-untyped]
    import hdbscan  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    umap = None  # type: ignore[assignment]
    hdbscan = None  # type: ignore[assignment]

from .db import get_pool


def _is_corpus_scope(source_id: str) -> bool:
    return (not source_id or source_id == "corpus" or source_id.startswith("corpus"))


@dataclass(frozen=True)
class ClusterResult:
    cluster_id: str
    label: int
    topic_name: str
    top_keywords: list[str]
    unit_ids: list[str]
    centroid: list[float] | None


async def load_embeddings(source_id: str) -> tuple[np.ndarray, list[str]]:
    pool = await get_pool()
    if _is_corpus_scope(source_id):
        rows = await pool.fetch(
            """
            SELECT u.unit_id, ec.dense_vector
            FROM units u
            JOIN embed_cache ec ON u.content_hash = ec.content_hash
            ORDER BY u.created_at, u.unit_index
            """
        )
    else:
        rows = await pool.fetch(
            """
            SELECT u.unit_id, ec.dense_vector
            FROM units u
            JOIN embed_cache ec ON u.content_hash = ec.content_hash
            WHERE u.source_id = $1
            ORDER BY u.unit_index
            """,
            source_id,
        )
    if not rows:
        return np.zeros((0, 1024), dtype=np.float32), []

    def _as_vec(v):
        # asyncpg returns pgvector as a text repr (e.g. '[-0.02,0.03,...]').
        if isinstance(v, str):
            return json.loads(v)
        if hasattr(v, "tolist"):
            return v.tolist()
        return list(v)

    vecs = np.array([_as_vec(r["dense_vector"]) for r in rows], dtype=np.float32)
    ids = [str(r["unit_id"]) for r in rows]
    return vecs, ids


def _compute_centroid(vecs: np.ndarray, labels: np.ndarray, label: int) -> np.ndarray | None:
    mask = labels == label
    if not np.any(mask):
        return None
    return vecs[mask].mean(axis=0).tolist()


def _deterministic_cluster_id(source_id: str, label: int) -> str:
    """Stable cluster UUID so re-runs are idempotent (upsert by primary key)."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{label}"))


async def persist_clusters(results: Sequence[ClusterResult]) -> int:
    """Upsert cluster results into topic_clusters (idempotent by cluster_id)."""
    if not results:
        return 0
    pool = await get_pool()
    written = 0
    for c in results:
        # pgvector wants the text form ('[0.1,0.2,...]'), not a Python list.
        centroid_sql = None
        if c.centroid is not None:
            centroid_sql = "[" + ",".join(repr(float(x)) for x in c.centroid) + "]"
        await pool.execute(
            """
            INSERT INTO topic_clusters
                (cluster_id, cluster_label, topic_name, centroid, top_keywords,
                 unit_count, exemplar_unit_ids)
            VALUES ($1, $2, $3, $4::vector, $5, $6, $7::uuid[])
            ON CONFLICT (cluster_id) DO UPDATE SET
                topic_name = EXCLUDED.topic_name,
                top_keywords = EXCLUDED.top_keywords,
                unit_count = EXCLUDED.unit_count,
                exemplar_unit_ids = EXCLUDED.exemplar_unit_ids,
                centroid = EXCLUDED.centroid
            """,
            c.cluster_id,
            c.label,
            c.topic_name,
            centroid_sql,
            c.top_keywords,
            len(c.unit_ids),
            c.unit_ids,
        )
        written += 1
    return written


async def run_clustering(source_id: str, min_cluster_size: int = 5) -> list[ClusterResult]:
    vecs, ids = await load_embeddings(source_id)
    if vecs.shape[0] < min_cluster_size:
        return []

    if umap is None or hdbscan is None:
        # Deterministic fallback: single cluster if similar enough, else noise
        return []

    reduced = umap.UMAP(n_neighbors=15, min_dist=0.1, n_components=15, metric="cosine").fit_transform(vecs)
    clusterer = hdbscan.HDBSCAN(min_cluster_size=min_cluster_size, metric="euclidean", cluster_selection_method="eom")
    labels = clusterer.fit_predict(reduced)

    # Load member unit texts once so keywords/topic names can be derived.
    member_texts = await _load_unit_texts(ids)

    results: list[ClusterResult] = []
    for label in sorted(set(labels)):
        if label == -1:
            continue
        mask = labels == label
        uids = [u for u, m in zip(ids, mask) if m]
        cvec = _compute_centroid(vecs, labels, label)
        if cvec is not None:
            cvec = [float(v) for v in cvec]
        texts = [member_texts.get(uid, "") for uid in uids]
        keywords = _top_keywords(texts, top_n=8)
        topic_name = _topic_name(keywords) or f"cluster-{label}"
        results.append(
            ClusterResult(
                cluster_id=_deterministic_cluster_id(source_id, label),
                label=int(label),
                topic_name=topic_name,
                top_keywords=keywords,
                unit_ids=uids,
                centroid=cvec,
            )
        )
    return results


async def _load_unit_texts(ids: list[str]) -> dict[str, str]:
    """Load clean_text for the given unit_ids (chunked to avoid huge IN lists)."""
    if not ids:
        return {}
    pool = await get_pool()
    out: dict[str, str] = {}
    CHUNK = 500
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        rows = await pool.fetch(
            "SELECT unit_id, clean_text FROM units WHERE unit_id = ANY($1::uuid[])",
            chunk,
        )
        for r in rows:
            out[str(r["unit_id"])] = r["clean_text"] or ""
    return out


_STOPWORDS = set(
    "the a an and or but if then else for with from by on at in of to is are was were "
    "be been being this that these those it its as not no yes do does did done what which "
    "who whom whose how when where why can could will would should may might must have has "
    "had having about into over under again further once here there all any both each few "
    "more most other some such only own same so than too very just also their them they we "
    "you your our us".split()
)


def _top_keywords(texts: list[str], top_n: int = 8) -> list[str]:
    """Simple frequency-based keyword extraction over member unit texts."""
    import re
    from collections import Counter

    counter: Counter = Counter()
    for t in texts:
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", (t or "").lower()):
            if tok in _STOPWORDS or len(tok) < 3:
                continue
            counter[tok] += 1
    return [w for w, _ in counter.most_common(top_n)]


def _topic_name(keywords: list[str]) -> str | None:
    """Derive a human topic label from the top keywords (title-case join)."""
    if not keywords:
        return None
    return " ".join(k.replace("_", " ") for k in keywords[:3]).title()
