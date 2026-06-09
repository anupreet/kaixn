"""kaixn — product development layer for the agentic world."""

from kaixn.engine import (
    render_agent_contract,
    structural_check,
    synthesize_proposal,
)
from kaixn.conflict import ConflictEngine, ConflictReport, Finding
from kaixn.gate import GateConfig, WriteGate
from kaixn.resolution import CommitResult, commit_proposal, make_override
from kaixn.types import (
    GateDecision,
    GateResult,
    NormCandidate,
    NormRecord,
    Operation,
    OpKind,
    OpStatus,
    OpType,
    Proposal,
    Verdict,
)

__all__ = [
    "WriteGate",
    "GateConfig",
    "ConflictEngine",
    "ConflictReport",
    "Finding",
    "commit_proposal",
    "make_override",
    "CommitResult",
    "synthesize_proposal",
    "structural_check",
    "render_agent_contract",
    "GateDecision",
    "GateResult",
    "NormCandidate",
    "NormRecord",
    "Operation",
    "OpKind",
    "OpType",
    "OpStatus",
    "Proposal",
    "Verdict",
]
