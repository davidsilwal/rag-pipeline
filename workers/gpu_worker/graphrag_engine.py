#!/usr/bin/env python3
"""workers/gpu_worker/graphrag_engine.py — GraphRAG entity/community extraction (§8 Stage 7).

Uses LiteLLM to talk to the local vLLM/Ollama endpoint configured in LOCAL_LLM_API_BASE.
Falls back to no-op extraction when LiteLLM is unavailable.

Issues fixed (2026-08-27):
  - litellm.completion() is sync → wrapped in asyncio.to_thread
  - Prompt was a single vague sentence → structured multi-role extraction prompt
  - Exceptions swallowed silently → log warnings and retry on bad JSON
  - Results never persisted → upsert into graphrag_entities/relationships/communities
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Sequence

try:
    import httpx as _httpx  # type: ignore[import-untyped]
except ImportError:
    _httpx = None  # type: ignore[assignment]

try:
    import litellm  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    litellm = None  # type: ignore[assignment]

from .db import get_pool

log = logging.getLogger("worker.graphrag")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_MAX_ATTEMPTS = int(os.getenv("GRAPHRAG_MAX_ATTEMPTS", "3"))
_MAX_INPUT_CHARS = int(os.getenv("GRAPHRAG_MAX_INPUT_CHARS", "50000"))
_BATCH_SIZE = int(os.getenv("GRAPHRAG_BATCH_SIZE", "5"))


@dataclass(frozen=True)
class GraphExtract:
    entities: list[dict]
    relationships: list[dict]
    communities: list[dict]


def _model() -> str:
    return os.getenv("LOCAL_LLM_MODEL", "free")


def _base() -> str:
    return os.getenv("LOCAL_LLM_API_BASE", "http://127.0.0.1:8000/v1")


def _max_tokens() -> int:
    return int(os.getenv("GRAPHRAG_MAX_TOKENS", "4096"))


# ---------------------------------------------------------------------------
# LLM completion (async-safe)
# ---------------------------------------------------------------------------

def _api_key() -> str:
    return (
        os.getenv("LITELLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
    )


def _sync_complete(prompt: str, max_tokens: int) -> str:
    """Synchronous LLM call via httpx — must run inside asyncio.to_thread.

    Uses direct httpx calls (not litellm) so we can pass response_format
    for guaranteed JSON output.  Tries models in priority order.
    """
    if _httpx is None:
        return ""
    api_key = _api_key()
    api_base = _base().rstrip('/')
    # Models in priority order — first one that works wins
    models = os.getenv("GRAPHRAG_MODELS",
                       "openrouter/nemotron-nano-omni,nvidia/nemotron-nano-omni,free")
    model_list = [m.strip() for m in models.split(",") if m.strip()]

    # First try with response_format=json_object for structured output
    for model in model_list:
        try:
            with _httpx.Client(timeout=180.0) as client:
                r = client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system",
                             "content": "You are a JSON extraction assistant. "
                                        "Output ONLY a single valid JSON object. "
                                        "No markdown fences, no explanations, no text before or after."},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.0,
                        "response_format": {"type": "json_object"},
                    },
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content
        except Exception as e:
            log.debug("graphrag model %s (json_mode) failed: %s", model, e)
            continue

    # Fallback: plain call without response_format
    for model in model_list:
        try:
            with _httpx.Client(timeout=180.0) as client:
                r = client.post(
                    f"{api_base}/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}",
                             "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system",
                             "content": "Output ONLY valid JSON. Nothing else."},
                            {"role": "user", "content": prompt},
                        ],
                        "max_tokens": max_tokens,
                        "temperature": 0.0,
                    },
                )
                r.raise_for_status()
                content = r.json()["choices"][0]["message"]["content"]
                if content and content.strip():
                    return content
        except Exception as e:
            log.warning("graphrag model %s failed: %s", model, e)
            continue

    return ""


async def _complete(prompt: str, max_tokens: int | None = None) -> str:
    """Async wrapper around the sync litellm call."""
    tokens = max_tokens or _max_tokens()
    return await asyncio.to_thread(_sync_complete, prompt, tokens)


# ---------------------------------------------------------------------------
# Prompt — structured extraction with explicit output format
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
CRITICAL: Output ONLY a valid JSON object. No explanations, no analysis, no commentary.

Given the text below, extract:
- entities: notable people, organizations, concepts, locations, technologies, events
- relationships: connections between entities
- communities: groups of related entities

Output exactly this JSON structure:
{{"entities":[{{"name":"...","type":"person|org|concept|location|technology|event","desc":"short description"}}],"relationships":[{{"from":"...","to":"...","rel":"...","desc":"..."}}],"communities":[{{"title":"...","summary":"...","members":["..."]}}]}}

If there are no meaningful entities, return: {{"entities":[],"relationships":[],"communities":[]}}

Text:
{text}

JSON: """


