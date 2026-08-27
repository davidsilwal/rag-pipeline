#!/usr/bin/env python3
"""workers/runner.py — Canonical fat-worker task loop (plan §2.3, Appendix A).

Replaces ``notebooks/deepnote_worker.py::run_pipeline()`` as the canonical
worker loop. The notebook path delegates here for Colab/Deepnote compatibility.

Loop:
    boot:  load config (WORKER_* env), detect capabilities, register with the
           Control API → {worker_id, token}
    loop:  claim tasks for enabled stages (long-poll), run each stage handler
           (idempotent), settle with complete/fail + lease token, heartbeat
    stop:  SIGTERM → graceful drain (finish ≤1 in-flight task) + deregister

Heavy dependencies (torch, FlagEmbedding, umap, hdbscan, litellm) are imported
lazily inside handlers only, so a thin/CPU profile never pays for them (§9A).

Run:
    python -m workers.runner
"""

from __future__ import annotations

import asyncio
import logging
import os
import pathlib
import platform
import signal
import socket

import httpx

log = logging.getLogger("worker.runner")

# ---------------------------------------------------------------------------
# Config (plan §9 env table)
# ---------------------------------------------------------------------------

DEFAULT_STAGES = ["discover", "extract", "chunk", "embed", "dedup",
                  "cluster", "consensus", "graphrag", "compile"]


def load_config() -> dict:
    return {
        "name": os.getenv("WORKER_ID") or socket.gethostname(),
        "profile": os.getenv("WORKER_PROFILE", "auto"),
        "stages": [s.strip() for s in
                   os.getenv("STAGES_ENABLED", ",".join(DEFAULT_STAGES)).split(",")
                   if s.strip()],
        "embed_device": os.getenv("EMBED_DEVICE", "auto"),
        "embed_allow_cpu": os.getenv("EMBED_ALLOW_CPU", "0") in {"1", "true", "True"},
        # auto | bgem3 | fallback (plan §7.1 EMBED_ALLOW_CPU: force the cheap,
        # deterministic CPU embedder on memory-constrained hosts).
        "embed_backend": os.getenv("EMBED_BACKEND", "auto"),
        "embed_batch_size": int(os.getenv("EMBED_BATCH_SIZE", "32")),
        "embedding_model": os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
        "max_concurrent": int(os.getenv("MAX_CONCURRENT_TASKS", "1")),
        "poll_interval": int(os.getenv("TASK_POLL_INTERVAL", "15")),
        "lease_ttl": int(os.getenv("TASK_LEASE_TTL", "600")),
        "long_poll": os.getenv("LONG_POLL", "1") in {"1", "true", "True"},
        "control_api_url": os.getenv("CONTROL_API_URL", "").rstrip("/"),
        "api_token": os.getenv("API_TOKEN", "") or os.getenv("CONTROL_API_KEY", ""),
        "discover_root": os.getenv("LOCAL_SOURCE_DIR", "") or os.getenv("ONEDRIVE_ROOT_FOLDER", ""),
    }


# ---------------------------------------------------------------------------
# Capability detection (plan §3.1 / §7.5)
# ---------------------------------------------------------------------------

def _gpu_caps() -> dict:
    try:
        import torch  # noqa: F401  (lazy)
        if torch.cuda.is_available():
            count = torch.cuda.device_count()
            return {"present": True, "vendor": "nvidia", "device_count": count,
                    "cuda": torch.version.cuda or "unknown"}
    except Exception:
        pass
    return {"present": False, "vendor": None, "device_count": 0, "cuda": None}


def _mem_mb() -> int:
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def _disk_free_mb() -> int:
    try:
        import shutil
        return shutil.disk_usage(os.getcwd()).free // (1024 * 1024)
    except Exception:
        return 0


def detect_capabilities(profile: str, embed_allow_cpu: bool, embedding_model: str) -> dict:
    gpu = _gpu_caps()
    cores = os.cpu_count() or 1
    mem = _mem_mb()
    caps = {
        "gpu": gpu,
        "cpu": {"cores": cores, "model": platform.processor() or platform.machine()},
        "memory": {"total_mb": mem, "free_mb": mem},
        "disk": {"free_mb": _disk_free_mb()},
        "models": [embedding_model] if (gpu["present"] or embed_allow_cpu) else [],
        "llm": {"endpoint": os.getenv("LOCAL_LLM_API_BASE", "")} if os.getenv("LOCAL_LLM_API_BASE") else {},
        "net": {"to_control_plane_ms": 0},
    }
    if profile == "thin":
        caps["models"] = []
        caps["embed_allow_cpu"] = False
    if embed_allow_cpu:
        caps["embed_allow_cpu"] = True
    return caps


