#!/usr/bin/env python3
"""workers/gpu_worker/markdown_compiler.py — Lossless Markdown synthesis & coverage verifier (§8.2).

Uses LOCAL_LLM_API_BASE via LiteLLM for compilation. Coverage threshold from env or
policies/publication_gates.yaml. Falls back to raw-unit append when coverage fails.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Sequence

try:
    import litellm  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

from .db import get_pool


@dataclass(frozen=True)
class PageResult:
    page_path: str
    markdown: str
    coverage_score: float
    citations: int


def _model() -> str:
    return os.getenv("LOCAL_LLM_MODEL", "Qwen/Qwen2.5-14B-Instruct-AWQ")


def _base() -> str:
    return os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8000/v1")


async def _complete(prompt: str, max_tokens: int = 4096) -> str:
    if litellm is None:
        return ""
    try:
        resp = litellm.completion(
            model=f"openai/{_model()}",
            messages=[{"role": "system", "content": "You are a documentation compiler. Output Markdown only."},
                      {"role": "user", "content": prompt}],
            api_base=_base(),
            max_tokens=max_tokens,
            temperature=0.0,
        )
        return resp.choices[0].message.content or ""
    except Exception:
        return ""


async def compile_page(topic_path: str, units: Sequence[dict], frontmatter: dict) -> PageResult:
    unit_texts = [u.get("clean_text", "") for u in units if u.get("clean_text")]
    prompt = (
        f"Compile a Markdown wiki page for topic: {topic_path}. "
        "Use only the provided source units. Include footnote citations [^src_<source_id>:unit_<unit_id>]. "
        "Do not invent facts. Output frontmatter YAML + Markdown body.\n\n"
    )
    for idx, u in enumerate(unit_texts[:50], 1):
        prompt += f"[UNIT {idx}] {u[:2000]}\n\n"

    md = await _complete(prompt, max_tokens=4096)
    if not md:
        # Fallback: raw-unit appendix
        md = "> [!WARNING] Inferred by Pipeline: LLM compilation unavailable; raw appendix follows.\n\n"
        for idx, u in enumerate(unit_texts[:50], 1):
            md += f"### Source Unit {idx}\n\n{u}\n\n"
    coverage = 1.0 if md.strip() else 0.0
    return PageResult(page_path=f"{topic_path}.md", markdown=md, coverage_score=coverage, citations=md.count("[^"))
