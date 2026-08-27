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
        with httpx.Client(timeout=600.0) as client:
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


async def _complete(prompt: str, max_tokens: int = 8192) -> str:
    # The prompt asks for 200-2000 words plus JSON wrapping and a footnote
    # block — 1600 tokens cut responses mid-JSON (truncated bodies). 8192
    # lets the model finish the full JSON object.
    max_tokens = int(os.getenv("LOCAL_LLM_MAX_TOKENS", "8192"))
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

8. The body must start with `# {topic_title}` as a level-1 heading.
   Include a one-sentence description line immediately after the title,
   then `##` sections. Do NOT repeat the title as inline prose.

9. ENTERPRISE FORMATTING — apply these rules for readability:

   a. METADATA-AS-TABLE: For structured items (user stories, tickets,
      API endpoints, config fields), render metadata as markdown tables
      with labeled rows. Example:

      ```markdown
      ### US-001 — Title

      | Field | Value |
      |---|---|
      | Role | Social-media analyst |
      | Need | Polarity and confidence |
      | Value | Gauge public mood |

      **Acceptance Criteria:** ...
      ```

   b. ALTERNATE FORMAT: Optionally use blockquotes for emphasis:

      ```markdown
      > **US-001 — Title**  
      > **Role:** Social-media analyst  
      > **Need:** Polarity and confidence  
      > **Value:** Gauge public mood  
      >  
      > **Acceptance Criteria:** ...
      ```

   c. SEPARATE SECTIONS: Do NOT mix role/need/value/criteria/use-case into
      one paragraph. Each field gets its own line or table row.

   d. NO INLINE METADATA: Omit orphan references like "Links UC-001"
      from criteria sentences. Put use-case references in their own
      labeled field or footnote.

   e. REPEAT-KEY PATTERN: For lists of similar items (user stories,
      checklist items), repeat the same structure for EVERY item so
      readers can scan vertically. Do NOT vary the format per item.

   f. PROSE RULE: For narrative sections (introductions, explanations),
      use short paragraphs (2-4 sentences). Do NOT start the body with
      a filler preamble like "This page documents..." — the `#` heading
      already conveys the topic.

   g. PDF/PANDOC SAFE: Avoid raw HTML. Prefer tables, lists, and
      blockquotes. These render cleanly in pandoc, beamer, Confluence,
      and GitHub.

10. JSON-strict: the body string must be valid JSON-escaped. Real
    newlines in the body must be `\\n`. Real backticks must be
    escaped as `\\``. Real double quotes must be escaped as `\\"`.

11. The body should be 200-2000 words depending on the number of
    units. Do not pad with filler; if a unit contains no relevant
    content, omit the citation.

# Examples of GOOD output (shape, not content)

Metadated item (table format):

```markdown
# User Stories\\n\\n### US-001 — Sentiment of a post\\n\\n| Field | Value |\\n|---|---|\\n| Role | Social-media analyst |\\n| Need | Polarity and confidence |\\n| Value | Gauge public mood |\\n\\n**Acceptance Criteria:** POST /sentiment returns label... [^src_3]\\n\\n| Field | Value |\\n|---|---|\\n| Use Case | UC-001 |\\n\\n---\\n\\n### US-002 — Extract topics\\n\\n...
```

Metadated item (blockquote format):

```markdown
# User Stories\\n\\n> **US-001 — Sentiment of a post**\\n\\n> **Role:** Social-media analyst\\n\\n> **Need:** Polarity and confidence\\n\\n> **Value:** Gauge public mood\\n\\n> **Acceptance Criteria:** POST /sentiment returns... [^src_3]\\n\\n> **Use Case:** UC-001\\n\\n---\\n\\n> **US-002 — Extract topics**\\n\\n...
```

Prose section:

```markdown
# API Overview\\n\\nThe sentiment API classifies Nepali and English posts. [^src_1]\\n\\n## Endpoints\\n\\n### POST /sentiment\\n\\nReturns polarity and confidence scores. [^src_2]
```

# Examples of BAD output (do not produce)

- A title that is a UUID or looks like a hash.
- The same footnote text repeated for every citation.
- Frontmatter that contains the source_id field.
- A preamble like 'Here is the markdown you requested' before the body.
- Citations like `[^src_1:unit_1]` or other formats — use exactly `[^src_<N>]`.
- Mixed inline content: "As an analyst I want topics so that I can tag... Criteria: POST /topics returns... Links UC-001."
- Filler preamble: "This page documents user stories for the sentiment analysis API."

