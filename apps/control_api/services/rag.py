#!/usr/bin/env python3
"""apps/control_api/services/rag.py — Retrieval-augmented generation (RAG).

Glues the retrieval layer (postgres FTS + pgvector) to the LLM gateway so
the dashboard can "ask" the knowledge base and get a grounded, cited answer:

  1. embed_query()   — embed the question via the LLM gateway (LiteLLM) if it
                       exposes an OpenAI-compatible /embeddings route.
  2. retrieve()      — hybrid lexical + dense retrieval over wiki_chunks
                       (RRF fusion), with full chunk text for grounding.
  3. build_prompt()  — pack retrieved chunks with numbered <source n> markers.
  4. complete()      — call chat/completions across configured model aliases.
  5. run_ask()       — orchestrate and return answer + cited sources.

Copies the worker's LLM conventions (LOCAL_LLM_API_BASE / LITELLM_API_KEY /
model-alias fallback list) rather than pulling in the worker's heavy deps.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from config import config
from services.fts import fts_query

log = logging.getLogger("rag")


# ---------------------------------------------------------------------------
# LLM gateway helpers (mirrors workers/gpu_worker/markdown_compiler.py)
# ---------------------------------------------------------------------------

def _api_base() -> str:
    return (config.local_llm_api_base or os.getenv("LOCAL_LLM_API_BASE", "")).rstrip("/")


def _api_key() -> str:
    return (
        os.getenv("LITELLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
        or "sk-no-key"
    )


def _models() -> list[str]:
    """Ordered model-alias fallback list for chat completions."""
    base = config.default_llm_model or os.getenv("LOCAL_LLM_MODEL", "free")
    alts_raw = os.getenv("LOCAL_LLM_FALLBACK_ALIASES", "free-auto")
    seen = [base]
    for a in alts_raw.split(","):
        a = a.strip()
        if a and a not in seen:
            seen.append(a)
    return seen


def _embedding_model() -> str:
    return config.embedding_model_name or os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3")


_HEADERS = None


def _headers() -> dict[str, str]:
    global _HEADERS
    if _HEADERS is None:
        _HEADERS = {
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        }
    return _HEADERS


def embed_query(query: str) -> list[float] | None:
    """Embed a query with the LLM gateway's /embeddings route (BGE-M3 via LiteLLM).

    Returns a float vector on success or None if the gateway has no embeddings
    route — callers fall back to lexical-only retrieval.
    """
    base = _api_base()
    if not base:
        return None
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{base}/embeddings",
                headers=_headers(),
                json={
                    "model": _embedding_model(),
                    "input": query,
                },
            )
            if r.status_code != 200:
                return None
            data = r.json()
            emb = ((data.get("data") or [{}])[0]).get("embedding")
            if not emb:
                return None
            return [float(x) for x in emb]
    except Exception as e:  # noqa: BLE001
        log.debug("query embedding unavailable: %s", e)
        return None


def complete(prompt: str, max_tokens: int = 1024) -> str:
    """Call chat/completions across model aliases; returns the answer text or ''."""
    base = _api_base()
    if not base:
        return ""
    max_tokens = int(os.getenv("RAG_MAX_TOKENS", str(max_tokens)))
    for model in _models():
        try:
            with httpx.Client(timeout=180.0) as client:
                r = client.post(
                    f"{base}/chat/completions",
                    headers=_headers(),
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a precise research assistant. Answer the "
                                    "user's question using ONLY the numbered sources "
                                    'provided. Cite the sources you rely on inline as '
                                    "[1], [2], etc. If the sources do not contain the "
                                    "answer, say so plainly and do not invent facts."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.0,
                    },
                )
                if r.status_code != 200:
                    log.debug("model %s failed: HTTP %d", model, r.status_code)
                    continue
                choices = r.json().get("choices") or []
                if not choices:
                    continue
                content = (choices[0].get("message") or {}).get("content") or ""
                if content.strip():
                    return content.strip()
        except Exception as e:  # noqa: BLE001
            log.debug("model %s error: %s", model, e)
            continue
    return ""


# ---------------------------------------------------------------------------
# Retrieval (hybrid FTS + dense, RRF fusion)
# ---------------------------------------------------------------------------

def _rrf_fuse(fts_rows: list[dict], dense_rows: list[dict], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    for r in fts_rows:
        scores[r["chunk_id"]] = scores.get(r["chunk_id"], 0.0) + 1.0 / (k + r["fts_rank"])
    for r in dense_rows:
        scores[r["chunk_id"]] = scores.get(r["chunk_id"], 0.0) + 1.0 / (k + r["dense_rank"])
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


async def _cluster_filter_sql() -> str:
    """Return a WHERE fragment restricting wiki_chunks to a topic cluster's pages.

    The cluster→content linkage is page-level: ``wiki_pages.source_unit_ids``
    overlaps the cluster's ``exemplar_unit_ids`` (same join the context-pack
    export uses). Filtering chunks by their ``page_id`` matches that view, so
    a cluster-scoped RAG slice is consistent with the cluster export bundle.
    """
    return """
        AND EXISTS (
            SELECT 1 FROM topic_clusters tc
            JOIN wiki_pages wp ON wp.source_unit_ids && tc.exemplar_unit_ids
            WHERE tc.cluster_id = CAST(:cluster_id AS uuid)
              AND wp.page_id = wiki_chunks.page_id
        )
    """


async def retrieve(
    conn: AsyncConnection,
    query_text: str,
    query_vector: list[float] | None,
    top_k: int = 6,
    scope: str = "all",
    cluster_id: str | None = None,
) -> list[dict[str, Any]]:
    """Retrieve top chunk candidates across FTS and (optionally) dense vectors.

    Fuses both rankings with RRF, then loads the full chunk text + provenance
    for the winners via the cached chunk_id list. When ``scope='cluster'`` and
    ``cluster_id`` is set, both legs are restricted to that topic cluster's
    units so the RAG slice matches the cluster-scoped export bundle.
    """
    candidates: dict[str, float] = {}
    cluster_filter = ""
    params: dict[str, Any] = {"q": fts_query(query_text)}
    if scope == "cluster" and cluster_id:
        cluster_filter = await _cluster_filter_sql()
        params["cluster_id"] = cluster_id

    # Lexical leg — always available.
    fts_rows = (await conn.execute(
        text(f"""
            SELECT chunk_id,
                   ROW_NUMBER() OVER (
                       ORDER BY ts_rank_cd(fts_vector, websearch_to_tsquery('simple', :q)) DESC
                   ) AS rn
            FROM wiki_chunks
            WHERE fts_vector @@ websearch_to_tsquery('simple', :q)
            {cluster_filter}
        """),
        params,
    )).mappings().all()
    for r in fts_rows:
        cid = str(r["chunk_id"])
        candidates[cid] = candidates.get(cid, 0.0) + 1.0 / (60 + r["rn"])
    log.info("fts hits: %d", len(fts_rows))

    # Dense leg — only if we have a query vector.
    if query_vector and len(query_vector) >= 256:
        vec = "[" + ",".join(repr(float(x)) for x in query_vector) + "]"
        dense_params: dict[str, Any] = {"vec": vec}
        if scope == "cluster" and cluster_id:
            dense_params["cluster_id"] = cluster_id
        dense_rows = (await conn.execute(
            text(f"""
                SELECT chunk_id,
                       ROW_NUMBER() OVER (ORDER BY dense_vector <=> CAST(:vec AS vector)) AS rn
                FROM wiki_chunks
                WHERE dense_vector IS NOT NULL
                {cluster_filter}
            """),
            dense_params,
        )).mappings().all()
        for r in dense_rows:
            cid = str(r["chunk_id"])
            candidates[cid] = candidates.get(cid, 0.0) + 1.0 / (60 + r["rn"])
        log.info("dense hits: %d", len(dense_rows))

    if not candidates:
        return []

    ranked = sorted(candidates.items(), key=lambda x: x[1], reverse=True)[:top_k * 2]
    ids = [str(cid) for cid, _ in ranked]
    placeholders = ",".join([f":id{i}" for i in range(len(ids))])
    rows = (await conn.execute(
        text(f"""
            SELECT chunk_id, file_path, heading_path, content
            FROM wiki_chunks
            WHERE chunk_id IN ({placeholders})
        """),
        {f"id{i}": cid for i, cid in enumerate(ids)},
    )).mappings().all()

    by_id = {}
    for r in rows:
        by_id[str(r["chunk_id"])] = {
            "chunk_id": str(r["chunk_id"]),
            "file_path": r["file_path"],
            "heading_path": list(r["heading_path"] or []),
            "content": r["content"] or "",
        }
    # Preserve retrieval order.
    ordered = [by_id[cid] for cid in ids if cid in by_id][:top_k]
    for o in ordered:
        o["score"] = candidates[o["chunk_id"]]
    return ordered


# ---------------------------------------------------------------------------
# Prompt + orchestration
# ---------------------------------------------------------------------------

def build_prompt(question: str, sources: list[dict[str, Any]]) -> str:
    blocks = []
    for i, s in enumerate(sources, 1):
        loc = " – ".join(
            x for x in ([s.get("file_path")] + list(s.get("heading_path", []))) if x
        )
        content = (s.get("content") or "")[:1500]
        blocks.append(f"[{i}] {loc}\n{content}")
    body = "\n\n".join(blocks)
    return (
        "Ground your answer in the numbered sources below. Quote them where "
        "relevant and cite inline as [n]. If they are insufficient, say what is "
        "missing rather than guessing.\n\n"
        f"SOURCES:\n{body}\n\n"
        f"QUESTION: {question}\n\n"
        "ANSWER:"
    )


async def run_ask(
    conn: AsyncConnection,
    question: str,
    top_k: int = 6,
    max_tokens: int = 1024,
    scope: str = "all",
    cluster_id: str | None = None,
) -> dict[str, Any]:
    """Run the full RAG loop and return a grounded, cited answer."""
    query_vector = await _async_embed(question)  # keep event loop free-ish
    sources = await retrieve(
        conn, question, query_vector, top_k, scope=scope, cluster_id=cluster_id
    )
    if not sources:
        return {
            "question": question,
            "answer": None,
            "sources": [],
            "llm_error": "No relevant chunks found in the knowledge base.",
        }

    prompt = build_prompt(question, sources)
    answer = complete(prompt, max_tokens)
    if not answer:
        return {
            "question": question,
            "answer": None,
            "sources": sources,
            "llm_error": "LLM gateway unreachable — retrieved chunks only.",
        }

    return {"question": question, "answer": answer, "sources": sources, "llm_error": None}


async def _async_embed(query: str) -> list[float] | None:
    """Run embed_query off the event loop (httpx blocking call)."""
    return await _to_thread(embed_query, query)


async def _to_thread(fn, *args, **kwargs):
    import asyncio
    return await asyncio.to_thread(fn, *args, **kwargs)