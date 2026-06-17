"""kaixn MCP server — the tool surface from the POC sequence diagram.

Tools (called by the PRD tool and Claude Code):
  connect_repo            onboarding: bootstrap the constitution from a repo
  active_norms            scoped principles/decisions (Claude Code pulls at impl)
  synthesize_proposal     intent → typed operations → structural check
  get_agent_contract      the grounded contract for a synthesized Proposal
  review_implementation   PR event → drift check vs the approved Proposal
  list_indexed_repos      repos with a generated playbook
  get_playbook            a repo's domain + engineering principles + PRD/spec index
  get_doc                 one PRD or Tech Spec's full markdown (build grounding)

The tool *logic* lives in `engine`/`store`/`gate`; this module is the thin MCP
wrapper plus an in-process AppState so the loop is demoable without a DB.
Requires the `server` extra:  pip install -e '.[server,anthropic]'
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from kaixn.embedding import Embedder, get_embedder
from kaixn.engine import (
    AnthropicSynthesizer,
    NaiveSynthesizer,
    Synthesizer,
    synthesize_proposal,
)
from kaixn.gate import WriteGate
from kaixn.llm import HeuristicSplitter
from kaixn.resolution import commit_proposal, make_override
from kaixn.store import InMemoryStore
from kaixn.types import Adjudication, Proposal, Verdict


class _ConsistentAdjudicator:
    """Offline gate adjudicator — passes everything (no semantic check)."""

    def adjudicate(self, statement, norm) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)

    def assess_coverage(self, norm, summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


@dataclass
class AppState:
    """In-process state for the POC. Swap reader for PgNormReader in prod."""

    embedder: Embedder = field(default_factory=get_embedder)
    proposals: dict[str, Proposal] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.store = InMemoryStore(self.embedder)
        # LLM synthesizer + conflict engine when a key is present; offline
        # fallback (structural verdicts only) otherwise.
        from kaixn.review import DriftReviewer

        if os.getenv("ANTHROPIC_API_KEY"):
            from kaixn.conflict import ConflictEngine
            from kaixn.extract import AnthropicExtractor
            from kaixn.llm import AnthropicAdjudicator, AnthropicSplitter

            self.synthesizer: Synthesizer = AnthropicSynthesizer()
            gate_adj = AnthropicAdjudicator()
            self.conflict_engine = ConflictEngine(self.store, gate_adj, self.embedder)
            self.gate = WriteGate(self.store, self.embedder, AnthropicSplitter(), gate_adj)
            self.extractor = AnthropicExtractor()
        else:
            from kaixn.extract import HeuristicExtractor

            gate_adj = _ConsistentAdjudicator()
            self.synthesizer = NaiveSynthesizer()
            self.conflict_engine = None
            self.gate = WriteGate(self.store, self.embedder,
                                  HeuristicSplitter(), gate_adj)
            self.extractor = HeuristicExtractor()
        self.reviewer = DriftReviewer(self.store, gate_adj, self.embedder)


STATE = AppState()


# --- tool implementations (pure-ish; testable without MCP) ------------------
def tool_active_norms(domain: str, scope: str = "all") -> list[dict]:
    principles = STATE.store.active_principles(domain, scope)
    return [{"id": n.id, "kind": n.kind, "domain": n.domain,
             "scope": n.scope, "statement": n.statement} for n in principles]


def tool_synthesize_proposal(intent: str, feature_id: str | None = None) -> dict:
    proposal = synthesize_proposal(
        intent, synthesizer=STATE.synthesizer, reader=STATE.store,
        feature_id=feature_id, conflict_engine=STATE.conflict_engine,
    )
    proposal.id = uuid.uuid4().hex[:16]
    STATE.proposals[proposal.id] = proposal
    report = proposal.conflict_report
    return {
        "proposal_id": proposal.id,
        "operations": [
            {"ord": o.ord, "kind": o.kind.value, "op_type": o.op_type.value,
             "status": o.status.value, "statement": o.statement,
             "target_norm_id": o.target_norm_id, "notes": o.notes}
            for o in proposal.operations
        ],
        "conflict_report": None if report is None else {
            "blocked": report.blocked,
            "counts": report.counts,
            "findings": [
                {"verdict": f.adjudication.verdict.value,
                 "norm": f.adjudication.norm.statement,
                 "norm_kind": f.adjudication.norm.kind,
                 "on": f.op.statement if f.op else None,
                 "resolution": f.adjudication.proposed_resolution}
                for f in report.findings
            ],
        },
        "agent_contract": proposal.agent_contract,
    }


def tool_get_agent_contract(proposal_id: str) -> str:
    proposal = STATE.proposals.get(proposal_id)
    return proposal.agent_contract if proposal else f"unknown proposal {proposal_id}"


def tool_resolve_override(proposal_id: str, finding_index: int,
                          new_decision: str) -> dict:
    """Resolve a decision conflict by appending a `supersede` operation that
    replaces the conflicting decision with `new_decision`."""
    proposal = STATE.proposals.get(proposal_id)
    if proposal is None or proposal.conflict_report is None:
        return {"error": f"no conflict report for proposal {proposal_id}"}
    findings = proposal.conflict_report.findings
    if not 0 <= finding_index < len(findings):
        return {"error": "finding_index out of range"}
    op = make_override(findings[finding_index], new_decision)
    op.ord = len(proposal.operations)
    proposal.operations.append(op)
    return {"proposal_id": proposal_id, "added_operation": {
        "op_type": op.op_type.value, "statement": op.statement,
        "target_norm_id": op.target_norm_id}}


def tool_commit_proposal(proposal_id: str) -> dict:
    """Apply an approved Proposal's norm-operations to the constitution."""
    proposal = STATE.proposals.get(proposal_id)
    if proposal is None:
        return {"error": f"unknown proposal {proposal_id}"}
    res = commit_proposal(proposal, STATE.store, STATE.gate)
    return {"committed": res.committed, "superseded": res.superseded,
            "deprecated": res.deprecated, "edges": res.edges,
            "deferred": [{"statement": o.statement, "reason": r}
                         for o, r in res.deferred]}