def _parse_response(raw: str) -> dict | None:
    """Parse LLM JSON response, aggressively extracting JSON from any text.

    Handles: raw JSON, markdown-fenced JSON, JSON buried in reasoning text,
    and partial extraction where the model explains before outputting.
    """
    text = (raw or "").strip()
    if not text:
        return None

    # 1. Direct parse
    try:
        data = json.loads(text)
        if isinstance(data, dict) and ("entities" in data or "relationships" in data):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Strip markdown code fences
    cleaned = re.sub(r"^```(?:json)?\s*\n?", "", text)
    cleaned = re.sub(r"\n?```\s*$", "", cleaned)
    cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        if isinstance(data, dict) and ("entities" in data or "relationships" in data):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Find the last complete JSON object with entities/relationships keys
    #    (often the model explains first, then outputs JSON at the end)
    for candidate in _find_json_objects(text):
        if isinstance(candidate, dict) and ("entities" in candidate or "relationships" in candidate):
            return candidate

    # 4. Handle common model output: {\"{\"entities\" (JSON prefixed with extra chars)
    #    Try progressively: from second { onward, find a valid JSON object.
    for idx in range(len(text)):
        if text[idx] == '{':
            # Try parsing from this { to the last }
            end_idx = text.rfind('}', idx)
            if end_idx > idx:
                try:
                    data = json.loads(text[idx:end_idx + 1])
                    if isinstance(data, dict) and ('entities' in data or 'relationships' in data):
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass
    return None


def _find_json_objects(text: str) -> list[dict]:
    """Find all complete JSON objects in text using bracket matching."""
    results = []
    i = 0
    while i < len(text):
        if text[i] == '{':
            depth = 0
            in_string = False
            escape = False
            for j in range(i, len(text)):
                c = text[j]
                if escape:
                    escape = False
                    continue
                if c == '\\':
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == '{':
                    depth += 1
                elif c == '}':
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[i:j+1])
                            if isinstance(obj, dict):
                                results.append(obj)
                        except (json.JSONDecodeError, ValueError):
                            pass
                        i = j + 1
                        break
            else:
                i += 1
        else:
            i += 1
    return results


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def _validate_entities(entities: list[dict]) -> list[dict]:
    """Keep only well-formed entities."""
    valid_types = {"person", "org", "concept", "location", "technology", "event"}
    out = []
    for e in entities:
        name = (e.get("name") or "").strip()
        etype = (e.get("type") or e.get("entity_type") or "").strip().lower()
        if not name or not etype:
            continue
        # Normalize type
        if etype not in valid_types:
            etype = "concept"
        out.append({
            "name": name,
            "entity_type": etype,
            "description": (e.get("desc") or e.get("description") or "")[:500],
        })
    return out


def _validate_relationships(rels: list[dict], entity_names: set[str]) -> list[dict]:
    """Keep only relationships where both endpoints are known entities."""
    out = []
    for r in rels:
        src = (r.get("from") or r.get("source") or r.get("source_entity") or "").strip()
        tgt = (r.get("to") or r.get("target") or r.get("target_entity") or "").strip()
        rtype = (r.get("rel") or r.get("relationship_type") or "relates_to").strip().lower()
        if not src or not tgt:
            continue
        # Only keep relationships between extracted entities (fuzzy match)
        src_match = _find_entity_name(src, entity_names)
        tgt_match = _find_entity_name(tgt, entity_names)
        if not src_match or not tgt_match:
            continue
        out.append({
            "source": src_match,
            "target": tgt_match,
            "relationship_type": rtype,
            "description": (r.get("desc") or r.get("description") or "")[:500],
        })
    return out


def _find_entity_name(name: str, known: set[str]) -> str | None:
    """Exact or case-insensitive match against known entity names."""
    if name in known:
        return name
    lower = name.lower()
    for n in known:
        if n.lower() == lower:
            return n
    return None


