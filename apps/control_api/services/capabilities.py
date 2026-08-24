#!/usr/bin/env python3
"""apps/control_api/services/capabilities.py — Capability-aware scheduling (plan §6).

Determines whether a worker's advertised `capabilities` satisfy the hardware
requirements of a stage. The hard gates are:

  * embed     — GPU present (or explicit CPU-embed / model opt-in)
  * cluster   — fat memory / many cores (or explicit opt-in)
  * consensus — an LLM endpoint (or explicit opt-in)

Everything else (extract/chunk/dedup/discover/graphrag/compile) runs anywhere.
A worker that opts out via `stages_enabled` is filtered before this gate.
"""

from __future__ import annotations


def stage_eligible(capabilities: dict, stage: str) -> bool:
    caps = capabilities or {}
    stage = (stage or "").lower()

    if stage == "embed":
        gpu = (caps.get("gpu") or {}).get("present", False)
        allow_cpu = bool(caps.get("embed_allow_cpu", False))
        models = caps.get("models") or []
        # A worker with any advertised model or an explicit CPU-embed flag may
        # embed; the scheduler never guesses (§2.1).
        return bool(gpu) or allow_cpu or bool(models)

    if stage == "cluster":
        # Relaxed for VPS: allow 6-core/12GB workers (was 32GB/8 cores)
        # Original gate was mem >=32768 or cores >=8; keep allow_cluster override
        if bool(caps.get("allow_cluster", False)):
            return True
        mem = int((caps.get("memory") or {}).get("total_mb", 0) or 0)
        cores = int((caps.get("cpu") or {}).get("cores", 0) or 0)
        # Lowered threshold so current 12GB/6-core worker can claim
        return mem >= 8192 or cores >= 4 or bool(caps.get("models"))

    if stage in ("consensus", "claims"):
        if bool(caps.get("allow_llm", False)):
            return True
        llm = caps.get("llm") or {}
        # Also eligible if worker has any model or embed_allow_cpu (i.e., can call LLM via proxy)
        if llm.get("endpoint"):
            return True
        # Relaxed: allow any worker with models or CPU embed to run consensus via remote LLM
        return bool(caps.get("models")) or bool(caps.get("embed_allow_cpu"))
    return True


def score_worker(capabilities: dict, stage: str, running: int, concurrency_max: int,
                 affinity: int = 0) -> float:
    """Load-aware ranking (plan §6.3). Eligibility is a hard gate elsewhere."""
    cap_ok = 1.0 if stage_eligible(capabilities, stage) else 0.0
    load_term = 1.0 - (running / max(concurrency_max, 1))
    return 100.0 * cap_ok + 30.0 * load_term + 10.0 * affinity
