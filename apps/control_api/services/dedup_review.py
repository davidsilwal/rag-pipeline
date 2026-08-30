#!/usr/bin/env python3
"""apps/control_api/services/dedup_review.py — Human review interface for dedup decisions.

Provides:
- List pending dedup pairs needing review
- Submit review decisions (keep/suppress/merge/skip)
- Get review stats
- Apply reviewed decisions to suppress/keep units
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text, update, delete
from sqlalchemy.ext.asyncio import AsyncConnection

from models import DedupPair, Unit

log = logging.getLogger("dedup_review")


async def get_pending_dedup_pairs(
    conn: AsyncConnection,
    limit: int = 50,
    offset: int = 0,
    min_similarity: float = 0.85,
    method: str | None = None,
) -> list[dict[str, Any]]:
    """Get dedup pairs that haven't been reviewed yet.

    Joins back to ``units`` and ``sources`` so each pair surfaces the two
    source documents/paths and text previews for a meaningful human review.
    ``method`` optionally filters to a specific detection method
    (``exact_sha256`` or ``minhash_lsh``).
    """
    method_filter = ""
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "min_sim": min_similarity,
    }
    if method:
        method_filter = "AND dp.method = :method"
        params["method"] = method

    query = f"""
        SELECT
            dp.pair_id,
            dp.kept_unit_id,
            dp.suppressed_unit_id,
            dp.similarity_score,
            dp.method,
            dp.created_at,
            s1.source_id AS kept_source_id,
            s1.file_name AS kept_source_name,
            s1.file_path AS kept_source_path,
            u1.content_hash as kept_content_hash,
            left(u1.clean_text, 200) as kept_text_preview,
            s2.source_id AS suppressed_source_id,
            s2.file_name AS suppressed_source_name,
            s2.file_path AS suppressed_source_path,
            u2.content_hash as suppressed_content_hash,
            left(u2.clean_text, 200) as suppressed_text_preview
        FROM dedup_pairs dp
        JOIN units u1 ON dp.kept_unit_id = u1.unit_id
        JOIN units u2 ON dp.suppressed_unit_id = u2.unit_id
        JOIN sources s1 ON u1.source_id = s1.source_id
        JOIN sources s2 ON u2.source_id = s2.source_id
        LEFT JOIN dedup_reviews dr ON dr.pair_id = dp.pair_id
        WHERE dr.review_id IS NULL
          AND dp.similarity_score >= :min_sim
          {method_filter}
        ORDER BY dp.similarity_score DESC, dp.created_at DESC
        LIMIT :limit OFFSET :offset
    """

    rows = await conn.execute(text(query), params)

    return [dict(r._mapping) for r in rows.fetchall()]


async def submit_dedup_review(
    conn: AsyncConnection,
    pair_id: str,
    reviewer: str,
    decision: str,
    notes: str = "",
) -> dict[str, Any]:
    """Submit a human review decision for a dedup pair."""
    # Validate decision
    valid_decisions = {"keep", "suppress", "merge", "skip"}
    if decision not in valid_decisions:
        return {"error": f"Invalid decision: {decision}. Must be one of {valid_decisions}"}

    # Upsert review decision
    query = """
        INSERT INTO dedup_reviews (pair_id, reviewer, decision, notes, created_at, updated_at)
        VALUES (:pair_id, :reviewer, :decision, :notes, now(), now())
        ON CONFLICT (pair_id) DO UPDATE
        SET reviewer = EXCLUDED.reviewer,
            decision = EXCLUDED.decision,
            notes = EXCLUDED.notes,
            updated_at = now()
        RETURNING review_id
    """

    result = await conn.execute(
        text(query),
        {
            "pair_id": pair_id,
            "reviewer": reviewer,
            "decision": decision,
            "notes": notes,
        },
    )

    review_id = result.fetchone()[0]

    # Apply the decision if it's keep/suppress (merge and skip are just recorded)
    if decision in {"keep", "suppress"}:
        await _apply_dedup_decision(conn, pair_id, decision)

    log.info("dedup review submitted: pair=%s reviewer=%s decision=%s", pair_id, reviewer, decision)

    return {
        "review_id": str(review_id),
        "pair_id": pair_id,
        "decision": decision,
        "applied": decision in {"keep", "suppress"},
    }


async def _apply_dedup_decision(conn: AsyncConnection, pair_id: str, decision: str) -> None:
    """Apply a dedup decision by updating unit status or suppressing."""
    # Get the pair details
    pair_row = await conn.execute(
        text("""
            SELECT kept_unit_id, suppressed_unit_id
            FROM dedup_pairs
            WHERE pair_id = CAST(:pid AS uuid)
        """),
        {"pid": pair_id},
    )
    pair = pair_row.fetchone()
    if not pair:
        return

    kept_id, suppressed_id = pair

    if decision == "keep":
        # Keep the first unit, suppress the second
        await conn.execute(
            text("""
                UPDATE units
                SET status = 'superseded'
                WHERE unit_id = CAST(:sid AS uuid)
            """),
            {"sid": suppressed_id},
        )
        await conn.execute(
            text("""
                UPDATE units
                SET status = 'active'
                WHERE unit_id = CAST(:kid AS uuid)
            """),
            {"kid": kept_id},
        )

    elif decision == "suppress":
        # Suppress the first unit, keep the second (reverse)
        await conn.execute(
            text("""
                UPDATE units
                SET status = 'superseded'
                WHERE unit_id = CAST(:kid AS uuid)
            """),
            {"kid": kept_id},
        )
        await conn.execute(
            text("""
                UPDATE units
                SET status = 'active'
                WHERE unit_id = CAST(:sid AS uuid)
            """),
            {"sid": suppressed_id},
        )


async def get_dedup_review_stats(conn: AsyncConnection) -> dict[str, Any]:
    """Get statistics on dedup review progress."""
    stats_query = """
        SELECT
            (SELECT count(*) FROM dedup_pairs) as total_pairs,
            (SELECT count(*) FROM dedup_reviews) as reviewed_pairs,
            (SELECT count(*) FROM dedup_pairs dp
             LEFT JOIN dedup_reviews dr ON dr.pair_id = dp.pair_id
             WHERE dr.review_id IS NULL) as pending_pairs
    """

    rows = await conn.execute(text(stats_query))
    row = rows.fetchone()

    if not row:
        return {}

    # Process decision breakdown
    breakdown_rows = await conn.execute(
        text("""
            SELECT decision, count(*) as count
            FROM dedup_reviews
            GROUP BY decision
            ORDER BY count DESC
        """)
    )
    breakdown = {r[0]: r[1] for r in breakdown_rows.fetchall()}

    return {
        "total_pairs": row[0],
        "reviewed_pairs": row[1],
        "pending_pairs": row[2],
        "completion_pct": (row[1] / row[0] * 100) if row[0] > 0 else 0,
        "decision_breakdown": breakdown,
    }


async def list_dedup_reviews(
    conn: AsyncConnection,
    limit: int = 50,
    offset: int = 0,
    decision: str | None = None,
) -> list[dict[str, Any]]:
    """List dedup review decisions with optional filtering."""
    where = ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if decision:
        where = "WHERE decision = :decision"
        params["decision"] = decision

    query = f"""
        SELECT
            dr.review_id,
            dr.pair_id,
            dr.reviewer,
            dr.decision,
            dr.notes,
            dr.created_at,
            dr.updated_at,
            drp.similarity_score,
            drp.method,
            s1.file_path as kept_file_path,
            s2.file_path as suppressed_file_path
        FROM dedup_reviews dr
        JOIN dedup_pairs drp ON dr.pair_id = drp.pair_id
        JOIN units u1 ON drp.kept_unit_id = u1.unit_id
        JOIN units u2 ON drp.suppressed_unit_id = u2.unit_id
        JOIN sources s1 ON u1.source_id = s1.source_id
        JOIN sources s2 ON u2.source_id = s2.source_id
        {where}
        ORDER BY dr.created_at DESC
        LIMIT :limit OFFSET :offset
    """

    rows = await conn.execute(text(query), params)
    return [dict(r._mapping) for r in rows.fetchall()]


async def apply_all_pending_suppressions(conn: AsyncConnection) -> dict[str, Any]:
    """Apply all 'suppress' decisions from reviews (batch job)."""
    # Get all reviewed suppress decisions not yet applied
    query = """
        SELECT dp.suppressed_unit_id
        FROM dedup_reviews dr
        JOIN dedup_pairs dp ON dr.pair_id = dp.pair_id
        WHERE dr.decision = 'suppress'
          AND NOT EXISTS (
            SELECT 1 FROM units u
            WHERE u.unit_id = dp.suppressed_unit_id
              AND u.status = 'superseded'
          )
    """

    rows = await conn.execute(text(query))
    suppressed_ids = [str(r[0]) for r in rows.fetchall()]

    if not suppressed_ids:
        return {"suppressed": 0, "already_suppressed": 0}

    # Batch suppress
    placeholders = ",".join([f":id{i}" for i in range(len(suppressed_ids))])
    params = {f"id{i}": sid for i, sid in enumerate(suppressed_ids)}

    update_query = f"""
        UPDATE units
        SET status = 'superseded'
        WHERE unit_id IN ({placeholders})
    """

    result = await conn.execute(text(update_query), params)
    suppressed_count = result.rowcount

    log.info("applied %d suppress decisions from reviews", suppressed_count)

    return {
        "suppressed": suppressed_count,
        "already_suppressed": len(suppressed_ids) - suppressed_count,
        "suppressed_unit_ids": suppressed_ids[:10],  # First 10 for logging
    }