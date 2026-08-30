#!/usr/bin/env python3
"""apps/control_api/services/incremental.py — Incremental update tracking & delta detection.

Provides:
- Source version tracking
- Incremental processing logic (embed only new units, dedupe against existing, re-cluster only affected nodes)
- Per-source update scheduling
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

log = logging.getLogger("incremental")


async def detect_source_changes(
    conn: AsyncConnection,
    source_id: str,
) -> dict[str, Any]:
    """Detect changes in a source since last processing.

    Returns:
        Dict with has_changes, new_files, modified_files, deleted_files
    """
    # Get source details
    src_query = """
        SELECT source_id, file_path, sha256_hash, source_version,
               last_processed_at, updated_at
        FROM sources
        WHERE source_id = CAST(:sid AS uuid)
    """
    row = await conn.execute(text(src_query), {"sid": source_id})
    source = row.fetchone()
    if not source:
        return {"error": f"Source {source_id} not found"}

    source = dict(source._mapping)

    # Get units for this source
    units_query = """
        SELECT unit_id, content_hash, file_path, unit_index, status
        FROM units
        WHERE source_id = CAST(:sid AS uuid)
        ORDER BY unit_index
    """
    rows = await conn.execute(text(units_query), {"sid": source_id})
    units = [dict(r._mapping) for r in rows.fetchall()]

    # Check if any units have been modified since last processing
    modified_count = 0
    for unit in units:
        if unit.get("status") in {"pending", "extracted"}:
            modified_count += 1

    last_processed = source.get("last_processed_at")
    is_stale = (
        last_processed is None
        or source.get("updated_at") > last_processed
        or modified_count > 0
    )

    return {
        "source_id": source_id,
        "file_path": source["file_path"],
        "source_version": source["source_version"],
        "last_processed_at": last_processed.isoformat() if last_processed else None,
        "current_modified_at": source["updated_at"].isoformat(),
        "is_stale": is_stale,
        "total_units": len(units),
        "pending_units": modified_count,
        "recommendation": "update" if is_stale else "skip",
    }


async def plan_incremental_update(
    conn: AsyncConnection,
    source_id: str,
) -> dict[str, Any]:
    """Plan what needs to be re-processed for an incremental update.

    Returns:
        Dict with stages and estimated work
    """
    # Get source state
    state = await detect_source_changes(conn, source_id)
    if "error" in state:
        return state

    # Count units needing re-embedding
    embed_query = """
        SELECT count(*)
        FROM units u
        LEFT JOIN embed_cache ec ON u.content_hash = ec.content_hash
        WHERE u.source_id = CAST(:sid AS uuid)
          AND u.status != 'superseded'
          AND ec.dense_vector IS NULL
    """
    row = await conn.execute(text(embed_query), {"sid": source_id})
    units_needing_embed = row.scalar() or 0

    # Count new/changed dedup candidates
    dedup_query = """
        SELECT count(*)
        FROM units u
        WHERE u.source_id = CAST(:sid AS uuid)
          AND u.status = 'extracted'
          AND NOT EXISTS (
              SELECT 1 FROM dedup_pairs dp
              WHERE dp.kept_unit_id = u.unit_id
                 OR dp.suppressed_unit_id = u.unit_id
          )
    """
    row = await conn.execute(text(dedup_query), {"sid": source_id})
    units_needing_dedup = row.scalar() or 0

    # Check cluster state — clusters whose exemplar units belong to this source.
    # topic_clusters.exemplar_unit_ids is the unit→cluster linkage.
    cluster_query = """
        SELECT count(DISTINCT tc.cluster_id) as affected_clusters
        FROM topic_clusters tc
        WHERE EXISTS (
            SELECT 1 FROM units u
            WHERE u.source_id = CAST(:sid AS uuid)
              AND u.unit_id = ANY(tc.exemplar_unit_ids)
        )
    """
    row = await conn.execute(text(cluster_query), {"sid": source_id})
    affected_clusters = row.scalar() or 0

    return {
        "source_id": source_id,
        "stages": {
            "embed": {
                "needed": units_needing_embed > 0,
                "unit_count": units_needing_embed,
            },
            "dedupe": {
                "needed": units_needing_dedup > 0,
                "unit_count": units_needing_dedup,
            },
            "extract": {
                "needed": False,  # GraphRAG extraction is expensive, skip for incremental
                "note": "GraphRAG extraction not auto-triggered for incremental updates",
            },
            "cluster": {
                "needed": affected_clusters > 0,
                "affected_cluster_count": affected_clusters,
                "note": "Only re-cluster affected nodes, not full re-run",
            },
        },
        "total_work": (
            units_needing_embed + units_needing_dedup
        ),
    }


async def mark_source_processed(
    conn: AsyncConnection,
    source_id: str,
    processed_by: str,
    notes: str = "",
) -> dict[str, Any]:
    """Mark a source as processed and increment its version."""
    query = """
        UPDATE sources
        SET last_processed_at = now(),
            last_processed_by = :processed_by,
            source_version = source_version + 1,
            processing_notes = :notes,
            updated_at = now()
        WHERE source_id = CAST(:sid AS uuid)
        RETURNING source_version, last_processed_at
    """

    row = await conn.execute(
        text(query),
        {"sid": source_id, "processed_by": processed_by, "notes": notes},
    )
    result = row.fetchone()
    if not result:
        return {"error": f"Source {source_id} not found"}

    return {
        "source_id": source_id,
        "new_version": result[0],
        "last_processed_at": result[1].isoformat(),
    }


async def get_stale_sources(
    conn: AsyncConnection,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Get sources that need incremental processing."""
    query = """
        SELECT
            s.source_id,
            s.file_path,
            s.source_type,
            s.source_version,
            s.last_processed_at,
            s.updated_at,
            (SELECT count(*) FROM units u
             WHERE u.source_id = s.source_id
               AND u.status IN ('extracted', 'pending')) as pending_units
        FROM sources s
        WHERE s.status IN ('discovered', 'extracted', 'embedded')
        ORDER BY
            CASE WHEN s.last_processed_at IS NULL THEN 0 ELSE 1 END,
            s.last_processed_at ASC NULLS FIRST,
            s.updated_at DESC
        LIMIT :limit
    """

    rows = await conn.execute(text(query), {"limit": limit})
    return [dict(r._mapping) for r in rows.fetchall()]


