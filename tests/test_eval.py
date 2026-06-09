"""Eval-harness scoring math — verified against a deterministic adjudicator so
the metrics themselves are trustworthy (separate from model quality)."""

from __future__ import annotations

from kaixn.eval import EvalCase, load_cases, score
from kaixn.types import Adjudication, NormRecord, Verdict


class FixedAdjudicator:
    """Predicts a verdict per norm id, regardless of statement."""

    def __init__(self, preds: dict[str, Verdict]) -> None:
        self._preds = preds

    def adjudicate(self, candidate_statement, norm) -> Adjudication:
        return Adjudication(self._preds.get(norm.id, Verdict.CONSISTENT), norm)

    def assess_coverage(self, norm, operations_summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


def _norm(nid, kind="principle"):
    return NormRecord(id=nid, statement=f"norm {nid}", domain="technical",
                      scope="all", kind=kind)


def test_scoring_precision_recall_math():
    cases = [
        EvalCase("a", _norm("n1"), Verdict.CONFLICT),     # pred conflict  → TP
        EvalCase("b", _norm("n2"), Verdict.CONFLICT),     # pred consistent→ FN(conflict)/FP(consistent)
        EvalCase("c", _norm("n3"), Verdict.CONSISTENT),   # pred consistent→ TP
        EvalCase("d", _norm("n4"), Verdict.CONSISTENT),   # pred conflict  → FN(consistent)/FP(conflict)
    ]
    adj = FixedAdjudicator({
        "n1": Verdict.CONFLICT,
        "n2": Verdict.CONSISTENT,
        "n3": Verdict.CONSISTENT,
        "n4": Verdict.CONFLICT,
    })
    report = score(adj, cases)

    assert report.n == 4
    assert report.accuracy == 0.5  # n1, n3 correct

    conflict = report.per_class["conflict"]
    assert (conflict.tp, conflict.fp, conflict.fn) == (1, 1, 1)
    assert conflict.precision == 0.5
    assert conflict.recall == 0.5

    consistent = report.per_class["consistent"]
    assert (consistent.tp, consistent.fp, consistent.fn) == (1, 1, 1)


def test_seed_dataset_loads():
    cases = load_cases("evals/conflict_cases.jsonl")
    assert len(cases) >= 20
    assert {c.expected for c in cases} >= {Verdict.CONFLICT, Verdict.CONSISTENT}
