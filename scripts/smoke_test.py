#!/usr/bin/env python3
"""scripts/smoke_test.py — End-to-end smoke test for the multi-worker API.

Validates the full orchestration surface against a LIVE control API:
  * auth: unauthenticated mutation is rejected (401)
  * workers: register → heartbeat → list → deregister
  * sources: register (enqueues extract) → blob round-trip → text round-trip
  * queue:    claim (SKIP LOCKED) → complete → producer chaining (chunk enqueued)
  * units:    POST /units (idempotent) → GET /units
  * embed_cache: POST /embed_cache upsert

Usage:
    CONTROL_API_URL=http://localhost:8000/api/v1 API_TOKEN=... python scripts/smoke_test.py
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import sys
import uuid

import httpx

BASE = os.getenv("CONTROL_API_URL", "http://localhost:8000/api/v1").rstrip("/")
TOKEN = os.getenv("API_TOKEN", "")

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")


def hdrs(token: str = TOKEN) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


async def main() -> int:
    if not TOKEN:
        print("API_TOKEN env var required")
        return 1
    # follow_redirects: Starlette redirect_slashes turns /workers → /workers/ etc.
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
        # --- auth ----------------------------------------------------------
        r = await c.post(f"{BASE}/sources/register", json={})
        check("mutation without token rejected (401)", r.status_code == 401,
              f"got {r.status_code}")

        # --- workers -------------------------------------------------------
        wname = f"smoke-{uuid.uuid4().hex[:6]}"
        r = await c.post(f"{BASE}/workers/register", headers=hdrs(), json={
            "name": wname, "platform": "smoke-test",
            "capabilities": {"gpu": {"present": False}, "cpu": {"cores": 2},
                             "memory": {"total_mb": 4096}},
            "stages_enabled": ["extract", "chunk", "embed"],
            "concurrency_max": 1,
        })
        check("worker register", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        wid, wtoken = r.json()["worker_id"], r.json()["token"]

        r = await c.post(f"{BASE}/workers/{wid}/heartbeat",
                         headers=hdrs(wtoken), json={"load": {"running": 0, "queue_len": 0}})
        check("worker heartbeat with worker token", r.status_code == 200, f"got {r.status_code}")

        r = await c.get(f"{BASE}/workers", headers=hdrs())
        names = [w["name"] for w in r.json()]
        check("worker list contains ours", wname in names)

        # --- sources + blob + text ------------------------------------------
        sha256 = sha(f"smoke-{uuid.uuid4().hex}")
        r = await c.post(f"{BASE}/sources/register", headers=hdrs(), json={
            "drive_item_id": f"smoke:{sha256}", "drive_id": "smoke",
            "file_path": "smoke/test.md", "file_name": "test.md",
            "mime_type": "text/markdown", "size_bytes": 42, "sha256_hash": sha256,
            "status": "discovered",
        })
        check("source register", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        sid = r.json()["source_id"]

        r = await c.post(f"{BASE}/sources/{sid}/blob", headers=hdrs(wtoken),
                         content=b"# Hello\n\nworld")
        check("blob store", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        r = await c.get(f"{BASE}/sources/{sid}/blob")
        check("blob fetch round-trip", r.status_code == 200 and b"# Hello" in r.content)

        r = await c.post(f"{BASE}/sources/{sid}/text", headers=hdrs(wtoken),
                         content="# Hello\n\nworld")
        check("text store", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        r = await c.get(f"{BASE}/sources/{sid}/text")
        check("text fetch round-trip", r.status_code == 200 and "# Hello" in r.text)

        # --- queue: claim + complete + chaining ------------------------------
        r = await c.post(f"{BASE}/tasks/claim", headers=hdrs(wtoken), json={
            "worker_id": wid, "stages": ["extract"], "max_tasks": 5, "long_poll": False,
        })
        tasks = r.json()
        check("claimed extract task for new source", any(
            t["scope_id"] == sid and t["stage"] == "extract" for t in tasks),
            f"tasks: {tasks}")

        claimed = next((t for t in tasks if t["scope_id"] == sid), None)
        if claimed:
            r = await c.post(f"{BASE}/tasks/{claimed['task_id']}/complete",
                             headers=hdrs(wtoken),
                             json={"lease_token": claimed["lease_token"],
                                   "result_meta": {"chars": 42}})
            check("complete task (token-guarded)", r.status_code == 200, f"got {r.status_code}")

            await asyncio.sleep(1)
            r = await c.get(f"{BASE}/tasks", headers=hdrs(),
                            params={"stage": "chunk", "status": "queued"})
            chunk_tasks = [t for t in r.json() if t["scope_id"] == sid]
            check("producer chaining: extract → chunk enqueued",
                  len(chunk_tasks) == 1, f"chunk tasks: {chunk_tasks}")

        # --- units -----------------------------------------------------------
        unit_hash = sha("unit content")
        r = await c.post(f"{BASE}/units", headers=hdrs(wtoken), json={
            "source_id": sid,
            "units": [{
                "doc_id": sid, "unit_index": 0, "unit_type": "markdown_chunk",
                "heading_path": ["Hello"], "raw_text": "world", "clean_text": "world",
                "content_hash": unit_hash,
            }],
        })
        check("units upsert", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")
        r = await c.get(f"{BASE}/units", params={"source_id": sid})
        check("units list by source", any(u["content_hash"] == unit_hash for u in r.json()))

        # --- embed_cache -----------------------------------------------------
        r = await c.post(f"{BASE}/embed_cache", headers=hdrs(wtoken), json={
            "content_hash": unit_hash, "model_id": "BAAI/bge-m3",
            "dense_vector": [0.1] * 1024, "sparse_weights": {"1": 0.5},
        })
        check("embed_cache upsert", r.status_code == 200, f"got {r.status_code}: {r.text[:200]}")

        # --- deregister ------------------------------------------------------
        r = await c.post(f"{BASE}/workers/{wid}/deregister", headers=hdrs(wtoken))
        check("worker deregister", r.status_code == 200, f"got {r.status_code}")

    print(f"\n🎯 Smoke test complete: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
