"""The tool-calling loop.

``Agent.stream(messages)`` is a generator that drives a multi-turn Anthropic
tool-use loop and yields events as they happen:

  {"type": "token", "text": ...}                     # assistant text delta (live)
  {"type": "step",  "tool": name, "status": running|done, "label": ...}
  {"type": "final", "text": ..., "captured": {tool_name: result, ...}}

It is sync + generator-based on purpose: it composes with kaixn's existing
SSE/StreamingResponse machinery (the playbook job manager) without an async
bridge. Tools run sequentially; PM-chat turns need a handful of calls, not fan-out.

Termination guards (the loop ends itself):
  • no tool_use in a turn  → that turn's text is the answer (normal exit)
  • a repeated identical tool-call signature → converged, stop
  • max_iterations / timeout_seconds → safety backstops
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any, Callable

from agentkit.tools import Tool, anthropic_tool_defs, err, ok


def _default_client():
    from anthropic import Anthropic

    return Anthropic(max_retries=2, timeout=120.0)


class Agent:
    def __init__(self, *, model: str, system: str, tools: list[Tool], ctx: Any = None,
                 max_tokens: int = 2048, max_iterations: int = 8,
                 timeout_seconds: float = 120.0,
                 client_factory: Callable[[], Any] | None = None) -> None:
        self.model = model
        self.system = system
        self.tools = tools
        self.ctx = ctx
        self.max_tokens = max_tokens
        self.max_iterations = max_iterations
        self.timeout_seconds = timeout_seconds
        self._client_factory = client_factory or _default_client
        self._by_name = {t.name: t for t in tools}

    def stream(self, messages: list[dict]) -> Iterator[dict]:
        """Drive the loop over a copy of ``messages`` (role/content dicts),
        yielding token/step/final events. ``captured`` on the final event holds
        each tool's last result so the caller can extract structured output
        (e.g. conflict findings) without re-running anything."""
        client = self._client_factory()
        tool_defs = anthropic_tool_defs(self.tools)
        msgs = list(messages)
        start = time.monotonic()
        seen: set[tuple] = set()
        captured: dict[str, Any] = {}
        text = ""

        for _ in range(self.max_iterations):
            if time.monotonic() - start > self.timeout_seconds:
                break
            kwargs: dict[str, Any] = {"model": self.model, "max_tokens": self.max_tokens,
                                      "messages": msgs}
            if self.system:
                kwargs["system"] = self.system
            if tool_defs:
                kwargs["tools"] = tool_defs
            text = ""
            with client.messages.stream(**kwargs) as stream:
                for delta in stream.text_stream:          # text only; tool_use assembled below
                    text += delta
                    yield {"type": "token", "text": delta}
                final = stream.get_final_message()

            tool_uses = [b for b in final.content if getattr(b, "type", None) == "tool_use"]
            if not tool_uses:                              # plain answer → done
                yield {"type": "final", "text": text, "captured": captured}
                return

            sig = tuple((b.name, json.dumps(b.input, sort_keys=True, default=str))
                        for b in tool_uses)
            if sig in seen:                                # converged on a repeat
                yield {"type": "final", "text": text, "captured": captured}
                return
            seen.add(sig)

            msgs.append({"role": "assistant", "content": final.content})
            results = []
            for b in tool_uses:
                yield {"type": "step", "tool": b.name, "args": dict(b.input or {}),
                       "status": "running"}
                out = self._dispatch(b)
                captured[b.name] = out
                yield {"type": "step", "tool": b.name, "status": "done"}
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
            msgs.append({"role": "user", "content": results})

        yield {"type": "final", "text": text, "captured": captured, "truncated": True}

    def _dispatch(self, block) -> str:
        tool = self._by_name.get(block.name)
        if tool is None:
            return err(f"unknown tool: {block.name}")
        try:
            result = tool.execute(dict(block.input or {}), self.ctx)
            return result if isinstance(result, str) else ok(result)
        except Exception as e:                             # noqa: BLE001 — feed back, don't abort
            return err(e)