def tool_connect_repo(path: str, max_files: int = 50) -> dict:
    """Bootstrap the constitution from a local repo path (reads .md/.txt)."""
    import pathlib

    from kaixn.bootstrap import bootstrap

    root = pathlib.Path(path)
    docs: dict[str, str] = {}
    for p in sorted(root.rglob("*")):
        if p.suffix.lower() in (".md", ".txt") and p.is_file():
            docs[str(p.relative_to(root))] = p.read_text(errors="ignore")
            if len(docs) >= max_files:
                break
    report = bootstrap(docs, extractor=STATE.extractor,
                       gate=STATE.gate, store=STATE.store)
    return {"documents": len(docs), "extracted": report.extracted,
            "proposed": len(report.proposed), "proposed_ids": report.proposed,
            "deferred": [{"statement": s, "reason": r} for s, r in report.deferred]}


def tool_promote_norm(norm_id: str) -> dict:
    from kaixn.bootstrap import promote

    return {"norm_id": norm_id, "promoted": promote(STATE.store, norm_id)}


def tool_review_implementation(proposal_id: str, files: list[str] | None = None,
                               changes: list[str] | None = None,
                               summary: str = "") -> dict:
    """Drift review: PR vs the approved Proposal + active constitution."""
    from kaixn.review import PullRequest

    proposal = STATE.proposals.get(proposal_id)
    if proposal is None:
        return {"error": f"unknown proposal {proposal_id}"}
    pr = PullRequest(files=files or [], changes=changes or [], summary=summary)
    report = STATE.reviewer.review(proposal, pr)
    return {"approve": report.approve,
            "comments": [{"kind": c.kind, "body": c.body} for c in report.comments]}


# --- playbook reads (the PRDs / Tech Specs / principles a builder grounds in) ---
# Backed by the public HTTP API (KAIXN_API_URL, default https://app.kaixn.com) so a
# remote build agent can pull them without database access — the in-process STATE
# above holds only this process's constitution.
def _api_get(path: str, params: dict | None = None):
    import httpx

    base = os.getenv("KAIXN_API_URL", "https://app.kaixn.com").rstrip("/")
    r = httpx.get(base + path, params=params or {}, timeout=30.0)
    r.raise_for_status()
    return r.json()


def tool_list_indexed_repos() -> list[dict]:
    """List the repos kaixn has a playbook for (PRDs, Tech Specs, domain model, and
    engineering principles are available for each). Start here to find a repo."""
    try:
        return _api_get("/api/repos").get("repos", [])
    except Exception as e:  # noqa: BLE001
        return [{"error": str(e)[:200]}]


def tool_get_playbook(repo_url: str) -> dict:
    """A repo's playbook overview: its domain entities, the mined ENGINEERING
    PRINCIPLES it follows, and the index of PRDs + Tech Specs (titles, slugs,
    groups). Call this first when building for a repo — it tells you which PRD and
    which Tech Specs to read next (via get_doc) and what principles to honor."""
    try:
        return _api_get("/api/playbook", {"repo": repo_url})
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def tool_get_doc(repo_url: str, kind: str, slug: str) -> dict:
    """One full document's markdown. kind='prd' for a customer-facing PRD, 'spec'
    for a Tech Spec (with its runtime sequence diagram). `slug` comes from the
    playbook index. Read the PRD you're implementing plus its related Tech Specs
    before writing code."""
    try:
        return _api_get("/api/doc", {"repo": repo_url, "kind": kind, "slug": slug})
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


def build_server():
    """Construct the FastMCP server with the tool surface."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("kaixn")
    mcp.tool()(tool_active_norms)
    mcp.tool()(tool_synthesize_proposal)
    mcp.tool()(tool_get_agent_contract)
    mcp.tool()(tool_resolve_override)
    mcp.tool()(tool_commit_proposal)
    mcp.tool()(tool_connect_repo)
    mcp.tool()(tool_promote_norm)
    mcp.tool()(tool_review_implementation)
    mcp.tool()(tool_list_indexed_repos)
    mcp.tool()(tool_get_playbook)
    mcp.tool()(tool_get_doc)
    return mcp


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
