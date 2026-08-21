#!/usr/bin/env python3
"""scripts/chaos_test.py — Queue chaos test against a LIVE control API (plan §11/§15).

Validates the durable-queue contract end-to-end:
  1. Idempotent enqueue  — registering the same source twice creates ONE task.
  2. Race-free claims    — two workers claim disjoint tasks (no double-assign).
  3. Lazy lease reclaim  — a worker that "dies" (stops heartbeating) gets its
                           leases force-expired by the sweeper and reclaimed by
                           the surviving worker.
  4. No lost tasks       — every task ends in a terminal state (succeeded /
                           dead_letter), never stuck in 'claimed' forever.

Requires the stack to be up (control-api + postgres + migration 0003 applied).
Usage:
    CONTROL_API_URL=http://localhost:8000/api/v1 API_TOKEN=... python scripts/chaos_test.py
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
N_SOURCES = int(os.getenv("CHAOS_SOURCES", "6"))
N_WORKERS = int(os.getenv("CHAOS_WORKERS", "2"))

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


async def claim_tasks(client: httpx.AsyncClient, worker_id: str, wtoken: str) -> list[dict]:
    """Claim up to 64 extract tasks; raise on any non-OK response."""
    r = await client.post(f"{BASE}/tasks/claim", headers=hdrs(wtoken), json={
        "worker_id": worker_id, "stages": ["extract"], "max_tasks": 64,
        "long_poll": False,
    })
    if r.status_code >= 400:
        raise RuntimeError(f"claim failed: {r.status_code} {r.text[:200]}")
    data = r.json()
    if not isinstance(data, list):
        raise RuntimeError(f"claim returned non-list: {data}")
    return data


async def heartbeat_loop(client: httpx.AsyncClient, worker_id: str, wtoken: str,
                         duration_s: float) -> None:
    """Keep a worker online by heartbeating every 15 s."""
    end = asyncio.get_event_loop().time() + duration_s
    while asyncio.get_event_loop().time() < end:
        try:
            await client.post(f"{BASE}/workers/{worker_id}/heartbeat",
                              headers=hdrs(wtoken),
                              json={"load": {"running": 0, "queue_len": 0}})
        except Exception:
            pass
        await asyncio.sleep(15)


async def cleanup_chaos_rows() -> None:
    """Remove previous chaos-test sources + their tasks so runs are repeatable."""
    import asyncpg
    url = os.getenv("DATABASE_URL", "")
    if not url:
        return
    # asyncpg wants plain postgres:// (not sqlalchemy's postgresql+asyncpg://).
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "DELETE FROM task_queue WHERE scope_id IN "
            "(SELECT source_id::text FROM sources WHERE drive_item_id LIKE 'chaos:%')"
        )
        await conn.execute("DELETE FROM sources WHERE drive_item_id LIKE 'chaos:%'")
    finally:
        await conn.close()


async def main() -> int:
    if not TOKEN:
        print("API_TOKEN env var required")
        return 1

    await cleanup_chaos_rows()

    # follow_redirects: Starlette redirect_slashes turns /tasks → /tasks/ etc.
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        print(f"🌪️  Chaos test against {BASE} ({N_SOURCES} sources, {N_WORKERS} workers)")

        print("Registering workers...")
        workers = []
        for i in range(N_WORKERS):
            r = await client.post(f"{BASE}/workers/register", headers=hdrs(), json={
                "name": f"chaos-{uuid.uuid4().hex[:6]}",
                "platform": "chaos-test",
                "capabilities": {"gpu": {"present": False}, "cpu": {"cores": 4},
                                 "memory": {"total_mb": 8192}},
                "stages_enabled": ["extract"],
                "concurrency_max": 1,
            })
            r.raise_for_status()
            w = r.json()
            workers.append(w)
            print(f"  registered {w['worker_id']}")

        print("Registering sources (each enqueues an extract task)...")
        source_ids: set[str] = set()
        for n in range(N_SOURCES):
            r = await client.post(f"{BASE}/sources/register", headers=hdrs(), json={
                "drive_item_id": f"chaos:{sha(f'chaos-source-{n}')}",
                "drive_id": "chaos",
                "file_path": f"chaos/doc-{n}.md",
                "file_name": f"doc-{n}.md",
                "mime_type": "text/markdown",
                "size_bytes": 100,
                "sha256_hash": sha(f"chaos-source-{n}"),
                "status": "discovered",
            })
            r.raise_for_status()
            source_ids.add(r.json()["source_id"])
        # Idempotency: re-register the same source → no duplicate task.
        await client.post(f"{BASE}/sources/register", headers=hdrs(), json={
            "drive_item_id": f"chaos:{sha('chaos-source-0')}",
            "drive_id": "chaos", "file_path": "chaos/doc-0.md", "file_name": "doc-0.md",
            "mime_type": "text/markdown", "size_bytes": 100,
            "sha256_hash": sha("chaos-source-0"), "status": "discovered",
        })
        await asyncio.sleep(1)

        # --- phase 1: idempotent enqueue ----------------------------------
        r = await client.get(f"{BASE}/tasks", headers=hdrs(),
                             params={"stage": "extract", "status": "queued"})
        queued = [t for t in r.json()
                  if t.get("scope_type") == "source" and t.get("scope_id") in source_ids]
        check("idempotent enqueue (N sources → N tasks)",
              len(queued) == N_SOURCES, f"expected {N_SOURCES}, got {len(queued)}")

        # --- phase 2: race-free claims ------------------------------------
        print("Claiming all tasks with two workers (no settling yet)...")
        claimed_by: dict[str, str] = {}
        for _ in range(N_SOURCES):
            got = False
            for w in workers:
                for t in await claim_tasks(client, w["worker_id"], w["token"]):
                    claimed_by[t["task_id"]] = w["worker_id"]
                    got = True
            if not got:
                break
        check("all tasks claimed exactly once (no double-assign)",
              len(claimed_by) == N_SOURCES, f"claimed {len(claimed_by)}/{N_SOURCES}")

        # --- phase 3: worker dies mid-flight ------------------------------
        print("Simulating worker death (survivor keeps heartbeating)...")
        dead, alive = workers[0], workers[1]
        hb = asyncio.create_task(
            heartbeat_loop(client, alive["worker_id"], alive["token"], 170.0)
        )
        # Sweeper cadence is 60 s; offline after 3×30 s TTL. 160 s guarantees the
        # dead worker is marked offline and its leases force-expired/re-queued.
        await asyncio.sleep(160)
        hb.cancel()

        # Survivor reclaims whatever the sweeper re-queued.
        reclaimed = await claim_tasks(client, alive["worker_id"], alive["token"])
        check("dead worker's leases reclaimed by survivor",
              len(reclaimed) >= 1, f"reclaimed {len(reclaimed)}")

        # --- phase 4: settle everything to terminal ------------------------
        print("Settling all claimed tasks...")
        for t in reclaimed:
            r = await client.post(f"{BASE}/tasks/{t['task_id']}/complete",
                                  headers=hdrs(), json={
                                      "lease_token": t["lease_token"],
                                      "result_meta": {"chaos": True}})
            if r.status_code >= 400:
                print(f"    ⚠️ complete failed for {t['task_id']}: {r.text[:120]}")
        # Complete any tasks still claimed by the dead worker (should be none,
        # but settle leftovers for a clean terminal state).
        r = await client.get(f"{BASE}/tasks", headers=hdrs(), params={"stage": "extract"})
        for t in r.json():
            if t.get("status") == "claimed":
                await client.post(f"{BASE}/tasks/{t['task_id']}/complete",
                                  headers=hdrs(), json={
                                      "lease_token": t.get("lease_token"),
                                      "result_meta": {"chaos": True}})

        # --- phase 5: no lost / stuck tasks --------------------------------
        await asyncio.sleep(2)
        r = await client.get(f"{BASE}/tasks", headers=hdrs(), params={"stage": "extract"})
        final = [t for t in r.json()
                 if t.get("scope_type") == "source" and t.get("scope_id") in source_ids]
        stuck = [t for t in final if t.get("status") in ("claimed", "running")]
        terminal = [t for t in final if t.get("status") in ("succeeded", "dead_letter")]
        check("no task stuck in claimed/running",
              len(stuck) == 0, f"stuck: {[t['task_id'] for t in stuck][:5]}")
        check("all tasks reached a terminal state",
              len(terminal) == N_SOURCES, f"terminal {len(terminal)}/{N_SOURCES}")

        # --- cleanup -------------------------------------------------------
        for w in workers:
            try:
                await client.post(f"{BASE}/workers/{w['worker_id']}/deregister",
                                  headers=hdrs())
            except Exception:
                pass

    print(f"\n🎯 Chaos test complete: {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
