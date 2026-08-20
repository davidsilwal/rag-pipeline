#!/usr/bin/env python3
"""workers/gpu_worker/claims_conflicts.py — Authority ladder & contradiction detector (§10)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

from .db import get_pool


@dataclass(frozen=True)
class Claim:
    claim_id: str
    subject: str
    predicate: str
    object: str
    statement_text: str
    authority_score: int
    source_unit_id: str


@dataclass(frozen=True)
class Conflict:
    topic_path: str
    claim_a_id: str
    claim_b_id: str
    conflict_type: str
    description: str
    severity: str


async def insert_claim(claim: Claim) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO claims (subject, predicate, object, statement_text, authority_score, source_unit_id)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT DO NOTHING
        """,
        claim.subject,
        claim.predicate,
        claim.object,
        claim.statement_text,
        claim.authority_score,
        claim.source_unit_id,
    )


async def insert_conflict(conflict: Conflict) -> None:
    pool = await get_pool()
    await pool.execute(
        """
        INSERT INTO conflicts (topic_path, claim_a_id, claim_b_id, conflict_type, description, severity)
        VALUES ($1, $2, $3, $4, $5, $6)
        """,
        conflict.topic_path,
        conflict.claim_a_id,
        conflict.claim_b_id,
        conflict.conflict_type,
        conflict.description,
        conflict.severity,
    )


async def load_claims_for_topic(topic_path: str) -> list[dict]:
    pool = await get_pool()
    rows = await pool.fetch(
        "SELECT claim_id, subject, predicate, object, authority_score FROM claims WHERE subject = $1 OR predicate = $1 OR object = $1",
        topic_path,
    )
    return [dict(r) for r in rows]


async def detect_conflicts(topic_path: str) -> list[Conflict]:
    claims = await load_claims_for_topic(topic_path)
    out: list[Conflict] = []
    for i, a in enumerate(claims):
        for b in claims[i + 1:]:
            if a["subject"] == b["subject"] and a["object"] == b["object"] and a["predicate"] == b["predicate"]:
                if a["authority_score"] != b["authority_score"]:
                    winner = a if a["authority_score"] >= b["authority_score"] else b
                    loser = b if winner is a else a
                    out.append(
                        Conflict(
                            topic_path=topic_path,
                            claim_a_id=winner["claim_id"],
                            claim_b_id=loser["claim_id"],
                            conflict_type="TEMPORAL_SUPERSEDED",
                            description=f"Authority mismatch for {a['subject']} {a['predicate']} {a['object']}",
                            severity="medium",
                        )
                    )
    return out
