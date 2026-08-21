#!/usr/bin/env python3
"""Unit tests for the multi-worker orchestration (plan §3–§6, Appendix A).

Covers pure logic only — no live Postgres required:
  * capability gating (services.capabilities.stage_eligible)
  * queue helpers (services.queue)
  * producer-chaining maps (routers.tasks)
  * worker/task ORM models (models.Worker, models.TaskQueue)
  * runner pure helpers (workers.runner)
"""

import pytest

# App modules resolve as top-level modules (see tests/conftest.py).
from services.capabilities import stage_eligible
from services.queue import _priority_for, advisory_lock_key
from routers.tasks import NEXT_STAGE, STAGE_TTLS, DEFAULT_TTL


# ---------------------------------------------------------------------------
# Capability gating (plan §6.1/§6.2)
# ---------------------------------------------------------------------------

def test_extract_chunk_always_eligible():
    assert stage_eligible({}, "extract") is True
    assert stage_eligible({}, "chunk") is True
    assert stage_eligible({"gpu": {"present": False}}, "dedup") is True


def test_embed_requires_gpu_or_optin_or_model():
    assert stage_eligible({}, "embed") is False
    assert stage_eligible({"gpu": {"present": False}}, "embed") is False
    assert stage_eligible({"gpu": {"present": True}}, "embed") is True
    assert stage_eligible({"embed_allow_cpu": True}, "embed") is True
    assert stage_eligible({"models": ["BAAI/bge-m3"]}, "embed") is True


def test_cluster_requires_fat_memory_or_optin():
    assert stage_eligible({"memory": {"total_mb": 4096}, "cpu": {"cores": 2}}, "cluster") is False
    assert stage_eligible({"memory": {"total_mb": 65536}}, "cluster") is True
    assert stage_eligible({"cpu": {"cores": 16}}, "cluster") is True
    assert stage_eligible({"allow_cluster": True}, "cluster") is True


def test_consensus_requires_llm_or_optin():
    assert stage_eligible({}, "consensus") is False
    assert stage_eligible({"llm": {"endpoint": "http://llm:4000/v1"}}, "consensus") is True
    assert stage_eligible({"allow_llm": True}, "consensus") is True


def test_case_insensitive_stage():
    assert stage_eligible({"gpu": {"present": True}}, "EMBED") is True
    assert stage_eligible({"allow_llm": True}, "Consensus") is True


# ---------------------------------------------------------------------------
# Queue helpers (plan §4.5 / §6.4)
# ---------------------------------------------------------------------------

def test_priority_ordering():
    assert _priority_for("extract") < _priority_for("chunk") < _priority_for("embed")
    assert _priority_for("embed") < _priority_for("dedup")
    assert _priority_for("compile") > _priority_for("cluster")
    assert _priority_for("unknown_stage") == 100


def test_advisory_lock_deterministic():
    a = advisory_lock_key("cluster")
    b = advisory_lock_key("cluster")
    assert a == b
    assert advisory_lock_key("cluster") != advisory_lock_key("consensus")
    # Must fit in a signed 64-bit int (Postgres advisory lock key range).
    assert -(2**63) <= a < 2**63


# ---------------------------------------------------------------------------
# Producer chaining (plan §4.5)
# ---------------------------------------------------------------------------

def test_next_stage_chain():
    assert NEXT_STAGE["extract"] == "chunk"
    assert NEXT_STAGE["chunk"] == "embed"
    assert NEXT_STAGE["embed"] == "dedup"
    assert NEXT_STAGE["cluster"] == "consensus"
    assert NEXT_STAGE["consensus"] == "graphrag"
    assert NEXT_STAGE["graphrag"] == "compile"


def test_stage_ttls_present():
    for stage in ("extract", "chunk", "embed", "dedup", "cluster", "consensus", "graphrag", "compile"):
        assert STAGE_TTLS.get(stage, DEFAULT_TTL) > 0


# ---------------------------------------------------------------------------
# ORM models (plan §3.1 / §4.2)
# ---------------------------------------------------------------------------

def test_worker_model_columns():
    from models import Worker
    cols = {c.name for c in Worker.__table__.columns}
    for expected in ("worker_id", "name", "platform", "status", "capabilities",
                     "stages_enabled", "concurrency_max", "worker_token",
                     "last_heartbeat"):
        assert expected in cols, f"Worker missing column {expected}"


def test_task_queue_model_columns():
    from models import TaskQueue
    cols = {c.name for c in TaskQueue.__table__.columns}
    for expected in ("task_id", "stage", "scope_type", "scope_id", "priority",
                     "status", "attempts", "max_attempts", "leased_by",
                     "lease_token", "lease_expires_at", "next_run_at"):
        assert expected in cols, f"TaskQueue missing column {expected}"


def test_sync_state_uses_sync_key_pk():
    # Regression: SyncState previously declared table_oid as PK; init.sql uses sync_key.
    from models import SyncState
    pk = [c.name for c in SyncState.__table__.primary_key.columns]
    assert pk == ["sync_key"]


# ---------------------------------------------------------------------------
# Runner pure helpers (Appendix A)
# ---------------------------------------------------------------------------

def test_fallback_embedder_deterministic():
    from workers.runner import _FallbackEmbedder
    emb = _FallbackEmbedder()
    texts = ["hello world", "hello world", "another text"]
    dense, sparse = emb.encode(texts)
    assert len(dense) == 3 and len(sparse) == 3
    assert dense[0] == dense[1]          # same input → same vector
    assert dense[0] != dense[2]          # different input → different vector
    assert len(dense[0]) == 1024         # BGE-M3 dimension
    assert all(isinstance(x, float) for x in dense[0])


def test_hash_text():
    import hashlib
    from workers.runner import _hash_text
    import asyncio
    expected = hashlib.sha256(b"abc").hexdigest()
    assert asyncio.run(_hash_text("abc")) == expected


def test_to_list_to_dict():
    from workers.runner import _to_list, _to_dict
    assert _to_list([1, 2]) == [1.0, 2.0]
    class FakeVec:
        def tolist(self):
            return [0.5, 0.25]
    assert _to_list(FakeVec()) == [0.5, 0.25]
    assert _to_dict({"a": 1}) == {"a": 1.0}
    assert _to_dict(None) == {}
    assert _to_dict({}) == {}


def test_extract_text_markdown_passthrough():
    import asyncio
    from workers.runner import _extract_text
    md = b"# Title\n\nbody"
    assert asyncio.run(_extract_text(md, "text/markdown")) == "# Title\n\nbody"
    assert asyncio.run(_extract_text(b"plain", "text/plain")) == "plain"
    # Unknown binary-ish content still decodes leniently.
    s = asyncio.run(_extract_text("héllo".encode("utf-8"), "application/pdf"))
    assert s == "héllo"


def test_stage_handler_registry_covers_all_stages():
    from workers.runner import STAGE_HANDLERS
    for stage in ("discover", "extract", "chunk", "embed", "dedup",
                  "cluster", "consensus", "graphrag", "compile"):
        assert stage in STAGE_HANDLERS, f"missing handler for {stage}"


def test_load_config_defaults():
    import os
    from workers.runner import load_config
    for k in ("WORKER_ID", "STAGES_ENABLED", "EMBED_BATCH_SIZE", "MAX_CONCURRENT_TASKS"):
        os.environ.pop(k, None)
    cfg = load_config()
    assert cfg["name"]
    assert "embed" in cfg["stages"]
    assert cfg["embed_batch_size"] == 32
    assert cfg["max_concurrent"] == 1
