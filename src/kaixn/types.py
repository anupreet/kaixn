"""Core value types shared across kaixn's app layer.

Mirrors the v0.2 schema (`migrations/001_init.sql`) but kept as plain
dataclasses so the gate/engine logic has no DB or LLM dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# ltree labels accept letters, digits, underscores and hyphens; anything else
# (spaces, slashes, punctuation) is a syntax error when cast to ltree. LLM
# extractors and synthesizers emit free-form scopes like "all.product design"
# or "all.user/auth", so every scope is normalized at construction.
_LTREE_BAD = re.compile(r"[^A-Za-z0-9_-]+")


def normalize_scope(scope: str | None) -> str:
    """Coerce any string into a valid ltree path rooted at `all`.

    Each dot-separated label has illegal characters folded to `_`; empty labels
    are dropped; the path is rooted at `all` so scope_governs/`@>` behave. Valid
    inputs (e.g. "all.product.billing") are returned unchanged."""
    if not scope:
        return "all"
    labels = []
    for raw in str(scope).split("."):
        label = _LTREE_BAD.sub("_", raw).strip("_-")
        if label:
            labels.append(label)
    if not labels:
        return "all"
    if labels[0] != "all":
        labels.insert(0, "all")
    return ".".join(labels)


# --- norms -----------------------------------------------------------------
@dataclass(slots=True)
class NormCandidate:
    """A proposed norm not yet committed to the constitution.

    Produced by an `assert` operation (a Proposal) or by bootstrap.
    """

    statement: str
    domain: str               # product | technical | product_design | ux
    scope: str = "all"        # ltree path, e.g. all.product.billing
    kind: str = "decision"    # principle | decision
    rationale: str = ""
    embedding: list[float] | None = None

    def __post_init__(self) -> None:
        self.scope = normalize_scope(self.scope)


@dataclass(slots=True)
class NormRecord:
    """An existing norm in the store."""

    id: str
    statement: str
    domain: str
    scope: str
    kind: str
    rationale: str = ""
    status: str = "active"    # active | proposed | superseded | deprecated
    embedding: list[float] | None = None


# --- adjudication ----------------------------------------------------------
class Verdict(str, Enum):
    CONSISTENT = "consistent"
    CONFLICT = "conflict"
    TENSION = "tension"
    GAP = "gap"


@dataclass(slots=True)
class Adjudication:
    """One (candidate × norm) judgment from the conflict engine."""

    verdict: Verdict
    norm: NormRecord
    evidence: str = ""
    proposed_resolution: str = ""


# --- write-gate output -----------------------------------------------------
@dataclass(slots=True)
class DupMatch:
    norm: NormRecord
    score: float
    method: str               # 'embedding' | 'lexical'


class GateDecision(str, Enum):
    ACCEPT = "accept"                   # novel, consistent → safe to commit
    SPLIT = "split"                     # compound → break into atomic parts first
    MERGE_CANDIDATE = "merge_candidate" # near-duplicate of an active norm
    CONFLICT = "conflict"               # contradicts an active norm


@dataclass(slots=True)
class GateResult:
    decision: GateDecision
    candidate: NormCandidate
    splits: list[str] = field(default_factory=list)
    duplicates: list[DupMatch] = field(default_factory=list)
    conflicts: list[Adjudication] = field(default_factory=list)
    notes: str = ""


# --- proposals & operations (v0.2 core) ------------------------------------
class OpKind(str, Enum):
    NORM = "norm"
    CODE = "code"


class OpType(str, Enum):
    ASSERT = "assert"
    MODIFY = "modify"
    DEPRECATE = "deprecate"
    SUPERSEDE = "supersede"
    IMPLEMENT = "implement"


class OpStatus(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    APPLIED = "applied"
    CONFLICT = "conflict"
    NEEDS_GROUNDING = "needs_grounding"


# Which op_types are valid for which kind (mirrors the schema CHECK constraint).
_NORM_OPS = {OpType.ASSERT, OpType.MODIFY, OpType.DEPRECATE, OpType.SUPERSEDE}
_CODE_OPS = {OpType.IMPLEMENT}


@dataclass(slots=True)
class Operation:
    """A single typed change-against-state. The PM never authors these;
    they are synthesized from intent and reviewed."""

    kind: OpKind
    op_type: OpType
    statement: str
    rationale: str = ""
    ord: int = 0
    status: OpStatus = OpStatus.PROPOSED
    notes: str = ""

    # norm-op fields
    domain: str | None = None         # for assert / the produced norm
    scope: str = "all"
    norm_kind: str = "decision"       # principle | decision (assert)
    target_norm_id: str | None = None # modify | deprecate | supersede target

    # code-op fields
    target_location: str = ""
    acceptance_criteria: str = ""

    def __post_init__(self) -> None:
        self.scope = normalize_scope(self.scope)

    def kind_type_coherent(self) -> bool:
        if self.kind is OpKind.NORM:
            return self.op_type in _NORM_OPS
        return self.op_type in _CODE_OPS


@dataclass(slots=True)
class Proposal:
    """The owned plan for a feature: an ordered set of typed operations."""

    intent_text: str
    operations: list[Operation] = field(default_factory=list)
    feature_id: str | None = None
    version: int = 1
    id: str | None = None
    agent_contract: str = ""
    conflict_report: "object | None" = None  # conflict.ConflictReport when run
