#!/usr/bin/env python3
"""workers/gpu_worker/graphrag_engine.py - GraphRAG entity/community extraction."""

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
    import httpx as _httpx
except ImportError:
    _httpx = None

try:
    import litellm
except ImportError:
    litellm = None

from .db import get_pool

log = logging.getLogger("worker.graphrag")

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
# LLM completion
# ---------------------------------------------------------------------------

def _api_key() -> str:
    return (
        os.getenv("LITELLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("LOCAL_LLM_API_KEY")
    )


def _sync_complete(prompt: str, max_tokens: int) -> str:
    if _httpx is None:
        return ""
    api_key = _api_key()
    api_base = _base().rstrip("/")
    models = os.getenv(
        "GRAPHRAG_MODELS",
        "openrouter/nemotron-nano-omi,nvidia/nemotron-nano-omi,free",
    )
    model_list = [m.strip() for m in models.split(",") if m.strip()]

    for model in model_list:
        try:
            with _httpx.Client(timeout=180.0) as client:
                r = client.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "You are a JSON extraction assistant. "
                                    "Output ONLY a single valid JSON object. "
                                    "No markdown fences, no explanations, no text before or after."
                                ),
                            },
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

    for model in model_list:
        try:
            with _httpx.Client(timeout=180.0) as client:
                r = client.post(
                    f"{api_base}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": "Output ONLY valid JSON. Nothing else."},
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
    tokens = max_tokens or _max_tokens()
    return await asyncio.to_thread(_sync_complete, prompt, tokens)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_EXTRACT_PROMPT = """\
CRITICAL: Output ONLY a valid JSON object. No explanations, no analysis, no commentary.

Given the text below, extract:
- entities: notable people, organizations, concepts, locations, technologies, events
- relationships: connections between entities
- communities: groups of related entities

Output exactly this JSON structure:
{{"entities":[{{"name":"...","type":"person|org|concept|location|technology|event","desc":"short description"}}], "relationships":[{{"from":"...","to":"...","rel":"...","desc":"..."}}], "communities":[{{"title":"...","summary":"...","members":["..."]}}]}}

If there are no meaningful entities, return: {{"entities":[],"relationships":[],"communities":[]}}

Text:
{text}

JSON: """


# ---------------------------------------------------------------------------
# JSON parsing with multi-level fallback
# ---------------------------------------------------------------------------

def _parse_response(raw: str) -> dict | None:
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
    try:
        data = json.loads(cleaned.strip())
        if isinstance(data, dict) and ("entities" in data or "relationships" in data):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Find complete JSON objects via bracket matching
    for candidate in _find_json_objects(text):
        if isinstance(candidate, dict) and ("entities" in candidate or "relationships" in candidate):
            return candidate

    # 4. Handle {"{"entities prefix
    for idx in range(len(text)):
        if text[idx] == "{":
            end_idx = text.rfind("}", idx)
            if end_idx > idx:
                try:
                    data = json.loads(text[idx : end_idx + 1])
                    if isinstance(data, dict) and ("entities" in data or "relationships" in data):
                        return data
                except (json.JSONDecodeError, ValueError):
                    pass

    # 5. Regex extraction from pure reasoning text
    return _regex_extract(text)


def _find_json_objects(text: str) -> list[dict]:
    results = []
    i = 0
    while i < len(text):
        if text[i] == "{":
            depth = 0
            in_string = False
            escape = False
            for j in range(i, len(text)):
                c = text[j]
                if escape:
                    escape = False
                    continue
                if c == "\\":
                    escape = True
                    continue
                if c == '"':
                    in_string = not in_string
                    continue
                if in_string:
                    continue
                if c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[i : j + 1])
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
# Regex extraction — last resort for reasoning text
# ---------------------------------------------------------------------------

_STOP_WORDS = frozenset({
    "the", "this", "that", "it", "its", "and", "or", "but", "in", "on", "at",
    "to", "for", "of", "with", "by", "from", "as", "is", "are", "was", "were",
    "be", "been", "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "must", "shall", "can", "none",
    "n/a", "unknown", "given", "following", "below", "above", "results",
    "entities", "relationships", "communities", "extracted", "identified",
})

