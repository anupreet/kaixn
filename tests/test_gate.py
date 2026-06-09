"""Write-gate behavior: atomicity, dedup, self-consistency, accept.

Runs fully offline — FakeEmbedder + HeuristicSplitter + a stub adjudicator +
the in-memory reader. No DB, no API keys.
"""

from __future__ import annotations

import pytest

from kaixn.embedding import FakeEmbedder
from kaixn.gate import GateConfig, WriteGate
from kaixn.llm import HeuristicSplitter
from kaixn.store import InMemoryNormReader
from kaixn.types import Adjudication, GateDecision, NormCandidate, NormRecord, Verdict


class StubAdjudicator:
    """Returns CONSISTENT unless a norm id is mapped to another verdict."""

    def __init__(self, verdicts: dict[str, Verdict] | None = None) -> None:
        self._verdicts = verdicts or {}

    def adjudicate(self, candidate_statement: str, norm: NormRecord) -> Adjudication:
        return Adjudication(self._verdicts.get(norm.id, Verdict.CONSISTENT), norm)


def make_gate(reader, adjudicator) -> WriteGate:
    return WriteGate(
        reader=reader,
        embedder=FakeEmbedder(),
        splitter=HeuristicSplitter(),
        adjudicator=adjudicator,
        config=GateConfig(),
    )


@pytest.fixture
def reader() -> InMemoryNormReader:
    return InMemoryNormReader(FakeEmbedder())


def test_compound_statement_is_split(reader):
    gate = make_gate(reader, StubAdjudicator())
    cand = NormCandidate(
        statement="Payments must be idempotent; refunds must be auditable",
        domain="technical", scope="all.product.billing",
    )
    result = gate.evaluate(cand)
    assert result.decision is GateDecision.SPLIT
    assert len(result.splits) == 2


def test_near_duplicate_flagged_for_merge(reader):
    reader.add(NormRecord(
        id="n1", statement="Payment requests must be idempotent",
        domain="technical", scope="all.product.billing", kind="decision",
    ))
    gate = make_gate(reader, StubAdjudicator())
    cand = NormCandidate(
        statement="Payment requests must be idempotent",  # identical wording
        domain="technical", scope="all.product.billing",
    )
    result = gate.evaluate(cand)
    assert result.decision is GateDecision.MERGE_CANDIDATE
    assert result.duplicates[0].norm.id == "n1"


def test_contradiction_is_conflict(reader):
    reader.add(NormRecord(
        id="p1", statement="We never block the main thread on network calls",
        domain="technical", scope="all", kind="principle",
    ))
    gate = make_gate(reader, StubAdjudicator({"p1": Verdict.CONFLICT}))
    cand = NormCandidate(
        statement="Fetch the user profile synchronously before first paint",
        domain="technical", scope="all.product.profile",
    )
    result = gate.evaluate(cand)
    assert result.decision is GateDecision.CONFLICT
    assert result.conflicts[0].norm.id == "p1"


def test_novel_consistent_statement_accepted(reader):
    reader.add(NormRecord(
        id="d1", statement="Use Stripe for card processing",
        domain="technical", scope="all.product.billing", kind="decision",
    ))
    gate = make_gate(reader, StubAdjudicator())
    cand = NormCandidate(
        statement="Emit a structured audit log entry for every refund",
        domain="technical", scope="all.product.billing",
    )
    result = gate.evaluate(cand)
    assert result.decision is GateDecision.ACCEPT


def test_governing_principle_is_checked_even_without_similarity(reader):
    # A global principle with no lexical/embedding overlap must still be
    # adjudicated — this is the "always check principles" guarantee.
    reader.add(NormRecord(
        id="p2", statement="All user-facing actions must be reversible",
        domain="ux", scope="all", kind="principle",
    ))
    gate = make_gate(reader, StubAdjudicator({"p2": Verdict.CONFLICT}))
    cand = NormCandidate(
        statement="Deleting an account happens instantly with no undo window",
        domain="ux", scope="all.product.account",
    )
    result = gate.evaluate(cand)
    assert result.decision is GateDecision.CONFLICT
