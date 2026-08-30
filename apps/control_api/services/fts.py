#!/usr/bin/env python3
"""apps/control_api/services/fts.py — shared full-text-search query helpers.

``websearch_to_tsquery('simple', q)`` ANDs every whitespace-separated token,
so a natural-language question like "what is deployment" becomes
``what & is & deployment`` and matches nothing in a small corpus slice even
when the meaningful term is common.  These helpers strip filler/stop words
so the remaining meaningful terms drive retrieval.
"""

from __future__ import annotations

import re

# English stop words + question filler that add no retrieval signal.
# Kept small on purpose: over-stripping can hurt quoted/numeric searches.
_STOPWORDS = frozenset(
    """
    a an and are as at be but by for from has have how i in is it its
    of on or that the this to was were what when where which who will
    with would you your we they them their do does did not no can could
    should may might must about into over under than then so just very
    """.split()
)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def meaningful_terms(query: str) -> list[str]:
    """Split a query into lowercase alphanumeric tokens, dropping stop words.

    Digits are kept (they often matter: IDs, versions, dates).  Terms are
    returned in original order, deduplicated.
    """
    seen: list[str] = []
    for tok in _TOKEN_RE.findall(query.lower()):
        if tok in _STOPWORDS:
            continue
        if tok not in seen:
            seen.append(tok)
    return seen


def fts_query(query: str) -> str:
    """Return the query text to feed ``websearch_to_tsquery('simple', ...)``.

    Falls back to the raw query when nothing meaningful remains, so empty or
    stop-word-only inputs still behave like the original ``websearch`` call.
    """
    terms = meaningful_terms(query)
    if not terms:
        return query
    return " ".join(terms)
