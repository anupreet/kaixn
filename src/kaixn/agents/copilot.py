"""Chat sessions for the PM copilot.

A ``ChatService`` holds in-process sessions (transient, like ``Kaixn.proposals``);
each ``ChatSession`` keeps the conversation history and, per turn, runs an
``agentkit.Agent`` configured with kaixn's tools + the composed system prompt. The
session streams the agent's events and, on the final turn, extracts any collision
findings (from the conflict-engine tool result) so the UI can render them.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

from agentkit import Agent
from kaixn.agents.prompts import build_system_prompt
from kaixn.agents.tools import PMContext, all_tools

_MODEL = "claude-sonnet-4-6"
_HISTORY_TURNS = 20            # keep the last N messages to bound context


def _repo_context(repo: str) -> str:
    """A short, static lay-of-the-land for the system prompt (no tool call needed):
    domain entities + the playbook's doc titles, if the repo is indexed."""
    try:
        from kaixn import playbook_store as ps

        pb = ps.from_env().get_playbook(repo)
    except Exception:                                  # noqa: BLE001 — context is best-effort
        pb = None
    if not pb:
        return "This repo is not indexed yet — use your tools to ground anything specific."
    ents = ", ".join(e.get("name", "") for e in (pb.get("entities") or [])[:14])
    docs = ", ".join(d.get("title", "") for d in (pb.get("docs") or [])[:16])
    parts = []
    if ents:
        parts.append(f"Domain objects: {ents}.")
    if docs:
        parts.append(f"Documented areas: {docs}.")
    parts.append("Use the tools for specifics — this is only an orientation.")
    return " ".join(parts)


def _extract_collisions(captured: dict) -> tuple[str | None, list]:
    """Pull (proposal_id, findings) out of the conflict-check tool result, if any."""
    raw = captured.get("check_feature_conflicts")
    if not raw:
        return None, []
    try:
        results = (json.loads(raw).get("results")) or {}
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None, []
    report = results.get("conflict_report") or {}
    return results.get("proposal_id"), (report.get("findings") or [])


class ChatSession:
    def __init__(self, *, service, repo: str, persona: str | None,
                 session_id: str, model: str = _MODEL, client_factory=None) -> None:
        self.service = service
        self.repo = repo
        self.persona = persona
        self.id = session_id
        self.model = model
        self.history: list[dict] = []
        self._client_factory = client_factory   # injectable for tests; None → real
        self._system = build_system_prompt(
            repo=repo, persona=persona, repo_context=_repo_context(repo))

    def stream(self, message: str) -> Iterator[dict]:
        """Run one turn. Yields token/step events, then a terminal `done` event
        carrying the answer, any collision findings, and the session id."""
        self.history.append({"role": "user", "content": message})
        agent = Agent(model=self.model, system=self._system, tools=all_tools(),
                      ctx=PMContext(service=self.service, repo=self.repo),
                      client_factory=self._client_factory)
        answer, captured = "", {}
        for ev in agent.stream(self.history[-_HISTORY_TURNS:]):
            kind = ev.get("type")
            if kind == "token":
                yield {"type": "token", "text": ev["text"]}
            elif kind == "step":
                yield {"type": "step", "tool": ev.get("tool"), "status": ev.get("status")}
            elif kind == "final":
                answer = ev.get("text", "")
                captured = ev.get("captured", {})
        self.history.append({"role": "assistant", "content": answer})
        proposal_id, findings = _extract_collisions(captured)
        yield {"type": "done", "text": answer, "session_id": self.id,
               "proposal_id": proposal_id, "conflict_findings": findings}


class ChatService:
    """Process-wide registry of chat sessions over a shared Kaixn service."""

    def __init__(self, service) -> None:
        self.service = service
        self._sessions: dict[str, ChatSession] = {}

    def session(self, repo: str, session_id: str | None, persona: str | None) -> ChatSession:
        if session_id and session_id in self._sessions:
            return self._sessions[session_id]
        sid = session_id or uuid.uuid4().hex[:16]
        sess = ChatSession(service=self.service, repo=repo, persona=persona, session_id=sid)
        self._sessions[sid] = sess
        return sess
