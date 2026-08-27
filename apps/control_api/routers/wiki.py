#!/usr/bin/env python3
"""apps/control_api/routers/wiki.py — Read wiki output."""

from __future__ import annotations

import asyncio
import io
import json
import math
import os
import re
import sys
import time
import uuid
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from database import get_engine
from deps import require_any_token

router = APIRouter(prefix="/wiki", tags=["wiki"])

# ── Graph (content-similarity) support ──────────────────────────────────────
# The wiki pages are LLM-compiled from sources and carry no explicit
# page-to-page links, so the graph's edges are derived from how much each
# pair of pages shares vocabulary (TF-IDF cosine over the markdown bodies).
# This mirrors the "openwiki visualize" node graph for link-free wikis.

GRAPH_MAX_NODES = 500
_GRAPH_STOP = frozenset(
    "the a an and or of to in on for with as is are was were be been by at from it its "
    "this that these those we our you your they their per not no can will would should could "
    "may might must via using use used using into over under between then than when where "
    "which who whom what how why all any some each other another both etc example e g i e "
    "like also however therefore thus well make made need needs section below above following "
    "table figure list provide provides including includes include related refers reference "
    "one two three first second third new old current existing page pages document docs file "
    "files value values data information content system systems user users project projects "
    ""
    .split()
)
_GRAPH_TOKEN_RE = re.compile(r"[a-z][a-z0-9_\-]{2,}")


def _graph_tokens(text_: str) -> list[str]:
    return [
        t
        for t in _GRAPH_TOKEN_RE.findall((text_ or "").lower())
        if t not in _GRAPH_STOP and not t.replace("-", "").isdigit()
    ]


def _tfidf_cosine(vectors: list[dict[str, float]]) -> list[list[float]]:
    """Cosine similarity matrix from sparse tf-idf dict vectors."""
    n = len(vectors)
    norms = [math.sqrt(sum(v * v for v in vec.values())) for vec in vectors]
    sim = [[0.0] * n for _ in range(n)]
    for i in range(n):
        if norms[i] == 0:
            continue
        for j in range(i + 1, n):
            if norms[j] == 0:
                continue
            shared = 0.0
            for term, w in vectors[i].items():
                wj = vectors[j].get(term)
                if wj:
                    shared += w * wj
            if shared:
                sim[i][j] = sim[j][i] = shared / (norms[i] * norms[j])
    return sim


def _tfidf_vectors(token_lists: list[list[str]]) -> list[dict[str, float]]:
    """Sparse tf-idf vectors for a corpus of token lists."""
    n = len(token_lists)
    df: dict[str, int] = {}
    for toks in token_lists:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    vectors: list[dict[str, float]] = []
    for toks in token_lists:
        freq: dict[str, int] = {}
        for t in toks:
            freq[t] = freq.get(t, 0) + 1
        vec: dict[str, float] = {}
        for t, c in freq.items():
            vec[t] = (1 + math.log(c)) * (math.log(n / (1 + df.get(t, 1))) + 1)
        vectors.append(vec)
    return vectors


def _shared_terms(
    v1: dict[str, float], v2: dict[str, float], k: int = 3
) -> list[str]:
    """Top terms shared by two tf-idf vectors, ranked by min weight."""
    common = set(v1) & set(v2)
    if not common:
        return []
    ranked = sorted(((min(v1[t], v2[t]), t) for t in common), reverse=True)
    return [t for _, t in ranked[:k]]


def _top_terms(vec: dict[str, float], k: int = 10) -> list[dict[str, float]]:
    """Top terms of a tf-idf vector with weights normalized to [0, 1]
    (relative within the vector), for the similarity comparison panel."""
    ranked = sorted(vec.items(), key=lambda kv: kv[1], reverse=True)[:k]
    if not ranked:
        return []
    mx = ranked[0][1] or 1.0
    return [{"term": t, "weight": round(w / mx, 4)} for t, w in ranked]


def _shared_terms_weighted(
    v1: dict[str, float], v2: dict[str, float], k: int = 3
) -> list[dict[str, float]]:
    """Terms shared by two vectors weighted by the weaker side, normalized to
    [0, 1] (ties broken by weight) — powers the comparison panel's bars."""
    common = set(v1) & set(v2)
    if not common:
        return []
    ranked = sorted(
        ((min(v1[t], v2[t]), t) for t in common), reverse=True
    )[:k]
    if not ranked:
        return []
    mx = ranked[0][0] or 1.0
    return [{"term": t, "weight": round(w / mx, 4)} for w, t in ranked]


