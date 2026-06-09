"""Pure-Python similarity helpers — no numpy dependency for the POC."""

from __future__ import annotations

import math
import re

_WORD = re.compile(r"[a-z0-9]+")

# Small stop list so lexical similarity reflects content, not glue words.
_STOP = {
    "a", "an", "the", "and", "or", "but", "to", "of", "in", "on", "for",
    "is", "are", "be", "we", "our", "must", "should", "shall", "will",
    "that", "this", "it", "as", "by", "with", "at", "from", "all",
}


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP}


def jaccard(a: str, b: str) -> float:
    """Token-set Jaccard — a cheap, embedding-free dedup signal."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
