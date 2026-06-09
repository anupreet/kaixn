"""LLM-backed steps the write gate depends on: atomicity splitting and
(candidate × norm) adjudication. Each is a Protocol so tests inject stubs and
the POC can run with the cheap heuristic splitter before wiring a real model.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from kaixn.types import Adjudication, NormRecord, Verdict


# --- atomicity -------------------------------------------------------------
class AtomicitySplitter(Protocol):
    def split(self, statement: str) -> list[str]:
        """Return >1 part if the statement bundles multiple claims, else [it]."""


class HeuristicSplitter:
    """Cheap, no-LLM prescreen. Conservative — splits only on strong
    separators to avoid false splits like "look and feel". The real splitter
    (LLM) handles subtle compounds; this keeps the POC running offline.
    """

    _SEPARATORS = re.compile(r";|\bas well as\b|,\s*and\b|\.\s+(?=[A-Z])")

    def split(self, statement: str) -> list[str]:
        parts = [p.strip(" .") for p in self._SEPARATORS.split(statement)]
        parts = [p for p in parts if len(p.split()) >= 3]
        return parts if len(parts) > 1 else [statement.strip()]


class AnthropicSplitter:
    """LLM atomicity split. One normative claim per returned item."""

    def __init__(self, model: str = "claude-haiku-4-5-20251001") -> None:
        self.model = model

    def split(self, statement: str) -> list[str]:
        from anthropic import Anthropic

        client = Anthropic()
        prompt = (
            "Split the following into atomic normative claims — one obligation "
            "per item. If it is already a single claim, return it unchanged. "
            'Reply with a JSON array of strings only.\n\n' + statement
        )
        msg = client.messages.create(
            model=self.model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            parts = json.loads(_first_json(msg.content[0].text))
            parts = [str(p).strip() for p in parts if str(p).strip()]
            return parts or [statement.strip()]
        except Exception:
            return [statement.strip()]


# --- adjudication ----------------------------------------------------------
class Adjudicator(Protocol):
    def adjudicate(self, candidate_statement: str, norm: NormRecord) -> Adjudication:
        """Judge a candidate statement against one existing norm (contradiction)."""

    def assess_coverage(self, norm: NormRecord, operations_summary: str) -> Adjudication:
        """Does the Proposal address what this norm REQUIRES? Returns GAP if the
        plan is silent on a requirement, else CONSISTENT. (Conflict-by-omission.)"""


class AnthropicAdjudicator:
    """The conflict engine's core judgment, used here for self-consistency."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model

    def adjudicate(self, candidate_statement: str, norm: NormRecord) -> Adjudication:
        from anthropic import Anthropic

        client = Anthropic()
        prompt = (
            "You compare a PROPOSED norm against an EXISTING one and decide if "
            "they can both hold. Verdict is one of: consistent, conflict, "
            "tension, gap. Reply JSON: "
            '{"verdict": "...", "evidence": "...", "resolution": "..."}.\n\n'
            f"EXISTING ({norm.kind}, {norm.domain}): {norm.statement}\n"
            f"PROPOSED: {candidate_statement}"
        )
        msg = client.messages.create(
            model=self.model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            data = json.loads(_first_json(msg.content[0].text))
            verdict = Verdict(str(data.get("verdict", "consistent")).lower())
            return Adjudication(verdict, norm,
                                evidence=data.get("evidence", ""),
                                proposed_resolution=data.get("resolution", ""))
        except Exception:
            return Adjudication(Verdict.CONSISTENT, norm)

    def assess_coverage(self, norm: NormRecord, operations_summary: str) -> Adjudication:
        from anthropic import Anthropic

        client = Anthropic()
        prompt = (
            "A norm may REQUIRE something of any change in its area. Decide "
            "whether the proposed operations address that requirement. If the "
            "norm imposes a requirement and NO operation addresses it, verdict "
            "is 'gap'. Otherwise 'consistent'. Reply JSON: "
            '{"verdict": "gap|consistent", "evidence": "..."}.\n\n'
            f"NORM ({norm.kind}, {norm.domain}): {norm.statement}\n\n"
            f"PROPOSED OPERATIONS:\n{operations_summary}"
        )
        msg = client.messages.create(
            model=self.model, max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        try:
            data = json.loads(_first_json(msg.content[0].text))
            verdict = Verdict(str(data.get("verdict", "consistent")).lower())
            return Adjudication(verdict, norm, evidence=data.get("evidence", ""))
        except Exception:
            return Adjudication(Verdict.CONSISTENT, norm)


def _first_json(text: str) -> str:
    """Extract the first JSON array/object from model output (strips fences)."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    m = re.search(r"[\[{].*[\]}]", text, re.DOTALL)
    return m.group(0) if m else text
