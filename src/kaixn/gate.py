"""The write gate.

Every norm entering the constitution — whether from an `assert` operation in a
Proposal or from bootstrap — passes through here. It keeps the constitution
atomic, duplicate-free, and internally consistent, which is what makes the
conflict engine's retrieval trustworthy downstream.

Three stages, cheapest first:

  1. atomicity   — compound statement?            → SPLIT
  2. dedup       — near-duplicate of an active norm? → MERGE_CANDIDATE
  3. consistency — contradicts an active norm?     → CONFLICT
  otherwise                                        → ACCEPT

The gate never writes. It returns a decision; the caller (Memory API) commits,
splits-and-resubmits, merges, or routes the conflict to a human.
"""

from __future__ import annotations

from dataclasses import dataclass

from kaixn.llm import Adjudicator, AtomicitySplitter
from kaixn.similarity import cosine, jaccard
from kaixn.store import NormReader
from kaixn.embedding import Embedder
from kaixn.types import (
    Adjudication,
    DupMatch,
    GateDecision,
    GateResult,
    NormCandidate,
    Verdict,
)


@dataclass(slots=True)
class GateConfig:
    embedding_dup_threshold: float = 0.86   # cosine ≥ → treat as duplicate
    lexical_dup_threshold: float = 0.60     # token Jaccard ≥ → treat as duplicate
    neighbor_top_k: int = 8
    check_principles: bool = True           # always adjudicate vs governing principles
    conflict_verdicts: tuple[Verdict, ...] = (Verdict.CONFLICT, Verdict.TENSION)


class WriteGate:
    def __init__(
        self,
        reader: NormReader,
        embedder: Embedder,
        splitter: AtomicitySplitter,
        adjudicator: Adjudicator,
        config: GateConfig | None = None,
    ) -> None:
        self._reader = reader
        self._embedder = embedder
        self._splitter = splitter
        self._adjudicator = adjudicator
        self._cfg = config or GateConfig()

    def evaluate(self, candidate: NormCandidate, *,
                 ignore_norm_ids: set[str] | None = None) -> GateResult:
        # `ignore_norm_ids` excludes specific norms from dedup/consistency — used
        # by `supersede`, which would otherwise "conflict" with the very norm it
        # is replacing.
        ignore = ignore_norm_ids or set()

        # 1. atomicity — one claim per norm.
        parts = self._splitter.split(candidate.statement)
        if len(parts) > 1:
            return GateResult(
                GateDecision.SPLIT, candidate, splits=parts,
                notes=f"compound statement → {len(parts)} atomic claims",
            )

        # ensure we have an embedding for dedup.
        if candidate.embedding is None:
            candidate.embedding = self._embedder.embed([candidate.statement])[0]

        neighbors = [n for n in self._reader.neighbors(candidate, top_k=self._cfg.neighbor_top_k)
                     if n.id not in ignore]

        # 2. dedup — embedding OR lexical near-match.
        dups: list[DupMatch] = []
        for n in neighbors:
            cos = cosine(candidate.embedding, n.embedding)
            lex = jaccard(candidate.statement, n.statement)
            if cos >= self._cfg.embedding_dup_threshold:
                dups.append(DupMatch(n, round(cos, 4), "embedding"))
            elif lex >= self._cfg.lexical_dup_threshold:
                dups.append(DupMatch(n, round(lex, 4), "lexical"))
        if dups:
            dups.sort(key=lambda d: d.score, reverse=True)
            return GateResult(
                GateDecision.MERGE_CANDIDATE, candidate, duplicates=dups,
                notes=f"near-duplicate of {len(dups)} active norm(s)",
            )

        # 3. self-consistency — must not contradict an active norm.
        #    Check semantic neighbors + (always) governing principles.
        to_check: dict[str, object] = {n.id: n for n in neighbors}
        if self._cfg.check_principles:
            for p in self._reader.active_principles(candidate.domain, candidate.scope):
                if p.id not in ignore:
                    to_check.setdefault(p.id, p)

        conflicts: list[Adjudication] = []
        for norm in to_check.values():
            adj = self._adjudicator.adjudicate(candidate.statement, norm)  # type: ignore[arg-type]
            if adj.verdict in self._cfg.conflict_verdicts:
                conflicts.append(adj)
        if conflicts:
            return GateResult(
                GateDecision.CONFLICT, candidate, conflicts=conflicts,
                notes=f"contradicts {len(conflicts)} active norm(s)",
            )

        return GateResult(GateDecision.ACCEPT, candidate, notes="novel & consistent")
