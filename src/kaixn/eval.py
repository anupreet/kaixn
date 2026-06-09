"""Eval harness for the conflict engine's core judgment (op × norm contradiction).

The engine is the product, so it can't ship on vibes. This scores an
`Adjudicator` against a labeled set of `(operation, norm, expected_verdict)`
cases and reports per-verdict precision/recall — with separate attention to
the verdicts that matter most (conflict, gap).

Run the real model:   python -m kaixn.eval evals/conflict_cases.jsonl
(needs ANTHROPIC_API_KEY; defaults to the AnthropicAdjudicator)
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from kaixn.llm import Adjudicator
from kaixn.types import NormRecord, Verdict


@dataclass(slots=True)
class EvalCase:
    op_statement: str
    norm: NormRecord
    expected: Verdict


@dataclass(slots=True)
class ClassMetrics:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        d = self.tp + self.fp
        return self.tp / d if d else 0.0

    @property
    def recall(self) -> float:
        d = self.tp + self.fn
        return self.tp / d if d else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


@dataclass(slots=True)
class EvalReport:
    n: int
    accuracy: float
    per_class: dict[str, ClassMetrics] = field(default_factory=dict)
    confusion: dict[tuple[str, str], int] = field(default_factory=dict)

    def format(self) -> str:
        lines = [f"cases: {self.n}    accuracy: {self.accuracy:.3f}", "",
                 f"{'verdict':<14}{'prec':>7}{'recall':>8}{'f1':>7}"]
        for name, m in self.per_class.items():
            lines.append(f"{name:<14}{m.precision:>7.3f}{m.recall:>8.3f}{m.f1:>7.3f}")
        return "\n".join(lines)


def load_cases(path: str) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            d = json.loads(line)
            n = d["norm"]
            cases.append(EvalCase(
                op_statement=d["op_statement"],
                norm=NormRecord(id=n.get("id", "x"), statement=n["statement"],
                                domain=n["domain"], scope=n.get("scope", "all"),
                                kind=n["kind"]),
                expected=Verdict(d["expected"]),
            ))
    return cases


def score(adjudicator: Adjudicator, cases: list[EvalCase]) -> EvalReport:
    per_class: dict[str, ClassMetrics] = defaultdict(ClassMetrics)
    confusion: dict[tuple[str, str], int] = Counter()
    correct = 0
    for c in cases:
        pred = adjudicator.adjudicate(c.op_statement, c.norm).verdict
        exp = c.expected
        confusion[(exp.value, pred.value)] += 1
        if pred is exp:
            correct += 1
            per_class[exp.value].tp += 1
        else:
            per_class[exp.value].fn += 1
            per_class[pred.value].fp += 1
    n = len(cases)
    return EvalReport(n=n, accuracy=correct / n if n else 0.0,
                      per_class=dict(per_class), confusion=dict(confusion))


def main(argv: list[str] | None = None) -> None:
    argv = argv or sys.argv[1:]
    path = argv[0] if argv else "evals/conflict_cases.jsonl"
    from kaixn.llm import AnthropicAdjudicator

    report = score(AnthropicAdjudicator(), load_cases(path))
    print(report.format())


if __name__ == "__main__":
    main()