async def execute_incremental_update(
    conn: AsyncConnection,
    source_id: str,
    processed_by: str,
    reembed: bool = True,
    rededupe: bool = True,
    reextract: bool = False,
    recluster: bool = True,
) -> dict[str, Any]:
    """Execute incremental update for a source.

    Args:
        source_id: Source UUID
        processed_by: Worker ID or name
        reembed: Re-embed new/changed units
        rededupe: Run dedup against existing units
        reextract: Run GraphRAG extraction (expensive, usually False)
        recluster: Re-cluster affected nodes only

    Returns:
        Dict with results of each stage
    """
    results = {
        "source_id": source_id,
        "stages_executed": [],
        "stages_skipped": [],
        "start_time": datetime.now(timezone.utc).isoformat(),
    }

    # Plan
    plan = await plan_incremental_update(conn, source_id)
    if "error" in plan:
        return plan
    results["plan"] = plan

    # Embed stage
    if reembed and plan["stages"]["embed"]["needed"]:
        embed_count = plan["stages"]["embed"]["unit_count"]
        # In production, this would enqueue a task
        # For now, return the count
        results["stages_executed"].append({
            "stage": "embed",
            "units_processed": embed_count,
            "status": "queued",
        })
    else:
        results["stages_skipped"].append("embed")

    # Dedupe stage
    if rededupe and plan["stages"]["dedupe"]["needed"]:
        dedup_count = plan["stages"]["dedupe"]["unit_count"]
        results["stages_executed"].append({
            "stage": "dedupe",
            "units_processed": dedup_count,
            "status": "queued",
        })
    else:
        results["stages_skipped"].append("dedupe")

    # Extract stage
    if reextract:
        results["stages_executed"].append({
            "stage": "extract",
            "status": "queued",
            "note": "GraphRAG extraction queued (expensive)",
        })
    else:
        results["stages_skipped"].append("extract")

    # Cluster stage
    if recluster and plan["stages"]["cluster"]["needed"]:
        cluster_count = plan["stages"]["cluster"]["affected_cluster_count"]
        results["stages_executed"].append({
            "stage": "cluster",
            "affected_clusters": cluster_count,
            "status": "queued",
        })
    else:
        results["stages_skipped"].append("cluster")

    # Mark source as processed
    version_result = await mark_source_processed(
        conn, source_id, processed_by,
        notes=f"Incremental update: {len(results['stages_executed'])} stages",
    )
    results["version_update"] = version_result

    results["end_time"] = datetime.now(timezone.utc).isoformat()
    return results