# ---------------------------------------------------------------------------
# Control API client
# ---------------------------------------------------------------------------

class ApiClient:
    def __init__(self, base_url: str, token: str):
        self.base = base_url
        self.token = token
        # follow_redirects: Starlette redirect_slashes turns /units → /units/ etc.
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0), follow_redirects=True
        )
        self.set_token(token)

    def set_token(self, token: str) -> None:
        self.token = token
        self._client.headers["Authorization"] = f"Bearer {token}" if token else ""

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kw):
        resp = await self._client.request(method, f"{self.base}{path}", **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        if not resp.content:
            return None
        try:
            return resp.json()
        except Exception:
            return resp.content

    async def get(self, path: str, **kw):
        return await self._request("GET", path, **kw)

    async def post(self, path: str, json_body=None, **kw):
        return await self._request("POST", path, json=json_body, **kw)

    async def head(self, path: str) -> bool:
        resp = await self._client.request("HEAD", f"{self.base}{path}")
        return resp.status_code < 400

    async def post_bytes(self, path: str, data: bytes, content_type: str) -> None:
        resp = await self._client.request(
            "POST", f"{self.base}{path}", content=data,
            headers={"Content-Type": content_type},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"POST {path} -> {resp.status_code}: {resp.text[:300]}")

    async def get_bytes(self, path: str) -> bytes:
        resp = await self._client.request("GET", f"{self.base}{path}")
        if resp.status_code >= 400:
            raise RuntimeError(f"GET {path} -> {resp.status_code}: {resp.text[:300]}")
        return resp.content


# ---------------------------------------------------------------------------
# Stage handlers (idempotent; return result_meta dict)
# ---------------------------------------------------------------------------

async def _fetch_units(api: ApiClient, source_id: str) -> list[dict]:
    # Corpus-scope tasks (graphrag/compile) operate on all units, not one source.
    params: dict = {"limit": 10000}
    if source_id and source_id != "corpus":
        params["source_id"] = source_id
    data = await api.get("/units", params=params)
    return data if isinstance(data, list) else []


async def _extract_text(raw: bytes, mime: str) -> str:
    """Best-effort text extraction. Markdown/plain text pass through; other
    formats fall back to a UTF-8 decode so the chunk stage always has text."""
    if mime and ("markdown" in mime or "text/plain" in mime or "text/markdown" in mime):
        return raw.decode("utf-8", errors="replace")
    try:
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return ""


async def _hash_text(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def handle_discover(api: ApiClient, cfg: dict, task: dict) -> dict:
    """Scan a configured local root and register new files (§9A.3 upload dedup)."""
    root = cfg["discover_root"]
    if not root:
        return {"registered": 0, "note": "no LOCAL_SOURCE_DIR/ONEDRIVE_ROOT_FOLDER set"}
    from workers.gpu_worker.discovery import discover
    manifest = discover(root)
    registered = 0
    skipped = 0
    for item in manifest:
        sha = item["sha256_hash"]
        if await api.head(f"/sources/by-hash/{sha}"):
            skipped += 1
            continue
        reg = await api.post("/sources/register", {
            "drive_item_id": f"local:{sha}",
            "drive_id": "local",
            "file_path": item["file_path"],
            "file_name": item["file_name"],
            "mime_type": item["mime_type"],
            "size_bytes": item["size_bytes"],
            "sha256_hash": sha,
            "status": "discovered",
        })
        try:
            raw = pathlib.Path(root, item["file_path"]).read_bytes()
            sid = (((reg or {}).get("source_id")) if isinstance(reg, dict) else None) or sha
            await api.post_bytes(f"/sources/{sid}/blob", raw, item["mime_type"] or "application/octet-stream")
        except Exception:
            pass
        registered += 1
    return {"registered": registered, "skipped_known": skipped}


async def handle_extract(api: ApiClient, cfg: dict, task: dict) -> dict:
    source_id = task["scope_id"]
    raw = await api.get_bytes(f"/sources/{source_id}/blob")
    meta = await api.get(f"/sources/by-id/{source_id}")
    mime = (meta or {}).get("mime_type", "") or ""
    file_name = (meta or {}).get("file_name", "") or ""
    # Rich documents (PDF/DOCX/PPTX/EPUB/images) go through Docling → structured
    # Markdown; plain text/code stays on a native UTF-8 path (plan §6.3).
    from workers.gpu_worker.docling_extract import extract_document
    result = extract_document(raw, mime, file_name)
    text = result["text"]
    await api.post_bytes(f"/sources/{source_id}/text", text.encode("utf-8"),
                         "text/plain; charset=utf-8")
    return {"chars": len(text), "mime_type": mime, "engine": result["engine"]}


async def handle_chunk(api: ApiClient, cfg: dict, task: dict) -> dict:
    source_id = task["scope_id"]
    meta = await api.get(f"/sources/by-id/{source_id}")
    mime = (meta or {}).get("mime_type", "") or ""
    file_name = (meta or {}).get("file_name", "") or ""

    from workers.gpu_worker.docling_extract import (
        chunk_document,
        docling_available,
        uses_docling,
    )

    units: list[dict] = []
    engine = "heading"
    # Rich documents get Docling HybridChunker chunks with page/bbox provenance
    # (docling.ai RAG recipe); everything else stays on the heading chunker.
    if uses_docling(mime) and docling_available():
        try:
            raw = await api.get_bytes(f"/sources/{source_id}/blob")
            docling_chunks = chunk_document(raw, mime, file_name)
            if docling_chunks:
                units = [
                    {
                        "doc_id": source_id,
                        "unit_index": i,
                        "unit_type": "docling_chunk",
                        "heading_path": c["heading_path"],
                        "raw_text": c["raw_text"],
                        "clean_text": c["clean_text"],
                        "page_number": c.get("page_number"),
                        "bbox_coords": c.get("bbox_coords"),
                        "content_hash": await _hash_text(c["clean_text"]),
                    }
                    for i, c in enumerate(docling_chunks)
                ]
                engine = "docling"
        except Exception as e:
            log.warning(
                "docling chunking failed for %s (%s); falling back to heading chunker",
                source_id,
                e,
            )

    if not units:
        raw = await api.get_bytes(f"/sources/{source_id}/text")
        text = raw.decode("utf-8", errors="replace")
        from workers.gpu_worker.chunker import chunk_markdown
        chunks = chunk_markdown(source_id, text)
        units = [
            {
                "doc_id": source_id,
                "unit_index": c.chunk_index,
                "unit_type": "markdown_chunk",
                "heading_path": c.heading_path,
                "raw_text": c.content,
                "clean_text": c.content,
                "content_hash": await _hash_text(c.content),
            }
            for c in chunks
        ]

    if units:
        await api.post("/units", {"source_id": source_id, "units": units})
    return {"chunks": len(units), "engine": engine}


async def handle_embed(api: ApiClient, cfg: dict, task: dict) -> dict:
    source_id = task["scope_id"]
    units = await _fetch_units(api, source_id)
    texts = [u.get("clean_text", "") for u in units if u.get("clean_text")]
    if not texts:
        return {"embedded": 0, "units": 0}

    use_gpu = cfg["embed_device"] == "cuda" or (
        cfg["embed_device"] == "auto" and _gpu_caps().get("present")
    )
    embedder = _get_embedder(cfg, use_gpu)
    dense, sparse = embedder.encode(texts, batch_size=cfg["embed_batch_size"],
                                    return_dense=True, return_sparse=True)
    written = 0
    for u, d, s in zip(units, dense, sparse):
        if not u.get("clean_text"):
            continue
        await api.post("/embed_cache", {
            "content_hash": u["content_hash"],
            "model_id": cfg["embedding_model"],
            "dense_vector": _to_list(d),
            "sparse_weights": _to_dict(s),
        })
        written += 1
    return {"embedded": written, "units": len(units), "device": "cuda" if use_gpu else "cpu"}


_embedder_cache: dict = {}


def _get_embedder(cfg: dict, use_gpu: bool):
    """Lazy embedder (BGEM3 if usable, else deterministic CPU fallback).

    EMBED_BACKEND=fallback skips the (memory-heavy) model load entirely —
    useful on constrained hosts where BGEM3 would be OOM-killed.
    """
    key = (cfg["embedding_model"], use_gpu, cfg.get("embed_backend"))
    if key in _embedder_cache:
        return _embedder_cache[key]
    backend = cfg.get("embed_backend", "auto")
    if backend != "fallback":
        try:
            from workers.gpu_worker.embedder import BGEM3Embedder
            embedder = BGEM3Embedder(model_name=cfg["embedding_model"], use_gpu=use_gpu,
                                     batch_size=cfg["embed_batch_size"])
            log.info("embedder: BGEM3 (%s)", "cuda" if use_gpu else "cpu")
        except Exception as e:
            if backend == "bgem3":
                raise
            log.warning("BGEM3 init failed (%s); using deterministic fallback embedder", e)
            embedder = _FallbackEmbedder(cfg["embedding_model"])
    else:
        log.info("embedder: deterministic fallback (EMBED_BACKEND=fallback)")
        embedder = _FallbackEmbedder(cfg["embedding_model"])
    _embedder_cache[key] = embedder
    return embedder


class _FallbackEmbedder:
    """Deterministic, dependency-free embedding (hash-seeded) — keeps the CPU
    worker functional when FlagEmbedding/torch are absent (plan §7.1)."""

    def __init__(self, model_name: str = "BAAI/bge-m3"):
        self.model_name = model_name

    def encode(self, texts, batch_size=32, return_dense=True, return_sparse=False,
               return_colbert_vecs=False, **kwargs):
        import hashlib
        dense_vecs, lexical = [], []
        for text in texts:
            seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:12], 16)
            dense_vecs.append([((seed ^ (i * 31)) % 2000) / 2000.0 for i in range(1024)])
            lexical.append({str(i % 1000): abs(((seed ^ (i * 37)) % 2000) / 2000.0)
                            for i in range(min(32, len(text.split())))})
        return dense_vecs, lexical


