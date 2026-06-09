"""Drift review — `review_implementation`. Shifts the *late* check (PR review)
to comparing the PR against the **approved Proposal** and the active constitution.

Two checks:
  * coverage  — does the PR actually implement each accepted code op?
                (a `target_location` with no matching change → missing)
  * violation — does any change contradict an active norm that wasn't part of
                the plan? (adjudicate each change vs relevant norms)

The point of the product: a violation caught here should have been caught at
plan time — but if the implementation drifted from the plan, this is the net.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from kaixn.conflict import DOMAINS
from kaixn.embedding import Embedder
from kaixn.llm import Adjudicator
from kaixn.store import NormReader
from kaixn.types import NormCandidate, NormRecord, OpKind, OpStatus, Proposal, Verdict


@dataclass(slots=True)
class PullRequest:
    files: list[str] = field(default_factory=list)
    summary: str = ""
    changes: list[str] = field(default_factory=list)  # described code changes


@dataclass(slots=True)
class ReviewComment:
    kind: str          # 'missing' | 'violation'
    body: str


@dataclass(slots=True)
class ReviewReport:
    comments: list[ReviewComment] = field(default_factory=list)
    approve: bool = True


class DriftReviewer:
    def __init__(self, store: NormReader, adjudicator: Adjudicator,
                 embedder: Embedder, *, neighbor_top_k: int = 8) -> None:
        self._store = store
        self._adj = adjudicator
        self._embedder = embedder
        self._top_k = neighbor_top_k

    def review(self, proposal: Proposal, pr: PullRequest) -> ReviewReport:
        report = ReviewReport()

        # 1. coverage — each accepted code op should be evidenced in the PR.
        for op in proposal.operations:
            if op.kind is not OpKind.CODE or op.status is OpStatus.CONFLICT:
                continue
            loc = op.target_location
            if loc and not any(loc in f for f in pr.files):
                report.comments.append(ReviewComment(
                    "missing", f"operation not implemented: `{loc}` — {op.statement}"))

        # 2. violation — each change vs the active constitution.
        for change in pr.changes:
            for norm in self._relevant(change):
                adj = self._adj.adjudicate(change, norm)
                if adj.verdict in (Verdict.CONFLICT, Verdict.TENSION):
                    report.comments.append(ReviewComment(
                        "violation",
                        f"{adj.verdict.value} vs {norm.kind} \"{norm.statement}\" "
                        f"— change: {change}"))

        report.approve = not report.comments
        return report

    def _relevant(self, change: str) -> list[NormRecord]:
        by_id: dict[str, NormRecord] = {}
        for d in DOMAINS:
            for p in self._store.active_principles(d, "all"):
                by_id[p.id] = p
        cand = NormCandidate(change, "technical", "all")
        cand.embedding = self._embedder.embed([change])[0]
        for n in self._store.neighbors(cand, top_k=self._top_k):
            by_id.setdefault(n.id, n)
        return list(by_id.values())
