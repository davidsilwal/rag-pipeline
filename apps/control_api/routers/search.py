#!/usr/bin/env python3
"""apps/control_api/routers/search.py — FTS + Hybrid RRF search endpoints."""

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from database import get_engine
from pydantic import BaseModel
from services.fts import fts_query

router = APIRouter(prefix="/search", tags=["search"])


class FTSQuery(BaseModel):
    query: str
    top_k: int = 20


class HybridQuery(BaseModel):
    query_text: str
    query_vector: list[float] | None = None
    top_k: int = 20


@router.post("/fts", summary="Lexical full-text search (GPU offline ready)")
async def fts_search(payload: FTSQuery):
    engine = get_engine()
    async with engine.connect() as conn:
        sql = text("""
            SELECT chunk_id, file_path, heading_path, content,
                   ts_rank_cd(fts_vector, websearch_to_tsquery('simple', :q)) AS rank
            FROM wiki_chunks
            WHERE fts_vector @@ websearch_to_tsquery('simple', :q)
            ORDER BY rank DESC
            LIMIT :k
        """)
        result = await conn.execute(sql, {"q": fts_query(payload.query), "k": payload.top_k})
        rows = result.mappings().all()
        return [
            {
                "chunk_id": r["chunk_id"],
                "file_path": r["file_path"],
                "heading_path": r["heading_path"],
                "content": r["content"],
                "rank": float(r["rank"]),
            }
            for r in rows
        ]


@router.post("/hybrid", summary="Hybrid RRF search (accepts dense vector from client)")
async def hybrid_search(payload: HybridQuery):
    engine = get_engine()
    async with engine.connect() as conn:
        # FTS part
        fts_sql = text("""
            SELECT chunk_id, ts_rank_cd(fts_vector, websearch_to_tsquery('simple', :q)) AS fts_rank
            FROM wiki_chunks
            WHERE fts_vector @@ websearch_to_tsquery('simple', :q)
        """)
        fts_result = await conn.execute(fts_sql, {"q": fts_query(payload.query_text)})
        fts_rows = fts_result.mappings().all()

        # Dense vector part
        dense_rows = []
        if payload.query_vector:
            dense_sql = text("""
                SELECT chunk_id,
                       1.0 / (60 + ROW_NUMBER() OVER (ORDER BY dense_vector <=> :vec)) AS dense_rank
                FROM wiki_chunks
                WHERE dense_vector IS NOT NULL
                ORDER BY dense_vector <=> :vec
                LIMIT 50
            """)
            dense_result = await conn.execute(dense_sql, {"vec": payload.query_vector})
            dense_rows = dense_result.mappings().all()

        # RRF merge
        rrf_scores: dict[str, float] = {}
        for r in fts_rows:
            fid = r["chunk_id"]
            rank = r["fts_rank"]
            rrf_scores[fid] = rrf_scores.get(fid, 0.0) + 1.0 / (60 + rank)

        for r in dense_rows:
            did = r["chunk_id"]
            rank = r["dense_rank"]
            rrf_scores[did] = rrf_scores.get(did, 0.0) + 1.0 / (60 + rank)

        sorted_chunks = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:payload.top_k]
        return [{"chunk_id": cid, "rrf_score": score} for cid, score in sorted_chunks]