_ENTITY_TYPE_RE = re.compile(
    r"\b(person|org(?:ani[sz]ation)?|company|corporation|institution|agency|"
    r"government|ministry|department|concept|notion|theory|principle|"
    r"location|country|city|region|place|"
    r"technolog(?:y|ical)?|software|framework|library|tool|platform|"
    r"system|protocol|language|database|api|model|algorithm|"
    r"event|conference|meeting|workshop)\b",
    re.IGNORECASE,
)


def _normalize_etype(raw: str) -> str:
    for m in _ENTITY_TYPE_RE.finditer(raw.lower()):
        t = m.group(1).lower()
        if t == "person":
            return "person"
        if t.startswith("org") or t in (
            "company", "corporation", "institution", "agency",
            "government", "ministry", "department",
        ):
            return "org"
        if t.startswith("loc") or t in ("country", "city", "region", "place"):
            return "location"
        if t.startswith("tech") or t in (
            "software", "framework", "library", "tool", "platform",
            "system", "protocol", "language", "database", "api", "model", "algorithm",
        ):
            return "technology"
        if t in ("event", "conference", "meeting", "workshop"):
            return "event"
    return "concept"


def _guess_type_from_desc(text: str) -> str:
    t = text.lower()
    if any(w in t for w in ("company", "corporation", "firm", "enterprise", "business", "startup", "org")):
        return "org"
    if any(w in t for w in ("person", "researcher", "developer", "engineer", "scientist", "author", "founder")):
        return "person"
    if any(w in t for w in ("country", "city", "state", "region", "located in", "based in")):
        return "location"
    if any(w in t for w in (
        "software", "framework", "library", "tool", "platform", "system",
        "app", "technology", "technical", "api", "database", "protocol",
        "algorithm", "model", "language",
    )):
        return "technology"
    if any(w in t for w in ("conference", "event", "meeting", "workshop", "summit")):
        return "event"
    return "concept"


def _regex_extract(text: str) -> dict | None:
    """Last-resort: extract entities from reasoning text when JSON parsing fails.

    Three patterns:
    1. "Name" (type) — parenthetical type
    2. "Name" is a/an TYPE... — description
    3. - "Name" — desc or - **Name** — desc (bullets)
    """
    entities: list[dict] = []

    # Pattern 1: "Name" (type)
    for m in re.finditer(r'"([^"]{2,80})"\s*\((\w+)\)', text):
        name = m.group(1).strip()
        etype = _normalize_etype(m.group(2))
        if name and len(name) >= 2:
            entities.append({"name": name, "entity_type": etype, "description": ""})

    # Pattern 2: "Name" is a/an TYPE description
    for m in re.finditer(r'"([^"]{2,80})"\s+is\s+(?:a|an)\s+([^\n]{5,300})', text, re.IGNORECASE):
        name = m.group(1).strip()
        desc = m.group(2).strip()[:500]
        if name and len(name) >= 3:
            etype = "concept"
            type_m = _ENTITY_TYPE_RE.search(desc)
            if type_m:
                etype = _normalize_etype(type_m.group(1))
            else:
                etype = _guess_type_from_desc(desc)
            entities.append({"name": name, "entity_type": etype, "description": desc})

    # Pattern 3: bullet/numbered list items
    for m in re.finditer(
        r'(?:^|\n)\s*(?:\d+[.)\s]+|[-*•]\s+)"([^"]{2,80})"(?:\s*[-\u2013\u2014:,]\s*(.+))?'
        r"|(?:^|\n)\s*(?:\d+[.)\s]+|[-*•]\s+)\*\*([^*]{2,80})\*\*(?:\s*[-\u2013\u2014:,]\s*(.+))?",
        text,
        re.MULTILINE,
    ):
        name = (m.group(1) or m.group(3) or "").strip()
        desc = (m.group(2) or m.group(4) or "").strip()[:500]
        if name and len(name) >= 3:
            etype = _guess_type_from_desc(desc or name)
            entities.append({"name": name, "entity_type": etype, "description": desc})

    # Deduplicate
    seen: set[str] = set()
    unique: list[dict] = []
    for e in entities:
        key = e["name"].lower()
        if key not in seen and key not in _STOP_WORDS and len(key) >= 2:
            seen.add(key)
            unique.append(e)

    if not unique:
        return None

    return {"entities": unique, "relationships": [], "communities": []}


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_entities(entities: list) -> list[dict]:
    valid_types = {"person", "org", "concept", "location", "technology", "event"}
    out = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        name = (e.get("name") or "").strip()
        etype = (e.get("type") or e.get("entity_type") or "").strip().lower()
        if not name or not etype:
            continue
        if etype not in valid_types:
            etype = "concept"
        out.append({
            "name": name,
            "entity_type": etype,
            "description": (e.get("desc") or e.get("description") or "")[:500],
        })
    return out


