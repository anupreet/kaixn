"""Conflict engine: contradiction pass, gap pass, principle hard-stop.

Offline with a programmable stub adjudicator.
"""

from __future__ import annotations

import pytest

from kaixn.conflict import ConflictEngine
from kaixn.embedding import FakeEmbedder
from kaixn.store import InMemoryNormReader
from kaixn.types import (
    Adjudication,
    NormRecord,
    Operation,
    OpKind,
    OpType,
    Proposal,
    Verdict,
)


class StubAdjudicator:
    """contradiction: norm_id → verdict.  coverage: norm_id → verdict (gap?)."""

    def __init__(self, contradict=None, coverage=None) -> None:
        self._c = contradict or {}
        self._g = coverage or {}

    def adjudicate(self, candidate_statement, norm) -> Adjudication:
        return Adjudication(self._c.get(norm.id, Verdict.CONSISTENT), norm)

    def assess_coverage(self, norm, operations_summary) -> Adjudication:
        return Adjudication(self._g.get(norm.id, Verdict.CONSISTENT), norm)


@pytest.fixture
def reader() -> InMemoryNormReader:
    r = InMemoryNormReader(FakeEmbedder())
    r.add(NormRecord(id="p1", statement="Never block the main thread",
                     domain="technical", scope="all", kind="principle"))
    r.add(NormRecord(id="d1", statement="Billing uses Stripe",
                     domain="technical", scope="all.product.billing",
                     kind="decision"))
    return r


def engine(reader, adj) -> ConflictEngine:
    return ConflictEngine(reader, adj, FakeEmbedder())


def test_conflict_vs_principle_blocks(reader):
    eng = engine(reader, StubAdjudicator(contradict={"p1": Verdict.CONFLICT}))
    prop = Proposal("x", [Operation(OpKind.CODE, OpType.IMPLEMENT,
                                    "fetch synchronously before paint",
                                    scope="all.product.profile",
                                    target_location="app/profile.py")])
    report = eng.run(prop)
    assert report.blocked is True
    assert report.counts.get("conflict") == 1


def test_conflict_vs_decision_does_not_block(reader):
    # A conflict against a decision is revisable, not a hard stop.
    eng = engine(reader, StubAdjudicator(contradict={"d1": Verdict.CONFLICT}))
    prop = Proposal("x", [Operation(OpKind.CODE, OpType.IMPLEMENT,
                                    "process payments via Adyen",
                                    scope="all.product.billing",
                                    target_location="api/billing.py")])
    report = eng.run(prop)
    assert report.blocked is False
    assert report.counts.get("conflict") == 1


def test_gap_pass_flags_unaddressed_requirement(reader):
    eng = engine(reader, StubAdjudicator(coverage={"p1": Verdict.GAP}))
    prop = Proposal("x", [Operation(OpKind.CODE, OpType.IMPLEMENT,
                                    "add a settings page",
                                    target_location="app/settings.py")])
    report = eng.run(prop)
    assert any(f.op is None and f.adjudication.verdict is Verdict.GAP
               for f in report.findings)


def test_clean_proposal_has_no_findings(reader):
    eng = engine(reader, StubAdjudicator())
    prop = Proposal("x", [Operation(OpKind.CODE, OpType.IMPLEMENT,
                                    "add a settings page",
                                    target_location="app/settings.py")])
    report = eng.run(prop)
    assert report.findings == []
    assert report.blocked is False
