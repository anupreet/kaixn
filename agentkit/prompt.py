"""Prompt composition.

System prompts are built from ordered, independent sections — static blocks
(identity, tools, guardrails) followed by dynamic per-turn context. Composition
is deliberately trivial: the value is in keeping each section single-purpose and
swappable, not in a clever assembler.
"""

from __future__ import annotations


def block(title: str, body: str) -> str:
    """One titled section, e.g. ``### IDENTITY\\n<body>``."""
    return f"### {title.strip().upper()}\n{body.strip()}"


def compose(*sections: str | None) -> str:
    """Join non-empty sections with blank lines, in the order given. Order is
    semantically load-bearing — put identity first and dynamic context last."""
    return "\n\n".join(s.strip() for s in sections if s and s.strip())