async def extract_for_units(unit_texts: Sequence[str]) -> GraphExtract:
    """Extract entities, relationships, and communities from unit texts.

    Processes texts in batches to stay within LLM context limits, retries
    on JSON parse failures, and logs extraction quality.
    """
    if not unit_texts:
        return GraphExtract([], [], [])

    # Combine and truncate texts to fit within context window
    combined = "\n\n".join(t[:2000] for t in unit_texts if t.strip())
    if len(combined) > _MAX_INPUT_CHARS:
        combined = combined[:_MAX_INPUT_CHARS]

    if not combined.strip():
        return GraphExtract([], [], [])

    all_entities: list[dict] = []
    all_rels: list[dict] = []
    all_communities: list[dict] = []

    # Split into chunks if very large
    chunks = [combined] if len(combined) <= _MAX_INPUT_CHARS else _split_text(combined, _MAX_INPUT_CHARS)

    for chunk in chunks:
        prompt = _EXTRACT_PROMPT.format(text=chunk)

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            raw = await _complete(prompt)
            if not raw:
                log.warning("graphrag extraction returned empty (attempt %d/%d)", attempt, _MAX_ATTEMPTS)
                continue

            data = _parse_response(raw)
            if data is None:
                log.warning("graphrag JSON parse failed (attempt %d/%d), response starts: %s",
                            attempt, _MAX_ATTEMPTS, raw[:200])
                continue

            entities = _validate_entities(data.get("entities", []))
            entity_names = {e["name"] for e in entities}
            rels = _validate_relationships(data.get("relationships", []), entity_names)
            communities_raw = data.get("communities", [])
            communities = []
            for c in communities_raw:
                title = (c.get("title") or "").strip()
                if not title:
                    continue
                communities.append({
                    "title": title,
                    "summary": (c.get("summary") or "")[:500],
                    "member_entities": c.get("members") or c.get("member_entities") or [],
                })

            all_entities.extend(entities)
            all_rels.extend(rels)
            all_communities.extend(communities)

            log.info("graphrag chunk %d/%d: %d entities, %d rels, %d communities",
                     chunks.index(chunk) + 1, len(chunks), len(entities), len(rels), len(communities))
            break
        else:
            log.warning("graphrag extraction failed after %d attempts for chunk", _MAX_ATTEMPTS)

    # Deduplicate entities by name
    seen_names: set[str] = set()
    unique_entities = []
    for e in all_entities:
        key = e["name"].lower()
        if key not in seen_names:
            seen_names.add(key)
            unique_entities.append(e)

    # Deduplicate relationships by (source, target, type)
    seen_rels: set[tuple[str, str, str]] = set()
    unique_rels = []
    for r in all_rels:
        key = (r["source"].lower(), r["target"].lower(), r["relationship_type"])
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(r)

    log.info("graphrag total: %d entities, %d relationships, %d communities",
             len(unique_entities), len(unique_rels), len(all_communities))

    return GraphExtract(entities=unique_entities, relationships=unique_rels, communities=all_communities)


def _split_text(text: str, max_chars: int) -> list[str]:
    """Split text at paragraph boundaries."""
    chunks = []
    current = ""
    for para in text.split("\n\n"):
        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append(current)
            current = para
        else:
            current = current + "\n\n" + para if current else para
    if current.strip():
        chunks.append(current)
    return chunks or [text[:max_chars]]


# ---------------------------------------------------------------------------
# Database persistence
# ---------------------------------------------------------------------------

async def save_graphrag_results(
    extract: GraphExtract,
    source_unit_ids: list[str] | None = None,
) -> dict:
    """Persist extracted entities, relationships, and communities to the DB.

    Uses UPSERT so re-runs are idempotent. Returns counts of what was written.
    """
    pool = await get_pool()
    src_ids = source_unit_ids or []
    written = {"entities": 0, "relationships": 0, "communities": 0}

    async with pool.acquire() as conn:
        async with conn.transaction():
            # -- Entities --
            entity_id_map: dict[str, str] = {}
            for e in extract.entities:
                name = e["name"]
                etype = e["entity_type"]
                row = await conn.fetchrow(
                    """
                    INSERT INTO graphrag_entities (name, entity_type, description, source_unit_ids, frequency)
                    VALUES ($1, $2, $3, $4, 1)
                    ON CONFLICT (name, entity_type) DO UPDATE SET
                        description = EXCLUDED.description,
                        source_unit_ids = graphrag_entities.source_unit_ids || EXCLUDED.source_unit_ids,
                        frequency = graphrag_entities.frequency + 1
                    RETURNING entity_id
                    """,
                    name, etype, e.get("description", ""), src_ids,
                )
                if row:
                    entity_id_map[name.lower()] = str(row["entity_id"])
                    written["entities"] += 1

            # -- Relationships --
            for r in extract.relationships:
                src_id = entity_id_map.get(r["source"].lower())
                tgt_id = entity_id_map.get(r["target"].lower())
                if not src_id or not tgt_id:
                    continue
                try:
                    await conn.execute(
                        """
                        INSERT INTO graphrag_relationships
                            (source_entity_id, target_entity_id, relationship_type, description, source_unit_ids)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (source_entity_id, target_entity_id, relationship_type) DO UPDATE SET
                            description = EXCLUDED.description,
                            source_unit_ids = graphrag_relationships.source_unit_ids || EXCLUDED.source_unit_ids,
                            weight = graphrag_relationships.weight + 0.1
                        """,
                        uuid.UUID(src_id), uuid.UUID(tgt_id),
                        r["relationship_type"], r.get("description", ""), src_ids,
                    )
                    written["relationships"] += 1
                except Exception as e:
                    log.warning("failed to save relationship %s→%s: %s", r["source"], r["target"], e)

            # -- Communities --
            for c in extract.communities:
                title = (c.get("title") or "")[:255]
                summary = c.get("summary") or ""
                members = c.get("member_entities") or []
                if not title:
                    continue
                await conn.execute(
                    """
                    INSERT INTO graphrag_communities (level, title, summary, findings, member_entities)
                    VALUES (0, $1, $2, '[]'::jsonb, $3)
                    ON CONFLICT DO NOTHING
                    """,
                    title, summary, members,
                )
                written["communities"] += 1

    log.info("graphrag saved: %s", written)
    return written
