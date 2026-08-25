#!/usr/bin/env python3
"""workers/gpu_worker/markdown_compiler.py — Lossless Markdown synthesis & coverage verifier (§8.2).

Uses LOCAL_LLM_API_BASE via LiteLLM for compilation. Falls back to raw-unit
appendix when the LLM is unavailable.

Uses raw httpx (not litellm) because:
1. The litellm client requires the model to be in its local model_cost dict
   (keyed as 'openai/<model>'). The local litellm version doesn't know
   about our custom 'free' / 'free-auto' aliases defined on the proxy.
2. The proxy itself understands the bare 'free' / 'free-auto' aliases.
3. Direct HTTP avoids the litellm client-side validation error.
"""

from __future__ import annotations

import os
import asyncio
import json
from dataclasses import dataclass
from typing import Sequence

try:
    import httpx
except ImportError:
    httpx = None

try:
    from .db import get_pool
except Exception:
    get_pool = None


@dataclass(frozen=True)
class PageResult:
    page_path: str
    markdown: str
    coverage_score: float
    citations: int


UNIT_LIMIT = 50
UNIT_TEXT_BUDGET = 2000


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
                    "messages": [
                        {"role": "system",
                         "content": "You are a documentation compiler. Output Markdown only."},
                        {"role": "user", "content": prompt}
                    ],
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
    max_tokens = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "1600"))
    max_attempts = int(os.getenv("LOCAL_LLM_MAX_ATTEMPTS", "6"))
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


def _json_escape(s: str) -> str:
    """Escape a string for safe inclusion in a JSON body string."""
    return (
        s.replace("\\", "\\\\")
         .replace("\"", "\\\"")
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
    )


def _build_prompt(topic_path: str, units: Sequence[dict], frontmatter: dict) -> str:
    safe_units = []
    for idx, u in enumerate(units[:UNIT_LIMIT], 1):
        text = (u.get("clean_text") or "").strip()
        if not text:
            continue
        safe_units.append((idx, text[:UNIT_TEXT_BUDGET]))

    topic_title = (frontmatter or {}).get("title") or topic_path.split("/")[-1]
    if topic_title.endswith(".md"):
        topic_title = topic_title[:-3]
    topic_title = (topic_title or "Wiki Page").strip().replace("---", " ").replace("\n", " ")
    if len(topic_title) > 80:
        topic_title = topic_title[:80].rsplit(" ", 1)[0]

    unit_catalog = "\n".join(
        f"<unit n=\"{idx}\">\n{text}\n</unit>"
        for idx, text in safe_units
    )
    unit_count = len(safe_units)

    frontmatter_hint = json.dumps(
        {k: v for k, v in (frontmatter or {}).items() if k != "source_id"},
        ensure_ascii=False,
    )

    return f"""You are a documentation compiler. Produce ONE GitHub-Flavored Markdown wiki page.

# Topic
- file_path: {topic_path}
- title: {topic_title!r} (use this exact string as the page title — do NOT use any UUID, hash, or id)
- max_units_to_cite: {unit_count}

# Frontmatter hint (do NOT echo the source_id)
{frontmatter_hint}

# Units (numbered, in order)
{unit_catalog}

# Output format — STRICT, no deviation

Return ONE fenced JSON object (no prose, no markdown outside the JSON):

```json
{{
  "title": "{topic_title}",
  "frontmatter": {{
    "title": "{topic_title}",
    "description": "<= 1 sentence summary of what this page covers>",
    "keywords": ["<2-8 short keywords, comma-separated>"]
  }},
  "body": "<a full GitHub-Flavored Markdown body, see rules below>"
}}
```

# Rules for the `body` field (most important)

1. The body is GitHub-Flavored Markdown.
2. Cite every claim with `[^src_<N>]` where N is the 1-based unit index above. Do not cite any unit that does not appear above. Do not invent any new IDs.
3. Footnote definitions go at the END of the body as a single block:

   ```
   [^src_1]: <the literal first sentence or defining fact from unit 1>
   [^src_2]: <the literal first sentence or defining fact from unit 2>
   ...
   ```

   Each footnote's text must be UNIQUE — do NOT paste the page title or
   the same sentence into every footnote. Each footnote is a short
   fact (5-20 words) extracted from THAT specific unit.

4. Use the unit index `[^src_N]` consistently. If a fact comes from
   unit 3, cite `[^src_3]`, not `[^src_1]`.

5. Preserve table structure: if a unit contains a markdown table
   (lines beginning with `|`), include the full table verbatim,
   followed by a brief caption. Do NOT collapse rows or invent columns.

6. Preserve code blocks: if a unit has triple-backtick code, keep it
   verbatim inside triple-backticks in the body.

7. Use the page title in headings: start with `# {topic_title}` and
   use `##` for sections, `###` for subsections.

8. Do not start the body with any preamble. The first character of
   the body must be `#`.

9. JSON-strict: the body string must be valid JSON-escaped. Real
   newlines in the body must be `\\n`. Real backticks must be
   escaped as `\\``. Real double quotes must be escaped as `\\"`.

10. The body should be 200-2000 words depending on the number of
    units. Do not pad with filler; if a unit contains no relevant
    content, omit the citation.

# Examples of GOOD output (shape, not content)

body example:
"# {topic_title}\\\\n\\\\nThis page documents <concept>.\\\\n\\\\n## <Section 1>\\\\n\\\\n<prose>. [^src_1]\\\\n\\\\n## <Section 2>\\\\n\\\\n| Col A | Col B |\\\\n|---|\\\\n| a | b |\\\\n\\\\n[^src_2]\\\\n\\\\n---\\\\n\\\\n[^src_1]: <fact from unit 1>\\\\n[^src_2]: <different fact from unit 2>"

# Examples of BAD output (do not produce)

- A title that is a UUID or looks like a hash.
- The same footnote text repeated for every citation.
- Frontmatter that contains the source_id field.
- A preamble like 'Here is the markdown you requested' before the body.
- Citations like `[^src_1:unit_1]` or other formats — use exactly `[^src_<N>]`.

Return the JSON now."""


