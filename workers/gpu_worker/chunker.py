#!/usr/bin/env python3
"""workers/gpu_worker/chunker.py — Heading-aware Markdown chunker for pgvector (§8 Stage 11)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class Chunk:
    file_path: str
    heading_path: list[str]
    chunk_index: int
    content: str
    lang: str


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def chunk_markdown(path: str, body: str, lang: str = "simple") -> list[Chunk]:
    headings = [(m.start(), m.group(2).strip(), m.group(1).count("#")) for m in _HEADING_RE.finditer(body)]
    if not headings:
        return [Chunk(file_path=path, heading_path=[], chunk_index=0, content=body.strip(), lang=lang)]

    out: list[Chunk] = []
    for idx, (start, title, level) in enumerate(headings):
        end = headings[idx + 1][0] if idx + 1 < len(headings) else len(body)
        hpath = [h for _, h, lvl in headings[: idx + 1] if lvl <= level]
        out.append(
            Chunk(
                file_path=path,
                heading_path=hpath[-3:],
                chunk_index=idx,
                content=body[start:end].strip(),
                lang=lang,
            )
        )
    return out
