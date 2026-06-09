"""The conflict engine — semantic adjudication of a Proposal vs the constitution.

Runs AFTER the deterministic structural check (`engine.structural_check`). Two
passes:

  1. contradiction — per (operation × relevant norm): consistent/conflict/tension
  2. gap           — per governing principle × the whole Proposal: is a required
                     thing left unaddressed? (conflict-by-omission, the crown jewel)

A `conflict` against a *principle* blocks the Proposal (hard stop). A conflict
against a *decision* is revisable — the resolution is a `supersede` operation.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from kaixn.embedding import Embedder
from kaixn.llm import Adjudicator
from kaixn.store import NormReader, scope_governs
from kaixn.types import (
    Adjudication,
    NormCandidate,
    NormRecord,
    Operation,
    OpKind,
    OpStatus,
    OpType,
    Proposal,
    Verdict,
)

DOMAINS = ("product", "technical", "product_design", "ux")


@dataclass(slots=True)
class Finding:
    adjudication: Adjudication
    op: Operation | None = None          # None → proposal-level gap


@dataclass(slots=True)
class ConflictReport:
    findings: list[Finding] = field(default_factory=list)
    blocked: bool = False                 # any conflict vs a principle
    counts: dict[str, int] = field(default_factory=dict)


class ConflictEngine:
    def __init__(self, reader: NormReader, adjudicator: Adjudicator,
                 embedder: Embedder, *, neighbor_top_k: int = 8) -> None:
        self._reader = reader
        self._adj = adjudicator
        self._embedder = embedder
        self._top_k = neighbor_top_k

    def run(self, proposal: Proposal) -> ConflictReport:
        findings: list[Finding] = []

        # 1. contradiction pass — skip ops already killed structurally.
        for op in proposal.operations:
            if op.status is OpStatus.CONFLICT:
                continue
            stmt, domain, scope = self._target(op)
            for norm in self._relevant(stmt, domain, scope, op.kind):
                adj = self._adj.adjudicate(stmt, norm)
                if adj.verdict in (Verdict.CONFLICT, Verdict.TENSION):
                    findings.append(Finding(adj, op))

        # 2. gap pass — governing principles vs the whole Proposal.
        summary = "\n".join(
            f"- {o.op_type.value}: {o.statement}" for o in proposal.operations
        )
        for principle in self._governing_principles(proposal):
            adj = self._adj.assess_coverage(principle, summary)
            if adj.verdict is Verdict.GAP:
                findings.append(Finding(adj, None))

        blocked = any(
            f.adjudication.verdict is Verdict.CONFLICT
            and f.adjudication.norm.kind == "principle"
            for f in findings
        )
        counts = dict(Counter(f.adjudication.verdict.value for f in findings))
        return ConflictReport(findings, blocked, counts)

    # -- retrieval helpers --------------------------------------------------
    def _target(self, op: Operation) -> tuple[str, str | None, str]:
        """The (statement, domain, scope) an op is checked under. Code ops have
        no single domain → domain=None means 'all domains'."""
        if op.kind is OpKind.CODE:
            return op.statement, None, op.scope or "all"
        domain = op.domain
        if domain is None and op.target_norm_id:
            target = self._reader.get(op.target_norm_id)
            domain = target.domain if target else None
        return op.statement, domain, op.scope or "all"

    def _relevant(self, stmt: str, domain: str | None, scope: str,
                  kind: OpKind) -> list[NormRecord]:
        by_id: dict[str, NormRecord] = {}
        # principles (the hard constraints): the op's domain, or all for code.
        for d in ([domain] if domain else DOMAINS):
            for p in self._reader.active_principles(d, scope):
                by_id[p.id] = p
        # decisions/neighbors via similarity in the most relevant domain.
        cand = NormCandidate(stmt, domain or "technical", scope)
        cand.embedding = self._embedder.embed([stmt])[0]
        for n in self._reader.neighbors(cand, top_k=self._top_k):
            by_id.setdefault(n.id, n)
        return list(by_id.values())

    def _governing_principles(self, proposal: Proposal) -> list[NormRecord]:
        scopes = {o.scope or "all" for o in proposal.operations} or {"all"}
        by_id: dict[str, NormRecord] = {}
        for d in DOMAINS:
            for s in scopes:
                for p in self._reader.active_principles(d, s):
                    by_id[p.id] = p
        return list(by_id.values())
