"""Code grounding — turn an abstract `implement` op into a located, checkable
change: target_location, acceptance_criteria. Runs on *accepted* ops only.

`RepoGrounder` works offline by matching op keywords to real repo files (so the
drift coverage check has a `target_location` to bite on). `AnthropicGrounder` is
the real path (reads code, proposes before/after).
"""

from __future__ import annotations

import pathlib
import re
from typing import Protocol

from kaixn.similarity import _tokens  # reuse stopword-filtered tokenizer
from kaixn.types import OpKind, OpStatus, Proposal, Operation


class Grounder(Protocol):
    def ground(self, op: Operation) -> Operation: ...


class RepoGrounder:
    """Locate the most relevant existing file for a code op by token overlap."""

    def __init__(self, root: str, suffixes: tuple[str, ...] = (".py",)) -> None:
        self._files = [
            str(p) for p in pathlib.Path(root).rglob("*")
            if p.is_file() and p.suffix in suffixes and ".git" not in p.parts
        ]

    def ground(self, op: Operation) -> Operation:
        if op.kind is not OpKind.CODE or op.target_location:
            return op
        want = _tokens(op.statement)
        best, best_score = "", 0
        for f in self._files:
            score = len(want & _tokens(f.replace("/", " ").replace("_", " ")))
            if score > best_score:
                best, best_score = f, score
        if best:
            op.target_location = best
            op.acceptance_criteria = op.statement
            if op.status is OpStatus.NEEDS_GROUNDING:
                op.status = OpStatus.PROPOSED
        return op


class AnthropicGrounder:
    def __init__(self, root: str, model: str = "claude-sonnet-4-6") -> None:
        self._root = root
        self.model = model

    def ground(self, op: Operation) -> Operation:
        if op.kind is not OpKind.CODE or op.target_location:
            return op
        import json

        from anthropic import Anthropic

        tree = "\n".join(sorted(
            str(p) for p in pathlib.Path(self._root).rglob("*.py"))[:400])
        prompt = (
            "Ground this implementation step against the repo. Pick the file(s) "
            "to change and write acceptance criteria. Reply JSON: "
            '{"target_location","acceptance_criteria"}.\n\n'
            f"STEP: {op.statement}\n\nFILES:\n{tree}"
        )
        client = Anthropic()
        msg = client.messages.create(model=self.model, max_tokens=512,
                                     messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text
        try:
            d = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
            op.target_location = d.get("target_location", "")
            op.acceptance_criteria = d.get("acceptance_criteria", op.statement)
            if op.target_location and op.status is OpStatus.NEEDS_GROUNDING:
                op.status = OpStatus.PROPOSED
        except Exception:
            pass
        return op


def ground_proposal(proposal: Proposal, grounder: Grounder) -> Proposal:
    """Ground every code op in place, then refresh the rendered contract."""
    from kaixn.engine import render_agent_contract

    for op in proposal.operations:
        grounder.ground(op)
    proposal.agent_contract = render_agent_contract(proposal)
    return proposal
