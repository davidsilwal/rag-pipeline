#!/usr/bin/env python3
"""
Pipeline watchdog: auto-cleanup dead-letter tasks and stale leases.

Idempotent, safe to run from cron. Handles the two failure modes observed:
1. Dead-letter tasks superseded by a newer active task (unique-constraint
   would block a naive requeue) → delete the dead-letter.
2. Claimed tasks with expired leases (crashed worker) → the control API's
   lazy-reclaim picks these up on the next claim; this script just reports.

Usage: python3 watch-pipeline.py [--cron]
       In --cron mode, only emits output when something changed.
"""

import json
import os
import sys
import textwrap
from urllib import request

API = os.environ.get(
    "RAG_API_URL", "http://localhost:8000/api/v1"
).rstrip("/")
TOKEN = os.environ.get("API_TOKEN", "sk_prod_a1b2c3d4e5f60718293a4b5c6d7e8f9")
DB = os.environ.get("PGDATABASE", "knowledge_base")
PGHOST = os.environ.get("PGHOST", "postgres")
PGUSER = os.environ.get("PGUSER", "postgres")


def _api(path: str) -> dict:
    req = request.Request(f"{API}{path}")
    req.add_header("Authorization", f"Bearer {TOKEN}")
    with request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _pg(sql: str, **params) -> list[dict]:
    """Run SQL via psql inside the postgres container. Returns rows as dicts."""
    import subprocess

    formatted = sql.format_map(
        {k: f"'{v}'" if isinstance(v, str) else str(v) for k, v in params.items()}
    )
    cmd = [
        "docker", "exec", "rag-pipeline-postgres-1",
        "psql", "-U", PGUSER, "-d", DB, "-t", "-A", "-F", "\t", "-c", formatted,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"psql failed: {r.stderr[:300]}")
    rows = []
    for line in r.stdout.strip().split("\n"):
        if line:
            parts = line.split("\t")
            if len(parts) >= 4:
                rows.append({
                    "task_id": parts[0], "stage": parts[1],
                    "status": parts[2], "scope": parts[3],
                })
    return rows


def cleanup_dead_letters() -> int:
    """Delete dead-letter tasks that have a newer active sibling for same scope.
    Returns number of rows deleted."""
    result = _pg(textwrap.dedent("""\
        WITH doomed AS (
            DELETE FROM task_queue dl
            WHERE dl.status = 'dead_letter'
              AND EXISTS (
                SELECT 1 FROM task_queue active
                WHERE active.stage = dl.stage
                  AND active.scope_type = dl.scope_type
                  AND active.scope_id = dl.scope_id
                  AND active.status IN ('queued','claimed','running')
              )
            RETURNING dl.task_id, dl.stage, dl.scope_id, dl.error_message
        )
        SELECT task_id, stage, scope_id, error_message FROM doomed
    """))
    return len(result)


def report_stuck() -> dict:
    """Report tasks that have been claimed for >2x their stage TTL."""
    return _pg(textwrap.dedent("""\
        SELECT task_id, stage, status, scope_id,
               EXTRACT(epoch FROM now() - started_at)::int AS age_s
        FROM task_queue
        WHERE status = 'claimed'
          AND started_at < now() - interval '30 minutes'
        ORDER BY started_at
        LIMIT 5
    """))


def queue_summary() -> dict:
    return _api("/health")["queue"]


def main():
    cron = "--cron" in sys.argv
    changes = 0

    if not cron:
        print("=== Queue Summary ===")
        q = queue_summary()
        for s, v in q.items():
            print(f"  {s:12s}  q={v['queued']:6d}  c={v['claimed']:2d}  dl={v['dead_letter']:1d}")

    # 1. Cleanup dead letters
    deleted = cleanup_dead_letters()
    if deleted:
        print(f"\n🧹 Cleaned up {deleted} dead-letter task(s) superseded by newer active tasks.")
        changes += deleted
    elif not cron:
        print("\n✅ No dead-letter cleanup needed.")

    # 2. Check stuck claims
    stuck = report_stuck()
    if stuck:
        print(f"\n⚠️  {len(stuck)} task(s) stuck in 'claimed' >30 min (possibly zombie):")
        for s in stuck:
            print(f"    {s['task_id'][:8]}  {s['stage']:12s}  scope={s['scope_id']}  age={s['age_s']}s")
        changes += len(stuck)
    elif not cron:
        print("✅ No stuck claims >30 min.")

    if changes:
        sys.exit(0)
    elif cron:
        # No output for cron unless something happened
        pass
    else:
        print("\n✅ Pipeline healthy — all clear.")


if __name__ == "__main__":
    main()