@router.get("/graph")
async def wiki_graph(
    scope: str = "",
    top_k: int = 4,
    min_score: float = 0.1,
    cross: bool = False,
    cross_k: int = 5,
    cross_terms: int = 5,
    _tok: str = Depends(require_any_token),
):
    """Pages inside `scope` (a folder prefix) as a graph: nodes are pages,
    edges connect the most content-similar pairs (TF-IDF cosine over the
    markdown bodies). With `cross=true`, also adds edges to the most related
    pages *outside* the scope, found by full-text searching the whole wiki's
    chunks with each scope page's most distinctive terms. Feed the result to
    a force-directed visualizer."""
    engine = get_engine()
    # "all" renders a macro view of the whole wiki: one node per
    # project/area cluster, edges = cross-cluster similarity.
    if scope == "all":
        return await _whole_wiki_graph(engine, top_k=top_k, min_score=min_score)

    async with engine.connect() as conn:
        sql = """
                SELECT page_id, file_path, title, page_type, markdown_body
                FROM wiki_pages
                """
        params: dict = {}
        if scope:
            # Match the folder exactly (a scope is a path prefix): `nepal-police`
            # must not pull in pages from `nepal-police-news-scrapper`.
            sql += " WHERE file_path = :scope OR file_path LIKE :prefix"
            params["scope"] = scope
            params["prefix"] = f"{scope}/%"
        rows = (await conn.execute(text(sql), params)).mappings().all()

    if len(rows) > GRAPH_MAX_NODES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Scope has {len(rows)} pages (max {GRAPH_MAX_NODES}). "
                "Pick a single project or knowledge area."
            ),
        )
    if not rows:
        return {"scope": scope, "nodes": [], "links": []}

    scope_depth = len([s for s in scope.split("/") if s])
    token_lists = [_graph_tokens(r["markdown_body"]) for r in rows]
    vectors = _tfidf_vectors(token_lists)
    sim = _tfidf_cosine(vectors)

    # Undirected edges: keep pairs that rank in the top-k for either endpoint
    # (above the score floor), deduped with the max score.
    n = len(rows)
    edge_scores: dict[tuple[int, int], float] = {}
    for i in range(n):
        ranked = sorted(
            ((sim[i][j], j) for j in range(n) if j != i and sim[i][j] >= min_score),
            reverse=True,
        )[:top_k]
        for score, j in ranked:
            a, b = (i, j) if i < j else (j, i)
            edge_scores[(a, b)] = max(edge_scores.get((a, b), 0.0), score)

    nodes = []
    for i, r in enumerate(rows):
        parts = [p for p in (r["file_path"] or "").split("/") if p]
        subfolder = parts[scope_depth] if len(parts) > scope_depth else ""
        body = r["markdown_body"] or ""
        nodes.append(
            {
                "id": str(r["page_id"]),
                "title": r["title"],
                "file_path": r["file_path"],
                "page_type": r["page_type"],
                "subfolder": subfolder,
                "preview": body[:220],
                "top_terms": _top_terms(vectors[i], 10),
            }
        )
    links = [
        {
            "source": str(rows[a]["page_id"]),
            "target": str(rows[b]["page_id"]),
            "score": round(score, 4),
            "terms": _shared_terms(vectors[a], vectors[b]),
            "term_weights": _shared_terms_weighted(vectors[a], vectors[b]),
        }
        for (a, b), score in edge_scores.items()
    ]

    # Cross-project edges: for each scope page, search the whole wiki's chunk
    # full-text index with its most distinctive terms and keep the top related
    # pages outside the scope.
    if cross:
        cross_result = await _cross_edges(
            engine, rows, cross_k=cross_k, cross_terms=cross_terms
        )
        nodes.extend(cross_result["nodes"])
        links.extend(cross_result["links"])

    return {"scope": scope, "nodes": nodes, "links": links}


