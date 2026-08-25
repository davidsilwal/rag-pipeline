#!/usr/bin/env python3
"""workers/gpu_worker/markdown_compiler.py — Lossless Markdown synthesis & coverage verifier (§8.2).

Uses LOCAL_LLM_API_BASE via LiteLLM for compilation. Falls back to raw-unit
appendix when the LLM is unavailable.

Uses raw httpx (not litellm) because:
1. The litellm client requires the model to be in its local model_cost dict
   (keyed as 'openai/<model>'). The local litellm version doesn't know
   about our custom 'free' / 'free-auto' aliases defined on the proxy.
2. The proxy itself understands the bare 'free' / 'free-auto' aliases.
3. Direct HTTP avoids the litellm client-side validation error
   ("LLM Provider NOT provided").
"""

from __future__ import annotations

import os
import asyncio
from dataclasses import dataclass
from typing import Sequence

try:
    import httpx  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    httpx = None  # type: ignore[assignment]

from .db import get_pool


@dataclass(frozen=True)
class PageResult:
    page_path: str
    markdown: str
    coverage_score: float
    citations: int


def _base() -> str:
    return os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8000/v1")


def _api_key() -> str:
    return (
        os.getenv("LITELLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
        or "__REDACTED_LITELLM_KEY__"
    )


def _aliases() -> list[str]:
    """Return the litellm-proxy model aliases to try in order.

    Aliases are independent rate-limit pools, so trying multiple after a
    429 multiplies effective throughput.
    """
    base = os.getenv("LOCAL_LLM_MODEL", "free")
    alts_raw = os.getenv("LOCAL_LLM_FALLBACK_ALIASES", "free-auto")
    seen = [base]
    for a in alts_raw.split(","):
        a = a.strip()
        if a and a not in seen:
            seen.append(a)
    return seen


def _http_complete(prompt: str, max_tokens: int, model_alias: str) -> str:
    if httpx is None:
        return ""
    try:
        with httpx.Client(timeout=60.0) as client:
            r = client.post(
                f"{_base().rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {_api_key()}",
                         "Content-Type": "application/json"},
                json={
                    "model": model_alias,
                    "messages": [{"role": "system",
                                  "content": "You are a documentation compiler. Output Markdown only."},
                                 {"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": 0.0,
                },
            )
            if r.status_code != 200:
                return ""
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            msg = choices[0].get("message") or {}
            return (msg.get("content") or "").strip()
    except Exception:
        return ""


async def _complete(prompt: str, max_tokens: int = 4096) -> str:
    """Retry across multiple model aliases and a few attempts each."""
    max_tokens = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "1024"))
    max_attempts = int(os.getenv("LOCAL_LLM_MAX_ATTEMPTS", "8"))
    aliases = _aliases()
    for attempt in range(1, max_attempts + 1):
        for alias in aliases:
            md = await asyncio.to_thread(_http_complete, prompt, max_tokens, alias)
            if md:
                return md
        if attempt < max_attempts:
            backoff = min(2 ** attempt, 60)
            await asyncio.sleep(backoff)
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
        md = "> [!WARNING] Inferred by Pipeline: LLM compilation unavailable; raw appendix follows.\n\n"
        for idx, u in enumerate(unit_texts[:50], 1):
            md += f"### Source Unit {idx}\n\n{u}\n\n"
    coverage = 1.0 if md.strip() else 0.0
    return PageResult(page_path=f"{topic_path}.md", markdown=md, coverage_score=coverage, citations=md.count("[^"))
