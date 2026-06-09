"""Structural check + synthesize_proposal — the deterministic floor.

Offline: FakeEmbedder + InMemoryNormReader + a stub synthesizer.
"""

from __future__ import annotations

import pytest

from kaixn.embedding import FakeEmbedder
from kaixn.engine import render_agent_contract, structural_check, synthesize_proposal
from kaixn.store import InMemoryNormReader
from kaixn.types import (
    NormRecord,
    Operation,
    OpKind,
    OpStatus,
    OpType,
    Proposal,
)


@pytest.fixture
def reader() -> InMemoryNormReader:
    return InMemoryNormReader(FakeEmbedder())


class StubSynthesizer:
    def __init__(self, ops: list[Operation]) -> None:
        self._ops = ops

    def synthesize(self, intent: str) -> list[Operation]:
        return self._ops


def test_implement_op_needs_grounding_until_located(reader):
    op = Operation(OpKind.CODE, OpType.IMPLEMENT, "add a refund endpoint")
    structural_check(op, reader)
    assert op.status is OpStatus.NEEDS_GROUNDING

    op2 = Operation(OpKind.CODE, OpType.IMPLEMENT, "add a refund endpoint",
                    target_location="api/billing/refunds.py")
    structural_check(op2, reader)
    assert op2.status is OpStatus.PROPOSED


def test_modify_against_missing_target_is_conflict(reader):
    op = Operation(OpKind.NORM, OpType.MODIFY, "loosen the retry limit",
                   target_norm_id="does-not-exist")
    structural_check(op, reader)
    assert op.status is OpStatus.CONFLICT
    assert "not found" in op.notes


def test_supersede_against_inactive_target_is_stale(reader):
    reader.add(NormRecord(id="n1", statement="Use Adyen for cards",
                          domain="technical", scope="all.product.billing",
                          kind="decision", status="superseded"))
    op = Operation(OpKind.NORM, OpType.SUPERSEDE, "Use Stripe for cards",
                   target_norm_id="n1")
    structural_check(op, reader)
    assert op.status is OpStatus.CONFLICT
    assert "stale target" in op.notes


def test_modify_against_active_target_passes(reader):
    reader.add(NormRecord(id="n2", statement="Retries capped at 3",
                          domain="technical", scope="all.product.billing",
                          kind="decision", status="active"))
    op = Operation(OpKind.NORM, OpType.MODIFY, "Retries capped at 5",
                   target_norm_id="n2")
    structural_check(op, reader)
    assert op.status is OpStatus.PROPOSED


def test_assert_with_target_is_incoherent(reader):
    op = Operation(OpKind.NORM, OpType.ASSERT, "Audit every refund",
                   target_norm_id="n2")
    structural_check(op, reader)
    assert op.status is OpStatus.CONFLICT


def test_synthesize_proposal_runs_structural_and_renders_contract(reader):
    ops = [
        Operation(OpKind.NORM, OpType.ASSERT, "Every refund emits an audit log",
                  domain="technical", scope="all.product.billing"),
        Operation(OpKind.CODE, OpType.IMPLEMENT, "wire the audit log on refund",
                  target_location="api/billing/refunds.py"),
        Operation(OpKind.NORM, OpType.MODIFY, "loosen retry cap",
                  target_norm_id="ghost"),  # stale → conflict
    ]
    proposal = synthesize_proposal(
        "Refunds must be auditable.",
        synthesizer=StubSynthesizer(ops), reader=reader,
    )
    statuses = {o.op_type: o.status for o in proposal.operations}
    assert statuses[OpType.ASSERT] is OpStatus.PROPOSED
    assert statuses[OpType.IMPLEMENT] is OpStatus.PROPOSED
    assert statuses[OpType.MODIFY] is OpStatus.CONFLICT
    assert "Agent Contract" in proposal.agent_contract
    assert "Structural conflicts" in proposal.agent_contract  # the flagged modify
