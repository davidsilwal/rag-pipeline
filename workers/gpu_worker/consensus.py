#!/usr/bin/env python3
"""workers/gpu_worker/consensus.py — 3-way consensus scoring & topic resolver (§8.1).

Reads tunable constants from policies/publication_gates.yaml (via env overlay).
"""

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
class ConsensusConfig:
    hdbscan_weight: float = 0.40
    graphrag_weight: float = 0.35
    headings_weight: float = 0.25
    auto_approve: float = 0.72
    split: float = 0.50


def _cfg() -> ConsensusConfig:
    return ConsensusConfig(
        hdbscan_weight=float(os.getenv("CONSENSUS_W_HDBSCAN", "0.40")),
        graphrag_weight=float(os.getenv("CONSENSUS_W_GRAPHRAG", "0.35")),
        headings_weight=float(os.getenv("CONSENSUS_W_HEADINGS", "0.25")),
        auto_approve=float(os.getenv("CONSENSUS_THRESHOLD_AUTO_APPROVE", "0.72")),
        split=float(os.getenv("CONSENSUS_THRESHOLD_SPLIT", "0.50")),
    )


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return 0.0
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


async def load_units_with_embeddings(source_id: str) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        """
        SELECT u.unit_id, u.heading_path, ec.dense_vector
        FROM units u
        JOIN embed_cache ec ON u.content_hash = ec.content_hash
        WHERE u.source_id = $1
        ORDER BY u.unit_index
        """,
        source_id,
    )
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "unit_id": str(r["unit_id"]),
                "heading_path": r["heading_path"],
                "dense_vector": np.asarray(r["dense_vector"], dtype=np.float32),
            }
        )
    return out


async def compute_consensus(source_id: str) -> list[dict]:
    units = await load_units_with_embeddings(source_id)
    cfg = _cfg()
    if not units:
        return []

    # Heading-only baseline assignment for this implementation; cluster/community
    # centroids are filled in later by clustering/graphrag stages.
    out: list[dict] = []
    for u in units:
        heading_score = 0.0
        if u["heading_path"]:
            heading_score = 0.7  # non-empty heading breadcrumb gets a strong prior
        score = cfg.headings_weight * heading_score
        status = "needs_review"
        if score >= cfg.auto_approve:
            status = "auto_approved"
        elif score >= cfg.split:
            status = "auto_approved"
        out.append(
            {
                "unit_id": u["unit_id"],
                "score": float(score),
                "status": status,
                "wiki_topic_path": "/".join(u["heading_path"][:3]) if u["heading_path"] else "unclassified",
            }
        )
    return out