async def _cross_edges(engine, scope_rows, cross_k: int, cross_terms: int):
    """Find pages outside `scope_rows` that share vocabulary, by querying the
    wiki_chunks FTS index (GIN) with each scope page's top tf-idf terms."""
    scope_ids = [str(r["page_id"]) for r in scope_rows]
    token_lists = [_graph_tokens(r["markdown_body"]) for r in scope_rows]

    n = len(scope_rows)
    df: dict[str, int] = {}
    for toks in token_lists:
        for t in set(toks):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(n / (1 + c)) + 1 for t, c in df.items()}

    per_page_terms: list[list[str]] = []
    for toks in token_lists:
        freq: dict[str, int] = {}
        for t in toks:
            freq[t] = freq.get(t, 0) + 1
        ranked = sorted(
            ((freq[t] * idf.get(t, 0), t) for t in freq), reverse=True
        )
        per_page_terms.append([t for _, t in ranked[:cross_terms]])

    # (scope_page_id, external_page_id) -> best rank. The per-page FTS
    # queries are the hot path, so run them concurrently across the pool.
    async def _search(i: int) -> list[tuple[str, float]]:
        terms = per_page_terms[i]
        if not terms:
            return []
        query = " OR ".join(terms)
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    """
                    SELECT c.page_id, MAX(ts_rank_cd(c.fts_vector,
                            websearch_to_tsquery('simple', :q))) AS rank
                    FROM wiki_chunks c
                    WHERE c.fts_vector @@ websearch_to_tsquery('simple', :q)
                      AND NOT (c.page_id = ANY(:scope_ids))
                    GROUP BY c.page_id
                    ORDER BY rank DESC
                    LIMIT :k
                    """
                ),
                {"q": query, "scope_ids": scope_ids, "k": cross_k},
            )
        return [
            (str(row["page_id"]), float(row["rank"] or 0))
            for row in res.mappings()
            if (row["rank"] or 0) > 0
        ]

    results = await asyncio.gather(
        *(_search(i) for i in range(len(per_page_terms)))
    )
    edges: dict[tuple[str, str], float] = {}
    for i, hits in enumerate(results):
        src = str(scope_rows[i]["page_id"])
        for pid, rank in hits:
            key = (src, pid)
            edges[key] = max(edges.get(key, 0.0), rank)

    if not edges:
        return {"nodes": [], "links": []}

    max_rank = max(edges.values())
    ext_ids = sorted({pid for _, pid in edges})
    async with engine.connect() as conn:
        ext_rows = (
            await conn.execute(
                text(
                    """
                    SELECT page_id, file_path, title, page_type, markdown_body
                    FROM wiki_pages
                    WHERE page_id = ANY(:ids)
                    """
                ),
                {"ids": ext_ids},
            )
        ).mappings().all()

    src_index = {str(r["page_id"]): i for i, r in enumerate(scope_rows)}
    ext_by_id = {str(r["page_id"]): r for r in ext_rows}
    ext_tokens = {
        str(r["page_id"]): set(_graph_tokens(r["markdown_body"]))
        for r in ext_rows
    }
    # Vectors over the union corpus (scope pages + external pages) so both
    # sides share a scale for the weighted shared-term comparison.
    union_vectors = _tfidf_vectors(
        token_lists + [_graph_tokens(r["markdown_body"]) for r in ext_rows]
    )
    ext_vec_by_id = {
        str(r["page_id"]): union_vectors[n + j]
        for j, r in enumerate(ext_rows)
    }
    nodes = []
    for pid in ext_ids:
        r = ext_by_id.get(pid)
        if not r:
            continue
        parts = [p for p in (r["file_path"] or "").split("/") if p]
        body = r["markdown_body"] or ""
        nodes.append(
            {
                "id": pid,
                "title": r["title"],
                "file_path": r["file_path"],
                "page_type": r["page_type"],
                "subfolder": parts[-2] if len(parts) >= 2 else "",
                "preview": body[:220],
                "cross": True,
                "top_terms": _top_terms(ext_vec_by_id[pid], 8),
            }
        )
    links = [
        {
            "source": src,
            "target": tgt,
            "score": round(rank / max_rank, 4),
            "cross": True,
            "terms": [
                t
                for t in per_page_terms[src_index[src]]
                if t in ext_tokens.get(tgt, set())
            ][:3],
            "term_weights": _shared_terms_weighted(
                union_vectors[src_index[src]], ext_vec_by_id[tgt]
            ),
        }
        for (src, tgt), rank in edges.items()
    ]
    return {"nodes": nodes, "links": links}


# The whole-wiki macro graph tokenizes every page — expensive (~15s), so the
# corpus-level work (cluster keys, vectors, similarity matrix) is cached in
# memory per worker, and the final response is cached in Redis so both
# uvicorn workers (and repeat visits) get it instantly.
_WHOLE_CACHE: dict = {"at": 0.0, "keys": None, "meta": None, "vectors": None, "sim": None}
_WHOLE_CACHE_TTL = 300
_WHOLE_REDIS_CLIENT = None


def _macro_cache_key(top_k: int, min_score: float) -> str:
    # v2: response shape now carries node top_terms + edge term_weights.
    return f"wiki:graph:v2:all:{top_k}:{min_score:.3f}"


def _get_redis():
    global _WHOLE_REDIS_CLIENT
    if _WHOLE_REDIS_CLIENT is None:
        from redis.asyncio import from_url

        url = os.environ.get("REDIS_URL", "redis://redis:6379/0")
        _WHOLE_REDIS_CLIENT = from_url(url, decode_responses=True)
    return _WHOLE_REDIS_CLIENT


