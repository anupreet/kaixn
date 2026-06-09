"""Proposal synthesis + the deterministic structural check.

`synthesize_proposal` is the first MCP tool in the sequence diagram:
  intent_text → typed operations → structural check → rendered agent_contract.

Per the v0.2 architecture, the structural pass (this module) runs *before* any
LLM adjudication and catches a whole class of conflicts deterministically:
a `modify`/`supersede` against a missing-or-inactive target, a malformed op,
a code op that needs grounding. The schema's CHECK constraints enforce the same
typing at the DB layer; this gives the app layer the same floor.
"""

from __future__ import annotations

import json
import re
from typing import Protocol

from kaixn.store import NormReader
from kaixn.types import (
    Operation,
    OpKind,
    OpStatus,
    OpType,
    Proposal,
)


# --- synthesis -------------------------------------------------------------
class Synthesizer(Protocol):
    def synthesize(self, intent: str) -> list[Operation]:
        """Decompose intent into typed, targeted operations."""


class NaiveSynthesizer:
    """Offline fallback (no LLM/keys). Turns each sentence of the intent into a
    `code/implement` operation so the loop is demoable without a model. It does
    NOT classify norm-ops — that needs the LLM synthesizer."""

    _SENT = re.compile(r"(?<=[.!?])\s+")

    def synthesize(self, intent: str) -> list[Operation]:
        sentences = [s.strip() for s in self._SENT.split(intent.strip()) if s.strip()]
        return [
            Operation(kind=OpKind.CODE, op_type=OpType.IMPLEMENT,
                      statement=s, ord=i)
            for i, s in enumerate(sentences)
        ] or [Operation(OpKind.CODE, OpType.IMPLEMENT, intent.strip(), ord=0)]


