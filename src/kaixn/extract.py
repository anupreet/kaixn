"""Norm extraction — mining candidate principles/decisions from existing text.

Used by bootstrap (`connect_repo`). Every candidate carries an **evidence span**
(the source text it came from) — the provenance that OpenJarvis's extraction
lacked. Heuristic extractor runs offline; Anthropic extractor is the real one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from kaixn.types import NormCandidate


@dataclass(slots=True)
class Extracted:
    candidate: NormCandidate
    evidence: str
    source: str = ""
    confidence: float = 1.0           # extractor's certainty (drives the queue)
    flags: list[str] = field(default_factory=list)  # e.g. 'classification', 'domain', 'binding'


class NormExtractor(Protocol):
    def extract(self, text: str, *, source: str = "") -> list[Extracted]: ...


_PRINCIPLE = re.compile(r"\b(must|must not|never|always|should|shall|do not|don'?t|ensure|require[sd]?)\b", re.I)
_DECISION = re.compile(r"\b(we use|we chose|we picked|standardi[sz]e on|decided to|adopt(?:ed)?|migrat(?:e|ed) to)\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")

_DOMAIN_HINTS = {
    "technical": ("api", "database", "latency", "thread", "deploy", "cache",
                  "service", "schema", "auth", "security", "queue", "endpoint"),
    "ux": ("ux", "accessib", "screen reader", "focus", "keyboard", "animation",
           "undo", "click", "tap", "interaction"),
    "product_design": ("layout", "spacing", "typography", "visual", "color",
                       "brand", "component", "design system"),
    "product": ("user", "customer", "pricing", "onboarding", "feature",
                "retention", "growth", "billing", "subscription"),
}


def _infer_domain(text: str) -> tuple[str, int]:
    low = text.lower()
    best, best_hits = "technical", 0
    for domain, hints in _DOMAIN_HINTS.items():
        hits = sum(h in low for h in hints)
        if hits > best_hits:
            best, best_hits = domain, hits
    return best, best_hits


def _strip_markdown(text: str) -> str:
    """Drop code fences, table rows, and headings so we extract prose, not
    structure. This is what separates real candidates from doc noise."""
    out: list[str] = []
    in_fence = False
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or s.startswith(("#", "|", ">")) or "|" in s:
            continue
        out.append(ln)
    return "\n".join(out)


def _looks_like_prose(s: str) -> bool:
    """Reject fragments: must be a real sentence, not a list-item fragment,
    link, or code-laden snippet."""
    if len(s.split()) < 5 or not s[:1].isalpha():
        return False
    if any(t in s for t in ("`", "§", "](", "->", "→", "::", "**")):
        return False
    return s.rstrip().endswith((".", "!", "?"))


class HeuristicExtractor:
    """Offline: pull normative *prose* sentences (must/never/always…) as
    principles and explicit choices (we use X / adopted Y) as decisions. Strips
    markdown structure first. Still imprecise — the LLM extractor is the real
    path — but candidates land as 'proposed' for human curation."""

    def extract(self, text: str, *, source: str = "") -> list[Extracted]:
        out: list[Extracted] = []
        for raw in _SENT.split(_strip_markdown(text)):
            s = re.sub(r"\s+", " ", raw.strip(" -*#\t")).strip()
            if not _looks_like_prose(s):
                continue
            is_decision = bool(_DECISION.search(s))
            is_principle = bool(_PRINCIPLE.search(s))
            if not (is_decision or is_principle):
                continue
            kind = "decision" if is_decision and not is_principle else "principle"
            domain, hits = _infer_domain(s)
            flags = []
            if is_decision and is_principle:
                flags.append("classification")   # both markers → uncertain kind
            if hits == 0:
                flags.append("domain")           # defaulted domain → uncertain
            out.append(Extracted(
                candidate=NormCandidate(statement=s, domain=domain,
                                        scope="all", kind=kind, rationale=""),
                evidence=s, source=source,
                confidence=0.35, flags=flags,     # heuristic doc mining is low-confidence
            ))
        return out


class AnthropicExtractor:
    """LLM extraction: atomic norms with kind/domain/scope + evidence span."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model

    def extract(self, text: str, *, source: str = "") -> list[Extracted]:
        import json

        from anthropic import Anthropic

        prompt = (
            "Extract atomic norms from this document. Each is a principle "
            "(durable/normative) or a decision (a specific recorded choice). "
            "One claim each. Include the exact source sentence as evidence. "
            "domain ∈ {product, technical, product_design, ux}. "
            'Reply JSON array: [{"statement","kind","domain","scope","evidence"}].'
            "\n\n" + text
        )
        client = Anthropic()
        msg = client.messages.create(model=self.model, max_tokens=4096,
                                     messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text
        raw = raw[raw.find("["): raw.rfind("]") + 1]
        out: list[Extracted] = []
        for d in json.loads(raw):
            out.append(Extracted(
                candidate=NormCandidate(statement=d["statement"], domain=d["domain"],
                                        scope=d.get("scope", "all"), kind=d["kind"]),
                evidence=d.get("evidence", d["statement"]), source=source,
            ))
        return out