async def _whole_wiki_graph(engine, top_k: int, min_score: float):
    """Macro graph of the entire wiki: one node per project / knowledge-area
    cluster (sized by page count), edges = cross-cluster content similarity
    with the shared terms labeled. Clicking a cluster drills down to its
    page-level graph (the cluster key is a valid `scope`)."""
    now = time.monotonic()
    if _WHOLE_CACHE["keys"] is None or now - _WHOLE_CACHE["at"] > _WHOLE_CACHE_TTL:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(
                    text(
                        "SELECT page_id, file_path, title, page_type, markdown_body "
                        "FROM wiki_pages"
                    )
                )
            ).mappings().all()

        clusters: dict[str, dict] = {}
        for r in rows:
            parts = [p for p in (r["file_path"] or "").split("/") if p]
            if parts[:1] == ["projects"] and len(parts) >= 2:
                key, label, kind = f"projects/{parts[1]}", parts[1], "project"
            elif parts:
                key, label, kind = parts[0], parts[0], "area"
            else:
                key, label, kind = "other", "other", "area"
            cluster = clusters.setdefault(key, {"label": label, "kind": kind, "pages": []})
            cluster["pages"].append(r)

        keys = list(clusters)
        token_lists = [
            _graph_tokens(" ".join(p["markdown_body"] or "" for p in c["pages"]))
            for c in clusters.values()
        ]
        vectors = _tfidf_vectors(token_lists)
        sim = _tfidf_cosine(vectors)
        meta = [
            {"label": c["label"], "kind": c["kind"], "count": len(c["pages"])}
            for c in clusters.values()
        ]
        _WHOLE_CACHE.update(
            {"at": now, "keys": keys, "meta": meta, "vectors": vectors, "sim": sim}
        )

    keys: list[str] = _WHOLE_CACHE["keys"]
    meta: list[dict] = _WHOLE_CACHE["meta"]
    vectors: list[dict[str, float]] = _WHOLE_CACHE["vectors"]
    sim: list[list[float]] = _WHOLE_CACHE["sim"]

    # Serve the prebuilt response from Redis when a worker already computed it.
    redis_key = _macro_cache_key(top_k, min_score)
    try:
        cached = await _get_redis().get(redis_key)
        if cached:
            return json.loads(cached)
    except Exception as e:  # pragma: no cover - debug
        print(f"[graph-cache] get failed: {type(e).__name__}: {e}", file=sys.stderr)

    n = len(keys)
    edge_scores: dict[tuple[int, int], float] = {}
    for i in range(n):
        ranked = sorted(
            ((sim[i][j], j) for j in range(n) if j != i and sim[i][j] >= min_score),
            reverse=True,
        )[:top_k]
        for score, j in ranked:
            a, b = (i, j) if i < j else (j, i)
            edge_scores[(a, b)] = max(edge_scores.get((a, b), 0.0), score)

    nodes = [
        {
            "id": key,
            "title": m["label"],
            "file_path": key,
            "page_type": m["kind"],
            "subfolder": m["kind"],
            "preview": "",
            "count": m["count"],
            "cluster": True,
            "kind": m["kind"],
            "top_terms": _top_terms(vectors[i], 8),
        }
        for i, (key, m) in enumerate(zip(keys, meta))
    ]
    links = [
        {
            "source": keys[a],
            "target": keys[b],
            "score": round(score, 4),
            "terms": _shared_terms(vectors[a], vectors[b]),
            "term_weights": _shared_terms_weighted(vectors[a], vectors[b]),
        }
        for (a, b), score in edge_scores.items()
    ]
    payload = {"scope": "all", "nodes": nodes, "links": links}
    try:
        await _get_redis().set(redis_key, json.dumps(payload), ex=_WHOLE_CACHE_TTL)
    except Exception as e:  # pragma: no cover - debug
        print(f"[graph-cache] set failed: {type(e).__name__}: {e}", file=sys.stderr)
    return payload


@router.get("/export", summary="Export wiki pages as a ZIP of markdown files")
async def export_wiki_pages(
    prefix: str | None = None,
    q: str | None = None,
    status: str | None = None,
    page_type: str | None = None,
    domain: str | None = None,
    _tok: str = Depends(require_any_token),
):
    """Return wiki pages as ``.md`` files inside a ZIP archive.

    Optional filters narrow the export:
      * ``prefix`` – folder path prefix (e.g. ``projects/mozambique``)
      * ``q``      – search substring matched against title and file_path
      * ``status`` – exact status filter (e.g. ``active``)
      * ``page_type`` – exact page_type filter
      * ``domain`` – exact domain filter
    """
    engine = get_engine()
    where: list[str] = []
    params: dict = {}

    if prefix:
        folder = prefix.rstrip("/")
        where.append("(file_path = :pfx OR file_path LIKE :pfx_slash)")
        params["pfx"] = folder
        params["pfx_slash"] = f"{folder}/%"
    if q:
        where.append("(LOWER(title) LIKE :q OR LOWER(file_path) LIKE :q)")
        params["q"] = f"%{q.lower()}%"
    if status:
        where.append("status = :status")
        params["status"] = status
    if page_type:
        where.append("page_type = :page_type")
        params["page_type"] = page_type
    if domain:
        where.append("domain = :domain")
        params["domain"] = domain

    sql = "SELECT file_path, title, markdown_body FROM wiki_pages"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY file_path"

    engine = get_engine()
    async with engine.connect() as conn:
        rows = (await conn.execute(text(sql), params)).mappings().all()

    if not rows:
        raise HTTPException(status_code=404, detail="No wiki pages match the export filters")

    # Build a descriptive filename when filtering (e.g. "wiki-mozambique.zip").
    parts = ["wiki"]
    if prefix:
        slug = prefix.rstrip("/").split("/")[-1]
        if slug:
            parts.append(slug)
    if q:
        parts.append(q[:20])
    if status:
        parts.append(status)
    filename = "-".join(parts) + ".zip"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for row in rows:
            fp = row["file_path"] or f"{row['title'] or 'untitled'}.md"
            if not fp.endswith(".md"):
                fp += ".md"
            zf.writestr(fp, row["markdown_body"] or "")
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition":
                f'attachment; filename="{filename}"',
        },
    )


