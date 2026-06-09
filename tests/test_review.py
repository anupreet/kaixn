"""Drift review: coverage (missing impl) + violation (vs constitution)."""

from __future__ import annotations

import pytest

from kaixn.embedding import FakeEmbedder
from kaixn.review import DriftReviewer, PullRequest
from kaixn.store import InMemoryStore
from kaixn.types import (
    Adjudication,
    NormCandidate,
    Operation,
    OpKind,
    OpStatus,
    OpType,
    Proposal,
    Verdict,
)


class StubAdjudicator:
    def __init__(self, conflict_marker: str | None = None) -> None:
        self._marker = conflict_marker

    def adjudicate(self, change, norm) -> Adjudication:
        v = (Verdict.CONFLICT if self._marker and self._marker in change.lower()
             else Verdict.CONSISTENT)
        return Adjudication(v, norm)

    def assess_coverage(self, norm, summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


@pytest.fixture
def store() -> InMemoryStore:
    s = InMemoryStore(FakeEmbedder())
    s.add_norm(NormCandidate("Never block the main thread", "technical", "all",
                             "principle"))
    return s


def _proposal() -> Proposal:
    op = Operation(OpKind.CODE, OpType.IMPLEMENT, "add refund endpoint",
                   target_location="api/billing/refunds.py")
    op.status = OpStatus.PROPOSED
    return Proposal("refunds", [op], id="p1")


def test_missing_implementation_is_flagged(store):
    reviewer = DriftReviewer(store, StubAdjudicator(), FakeEmbedder())
    pr = PullRequest(files=["api/billing/charges.py"], changes=[])  # wrong file
    report = reviewer.review(_proposal(), pr)
    assert any(c.kind == "missing" for c in report.comments)
    assert report.approve is False


def test_violation_against_constitution_is_flagged(store):
    reviewer = DriftReviewer(store, StubAdjudicator(conflict_marker="synchronous"),
                             FakeEmbedder())
    pr = PullRequest(files=["api/billing/refunds.py"],
                     changes=["made the refund call synchronous on the main thread"])
    report = reviewer.review(_proposal(), pr)
    assert any(c.kind == "violation" for c in report.comments)
    assert report.approve is False


def test_clean_pr_is_approved(store):
    reviewer = DriftReviewer(store, StubAdjudicator(), FakeEmbedder())
    pr = PullRequest(files=["api/billing/refunds.py"],
                     changes=["added an async refund endpoint"])
    report = reviewer.review(_proposal(), pr)
    assert report.comments == []
    assert report.approve is True
