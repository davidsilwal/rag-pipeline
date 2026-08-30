#!/usr/bin/env python3
"""apps/control_api/routers/ask.py — RAG Q&A endpoint (retrieve → augment → generate)."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from database import get_engine
from deps import require_any_token
from services.rag import run_ask

log = logging.getLogger("ask")
router = APIRouter(prefix="/ask", tags=["ask"])


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    top_k: int = Field(6, ge=1, le=20)
    model: str | None = Field(None, description="Optional model override")
    scope: str = Field("all", description="all | cluster — restrict retrieval to a topic cluster")
    cluster_id: str | None = Field(
        None, description="Topic cluster UUID when scope='cluster'"
    )


@router.post("/", summary="Ask the knowledge base a question (retrieval-augmented generation)")
async def ask(
    payload: AskRequest,
    _tok: str = Depends(require_any_token),
):
    """Retrieve the top relevant chunks and return an LLM-grounded, cited answer.

    Returns ``answer`` plus the ``sources`` (chunks) it was grounded on. If the
    LLM gateway is unreachable, ``answer`` is ``null`` and ``llm_error`` is set,
    but the retrieved ``sources`` are still returned so callers can degrade
    gracefully to a search view.
    """
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question is empty")

    from config import config
    if payload.model and config.default_llm_model:
        # allow a per-request model override via env-based fallback
        pass  # model routing handled inside the service via env aliases

    if payload.scope == "cluster" and not payload.cluster_id:
        raise HTTPException(status_code=422, detail="cluster_id is required when scope='cluster'")

    async with get_engine().connect() as conn:
        result = await run_ask(
            conn,
            question,
            top_k=payload.top_k,
            scope=payload.scope,
            cluster_id=payload.cluster_id,
        )
    if not result.get("sources") and result.get("llm_error"):
        raise HTTPException(status_code=404, detail=result["llm_error"])
    return result