@router.get("/pages/stats", summary="Lightweight project/category stats for the wiki list")
async def wiki_pages_stats(_tok: str = Depends(require_any_token)):
    """Return page counts grouped by top-level folder (project/category)
    and by type, without loading markdown bodies. Fast even for 10K+ pages."""
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text("""
                    SELECT
                        CASE
                            WHEN position('/' in file_path) > 0
                            THEN split_part(file_path, '/', 1)
                            ELSE file_path
                        END AS top_folder,
                        page_type,
                        COUNT(*) AS cnt,
                        MAX(updated_at) AS last_updated
                    FROM wiki_pages
                    GROUP BY top_folder, page_type
                """),
            )
        ).mappings().all()
        proj_subs = (
            await conn.execute(
                text("""
                    SELECT split_part(file_path, '/', 2) AS proj_name,
                           COUNT(*) AS cnt,
                           MAX(updated_at) AS last_updated,
                           COUNT(CASE WHEN array_length(string_to_array(file_path, '/'), 1) >= 4 THEN 1 END) AS subfolder_count
                    FROM wiki_pages
                    WHERE file_path LIKE 'projects/%'
                    GROUP BY proj_name
                """),
            )
        ).mappings().all()
    total = sum(r["cnt"] for r in rows)
    projects: list[dict] = []
    categories: list[dict] = []
    types: dict[str, int] = {}
    for r in rows:
        folder = r["top_folder"]
        cnt = r["cnt"]
        pt = r["page_type"]
        types[pt] = types.get(pt, 0) + cnt
        if folder == "projects":
            continue  # grouped below
        categories.append({
            "name": folder,
            "count": cnt,
            "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None,
        })
    for r in proj_subs:
        projects.append({
            "name": r["proj_name"],
            "count": r["cnt"],
            "subfolders": r["subfolder_count"],
            "last_updated": r["last_updated"].isoformat() if r["last_updated"] else None,
        })
    return {
        "total": total,
        "types": types,
        "projects": sorted(projects, key=lambda p: p["name"]),
        "categories": sorted(categories, key=lambda c: -c["count"]),
    }


