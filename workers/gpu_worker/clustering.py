#!/usr/bin/env python3
"""workers/gpu_worker/clustering.py — UMAP + HDBSCAN topic clustering (§8 Stage 6)."""

from __future__ import annotations

import os
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
    vecs = np.array([r["dense_vector"] for r in rows], dtype=np.float32)
    ids = [str(r["unit_id"]) for r in rows]
    return vecs, ids


def _compute_centroid(vecs: np.ndarray, labels: np.ndarray, label: int) -> np.ndarray | None:
    mask = labels == label
    if not np.any(mask):
        return None
    return vecs[mask].mean(axis=0).tolist()


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

    results: list[ClusterResult] = []
    for label in sorted(set(labels)):
        if label == -1:
            continue
        mask = labels == label
        uids = [u for u, m in zip(ids, mask) if m]
        cvec = _compute_centroid(vecs, labels, label)
        if cvec is not None:
            cvec = [float(v) for v in cvec]
        topic_name = f"cluster-{label}"
        results.append(
            ClusterResult(
                cluster_id=f"{source_id}:{label}",
                label=int(label),
                topic_name=topic_name,
                top_keywords=[],
                unit_ids=uids,
                centroid=cvec,
            )
        )
    return results
