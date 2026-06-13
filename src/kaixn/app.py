"""kaixn application service — the store-agnostic engine behind the HTTP API.

`Kaixn` wires the embedder, constitution store, synthesizer, write gate, conflict
engine, extractor and drift reviewer from environment configuration, then exposes
the end-to-end product loop as plain methods:

    connect_repo_url -> bootstrap norms from a GitHub repo
    list_norms / promote_norm
    synthesize     -> intent -> typed operations + conflict report
    resolve_override / commit / review

It is the same surface as `server.py`'s MCP tools, but parameterized over the
store so the web app can run on Postgres (`PgStore`) in production and the
in-memory store for a zero-dependency demo.

Environment:
    KAIXN_DSN          postgres DSN -> use PgStore; unset -> InMemoryStore
    KAIXN_EMBEDDER     fake | ollama | openai   (embedding.get_embedder)
    ANTHROPIC_API_KEY  present -> LLM synthesis/adjudication/extraction
    OPENAI_API_KEY     required when KAIXN_EMBEDDER=openai
"""

from __future__ import annotations

import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass

from kaixn.bootstrap import bootstrap, promote
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
from kaixn.review import DriftReviewer, PullRequest
from kaixn.store import InMemoryStore, PgStore, pg_connect
from kaixn.types import Adjudication, Proposal, Verdict

_GITHUB_RE = re.compile(
    r"^https://(www\.)?github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/?$"
)


class _ConsistentAdjudicator:
    """Offline gate adjudicator — passes everything (no semantic check)."""

    def adjudicate(self, statement, norm) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)

    def assess_coverage(self, norm, summary) -> Adjudication:
        return Adjudication(Verdict.CONSISTENT, norm)


def normalize_repo_url(url: str) -> str:
    """Validate + normalize a GitHub URL to an https clone URL.

    Accepts ``github.com/org/repo``, with or without scheme / ``.git`` / trailing
    slash. Raises ValueError for anything that is not a GitHub repo URL — this is
    a remote-fetch boundary, so we are strict about what we will clone.
    """
    u = url.strip()
    if not u:
        raise ValueError("empty repo URL")
    if u.startswith("git@github.com:"):
        u = "https://github.com/" + u[len("git@github.com:"):]
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    u = u.replace("http://", "https://", 1)
    if u.endswith(".git"):
        u = u[:-4]
    if not _GITHUB_RE.match(u):
        raise ValueError(f"not a GitHub repo URL: {url}")
    return u