def _validate_relationships(rels: list, entity_names: set[str]) -> list[dict]:
    out = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        src = (r.get("from") or r.get("source") or r.get("source_entity") or "").strip()
        tgt = (r.get("to") or r.get("target") or r.get("target_entity") or "").strip()
        rtype = (r.get("rel") or r.get("relationship_type") or "relates_to").strip().lower()
        if not src or not tgt:
            continue
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
    if name in known:
        return name
    lower = name.lower()
    for n in known:
        if n.lower() == lower:
            return n
    return None


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

async def extract_for_units(unit_texts: Sequence[str]) -> GraphExtract:
    if not unit_texts:
        return GraphExtract([], [], [])

    combined = "\n\n".join(t[:2000] for t in unit_texts if t.strip())
    if len(combined) > _MAX_INPUT_CHARS:
        combined = combined[:_MAX_INPUT_CHARS]

    if not combined.strip():
        return GraphExtract([], [], [])

    all_entities: list[dict] = []
    all_rels: list[dict] = []
    all_communities: list[dict] = []

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
                log.warning(
                    "graphrag JSON parse failed (attempt %d/%d), response starts: %s",
                    attempt, _MAX_ATTEMPTS, raw[:200],
                )
                continue

            entities = _validate_entities(data.get("entities", []))
            entity_names = {e["name"] for e in entities}
            rels = _validate_relationships(data.get("relationships", []), entity_names)
            communities_raw = data.get("communities", [])
            communities = []
            for c in communities_raw:
                if not isinstance(c, dict):
                    continue
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

            log.info(
                "graphrag chunk %d/%d: %d entities, %d rels, %d communities",
                chunks.index(chunk) + 1, len(chunks),
                len(entities), len(rels), len(communities),
            )
            break
        else:
            log.warning("graphrag extraction failed after %d attempts for chunk", _MAX_ATTEMPTS)

    # Deduplicate entities
    seen_names: set[str] = set()
    unique_entities = []
    for e in all_entities:
        key = e["name"].lower()
        if key not in seen_names:
            seen_names.add(key)
            unique_entities.append(e)

    # Deduplicate relationships
    seen_rels: set[tuple[str, str, str]] = set()
    unique_rels = []
    for r in all_rels:
        key = (r["source"].lower(), r["target"].lower(), r["relationship_type"])
        if key not in seen_rels:
            seen_rels.add(key)
            unique_rels.append(r)

    log.info(
        "graphrag total: %d entities, %d relationships, %d communities",
        len(unique_entities), len(unique_rels), len(all_communities),
    )

    return GraphExtract(entities=unique_entities, relationships=unique_rels, communities=all_communities)


def _split_text(text: str, max_chars: int) -> list[str]:
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
    pool = await get_pool()
    src_ids = source_unit_ids or []
    written = {"entities": 0, "relationships": 0, "communities": 0}

    async with pool.acquire() as conn:
        async with conn.transaction():
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
                    log.warning("failed to save relationship %s->%s: %s", r["source"], r["target"], e)

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
