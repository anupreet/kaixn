"""The resolution loop — turning an approved Proposal into constitution changes.

Two PM actions from the sequence diagram:

  * edit intent  → re-run `synthesize_proposal` (a new Proposal version)
  * override     → `make_override` appends a `supersede` operation that replaces
                   the conflicting decision with a revision

…and the commit that applies an approved Proposal's norm-operations to the
constitution, each passing through the write gate. This is where the write gate,
conflict engine, and supersede edges meet:

  assert     → gate → new active norm + `creates` edge
  supersede  → gate (ignoring the target) → new norm, old → superseded,
               `supersedes` edge + `creates` edge
  modify     → same mechanics as supersede (append-only: never mutate in place)
  deprecate  → target → status 'deprecated'

A norm-op the gate won't pass (split / duplicate / conflict) is *deferred*, not
forced — the caller routes it back to a human.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kaixn.conflict import Finding
from kaixn.gate import WriteGate
from kaixn.store import InMemoryStore
from kaixn.types import (
    GateDecision,
    NormCandidate,
    Operation,
    OpKind,
    OpStatus,
    OpType,
    Proposal,
)


@dataclass(slots=True)
class CommitResult:
    committed: list[str] = field(default_factory=list)     # new norm ids
    superseded: list[str] = field(default_factory=list)    # replaced norm ids
    deprecated: list[str] = field(default_factory=list)
    deferred: list[tuple[Operation, str]] = field(default_factory=list)
    edges: int = 0

    @property
    def clean(self) -> bool:
        return not self.deferred


def make_override(finding: Finding, new_statement: str, *,
                  kind: str = "decision") -> Operation:
    """Build the `supersede` operation that resolves a decision conflict by
    replacing it with `new_statement`."""
    norm = finding.adjudication.norm
    return Operation(
        kind=OpKind.NORM, op_type=OpType.SUPERSEDE,
        statement=new_statement, target_norm_id=norm.id,
        domain=norm.domain, scope=norm.scope, norm_kind=kind,
        rationale="override: resolves a constitution conflict",
        notes="override",
    )


def _candidate(op: Operation, *, domain: str | None = None) -> NormCandidate:
    return NormCandidate(
        statement=op.statement, domain=domain or op.domain or "technical",
        scope=op.scope or "all", kind=op.norm_kind, rationale=op.rationale,
    )


def _provenance(store: InMemoryStore, proposal: Proposal, norm_id: str, rel: str) -> None:
    src = proposal.id or "proposal"
    store.add_edge(src, "proposal", norm_id, "norm", rel)


def commit_proposal(proposal: Proposal, store: InMemoryStore,
                    gate: WriteGate) -> CommitResult:
    """Apply the approved Proposal's norm-operations to the constitution."""
    res = CommitResult()
    for op in proposal.operations:
        if op.kind is not OpKind.NORM:
            continue                              # code ops are for the agent
        if op.status is OpStatus.CONFLICT:
            res.deferred.append((op, op.notes or "structural conflict"))
            continue

        if op.op_type is OpType.ASSERT:
            cand = _candidate(op)
            g = gate.evaluate(cand)
            if g.decision is GateDecision.ACCEPT:
                n = store.add_norm(cand)
                _provenance(store, proposal, n.id, "creates")
                res.committed.append(n.id)
                res.edges += 1
            else:
                res.deferred.append((op, g.decision.value))

        elif op.op_type in (OpType.SUPERSEDE, OpType.MODIFY):
            target = store.get(op.target_norm_id) if op.target_norm_id else None
            if target is None or target.status != "active":
                res.deferred.append((op, "stale target"))
                continue
            cand = _candidate(op, domain=target.domain)
            g = gate.evaluate(cand, ignore_norm_ids={target.id})
            if g.decision is GateDecision.ACCEPT:
                n = store.add_norm(cand)
                store.set_status(target.id, "superseded")
                store.add_edge(n.id, "norm", target.id, "norm", "supersedes",
                               {"via": op.op_type.value})
                _provenance(store, proposal, n.id, "creates")
                res.committed.append(n.id)
                res.superseded.append(target.id)
                res.edges += 2
            else:
                res.deferred.append((op, g.decision.value))

        elif op.op_type is OpType.DEPRECATE:
            target = store.get(op.target_norm_id) if op.target_norm_id else None
            if target is not None and target.status == "active":
                store.set_status(target.id, "deprecated")
                res.deprecated.append(target.id)
            else:
                res.deferred.append((op, "stale target"))

    return res