def _to_list(v):
    if hasattr(v, "tolist"):
        return v.tolist()
    return [float(x) for x in v]


def _to_dict(v):
    if v is None:
        return {}
    if isinstance(v, dict):
        return {str(k): float(x) for k, x in v.items()}
    return {str(k): float(x) for k, x in (v or {}).items()}


async def handle_dedup(api: ApiClient, cfg: dict, task: dict) -> dict:
    from workers.gpu_worker.dedup import run_dedup
    pairs = await run_dedup(task["scope_id"])
    return {"pairs": pairs}


async def handle_cluster(api: ApiClient, cfg: dict, task: dict) -> dict:
    from workers.gpu_worker.clustering import run_clustering
    clusters = await run_clustering(task["scope_id"])
    return {"clusters": len(clusters)}


async def handle_consensus(api: ApiClient, cfg: dict, task: dict) -> dict:
    from workers.gpu_worker.consensus import compute_consensus
    results = await compute_consensus(task["scope_id"])
    return {"units_scored": len(results)}


async def handle_graphrag(api: ApiClient, cfg: dict, task: dict) -> dict:
    """Stage 7: Extract entities, relationships, and communities from units.

    Uses the LLM to build a knowledge graph, then persists it to the DB.

    For corpus-scope tasks, sources are batched together (50 sources per
    LLM call) to reduce the total number of LLM calls from ~14K to ~300.
    Sources with < 300 chars of total text are skipped.
    """
    from workers.gpu_worker.graphrag_engine import extract_for_units, save_graphrag_results

    scope_id = task["scope_id"]
    MIN_TEXT_CHARS = 300       # skip tiny sources
    SOURCES_PER_LLM = 15      # batch N sources per LLM call
    MAX_CHARS_PER_SRC = 1500   # truncate each source's combined text

    if scope_id and scope_id != "corpus":
        units = await _fetch_units(api, scope_id)
        texts = [u.get("clean_text", "") for u in units if u.get("clean_text")]
        unit_ids = [u.get("unit_id") for u in units if u.get("unit_id")]
        if not texts:
            return {"entities": 0, "relationships": 0, "communities": 0, "note": "no units"}
        g = await extract_for_units(texts)
        saved = await save_graphrag_results(g, unit_ids)
        return {"entities": len(g.entities), "relationships": len(g.relationships),
                "communities": len(g.communities), "saved": saved}

    # Corpus-scope: two-step approach:
    # 1) Get source metadata (source_ids + unit counts) in ONE bulk query
    # 2) Fetch text in small batches and extract per LLM-call batch.
    src_data = await api.get("/units/by-source", params={"min_chars": MIN_TEXT_CHARS, "limit": 100000})
    sources = src_data if isinstance(src_data, list) else []
    log.info("graphrag corpus: %d eligible sources in one bulk query", len(sources))

    total_entities = 0
    total_rels = 0
    total_communities = 0
    total_saved = {"entities": 0, "relationships": 0, "communities": 0}
    total_llm_batches = (len(sources) + SOURCES_PER_LLM - 1) // SOURCES_PER_LLM

    # Process in LLM batches. For each LLM batch, first fetch text via
    # /units/text-batch (one HTTP call per LLM batch, not per source),
    # then run multiple LLM calls in parallel.
    CONCURRENT_LLM = 8  # parallel LLM calls within each batch

    for llm_idx in range(0, len(sources), SOURCES_PER_LLM):
        llm_batch = sources[llm_idx:llm_idx + SOURCES_PER_LLM]
        llm_batch_num = llm_idx // SOURCES_PER_LLM + 1
        src_ids = ",".join(s["source_id"] for s in llm_batch)

        # Fetch combined text for this batch in one HTTP call
        # Try the full batch first; on failure, split into smaller chunks
        text_rows: list[dict] = []
        try:
            text_data = await api.get("/units/text-batch", params={"source_ids": src_ids, "max_chars_per_source": MAX_CHARS_PER_SRC})
            text_rows = text_data if isinstance(text_data, list) else []
        except Exception as e:
            log.warning("graphrag text-batch %d failed (%d sources), retrying in halves: %s",
                        llm_batch_num, len(llm_batch), e)
            # Retry with smaller chunks
            half = len(llm_batch) // 2 or 1
            for chunk_start in range(0, len(llm_batch), half):
                chunk = llm_batch[chunk_start:chunk_start + half]
                chunk_ids = ",".join(s["source_id"] for s in chunk)
                try:
                    chunk_data = await api.get("/units/text-batch", params={"source_ids": chunk_ids, "max_chars_per_source": MAX_CHARS_PER_SRC})
                    text_rows.extend(chunk_data if isinstance(chunk_data, list) else [])
                except Exception as e2:
                    log.warning("graphrag text-batch retry also failed: %s", e2)
                    continue

        # Split into sub-groups for parallel LLM calls
        sub_size = max(1, len(text_rows) // CONCURRENT_LLM)
        sub_groups = []
        for i in range(0, len(text_rows), sub_size):
            sub_groups.append(text_rows[i:i + sub_size])

        async def _process_subgroup(sub_rows: list) -> dict:
            batch_texts = [r.get("combined_text", "") for r in sub_rows if r.get("combined_text", "").strip()]
            batch_unit_ids = []
            for r in sub_rows:
                batch_unit_ids.extend(r.get("unit_ids", []))
            if not batch_texts:
                return {"entities": 0, "rels": 0, "communities": 0, "saved": {"entities": 0, "relationships": 0, "communities": 0}}
            g = await extract_for_units(batch_texts)
            saved = await save_graphrag_results(g, batch_unit_ids)
            return {"entities": len(g.entities), "rels": len(g.relationships), "communities": len(g.communities), "saved": saved}

        # Run sub-groups in parallel
        results = await asyncio.gather(*[_process_subgroup(sg) for sg in sub_groups])

        batch_entities = sum(r["entities"] for r in results)
        batch_rels = sum(r["rels"] for r in results)
        batch_communities = sum(r["communities"] for r in results)
        for r in results:
            for k in total_saved:
                total_saved[k] += r["saved"].get(k, 0)
        total_entities += batch_entities
        total_rels += batch_rels
        total_communities += batch_communities

        log.info("graphrag batch %d/%d: %d entities, %d rels, %d communities",
                 llm_batch_num, total_llm_batches,
                 batch_entities, batch_rels, batch_communities)

    return {
        "entities": total_entities,
        "relationships": total_rels,
        "communities": total_communities,
        "saved": total_saved,
        "sources_processed": len(sources),
        "sources_skipped": 0,
    }


async def handle_compile(api: ApiClient, cfg: dict, task: dict) -> dict:
    from workers.gpu_worker.markdown_compiler import compile_page
    scope_id = task.get("scope_id", "page")
    # Determine the original source file_path so wiki_pages.file_path mirrors
    # sources.file_path (the key the dashboard uses to look up pages). When
    # running on a per-source scope, fetch the source row; for the synthetic
    # `corpus` scope, fall back to a markdown filename derived from the scope.
    source_fp: str | None = None
    if task.get("scope_type") == "source":
        try:
            src = await api.get(f"/sources/by-id/{scope_id}")
            if src:
                source_fp = src.get("file_path")
        except Exception:
            source_fp = None
    if source_fp:
        file_path = source_fp
    elif task.get("scope_type") == "corpus":
        file_path = "corpus.md"
    else:
        file_path = f"{scope_id}.md"
    units = await _fetch_units(api, scope_id)
    page = await compile_page(scope_id, units, {})
    # Derive title from the original file_path (e.g. projects/ai/x.md → x)
    if source_fp:
        title = source_fp.split("/")[-1].removesuffix(".md") or scope_id
    else:
        title = file_path.split("/")[-1].removesuffix(".md") or scope_id
    page_id = str(__import__("uuid").uuid5(__import__("uuid").NAMESPACE_URL, source_fp or scope_id))
    page_payload = {
        "page_id": page_id,
        "file_path": file_path,
        "title": title,
        "page_type": "source",
        "domain": "docs",
        "status": "active",
        "frontmatter": {
            "source_id": scope_id,
            "source_path": source_fp,
        } if source_fp else {},
        "markdown_body": page.markdown,
        "source_unit_ids": [u.get("unit_id") for u in units if u.get("unit_id")],
        "chunks": [
            {
                "page_id": page_id,
                "file_path": file_path,
                "chunk_index": idx,
                "content": u.get("clean_text") or "",
                "heading_path": u.get("heading_path") or [],
                "content_hash": u.get("content_hash") or "",
                "chunk_metadata": {"unit_id": str(u.get("unit_id") or ""), "unit_type": u.get("unit_type")},
            }
            for idx, u in enumerate(units) if (u.get("clean_text") or "").strip()
        ],
    }
    try:
        await api.post("/wiki/pages", {"pages": [page_payload]})
    except Exception as e:
        log.warning("wiki write failed: %s", e)
    return {"page_path": file_path, "coverage": page.coverage_score,
            "citations": page.citations}


STAGE_HANDLERS = {
    "discover": handle_discover,
    "extract": handle_extract,
    "chunk": handle_chunk,
    "embed": handle_embed,
    "dedup": handle_dedup,
    "cluster": handle_cluster,
    "consensus": handle_consensus,
    "graphrag": handle_graphrag,
    "compile": handle_compile,
}


# ---------------------------------------------------------------------------
# Task execution
# ---------------------------------------------------------------------------

async def _heartbeat_loop(api: ApiClient, task: dict, stop: asyncio.Event) -> None:
    """Extend the lease every ~10 s while the task runs (plan §5)."""
    ttl = cfg_lease_ttl()
    while not stop.is_set():
        await asyncio.sleep(min(10, max(ttl // 3, 5)))
        try:
            await api.post(f"/tasks/{task['task_id']}/heartbeat",
                           {"lease_token": task["lease_token"]})
        except Exception:
            pass


_cfg_lease_ttl = [600]


def cfg_lease_ttl() -> int:
    return _cfg_lease_ttl[0]


async def run_task(api: ApiClient, cfg: dict, task: dict, worker_token: str) -> None:
    stage = task["stage"]
    stop = asyncio.Event()
    hb = asyncio.create_task(_heartbeat_loop(api, task, stop))
    try:
        handler = STAGE_HANDLERS.get(stage)
        if handler is None:
            # Thin safety net (plan §9A.4): never silently drop.
            await api.post(f"/tasks/{task['task_id']}/fail", {
                "lease_token": task["lease_token"],
                "error_message": f"stage_not_supported:{stage}",
                "will_retry": False,
            })
            return
        log.info("task %s stage=%s scope=%s/%s", task["task_id"], stage,
                 task["scope_type"], task["scope_id"])
        meta = await handler(api, cfg, task)
        await api.post(f"/tasks/{task['task_id']}/complete", {
            "lease_token": task["lease_token"],
            "result_meta": meta,
        })
        log.info("task %s stage=%s done: %s", task["task_id"], stage, meta)
    except Exception as e:
        log.warning("task %s stage=%s failed: %s", task["task_id"], stage, e)
        try:
            await api.post(f"/tasks/{task['task_id']}/fail", {
                "lease_token": task["lease_token"],
                "error_message": f"{type(e).__name__}: {e}",
                "will_retry": True,
            })
        except Exception:
            log.warning("could not report failure for task %s", task["task_id"])
    finally:
        stop.set()
        hb.cancel()


# ---------------------------------------------------------------------------
# Worker loop
# ---------------------------------------------------------------------------

async def run_worker_forever(cfg: dict | None = None) -> None:
    cfg = cfg or load_config()
    _cfg_lease_ttl[0] = cfg["lease_ttl"]

    if not cfg["control_api_url"] or not cfg["api_token"]:
        raise SystemExit("CONTROL_API_URL and API_TOKEN must be set")

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    api = ApiClient(cfg["control_api_url"], cfg["api_token"])
    caps = detect_capabilities(cfg["profile"], cfg["embed_allow_cpu"], cfg["embedding_model"])

    reg = await api.post("/workers/register", {
        "name": cfg["name"],
        "platform": cfg["profile"] if cfg["profile"] != "auto" else "docker",
        "hostname": socket.gethostname(),
        "version": "2.3",
        "capabilities": caps,
        "stages_enabled": cfg["stages"],
        "concurrency_max": cfg["max_concurrent"],
    })
    worker_id = reg["worker_id"]
    worker_token = reg["token"]
    # From here on authenticate with the per-worker token (plan §13).
    api.set_token(worker_token)

    # ------------------------------------------------------------------
    # Worker-level heartbeat (keeps the worker "online" in the control API).
    # Without this, the sweeper marks the worker offline after 3×30s = 90s
    # even though it's actively processing long-running tasks.
    # ------------------------------------------------------------------
    async def _worker_heartbeat_loop():
        while True:
            await asyncio.sleep(15)
            try:
                await api.post(f"/workers/{worker_id}/heartbeat", {"load": {}})
            except Exception:
                pass

    hb_task = asyncio.create_task(_worker_heartbeat_loop())

    # ------------------------------------------------------------------
    # Main task loop
    # ------------------------------------------------------------------
    concurrency = int(cfg.get("max_concurrent", 1) or 1)
    log.info("worker started id=%s stages=%s concurrency=%s", worker_id, cfg.get("stages"), concurrency)
    while True:
        claimed = await api.post("/tasks/claim", {"worker_id": worker_id, "stages": cfg.get("stages"), "max_tasks": concurrency})
        # API returns a bare list (or empty list) — be tolerant of both shapes
        if isinstance(claimed, dict):
            tasks = claimed.get("tasks") or claimed.get("data") or []
        elif isinstance(claimed, list):
            tasks = claimed
        else:
            tasks = []
            try:
                await api.post(f"/workers/{worker_id}/heartbeat", {"load": {}})
            except Exception:
                pass
            await asyncio.sleep(min(1.0, cfg.get("lease_ttl", 5)))
            continue
        for task in tasks:
            stage = task.get("stage", "unknown")
            stage_task_id = task.get("task_id")
            try:
                handler = STAGE_HANDLERS.get(stage)
                if handler is None:
                    log.warning("unsupported stage=%s task=%s", stage, stage_task_id)
                    continue
                log.info("run start stage=%s task=%s scope=%s", stage, stage_task_id, task.get("scope_id"))
                result = await handler(api, cfg, task)
                await api.post(f"/tasks/{task['task_id']}/complete", {
                    "lease_token": task.get("lease_token"),
                    "result_meta": result or {},
                })
                log.info("run ok stage=%s task=%s result=%s", stage, stage_task_id, result)
            except Exception as exc:
                log.exception("run failed stage=%s task=%s err=%s", stage, stage_task_id, exc)
                try:
                    await api.post("/tasks/complete", {
                        "task_id": stage_task_id,
                        "status": "failed",
                        "error": {"message": str(exc)},
                        "worker_id": worker_id,
                    })
                except Exception:
                    pass


if __name__ == "__main__":
    asyncio.run(run_worker_forever())