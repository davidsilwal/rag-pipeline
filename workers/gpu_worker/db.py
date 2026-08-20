#!/usr/bin/env python3
"""workers/gpu_worker/db.py — Postgres connection-pool helper for the GPU worker."""

import os
import asyncpg

_pool: asyncpg.pool.Pool | None = None


async def get_pool() -> asyncpg.pool.Pool:
    global _pool
    if _pool is None:
        url = os.getenv("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL must be set for the GPU worker")
        _pool = await asyncpg.create_pool(url, min_size=1, max_size=10)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
