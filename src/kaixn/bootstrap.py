"""Bootstrap — seed the constitution from an existing repo's docs/ADRs/code.

Each extracted candidate flows through the SAME write gate the per-feature loop
uses, and lands as ``status='proposed'`` — never auto-trusted. A curator
``promote``s candidates to ``active``. This is the OpenJarvis-fix in practice:
provenance (evidence edges), atomicity + dedup (the gate), self-consistency
(gate conflict), and a human in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kaixn.extract import Extracted, NormExtractor
from kaixn.gate import WriteGate
from kaixn.similarity import jaccard
from kaixn.store import InMemoryStore
from kaixn.types import GateDecision, NormCandidate

CONFIDENCE_FLOOR = 0.7   # below this, the connecting human must confirm


@dataclass(slots=True)
class Ambiguity:
    """A decision the *connecting user* must make — surfaced, never auto-resolved."""

    type: str                 # duplicate | conflict | confirm | classification | domain
    statement: str
    question: str
    options: list[str]
    recommended: str
    evidence: str = ""
    norm_id: str | None = None        # the proposed norm (if one was created)
    other_norm_id: str | None = None  # the existing norm (duplicate/conflict)


@dataclass(slots=True)
class BootstrapReport:
    extracted: int = 0
    proposed: list[str] = field(default_factory=list)        # norm ids (proposed)
    deferred: list[tuple[str, str]] = field(default_factory=list)  # (statement, reason)
    ambiguities: list[Ambiguity] = field(default_factory=list)     # the resolver queue


def _commit_candidate(ex: Extracted, gate: WriteGate, store: InMemoryStore,
                      report: BootstrapReport, accepted: list[str]) -> None:
    cand = ex.candidate
    g = gate.evaluate(cand)

    if g.decision is GateDecision.SPLIT:
        for part in g.splits:                       # one level of splitting
            sub = Extracted(NormCandidate(part, cand.domain, cand.scope, cand.kind),
                            evidence=ex.evidence, source=ex.source,
                            confidence=ex.confidence, flags=ex.flags)
            _commit_candidate(sub, gate, store, report, accepted)
        return

    # The gate found a near-duplicate — the human decides merge vs keep-both.
    if g.decision is GateDecision.MERGE_CANDIDATE:
        dup = g.duplicates[0].norm
        report.ambiguities.append(Ambiguity(
            type="duplicate", statement=cand.statement,
            question=f"This looks like an existing norm: \"{dup.statement}\". Same thing?",
            options=["Merge into existing", "Keep as a separate norm", "Discard"],
            recommended="Merge into existing", evidence=ex.evidence,
            other_norm_id=dup.id))
        return

    # The gate found a contradiction — the human decides which holds.
    if g.decision is GateDecision.CONFLICT:
        other = g.conflicts[0].norm
        report.ambiguities.append(Ambiguity(
            type="conflict", statement=cand.statement,
            question=f"This contradicts \"{other.statement}\". How to resolve?",
            options=["Supersede the existing norm", "Discard this candidate",
                     "Keep both (different scope)"],
            recommended="Discard this candidate", evidence=ex.evidence,
            other_norm_id=other.id))
        return

    # intra-batch dedup (proposed norms aren't active, so the gate can't see them)
    if any(jaccard(cand.statement, s) >= 0.8 for s in accepted):
        report.deferred.append((cand.statement, "intra_batch_duplicate"))
        return

    # ACCEPT → propose it; low-confidence or flagged candidates also raise a
    # question for the connecting user to confirm/classify/place.
    norm = store.add_norm(cand, status="proposed")
    store.add_edge(ex.source or "source", "source", norm.id, "norm", "creates",
                   {"evidence": ex.evidence, "confidence": ex.confidence})
    report.proposed.append(norm.id)
    accepted.append(cand.statement)

    if "classification" in ex.flags:
        report.ambiguities.append(Ambiguity(
            type="classification", statement=cand.statement,
            question="Is this a durable principle or a specific decision?",
            options=["Principle", "Decision"], recommended=cand.kind.capitalize(),
            evidence=ex.evidence, norm_id=norm.id))
    elif "binding" in ex.flags:
        report.ambiguities.append(Ambiguity(
            type="confirm", statement=cand.statement,
            question="Is this a binding decision, or just an available option?",
            options=["Binding decision (promote)", "Just available (drop)", "Edit wording"],
            recommended="Binding decision (promote)", evidence=ex.evidence,
            norm_id=norm.id))
    elif "domain" in ex.flags or ex.confidence < CONFIDENCE_FLOOR:
        report.ambiguities.append(Ambiguity(
            type="confirm", statement=cand.statement,
            question=f"Confirm this norm (extractor confidence {ex.confidence:.0%})?",
            options=["Confirm (promote)", "Edit wording", "Reclassify domain", "Discard"],
            recommended="Confirm (promote)", evidence=ex.evidence, norm_id=norm.id))


def bootstrap(documents: dict[str, str], *, extractor: NormExtractor,
              gate: WriteGate, store: InMemoryStore) -> BootstrapReport:
    """Mine `documents` (source → text) into proposed norms."""
    report = BootstrapReport()
    accepted: list[str] = []
    for source, text in documents.items():
        for ex in extractor.extract(text, source=source):
            report.extracted += 1
            _commit_candidate(ex, gate, store, report, accepted)
    return report


def bootstrap_extracted(items: list[Extracted], *, gate: WriteGate,
                        store: InMemoryStore) -> BootstrapReport:
    """Bootstrap from already-extracted items (e.g. from CodebaseExtractor)."""
    report = BootstrapReport()
    accepted: list[str] = []
    for ex in items:
        report.extracted += 1
        _commit_candidate(ex, gate, store, report, accepted)
    return report


def promote(store: InMemoryStore, norm_id: str) -> bool:
    """Curator action: promote a proposed candidate to active. Returns success."""
    n = store.get(norm_id)
    if n is None or n.status != "proposed":
        return False
    store.set_status(norm_id, "active")
    return True


def resolve_ambiguity(store: InMemoryStore, amb: Ambiguity, choice: str, *,
                      new_text: str | None = None,
                      new_domain: str | None = None) -> str:
    """Apply the connecting user's resolution. Returns a short outcome string."""
    c = choice.lower()
    if amb.type == "duplicate":
        if "merge" in c or "discard" in c:
            return "dropped (folded into existing)"
        if "separate" in c and amb.statement:           # keep both
            store.add_norm(NormCandidate(amb.statement, "technical", "all"),
                           status="proposed")
            return "kept as a separate proposed norm"
    if amb.type == "conflict":
        if "supersede" in c and amb.other_norm_id:
            old = store.get(amb.other_norm_id)
            new = store.add_norm(NormCandidate(amb.statement, old.domain, old.scope,
                                               old.kind), status="active")
            store.set_status(old.id, "superseded")
            store.add_edge(new.id, "norm", old.id, "norm", "supersedes")
            return f"superseded {amb.other_norm_id}"
        return "candidate discarded"
    # confirm / classification / domain all act on a proposed norm_id
    if amb.norm_id is None:
        return "no-op"
    if "promote" in c or "confirm" in c or "binding" in c:
        store.set_status(amb.norm_id, "active")
        return "promoted to active"
    if "drop" in c or "discard" in c:
        store.set_status(amb.norm_id, "deprecated")
        return "discarded"
    if "edit" in c and new_text:
        store.get(amb.norm_id).statement = new_text
        return "wording updated"
    if amb.type == "classification" and choice in ("Principle", "Decision"):
        store.get(amb.norm_id).kind = choice.lower()
        return f"reclassified as {choice.lower()}"
    if "reclassify" in c and new_domain:
        store.get(amb.norm_id).domain = new_domain
        return f"domain set to {new_domain}"
    return "no-op"