class AnthropicSynthesizer:
    """LLM synthesis. `candidate_norms` (id + statement) are passed in so the
    model can target existing norms for modify/supersede ops."""

    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        self.model = model

    def synthesize(self, intent: str, candidate_norms: list | None = None) -> list[Operation]:
        from anthropic import Anthropic

        norm_lines = "\n".join(
            f"  {n.id}: {n.statement}" for n in (candidate_norms or [])
        ) or "  (none)"
        prompt = (
            "Decompose this product intent into atomic, typed operations.\n"
            "Each operation is one of:\n"
            "  norm/assert      — introduce a new principle or decision\n"
            "  norm/modify      — change an existing norm (needs target id)\n"
            "  norm/deprecate   — retire an existing norm (needs target id)\n"
            "  norm/supersede   — replace a decision with a revision (target id)\n"
            "  code/implement   — a concrete change to the codebase\n\n"
            "Existing norms you may target (id: statement):\n" + norm_lines + "\n\n"
            "Reply with a JSON array; each item: {kind, op_type, statement, "
            'rationale, domain?, scope?, norm_kind?, target_norm_id?, '
            "target_location?}.\n\nINTENT:\n" + intent
        )
        client = Anthropic()
        msg = client.messages.create(
            model=self.model, max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        return _parse_operations(msg.content[0].text)


def _parse_operations(text: str) -> list[Operation]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    m = re.search(r"\[.*\]", text, re.DOTALL)
    raw = json.loads(m.group(0) if m else text)
    ops: list[Operation] = []
    for i, d in enumerate(raw):
        ops.append(Operation(
            kind=OpKind(d["kind"]),
            op_type=OpType(d["op_type"]),
            statement=d["statement"],
            rationale=d.get("rationale", ""),
            ord=i,
            domain=d.get("domain"),
            scope=d.get("scope", "all"),
            norm_kind=d.get("norm_kind", "decision"),
            target_norm_id=d.get("target_norm_id"),
            target_location=d.get("target_location", ""),
        ))
    return ops


# --- structural check (deterministic) --------------------------------------
def structural_check(op: Operation, reader: NormReader) -> Operation:
    """Set op.status/notes from typing rules alone. Mutates and returns op."""
    if not op.kind_type_coherent():
        op.status = OpStatus.CONFLICT
        op.notes = f"incoherent: {op.op_type.value} is not a {op.kind.value} op"
        return op

    if op.kind is OpKind.CODE:                       # implement
        if not op.target_location:
            op.status = OpStatus.NEEDS_GROUNDING
            op.notes = "code op not yet grounded to a location"
        return op

    # norm ops
    if op.op_type is OpType.ASSERT:
        if op.target_norm_id:
            op.status = OpStatus.CONFLICT
            op.notes = "assert must not target an existing norm"
        # dedup vs active norms happens in the write gate at commit time.
        return op

    # modify / deprecate / supersede must name a live target.
    if not op.target_norm_id:
        op.status = OpStatus.CONFLICT
        op.notes = f"{op.op_type.value} requires a target norm"
        return op
    target = reader.get(op.target_norm_id)
    if target is None:
        op.status = OpStatus.CONFLICT
        op.notes = f"target norm {op.target_norm_id} not found"
    elif target.status != "active":
        op.status = OpStatus.CONFLICT
        op.notes = f"target norm is {target.status}, not active (stale target)"
    return op


def synthesize_proposal(
    intent: str,
    *,
    synthesizer: Synthesizer,
    reader: NormReader,
    feature_id: str | None = None,
    conflict_engine=None,
) -> Proposal:
    """Intent → typed operations → structural check → (semantic conflict pass) →
    rendered Proposal.

    The deterministic structural floor always runs. If a `conflict_engine` is
    supplied, the semantic adjudication runs too and the report is attached;
    without one (offline), you get structural verdicts only.
    """
    operations = synthesizer.synthesize(intent)
    for op in operations:
        structural_check(op, reader)
    proposal = Proposal(intent_text=intent, operations=operations,
                        feature_id=feature_id)
    if conflict_engine is not None:
        proposal.conflict_report = conflict_engine.run(proposal)
    proposal.agent_contract = render_agent_contract(proposal)
    return proposal


# --- agent contract rendering ----------------------------------------------
def render_agent_contract(proposal: Proposal) -> str:
    """Markdown rendering of the structured Proposal — the coding-agent view.

    The structured operations are the source of truth; this is a projection.
    """
    norm_ops = [o for o in proposal.operations if o.kind is OpKind.NORM]
    code_ops = [o for o in proposal.operations if o.kind is OpKind.CODE]

    lines = ["# Agent Contract", "", "## Intent", proposal.intent_text, ""]
    if norm_ops:
        lines += ["## Constitution changes"]
        for o in norm_ops:
            tgt = f" → `{o.target_norm_id}`" if o.target_norm_id else ""
            lines.append(f"- **{o.op_type.value}**{tgt} ({o.status.value}): {o.statement}")
        lines.append("")
    lines += ["## Implementation"]
    for o in code_ops:
        loc = f" — `{o.target_location}`" if o.target_location else ""
        lines.append(f"- [{o.status.value}]{loc} {o.statement}")
        if o.acceptance_criteria:
            lines.append(f"  - acceptance: {o.acceptance_criteria}")
    flagged = [o for o in proposal.operations if o.status is OpStatus.CONFLICT]
    if flagged:
        lines += ["", "## ⚠️ Structural conflicts (resolve before implementing)"]
        for o in flagged:
            lines.append(f"- {o.statement} — {o.notes}")

    report = proposal.conflict_report
    if report is not None and getattr(report, "findings", None):
        verb = "BLOCKED" if report.blocked else "review"
        lines += ["", f"## ⚠️ Constitution conflicts ({verb})"]
        for f in report.findings:
            adj = f.adjudication
            where = f.op.statement if f.op else "(whole proposal)"
            lines.append(
                f"- **{adj.verdict.value}** vs {adj.norm.kind} "
                f"\"{adj.norm.statement}\" — on: {where}"
                + (f" — {adj.proposed_resolution}" if adj.proposed_resolution else "")
            )
    return "\n".join(lines)
