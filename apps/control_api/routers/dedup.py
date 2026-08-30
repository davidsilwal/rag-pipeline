#!/usr/bin/env python3
"""apps/control_api/routers/dedup.py — Human review interface for dedup decisions."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path as FastAPIPath, Query
from pydantic import BaseModel, Field

from database import get_engine
from deps import require_any_token
from services.dedup_review import (
    apply_all_pending_suppressions,
    get_dedup_review_stats,
    get_pending_dedup_pairs,
    list_dedup_reviews,
    submit_dedup_review,
)

log = logging.getLogger("dedup")
router = APIRouter(prefix="/dedup", tags=["dedup"])


class DedupReviewRequest(BaseModel):
    reviewer: str = Field(..., description="Reviewer name or ID")
    decision: str = Field(..., description="keep | suppress | merge | skip")
    notes: str = Field("", description="Optional notes explaining the decision")


@router.get("/pending", summary="List pending dedup pairs needing review")
async def list_pending_pairs(
    _tok: str = Depends(require_any_token),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_similarity: float = Query(0.85, ge=0.0, le=1.0),
    method: str | None = Query(
        None,
        description="Filter by detection method: exact_sha256 or minhash_lsh",
    ),
):
    """Get dedup pairs that haven't been reviewed yet, ordered by similarity.

    Optionally filter by detection ``method`` — ``exact_sha256`` (hash-identical)
    or ``minhash_lsh`` (near-duplicate) — so operators can review near-dups
    separately from exact copies.
    """
    async with get_engine().connect() as conn:
        pairs = await get_pending_dedup_pairs(
            conn, limit, offset, min_similarity, method
        )
        return {"pairs": pairs, "limit": limit, "offset": offset, "total": len(pairs)}


@router.post("/review/{pair_id}", summary="Submit a review decision for a dedup pair")
async def submit_review(
    pair_id: UUID = FastAPIPath(..., description="Dedup pair UUID"),
    payload: DedupReviewRequest = ...,
    _tok: str = Depends(require_any_token),
):
    """Submit a human review decision for a dedup pair.

    - **keep**: Keep the first unit, suppress the second
    - **suppress**: Suppress the first unit, keep the second (reverse)
    - **merge**: Both units should be kept but merged
    - **skip**: Neither unit should be suppressed (manual override)
    """
    # begin() (not connect()) so the review INSERT + unit status updates commit.
    async with get_engine().begin() as conn:
        result = await submit_dedup_review(
            conn,
            str(pair_id),
            payload.reviewer,
            payload.decision,
            payload.notes,
        )
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result


@router.get("/reviews", summary="List submitted dedup review decisions")
async def list_reviews(
    _tok: str = Depends(require_any_token),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    decision: str | None = Query(None, description="Filter by decision"),
):
    """List submitted review decisions."""
    async with get_engine().connect() as conn:
        reviews = await list_dedup_reviews(conn, limit, offset, decision)
        return {"reviews": reviews, "limit": limit, "offset": offset}


@router.get("/stats", summary="Get dedup review statistics")
async def get_stats(
    _tok: str = Depends(require_any_token),
):
    """Get statistics on dedup review progress."""
    async with get_engine().connect() as conn:
        stats = await get_dedup_review_stats(conn)
        return stats


@router.post("/apply", summary="Apply all pending suppression decisions")
async def apply_suppressions(
    _tok: str = Depends(require_any_token),
):
    """Apply all 'suppress' decisions from reviews (batch operation)."""
    # begin() so the suppression UPDATEs commit.
    async with get_engine().begin() as conn:
        result = await apply_all_pending_suppressions(conn)
        return result
