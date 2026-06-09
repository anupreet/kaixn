"""Dogfood: point kaixn at the kaixn repo and run the whole loop offline.

  python scripts/dogfood.py

Offline (no API key) this uses the heuristic extractor / naive synthesizer and
a deterministic keyword adjudicator so the conflict + drift checks actually fire
on a real constitution. With ANTHROPIC_API_KEY set, swap in the LLM components.
"""

from __future__ import annotations

import collections
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from kaixn.bootstrap import bootstrap, promote          # noqa: E402
from kaixn.conflict import ConflictEngine                # noqa: E402
from kaixn.embedding import FakeEmbedder                 # noqa: E402
from kaixn.engine import NaiveSynthesizer, synthesize_proposal  # noqa: E402
from kaixn.extract import HeuristicExtractor             # noqa: E402
from kaixn.gate import WriteGate                         # noqa: E402
from kaixn.grounding import RepoGrounder, ground_proposal  # noqa: E402
from kaixn.llm import HeuristicSplitter                  # noqa: E402
from kaixn.review import DriftReviewer, PullRequest      # noqa: E402
from kaixn.store import InMemoryStore                    # noqa: E402
from kaixn.types import Adjudication, Verdict            # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]


class KeywordAdjudicator:
    """Deterministic stand-in for the LLM judge: a change CONFLICTS with a norm
    when the norm says never/no/must-not about a topic the change does. Crude,
    but lets the offline demo show real conflict/drift behavior."""

    _NEG = ("never", "must not", "no ", "not ", "without", "avoid", "don't")

    def _topic(self, text: str) -> set[str]:
        from kaixn.similarity import _tokens
        return _tokens(text)

    def adjudicate(self, statement, norm) -> Adjudication:
        n = norm.statement.lower()
        negated = any(k in n for k in self._NEG)
        overlap = len(self._topic(statement) & self._topic(norm.statement))
        if negated and overlap >= 2:
            return Adjudication(Verdict.CONFLICT, norm,
                                evidence=f"change touches '{norm.statement}'")
        return Adjudication(Verdict.CONSISTENT, norm)

    def assess_coverage(self, norm, summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


def banner(t):
    print(f"\n{'='*64}\n{t}\n{'='*64}")


def main() -> None:
    emb = FakeEmbedder()
    store = InMemoryStore(emb)
    adj = KeywordAdjudicator()
    gate = WriteGate(store, emb, HeuristicSplitter(), adj)

    banner("1. connect_repo → bootstrap constitution from kaixn's own docs")
    docs = {str(p.relative_to(ROOT)): p.read_text(errors="ignore")
            for p in (ROOT / "docs").rglob("*.md")}
    docs[str("README.md")] = (ROOT / "README.md").read_text(errors="ignore")
    report = bootstrap(docs, extractor=HeuristicExtractor(), gate=gate, store=store)
    print(f"docs={len(docs)}  extracted={report.extracted}  "
          f"proposed={len(report.proposed)}  deferred={len(report.deferred)}")
    by_domain = collections.Counter(store.get(n).domain for n in report.proposed)
    by_kind = collections.Counter(store.get(n).kind for n in report.proposed)
    print("by domain:", dict(by_domain), "| by kind:", dict(by_kind))
    print("sample principles:")
    for nid in report.proposed:
        n = store.get(nid)
        if n.kind == "principle":
            print(f"  • [{n.domain}] {n.statement[:90]}")
    print("deferred (gate caught):")
    for stmt, reason in report.deferred[:5]:
        print(f"  - {reason}: {stmt[:70]}")

    banner("2. curator promotes all candidates → active")
    for nid in report.proposed:
        promote(store, nid)
    active = sum(len(store.active_principles(d, "all"))
                for d in ("product", "technical", "product_design", "ux"))
    print(f"active principles now: {active}")

    banner("3. PM intent → synthesize_proposal → ground → conflict check")
    intent = ("Add a Postgres-backed store. Block the request thread on the "
              "database call until it returns. Skip writing any tests for it.")
    engine = ConflictEngine(store, adj, emb)
    proposal = synthesize_proposal(intent, synthesizer=NaiveSynthesizer(),
                                   reader=store, conflict_engine=engine)
    ground_proposal(proposal, RepoGrounder(str(ROOT / "src")))
    print(f"operations: {len(proposal.operations)}")
    for o in proposal.operations:
        print(f"  [{o.status.value}] {o.op_type.value}: {o.statement[:60]}"
              f"  -> {o.target_location or '(ungrounded)'}")
    rep = proposal.conflict_report
    print(f"\nconflict report — blocked={rep.blocked} counts={rep.counts}")
    for f in rep.findings:
        print(f"  • {f.adjudication.verdict.value} vs \"{f.adjudication.norm.statement[:60]}\"")

    banner("4. review_implementation → drift vs approved Proposal")
    pr = PullRequest(
        files=["src/kaixn/store.py"],   # implements one op, misses the others' files
        changes=["made the database call block the request thread synchronously"],
    )
    review = DriftReviewer(store, adj, emb).review(proposal, pr)
    print(f"approve={review.approve}")
    for c in review.comments:
        print(f"  - {c.kind}: {c.body[:90]}")


if __name__ == "__main__":
    main()
