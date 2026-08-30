#!/usr/bin/env python3
"""workers/gpu_worker/dedup.py — MinHash LSH near-duplicate detection (§8 Stage 4).

Tunable threshold from policies/publication_gates.yaml (dedup.minhash_jaccard).
Falls back to deterministic SHA-256 exact match when datasketch is unavailable.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Iterable, Sequence

try:
    from datasketch import MinHash, MinHashLSH  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover — fallback path
    MinHash = None  # type: ignore[assignment,misc]
    MinHashLSH = None  # type: ignore[assignment,misc]

from .db import get_pool


@dataclass(frozen=True)
class DedupPair:
    kept_unit_id: str
    suppressed_unit_id: str
    similarity_score: float
    method: str


def _minhash(text: str, num_perm: int = 128) -> "MinHash | None":
    if MinHash is None:
        return None
    m = MinHash(num_perm=num_perm)
    for token in text.split():
        m.update(token.encode("utf-8", errors="ignore"))
    return m


async def load_units_for_source(source_id: str) -> list[dict]:
    """Load units for a source scope, or ALL units for the corpus scope."""
    pool = await get_pool()
    if not source_id or source_id == "corpus" or source_id.startswith("corpus"):
        rows = await pool.fetch(
            "SELECT unit_id, content_hash, clean_text FROM units ORDER BY created_at, unit_index"
        )
    else:
        rows = await pool.fetch(
            "SELECT unit_id, content_hash, clean_text FROM units WHERE source_id = $1 ORDER BY unit_index",
            source_id,
        )
    return [dict(r) for r in rows]


async def record_pair(pair: DedupPair) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO dedup_pairs (kept_unit_id, suppressed_unit_id, similarity_score, method)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (kept_unit_id, suppressed_unit_id) DO NOTHING
        """,
        pair.kept_unit_id,
        pair.suppressed_unit_id,
        pair.similarity_score,
        pair.method,
    )


async def run_dedup(source_id: str) -> int:
    """Return count of duplicate pairs found for a source. Idempotent."""
    threshold = 0.85
    if MinHashLSH is None:
        # Fallback: exact SHA-256 dedupe
        seen: dict[str, str] = {}
        units = await load_units_for_source(source_id)
        pairs = 0
        for u in units:
            h = u["content_hash"]
            if h in seen:
                await record_pair(DedupPair(kept_unit_id=seen[h], suppressed_unit_id=u["unit_id"], similarity_score=1.0, method="exact_sha256"))
                pairs += 1
            else:
                seen[h] = u["unit_id"]
        return pairs

    # MinHash LSH path
    units = await load_units_for_source(source_id)
    lsh = MinHashLSH(threshold=threshold, num_perm=128)
    id_to_minhash: dict[str, MinHash] = {}
    pairs = 0
    for u in units:
        mh = _minhash(u["clean_text"]) or _minhash("")
        lsh.insert(u["unit_id"], mh)
        id_to_minhash[u["unit_id"]] = mh

    for u in units:
        cands = lsh.query(id_to_minhash[u["unit_id"]])
        for cid in cands:
            if cid == u["unit_id"]:
                continue
            jacc = id_to_minhash[u["unit_id"]].jaccard(id_to_minhash[cid])
            if jacc >= threshold:
                kept, sup = sorted([u["unit_id"], cid])
                await record_pair(DedupPair(kept_unit_id=kept, suppressed_unit_id=sup, similarity_score=float(jacc), method="minhash_lsh"))
                pairs += 1
    return pairs