@dataclass
class Kaixn:
    """The product-development service. One instance per process."""

    embedder: Embedder
    store: object  # InMemoryStore | PgStore
    synthesizer: Synthesizer
    conflict_engine: object | None
    gate: WriteGate
    extractor: object
    reviewer: DriftReviewer
    llm_enabled: bool
    backend: str  # "postgres" | "memory"

    # proposals live in-process keyed by id (they are transient until committed)
    proposals: dict[str, Proposal] | None = None

    def __post_init__(self) -> None:
        if self.proposals is None:
            self.proposals = {}

    # -- construction --------------------------------------------------------
    @classmethod
    def from_env(cls) -> "Kaixn":
        embedder = get_embedder()
        dsn = os.getenv("KAIXN_DSN")
        if dsn:
            store: object = PgStore(pg_connect(dsn), embedder)
            backend = "postgres"
        else:
            store = InMemoryStore(embedder)
            backend = "memory"

        llm = bool(os.getenv("ANTHROPIC_API_KEY"))
        if llm:
            from kaixn.conflict import ConflictEngine
            from kaixn.extract import AnthropicExtractor
            from kaixn.llm import AnthropicAdjudicator, AnthropicSplitter

            synthesizer: Synthesizer = AnthropicSynthesizer()
            gate_adj: object = AnthropicAdjudicator()
            conflict_engine: object | None = ConflictEngine(store, gate_adj, embedder)
            gate = WriteGate(store, embedder, AnthropicSplitter(), gate_adj)
            extractor: object = AnthropicExtractor()
        else:
            from kaixn.extract import HeuristicExtractor

            synthesizer = NaiveSynthesizer()
            gate_adj = _ConsistentAdjudicator()
            conflict_engine = None
            gate = WriteGate(store, embedder, HeuristicSplitter(), gate_adj)
            extractor = HeuristicExtractor()

        return cls(
            embedder=embedder, store=store, synthesizer=synthesizer,
            conflict_engine=conflict_engine, gate=gate, extractor=extractor,
            reviewer=DriftReviewer(store, gate_adj, embedder),
            llm_enabled=llm, backend=backend,
        )

    # -- status --------------------------------------------------------------
    def status(self) -> dict:
        return {
            "backend": self.backend,
            "embedder": type(self.embedder).__name__,
            "embed_dim": getattr(self.embedder, "dim", None),
            "llm_enabled": self.llm_enabled,
            "active_norms": len(self.store.all_norms(status="active")),
            "proposed_norms": len(self.store.all_norms(status="proposed")),
        }

    # -- repo onboarding -----------------------------------------------------
    def connect_repo_url(self, repo_url: str, *, max_files: int = 50) -> dict:
        """Clone a GitHub repo (shallow), mine .md/.txt docs into proposed norms."""
        url = normalize_repo_url(repo_url)
        tmp = tempfile.mkdtemp(prefix="kaixn-clone-")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", url, tmp],
                check=True, capture_output=True, text=True, timeout=120,
            )
            return self._bootstrap_path(tmp, url=url, max_files=max_files)
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"git clone failed: {e.stderr.strip()[:400]}") from e
        except subprocess.TimeoutExpired as e:
            raise RuntimeError("git clone timed out after 120s") from e
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _bootstrap_path(self, path: str, *, url: str, max_files: int) -> dict:
        root = pathlib.Path(path)
        docs: dict[str, str] = {}
        for p in sorted(root.rglob("*")):
            if p.suffix.lower() in (".md", ".txt") and p.is_file():
                try:
                    docs[str(p.relative_to(root))] = p.read_text(errors="ignore")
                except OSError:
                    continue
                if len(docs) >= max_files:
                    break
        report = bootstrap(docs, extractor=self.extractor,
                           gate=self.gate, store=self.store)
        return {
            "repo": url,
            "documents": len(docs),
            "extracted": report.extracted,
            "proposed": len(report.proposed),
            "proposed_norms": [self._norm_dict(self.store.get(nid))
                               for nid in report.proposed
                               if self.store.get(nid) is not None],
            "deferred": [{"statement": s, "reason": r} for s, r in report.deferred],
        }

    # -- norms ---------------------------------------------------------------
    @staticmethod
    def _norm_dict(n) -> dict:
        return {"id": n.id, "kind": n.kind, "domain": n.domain,
                "scope": n.scope, "status": n.status, "statement": n.statement}

    def list_norms(self, status: str | None = None) -> list[dict]:
        return [self._norm_dict(n) for n in self.store.all_norms(status=status)]

    def promote_norm(self, norm_id: str) -> dict:
        return {"norm_id": norm_id, "promoted": promote(self.store, norm_id)}

    # -- proposals -----------------------------------------------------------
    def synthesize(self, intent: str, feature_id: str | None = None) -> dict:
        proposal = synthesize_proposal(
            intent, synthesizer=self.synthesizer, reader=self.store,
            feature_id=feature_id, conflict_engine=self.conflict_engine,
        )
        proposal.id = uuid.uuid4().hex[:16]
        self.proposals[proposal.id] = proposal
        return self._proposal_dict(proposal)

    def _proposal_dict(self, proposal: Proposal) -> dict:
        report = proposal.conflict_report
        return {
            "proposal_id": proposal.id,
            "intent": proposal.intent_text,
            "operations": [
                {"ord": o.ord, "kind": o.kind.value, "op_type": o.op_type.value,
                 "status": o.status.value, "statement": o.statement,
                 "target_norm_id": o.target_norm_id,
                 "target_location": o.target_location, "notes": o.notes}
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

    def get_proposal(self, proposal_id: str) -> dict | None:
        p = self.proposals.get(proposal_id)
        return self._proposal_dict(p) if p else None

    def resolve_override(self, proposal_id: str, finding_index: int,
                         new_decision: str) -> dict:
        proposal = self.proposals.get(proposal_id)
        if proposal is None or proposal.conflict_report is None:
            return {"error": f"no conflict report for proposal {proposal_id}"}
        findings = proposal.conflict_report.findings
        if not 0 <= finding_index < len(findings):
            return {"error": "finding_index out of range"}
        op = make_override(findings[finding_index], new_decision)
        op.ord = len(proposal.operations)
        proposal.operations.append(op)
        return self._proposal_dict(proposal)

    def commit(self, proposal_id: str) -> dict:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            return {"error": f"unknown proposal {proposal_id}"}
        res = commit_proposal(proposal, self.store, self.gate)
        return {"committed": res.committed, "superseded": res.superseded,
                "deprecated": res.deprecated, "edges": res.edges,
                "deferred": [{"statement": o.statement, "reason": r}
                             for o, r in res.deferred]}

    def review(self, proposal_id: str, *, files=None, changes=None,
               summary: str = "") -> dict:
        proposal = self.proposals.get(proposal_id)
        if proposal is None:
            return {"error": f"unknown proposal {proposal_id}"}
        pr = PullRequest(files=files or [], changes=changes or [], summary=summary)
        report = self.reviewer.review(proposal, pr)
        return {"approve": report.approve,
                "comments": [{"kind": c.kind, "body": c.body}
                             for c in report.comments]}