@router.get("/pages")
async def list_wiki_pages(
    limit: int = 50,
    offset: int = 0,
    prefix: str | None = None,
    _tok: str = Depends(require_any_token),
):
    engine = get_engine()
    async with engine.connect() as conn:
        sql = """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, left(markdown_body, 200) AS markdown_preview,
                       git_commit_sha, created_at, updated_at
                FROM wiki_pages
                """
        params: dict = {"off": offset}
        where: list[str] = []
        # Optional folder prefix filter, e.g. ?prefix=projects/mozambique/ for
        # the sibling-docs sidebar on the reader page. Matched as a folder
        # boundary so a prefix never bleeds into similarly-named siblings.
        if prefix:
            folder = prefix.rstrip("/")
            where.append("file_path = :pfx OR file_path LIKE :pfx_slash")
            params["pfx"] = folder
            params["pfx_slash"] = f"{folder}/%"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY updated_at DESC"
        # limit <= 0 means "return everything" — the dashboard lists the full
        # wiki and filters/paginates client-side.
        if limit > 0:
            sql += " LIMIT :lim OFFSET :off"
            params["lim"] = limit
        else:
            sql += " OFFSET :off"
        result = await conn.execute(text(sql), params)
        rows = result.mappings().all()
    return [
        {
            "page_id": str(row["page_id"]),
            "file_path": row["file_path"],
            "title": row["title"],
            "page_type": row["page_type"],
            "domain": row["domain"],
            "status": row["status"],
            "frontmatter": row["frontmatter"],
            "markdown_preview": row["markdown_preview"],
            "git_commit_sha": row["git_commit_sha"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
        for row in rows
    ]


@router.post("/pages", summary="Upsert wiki pages with chunks")
async def upsert_wiki_pages(request: Request, _tok: str = Depends(require_any_token)):
    body = await request.json()
    items = body if isinstance(body, list) else body.get("pages") or body.get("items") or []
    if not items:
        raise HTTPException(status_code=400, detail="Expected list of pages or {pages:[...]}")

    engine = get_engine()
    async with engine.begin() as conn:
        for page in items:
            page_id = page.get("page_id")
            if not page_id:
                continue
            chunks = page.get("chunks") or []
            await conn.execute(
                text("""
                INSERT INTO wiki_pages
                    (page_id, file_path, title, page_type, domain, status, frontmatter, markdown_body, source_unit_ids)
                VALUES
                    (:page_id, :file_path, :title, :page_type, :domain, :status, :frontmatter, :markdown_body, :source_unit_ids)
                ON CONFLICT (page_id) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    title = EXCLUDED.title,
                    page_type = EXCLUDED.page_type,
                    domain = EXCLUDED.domain,
                    status = EXCLUDED.status,
                    frontmatter = EXCLUDED.frontmatter,
                    markdown_body = EXCLUDED.markdown_body,
                    source_unit_ids = EXCLUDED.source_unit_ids,
                    updated_at = now()
                """),
                {
                    "page_id": page_id,
                    "file_path": page.get("file_path"),
                    "title": page.get("title"),
                    "page_type": page.get("page_type") or "page",
                    "domain": page.get("domain") or "docs",
                    "status": page.get("status", "active"),
                    "frontmatter": json.dumps(page.get("frontmatter") or {}),
                    "markdown_body": page.get("markdown_body") or "",
                    "source_unit_ids": page.get("source_unit_ids") or [],
                },
            )
            for chunk in chunks:
                # chunk_id is a UUID column; when the producer omits one, derive a
                # deterministic UUID from (page_id, chunk_index) so re-runs are
                # stable and unique per chunk.
                chunk_id = chunk.get("chunk_id") or str(
                    uuid.uuid5(uuid.NAMESPACE_URL, f"{page_id}:{chunk.get('chunk_index') or 0}")
                )
                await conn.execute(
                    text("""
                    INSERT INTO wiki_chunks
                        (chunk_id, page_id, file_path, heading_path, chunk_index, content, content_hash, dense_vector, sparse_weights, chunk_metadata)
                    VALUES
                        (:chunk_id, :page_id, :file_path, :heading_path, :chunk_index, :content, :content_hash, :dense_vector, :sparse_weights, :chunk_metadata)
                    ON CONFLICT (page_id, chunk_index) DO UPDATE SET
                        content = EXCLUDED.content,
                        content_hash = EXCLUDED.content_hash,
                        dense_vector = EXCLUDED.dense_vector,
                        sparse_weights = EXCLUDED.sparse_weights,
                        chunk_metadata = EXCLUDED.chunk_metadata
                    """),
                    {
                        "chunk_id": chunk_id,
                        "page_id": page_id,
                        "file_path": chunk.get("file_path") or page.get("file_path"),
                        "heading_path": chunk.get("heading_path") or [],
                        "chunk_index": int(chunk.get("chunk_index") or 0),
                        "content": chunk.get("content") or "",
                        "content_hash": chunk.get("content_hash") or "",
                        "dense_vector": chunk.get("dense_vector"),
                        "sparse_weights": chunk.get("sparse_weights"),
                        "chunk_metadata": json.dumps(chunk.get("metadata") or chunk.get("chunk_metadata") or {}),
                    },
                )
    return {"pages": len(items), "chunks": sum(len(page.get("chunks") or []) for page in items)}


@router.patch("/pages/{page_id}")
async def update_wiki_page(page_id: str, request: Request, _tok: str = Depends(require_any_token)):
    """Update a wiki page's content (title and/or markdown body). Used by the
    dashboard's wiki editor for manual edits. Manual edits are overwritten the
    next time the source is recompiled by the pipeline."""
    body = await request.json()
    markdown_body = body.get("markdown_body")
    title = body.get("title")
    if markdown_body is None and title is None:
        raise HTTPException(status_code=400, detail="markdown_body or title is required")

    sets = ["updated_at = now()"]
    params: dict = {"pid": page_id}
    if markdown_body is not None:
        sets.append("markdown_body = :markdown_body")
        params["markdown_body"] = markdown_body
    if title is not None:
        sets.append("title = :title")
        params["title"] = title

    engine = get_engine()
    async with engine.begin() as conn:
        result = await conn.execute(
            text(f"UPDATE wiki_pages SET {', '.join(sets)} WHERE page_id = :pid RETURNING page_id"),
            params,
        )
        if result.first() is None:
            raise HTTPException(status_code=404, detail="Wiki page not found")
    return {"page_id": page_id, "status": "updated"}


@router.get("/pages/{page_id}")
async def get_wiki_page(page_id: str, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, markdown_body, source_unit_ids, last_verified_at, created_at, updated_at
                FROM wiki_pages
                WHERE page_id = :pid
                """
            ),
            {"pid": page_id},
        )
        row = result.mappings().first()
    if not row:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Wiki page not found")
    return {
        "page_id": str(row["page_id"]),
        "file_path": row["file_path"],
        "title": row["title"],
        "page_type": row["page_type"],
        "domain": row["domain"],
        "status": row["status"],
        "frontmatter": row["frontmatter"],
        "markdown_body": row["markdown_body"],
        "source_unit_ids": row["source_unit_ids"],
        "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


@router.get("/by-file/{file_path:path}")
async def get_wiki_page_by_file(file_path: str, _tok: str = Depends(require_any_token)):
    """Resolve a wiki page by its source file_path. Returns the page if it
    has been compiled; 404 with a hint if it hasn't been generated yet."""
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, markdown_body, source_unit_ids,
                       last_verified_at, created_at, updated_at
                FROM wiki_pages
                WHERE file_path = :fp
                """
            ),
            {"fp": file_path},
        )
        row = result.mappings().first()
        if row:
            return {
                "page_id": str(row["page_id"]),
                "file_path": row["file_path"],
                "title": row["title"],
                "page_type": row["page_type"],
                "domain": row["domain"],
                "status": row["status"],
                "frontmatter": row["frontmatter"],
                "markdown_body": row["markdown_body"],
                "source_unit_ids": row["source_unit_ids"],
                "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
                "created_at": row["created_at"].isoformat() if row["created_at"] else None,
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
        # Page not compiled yet — check the source so we can tell the dashboard
        # whether the compile is queued or simply never going to happen.
        src = (await conn.execute(
            text("SELECT source_id, status FROM sources WHERE file_path = :fp"),
            {"fp": file_path},
        )).mappings().first()
    if src is None:
        raise HTTPException(
            status_code=404,
            detail=f"No source found at {file_path}",
        )
    # Source exists, page is not yet compiled. Return a structured 404 so the
    # dashboard can render a helpful "compile pending" state instead of a
    # blank "Page not found" message.
    raise HTTPException(
        status_code=404,
        detail={
            "message": f"Wiki page for {file_path} has not been compiled yet",
            "source_id": str(src["source_id"]),
            "source_status": src["status"],
        },
    )



@router.get("/by-source-id/{source_id}")
async def get_wiki_page_by_source_id(source_id: str, _tok: str = Depends(require_any_token)):
    """Resolve a wiki page by its source_id. Useful when the dashboard URL
    shows the source_id (e.g. from a copy-page_id action that incorrectly
    captured the source_id instead of the page_id)."""
    engine = get_engine()
    async with engine.connect() as conn:
        # First, fetch the source to get its file_path
        src = (await conn.execute(
            text("SELECT file_path, status FROM sources WHERE source_id = :sid"),
            {"sid": source_id},
        )).mappings().first()
        if src is None:
            raise HTTPException(
                status_code=404,
                detail=f"No source found with id {source_id}",
            )
        # Then, look up the wiki page by file_path
        result = await conn.execute(
            text(
                """
                SELECT page_id, file_path, title, page_type, domain, status,
                       frontmatter, markdown_body, source_unit_ids,
                       last_verified_at, created_at, updated_at
                FROM wiki_pages
                WHERE file_path = :fp
                """
            ),
            {"fp": src["file_path"]},
        )
        row = result.mappings().first()
    if row:
        return {
            "page_id": str(row["page_id"]),
            "file_path": row["file_path"],
            "title": row["title"],
            "page_type": row["page_type"],
            "domain": row["domain"],
            "status": row["status"],
            "frontmatter": row["frontmatter"],
            "markdown_body": row["markdown_body"],
            "source_unit_ids": row["source_unit_ids"],
            "last_verified_at": row["last_verified_at"].isoformat() if row["last_verified_at"] else None,
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        }
    # Page not compiled yet
    raise HTTPException(
        status_code=404,
        detail={
            "message": f"Wiki page for {src.file_path} has not been compiled yet",
            "source_id": source_id,
            "source_status": src["status"],
        },
    )
@router.get("/chunks")
async def list_wiki_chunks(limit: int = 50, page_id: str | None = None, _tok: str = Depends(require_any_token)):
    engine = get_engine()
    async with engine.connect() as conn:
        sql = "SELECT chunk_id, page_id, content, chunk_metadata AS metadata, created_at FROM wiki_chunks"
        params = {"lim": limit}
        if page_id:
            sql += " WHERE page_id = :pid"
            params["pid"] = page_id
        sql += " ORDER BY created_at DESC LIMIT :lim"
        result = await conn.execute(text(sql), params)
        rows = result.mappings().all()
    return [
        {
            "chunk_id": str(row["chunk_id"]),
            "page_id": str(row["page_id"]),
            "content": row["content"],
            "metadata": row["metadata"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        }
        for row in rows
    ]


# ── GraphRAG knowledge graph read endpoints ──────────────────────────────────

@router.get("/graphrag/entities")
async def list_graphrag_entities(
    limit: int = 200,
    offset: int = 0,
    entity_type: str | None = None,
    search: str | None = None,
    _tok: str = Depends(require_any_token),
):
    """Return extracted knowledge-graph entities."""
    engine = get_engine()
    async with engine.connect() as conn:
        where: list[str] = []
        params: dict = {"lim": limit, "off": offset}
        if entity_type:
            where.append("entity_type = :etype")
            params["etype"] = entity_type
        if search:
            where.append("LOWER(name) LIKE :q")
            params["q"] = f"%{search.lower()}%"
        sql = "SELECT entity_id, name, entity_type, description, frequency FROM graphrag_entities"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY frequency DESC, name LIMIT :lim OFFSET :off"
        rows = (await conn.execute(text(sql), params)).mappings().all()
    return [
        {
            "entity_id": str(r["entity_id"]),
            "name": r["name"],
            "entity_type": r["entity_type"],
            "description": r["description"],
            "frequency": r["frequency"],
        }
        for r in rows
    ]


@router.get("/graphrag/relationships")
async def list_graphrag_relationships(
    limit: int = 200,
    offset: int = 0,
    entity_id: str | None = None,
    _tok: str = Depends(require_any_token),
):
    """Return extracted relationships, optionally filtered by entity."""
    engine = get_engine()
    async with engine.connect() as conn:
        sql = """
            SELECT r.rel_id, r.relationship_type, r.description, r.weight,
                   se.name AS source_name, se.entity_type AS source_type,
                   te.name AS target_name, te.entity_type AS target_type
            FROM graphrag_relationships r
            JOIN graphrag_entities se ON r.source_entity_id = se.entity_id
            JOIN graphrag_entities te ON r.target_entity_id = te.entity_id
        """
        params: dict = {"lim": limit, "off": offset}
        if entity_id:
            sql += " WHERE (r.source_entity_id = :eid OR r.target_entity_id = :eid)"
            params["eid"] = entity_id
        sql += " ORDER BY r.weight DESC LIMIT :lim OFFSET :off"
        rows = (await conn.execute(text(sql), params)).mappings().all()
    return [
        {
            "rel_id": str(r["rel_id"]),
            "source": r["source_name"],
            "source_type": r["source_type"],
            "target": r["target_name"],
            "target_type": r["target_type"],
            "relationship_type": r["relationship_type"],
            "description": r["description"],
            "weight": r["weight"],
        }
        for r in rows
    ]


@router.get("/graphrag/communities")
async def list_graphrag_communities(
    limit: int = 100,
    offset: int = 0,
    _tok: str = Depends(require_any_token),
):
    """Return extracted knowledge-graph communities."""
    engine = get_engine()
    async with engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT community_id, level, title, summary, member_entities "
                    "FROM graphrag_communities ORDER BY created_at DESC LIMIT :lim OFFSET :off"
                ),
                {"lim": limit, "off": offset},
            )
        ).mappings().all()
    return [
        {
            "community_id": str(r["community_id"]),
            "level": r["level"],
            "title": r["title"],
            "summary": r["summary"],
            "member_entities": r["member_entities"],
        }
        for r in rows
    ]


@router.get("/graphrag/stats")
async def graphrag_stats(_tok: str = Depends(require_any_token)):
    """Summary counts for the knowledge graph."""
    engine = get_engine()
    async with engine.connect() as conn:
        entities = (await conn.execute(text("SELECT COUNT(*) AS n FROM graphrag_entities"))).scalar() or 0
        rels = (await conn.execute(text("SELECT COUNT(*) AS n FROM graphrag_relationships"))).scalar() or 0
        comms = (await conn.execute(text("SELECT COUNT(*) AS n FROM graphrag_communities"))).scalar() or 0
        by_type = (
            await conn.execute(
                text("SELECT entity_type, COUNT(*) AS n FROM graphrag_entities GROUP BY entity_type ORDER BY n DESC")
            )
        ).mappings().all()
    return {
        "entities": entities,
        "relationships": rels,
        "communities": comms,
        "by_type": [{"type": r["entity_type"], "count": r["n"]} for r in by_type],
    }


@router.get("/graphrag/progress")
async def graphrag_progress(_tok: str = Depends(require_any_token)):
    """GraphRAG processing progress: sources with extracted entities vs total sources with units."""
    engine = get_engine()
    async with engine.connect() as conn:
        # Total sources that have at least one unit (i.e. eligible for graphrag)
        total = (
            await conn.execute(
                text("SELECT COUNT(DISTINCT source_id) AS n FROM units")
            )
        ).scalar() or 0
        # Sources that have at least one entity linked via their units
        processed = (
            await conn.execute(
                text(
                    "SELECT COUNT(DISTINCT u.source_id) AS n "
                    "FROM units u "
                    "JOIN graphrag_entities e ON u.unit_id = ANY(e.source_unit_ids)"
                )
            )
        ).scalar() or 0
        # Check if the graphrag task is still running
        running_task = (
            await conn.execute(
                text(
                    "SELECT status, attempts FROM task_queue "
                    "WHERE stage = 'graphrag' ORDER BY updated_at DESC LIMIT 1"
                )
            )
        ).mappings().first()
    task_status = running_task["status"] if running_task else None
    task_attempts = running_task["attempts"] if running_task else 0
    return {
        "processed": processed,
        "total": total,
        "task_status": task_status,
        "task_attempts": task_attempts,
    }