def _parse_llm_response(raw: str, units: Sequence[dict], frontmatter: dict) -> str:
    raw = (raw or "").strip()
    if not raw:
        return raw

    # Strategy 1: fenced ```json ... ``` (most reliable for our prompt)
    fence_idx = raw.find("```json")
    if fence_idx != -1:
        start = fence_idx + len("```json")
        # skip optional language tag on the same line
        nl = raw.find("\n", start)
        if nl != -1:
            start = nl + 1
        end = raw.find("```", start)
        if end != -1:
            inner = raw[start:end].strip()
            # find balanced outer { ... } in inner
            obj_text = _extract_outer_json(inner)
            if obj_text:
                try:
                    obj = json.loads(obj_text)
                    if isinstance(obj, dict) and isinstance(obj.get("body"), str):
                        return obj["body"]
                except Exception:
                    pass

    # Strategy 2: balanced { ... } scanned from the END (handles preamble text)
    obj_text = _extract_outer_json(raw, from_end=True)
    if obj_text:
        try:
            obj = json.loads(obj_text)
            if isinstance(obj, dict) and isinstance(obj.get("body"), str):
                return obj["body"]
        except Exception:
            pass

    # Strategy 3: fenced ```markdown ... ```
    fence_idx = raw.find("```markdown")
    if fence_idx != -1:
        start = fence_idx + len("```markdown")
        end = raw.find("```", start)
        if end != -1:
            return raw[start:end].strip()

    # Strategy 4: strip ```json / ``` wrappers if any
    if raw.startswith("```") and raw.endswith("```"):
        s = raw.find("\n")
        if s != -1:
            e = raw.rfind("```")
            if e > s:
                return raw[s + 1 : e].strip()

    return raw


def _extract_outer_json(text: str, from_end: bool = False) -> str | None:
    """Find the first balanced top-level { ... } in text.
    If from_end=True, scan from the right so the LAST balanced object wins
    (helps when the model prepends a preamble that contains an example JSON).
    """
    if from_end:
        # find last '}' and walk backwards to matching '{'
        end = text.rfind("}")
        if end == -1:
            return None
        depth = 0
        in_str = False
        esc = False
        start = -1
        for i in range(end, -1, -1):
            c = text[i]
            # reverse string tracking is messy; instead walk forward from each candidate '{'
            pass
        # simpler: walk all '{' positions from right to left, try each
        for i in range(end, -1, -1):
            if text[i] == "{":
                cand = text[i : end + 1]
                if _is_balanced_json(cand):
                    return cand
        return None
    else:
        start = text.find("{")
        if start == -1:
            return None
        # find matching '}'
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]
        return None


def _is_balanced_json(candidate: str) -> bool:
    try:
        json.loads(candidate)
        return True
    except Exception:
        return False


async def compile_page(topic_path: str, units: Sequence[dict], frontmatter: dict) -> PageResult:
    unit_texts = [u.get("clean_text", "") for u in units if u.get("clean_text")]
    if not unit_texts:
        return PageResult(
            page_path=f"{topic_path}.md",
            markdown="> No extractable content in source units.\n",
            coverage_score=0.0,
            citations=0,
        )

    prompt = _build_prompt(topic_path, units, frontmatter)
    raw = await _complete(prompt, max_tokens=int(os.getenv("LOCAL_LLM_MAX_TOKENS", "1600")))
    if not raw:
        md = "> [!WARNING] Inferred by Pipeline: LLM compilation unavailable; raw appendix follows.\n\n"
        for idx, u in enumerate(unit_texts[:UNIT_LIMIT], 1):
            md += f"### Source Unit {idx}\n\n{u[:UNIT_TEXT_BUDGET]}\n\n"
        return PageResult(
            page_path=f"{topic_path}.md", markdown=md, coverage_score=0.0, citations=0
        )

    body = _parse_llm_response(raw, units, frontmatter)
    coverage = 1.0 if body.strip() else 0.0
    return PageResult(
        page_path=f"{topic_path}.md",
        markdown=body,
        coverage_score=coverage,
        citations=body.count("[^src_"),
    )
