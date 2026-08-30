#!/usr/bin/env python3
"""workers/gpu_worker/consensus.py — 3-way consensus scoring & topic resolver (§8.1).

Reads tunable constants from policies/publication_gates.yaml (via env overlay).
"""

from __future__ import annotations

import json
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
    """Load embedded units for a source scope, or ALL embedded units for corpus."""
    pool = await get_pool()
    if not source_id or source_id == "corpus" or source_id.startswith("corpus"):
        rows = await pool.fetch(
            """
            SELECT u.unit_id, u.heading_path, ec.dense_vector
            FROM units u
            JOIN embed_cache ec ON u.content_hash = ec.content_hash
            ORDER BY u.created_at, u.unit_index
            """
        )
    else:
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
    def _as_vec(v):
        # asyncpg returns pgvector as a text repr (e.g. '[-0.02,0.03,...]').
        if isinstance(v, str):
            return json.loads(v)
        if hasattr(v, "tolist"):
            return v.tolist()
        return list(v)

    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "unit_id": str(r["unit_id"]),
                "heading_path": r["heading_path"],
                "dense_vector": np.asarray(_as_vec(r["dense_vector"]), dtype=np.float32),
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


async def persist_consensus(results: list[dict]) -> int:
    """Persist consensus rows into cluster_consensus (idempotent upsert).

    One row per unique wiki_topic_path; the hdbscan_cluster_id is resolved by
    finding the topic_cluster whose exemplar_unit_ids contain any of the
    scored units, so the dashboard's consensus_score join actually links.
    """
    if not results:
        return 0
    pool = await get_pool()

    # Map unit_id -> cluster_id from topic_clusters.exemplar_unit_ids.
    unit_to_cluster: dict[str, str] = {}
    try:
        cl_rows = await pool.fetch(
            "SELECT cluster_id, exemplar_unit_ids FROM topic_clusters"
        )
        for r in cl_rows:
            cid = str(r["cluster_id"])
            for uid in (r["exemplar_unit_ids"] or []):
                unit_to_cluster.setdefault(str(uid), cid)
    except Exception:
        pass

    # Aggregate per wiki_topic_path: mean score + status + linked cluster.
    by_path: dict[str, dict] = {}
    for r in results:
        path = r["wiki_topic_path"] or "unclassified"
        agg = by_path.setdefault(
            path,
            {"score_sum": 0.0, "count": 0, "status": "needs_review", "cluster_id": None},
        )
        agg["score_sum"] += r.get("score", 0.0)
        agg["count"] += 1
        if r.get("status") in ("auto_approved",):
            agg["status"] = "auto_approved"
        cid = unit_to_cluster.get(str(r["unit_id"]))
        if cid and agg["cluster_id"] is None:
            agg["cluster_id"] = cid

    written = 0
    for path, agg in by_path.items():
        confidence = agg["score_sum"] / max(agg["count"], 1)
        await pool.execute(
            """
            INSERT INTO cluster_consensus
                (wiki_topic_path, hdbscan_cluster_id, heading_pattern,
                 confidence_score, status)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (wiki_topic_path) DO UPDATE SET
                hdbscan_cluster_id = EXCLUDED.hdbscan_cluster_id,
                heading_pattern = EXCLUDED.heading_pattern,
                confidence_score = EXCLUDED.confidence_score,
                status = EXCLUDED.status
            """,
            path,
            agg["cluster_id"],
            path.split("/")[-1],
            float(confidence),
            agg["status"],
        )
        written += 1
    return written
