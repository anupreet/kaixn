"""Resolution loop: commit asserts, override-as-supersede, gate deferral.

Offline: FakeEmbedder + InMemoryStore + HeuristicSplitter + stub adjudicator.
"""

from __future__ import annotations

import pytest

from kaixn.conflict import Finding
from kaixn.embedding import FakeEmbedder
from kaixn.gate import WriteGate
from kaixn.resolution import commit_proposal, make_override
from kaixn.llm import HeuristicSplitter
from kaixn.store import InMemoryStore
from kaixn.types import (
    Adjudication,
    NormCandidate,
    Operation,
    OpKind,
    OpType,
    Proposal,
    Verdict,
)


class ConsistentAdjudicator:
    def adjudicate(self, stmt, norm) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)

    def assess_coverage(self, norm, summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


@pytest.fixture
def store() -> InMemoryStore:
    return InMemoryStore(FakeEmbedder())


def make_gate(store, adjudicator=None) -> WriteGate:
    return WriteGate(store, FakeEmbedder(), HeuristicSplitter(),
                     adjudicator or ConsistentAdjudicator())


def test_commit_assert_creates_active_norm_with_edge(store):
    gate = make_gate(store)
    prop = Proposal("x", [Operation(OpKind.NORM, OpType.ASSERT,
                                    "Every refund emits an audit log entry",
                                    domain="technical", scope="all.product.billing")],
                    id="prop-1")
    res = commit_proposal(prop, store, gate)
    assert len(res.committed) == 1
    nid = res.committed[0]
    assert store.get(nid).status == "active"
    assert any(e.rel_type == "creates" and e.dst_id == nid for e in store.edges)


def test_duplicate_assert_is_deferred_not_forced(store):
    store.add_norm(  # already in the constitution
        NormCandidate(
            "Every refund emits an audit log entry", "technical", "all.product.billing"))
    gate = make_gate(store)
    prop = Proposal("x", [Operation(OpKind.NORM, OpType.ASSERT,
                                    "Every refund emits an audit log entry",
                                    domain="technical", scope="all.product.billing")])
    res = commit_proposal(prop, store, gate)
    assert res.committed == []
    assert res.deferred and "merge_candidate" in res.deferred[0][1]


def test_override_supersedes_decision(store):
    old = store.add_norm(  # active decision we will override
        NormCandidate(
            "Billing uses Adyen for card processing", "technical", "all.product.billing"))
    # A conflict finding against that decision.
    finding = Finding(Adjudication(Verdict.CONFLICT, old,
                                   proposed_resolution="switch to Stripe"))
    override_op = make_override(finding, "Billing uses Stripe for card processing")
    prop = Proposal("x", [override_op], id="prop-2")

    # The gate would normally flag the new statement as conflicting with `old`;
    # supersede must ignore the target. Use an adjudicator that conflicts on it.
    class ConflictsOnOld:
        def adjudicate(self, stmt, norm):
            v = Verdict.CONFLICT if norm.id == old.id else Verdict.CONSISTENT
            return Adjudication(v, norm)
        def assess_coverage(self, norm, s):
            return Adjudication(Verdict.CONSISTENT, norm)

    res = commit_proposal(prop, store, make_gate(store, ConflictsOnOld()))
    assert old.id in res.superseded
    assert store.get(old.id).status == "superseded"
    new_id = res.committed[0]
    assert store.get(new_id).status == "active"
    assert store.supersede_chain(old.id) == [new_id]
    # superseded norm no longer surfaces as an active neighbor
    assert all(n.id != old.id
               for n in store.neighbors(
                   NormCandidate(
                       "card processing provider", "technical", "all.product.billing"),
                   top_k=8))


def test_deprecate_sets_status(store):
    n = store.add_norm(
        NormCandidate(
            "Support IE11", "technical", "all"))
    prop = Proposal("x", [Operation(OpKind.NORM, OpType.DEPRECATE,
                                    "drop IE11 support", target_norm_id=n.id)])
    res = commit_proposal(prop, store, make_gate(store))
    assert n.id in res.deprecated
    assert store.get(n.id).status == "deprecated"
