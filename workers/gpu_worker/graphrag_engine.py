#!/usr/bin/env python3
"""workers/gpu_worker/graphrag_engine.py — GraphRAG entity/community extraction (§8 Stage 7).

Uses LiteLLM to talk to the local vLLM/Ollama endpoint configured in LOCAL_LLM_API_BASE.
Falls back to no-op extraction when LiteLLM is unavailable.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Sequence

try:
    import litellm  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

from .db import get_pool


@dataclass(frozen=True)
class GraphExtract:
    entities: list[dict]
    relationships: list[dict]
    communities: list[dict]


def _model() -> str:
    return os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-14B-Instruct-AWQ")


def _base() -> str:
    return os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8000/v1")


async def _complete(prompt: str, max_tokens: int = 1024) -> str:
    if litellm is None:
        return ""
    try:
        resp = litellm.completion(
            model=f"openai/{_model()}",
            messages=[{"role": "user", "content": prompt}],
            api_base=_base(),
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return ""


async def extract_for_units(unit_texts: Sequence[str]) -> GraphExtract:
    if not unit_texts:
        return GraphExtract([], [], [])

    prompt = (
        "Extract entities, relationships, and communities from the following text. "
        "Return strict JSON with keys {\"entities\":[...],\"relationships\":[...],\"communities\":[...]}. "
        "Each entity: {name, entity_type, description, source_unit_ids:[...]}. "
        "Each relationship: {source_entity, target_entity, relationship_type, description}.\n\n"
        + "\n\n".join(f"[UNIT]\n{t[:2000]}" for t in list(unit_texts)[:10])
    )
    raw = await _complete(prompt, max_tokens=4096)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return GraphExtract([], [], [])

    return GraphExtract(
        entities=data.get("entities", []),
        relationships=data.get("relationships", []),
        communities=data.get("communities", []),
    )
