"""Bootstrap: heuristic extraction, proposed-not-active, provenance, promote."""

from __future__ import annotations

import pytest

from kaixn.bootstrap import bootstrap, promote
from kaixn.embedding import FakeEmbedder
from kaixn.extract import HeuristicExtractor
from kaixn.gate import WriteGate
from kaixn.llm import HeuristicSplitter
from kaixn.store import InMemoryStore
from kaixn.types import Adjudication, Verdict


class ConsistentAdjudicator:
    def adjudicate(self, stmt, norm) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)

    def assess_coverage(self, norm, summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(FakeEmbedder())


def make_gate(store) -> WriteGate:
    return WriteGate(store, FakeEmbedder(), HeuristicSplitter(), ConsistentAdjudicator())


def test_heuristic_extracts_principles_and_decisions():
    text = ("The API must never block the main thread on a network call. "
            "We use Stripe for all card payments. "
            "The weather today is sunny and pleasant.")  # not normative → skipped
    ex = HeuristicExtractor().extract(text, source="docs/eng.md")
    kinds = {e.candidate.kind for e in ex}
    assert "principle" in kinds and "decision" in kinds
    assert len(ex) == 2  # the weather sentence is dropped


def test_bootstrap_creates_proposed_norms_with_evidence(store):
    docs = {"docs/eng.md":
            "Services must emit structured logs. We adopted PostgreSQL for storage."}
    report = bootstrap(docs, extractor=HeuristicExtractor(),
                       gate=make_gate(store), store=store)
    assert report.proposed                      # at least one candidate proposed
    for nid in report.proposed:
        n = store.get(nid)
        assert n.status == "proposed"           # NOT active — needs promotion
    # provenance edge with evidence recorded
    assert any(e.rel_type == "creates" and e.metadata.get("evidence")
               for e in store.edges)


def test_bootstrap_proposed_norms_are_not_yet_active(store):
    docs = {"d": "The system must encrypt data at rest."}
    report = bootstrap(docs, extractor=HeuristicExtractor(),
                       gate=make_gate(store), store=store)
    # proposed norms must not surface via active_principles
    assert store.active_principles("technical", "all") == []
    # promote one → now active
    assert promote(store, report.proposed[0]) is True
    assert store.get(report.proposed[0]).status == "active"


def test_intra_batch_duplicates_are_deferred(store):
    docs = {"a": "Services must emit structured logs.",
            "b": "Services must emit structured logs."}  # same claim twice
    report = bootstrap(docs, extractor=HeuristicExtractor(),
                       gate=make_gate(store), store=store)
    assert len(report.proposed) == 1
    assert any("duplicate" in reason for _, reason in report.deferred)


def test_promote_only_works_on_proposed(store):
    docs = {"d": "The API must be versioned."}
    report = bootstrap(docs, extractor=HeuristicExtractor(),
                       gate=make_gate(store), store=store)
    nid = report.proposed[0]
    assert promote(store, nid) is True
    assert promote(store, nid) is False  # already active, can't re-promote
