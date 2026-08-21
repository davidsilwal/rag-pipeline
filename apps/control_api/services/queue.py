#!/usr/bin/env python3
"""apps/control_api/services/queue.py — Durable task queue helpers (plan §4).

Shared enqueue + advisory-lock primitives. Claim itself lives in the tasks
router because it is the one query that must use FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

from sqlalchemy import text

GLOBAL_STAGES = {"cluster", "consensus", "graphrag", "compile"}


async def enqueue_stage(conn, stage: str, scope_type: str, scope_id: str,
                        priority: int = 100, payload: dict | None = None,
                        max_attempts: int = 3, next_run_at=None) -> bool:
    """Idempotently enqueue a task. Returns True if a new task was inserted.

    Relies on the partial unique index uq_task_queue_active_scope
    (stage, scope_type, scope_id) for queued/claimed/running — so a duplicate
    enqueue is a no-op, satisfying at-least-once with idempotent producers.
    """
    from datetime import datetime

    sql = text("""
        INSERT INTO task_queue
            (stage, scope_type, scope_id, priority, status, max_attempts, payload, next_run_at)
        VALUES (:stage, :scope_type, :scope_id, :priority, 'queued', :max_attempts,
                CAST(:payload AS jsonb), :next_run_at)
        ON CONFLICT (stage, scope_type, scope_id)
        WHERE status IN ('queued','claimed','running')
        DO NOTHING
        RETURNING task_id
    """)
    result = await conn.execute(sql, {
        "stage": stage,
        "scope_type": scope_type,
        "scope_id": scope_id,
        "priority": priority,
        "max_attempts": max_attempts,
        "payload": _json_dumps(payload or {}),
        "next_run_at": next_run_at or datetime.utcnow(),
    })
    return result.rowcount is not None and result.rowcount > 0


async def enqueue_after_source_registered(conn, source_id: str) -> None:
    """Producer chaining kickoff: source → extract → chunk → embed → dedup."""
    await enqueue_stage(conn, "extract", "source", source_id, priority=_priority_for("extract"))


async def enqueue_next_stage(conn, stage: str, scope_type: str, scope_id: str,
                             payload: dict | None = None) -> None:
    """Enqueue the downstream stage for a completed task (fan-out, §4.5)."""
    await enqueue_stage(conn, stage, scope_type, scope_id, priority=_priority_for(stage), payload=payload)


def _priority_for(stage: str) -> int:
    # Lower = sooner (plan §6.4). Wiki knowledge P0, assets P3.
    return {"extract": 0, "chunk": 10, "embed": 20, "dedup": 30,
            "cluster": 40, "consensus": 50, "graphrag": 60, "compile": 70}.get(stage, 100)


def advisory_lock_key(*parts: str) -> int:
    """Deterministic int64 advisory-lock key from stable strings."""
    import hashlib
    h = hashlib.sha256(":".join(parts).encode()).digest()
    return int.from_bytes(h[:8], "big", signed=True)


def _json_dumps(obj) -> str:
    import json
    return json.dumps(obj, default=str)