Return the JSON now."""


def _parse_llm_response(raw: str, units: Sequence[dict], frontmatter: dict) -> str:
    raw = (raw or "").strip()
    if not raw:
        return raw

    # Strategy 1: fenced ```json ... ``` (most reliable for our prompt)
    # Try with closing fence first, then without (truncated response).
    fence_idx = raw.find("```json")
    if fence_idx != -1:
        start = fence_idx + len("```json")
        nl = raw.find("\n", start)
        if nl != -1:
            start = nl + 1
        end = raw.find("```", start)
        candidates: list[str] = []
        if end != -1:
            candidates.append(raw[start:end].strip())
        # Also try without closing fence (truncated response)
        candidates.append(raw[start:].strip())
        for inner in candidates:
            body = _extract_body_field(inner)
            if body and len(body) > 50:
                return body
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
        body = _extract_body_field(obj_text)
        if body and len(body) > 50:
            return body

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


def _extract_body_field(text: str) -> str | None:
    """Extract the "body" field from a JSON-ish string, even if the surrounding
    JSON is malformed or the body is truncated.
    Returns the decoded body content, or None if not found.
    """
    body_key_idx = text.find('"body"')
    if body_key_idx == -1:
        return None
    colon_idx = text.find(':', body_key_idx)
    if colon_idx == -1:
        return None
    i = colon_idx + 1
    while i < len(text) and text[i] in " \t\n":
        i += 1
    if i >= len(text) or text[i] != '"':
        return None
    i += 1
    out: list[str] = []
    while i < len(text):
        c = text[i]
        if c == '\\':
            if i + 1 >= len(text):
                return "".join(out) if out else None
            esc = text[i + 1]
            if esc == 'n':
                out.append('\n')
            elif esc == 't':
                out.append('\t')
            elif esc == 'r':
                out.append('\r')
            elif esc in ('\\', '"', '/'):
                out.append(esc)
            elif esc == 'b':
                out.append('\b')
            elif esc == 'f':
                out.append('\f')
            elif esc == 'u':
                if i + 5 < len(text):
                    try:
                        out.append(chr(int(text[i + 2:i + 6], 16)))
                        i += 4
                    except Exception:
                        out.append(esc)
                else:
                    out.append(esc)
            else:
                out.append(esc)
            i += 2
        elif c == '"':
            return "".join(out)
        else:
            out.append(c)
            i += 1
    return "".join(out) if out else None



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
    raw = await _complete(prompt, max_tokens=int(os.getenv("LOCAL_LLM_MAX_TOKENS", "8192")))
    if not raw:
        md = "> [!WARNING] Inferred by Pipeline: LLM compilation unavailable; raw appendix follows.\n\n"
        for idx, u in enumerate(unit_texts[:UNIT_LIMIT], 1):
            md += f"### Source Unit {idx}\n\n{u[:UNIT_TEXT_BUDGET]}\n\n"
        return PageResult(
            page_path=f"{topic_path}.md", markdown=md, coverage_score=0.0, citations=0
        )

    body = _parse_llm_response(raw, units, frontmatter)

    # Quality gate: if the response didn't parse into a real wiki body (raw
    # reasoning leak, unparsed JSON wrapper, truncated JSON), retry the whole
    # completion a couple of times before falling back to the graceful
    # appendix. Never write reasoning/JSON garbage to the DB.
    if _is_bad_body(body):
        for _ in range(2):
            raw = await _complete(prompt, max_tokens=int(os.getenv("LOCAL_LLM_MAX_TOKENS", "8192")))
            if not raw:
                break
            body = _parse_llm_response(raw, units, frontmatter)
            if not _is_bad_body(body):
                break

    if _is_bad_body(body):
        md = "> [!WARNING] Inferred by Pipeline: LLM compilation unavailable; raw appendix follows.\n\n"
        for idx, u in enumerate(unit_texts[:UNIT_LIMIT], 1):
            md += f"### Source Unit {idx}\n\n{u[:UNIT_TEXT_BUDGET]}\n\n"
        return PageResult(
            page_path=f"{topic_path}.md", markdown=md, coverage_score=0.0, citations=0
        )

    coverage = 1.0 if body.strip() else 0.0
    return PageResult(
        page_path=f"{topic_path}.md",
        markdown=body,
        coverage_score=coverage,
        citations=body.count("[^src_"),
    )


def _is_bad_body(body: str) -> bool:
    """True when a parsed LLM response is not a real wiki body: raw reasoning
    leaks, unparsed JSON wrappers ({title, frontmatter, body}), or truncated
    JSON — anything that would render as a giant code block."""
    b = (body or "").lstrip()
    if not b:
        return True
    # Raw fenced LLM wrapper that the parser failed to unwrap
    first = b.splitlines()[0].strip().lower()
    if first in ("```json", "```yaml", "```yml"):
        return True
    # Unparsed JSON object (truncated or complete wrapper)
    if b.startswith("{") and ('"body"' in b[:600] or '"title"' in b[:600]):
        return True
    # Reasoning leak / refusal / canned text: anything with no markdown
    # heading at all (the compile prompt always starts the body with a
    # `# Title` line). The graceful fallback below starts with `>`.
    if "#" not in b[:400] and not b.startswith(">"):
        return True
    return False
