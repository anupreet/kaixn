"""Tool abstraction for the agent loop.

A tool is a *source of context*, not a procedure: it has a name, a description
the model reads to decide when it's useful, a JSON-Schema for its arguments, and
an ``execute`` that returns a JSON-serializable result. Results and errors are
both returned (never raised) so a failed tool feeds back into the loop as context
the model can recover from, rather than aborting the turn.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, runtime_checkable

# Cap a single tool result so one verbose tool can't blow the context window.
_MAX_RESULT_CHARS = 20000


@runtime_checkable
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict

    def execute(self, args: dict, ctx: Any) -> Any:
        """Run the tool. Return a JSON-serializable value; raising is fine — the
        loop converts the exception into an error result fed back to the model."""
        ...


def anthropic_tool_defs(tools: list[Tool]) -> list[dict]:
    """The Anthropic ``tools=`` payload: name + description + input_schema."""
    return [{"name": t.name, "description": t.description,
             "input_schema": t.input_schema} for t in tools]


def ok(results: Any) -> str:
    """A success tool_result payload (string content for Anthropic)."""
    return json.dumps({"success": True, "results": results}, default=str)[:_MAX_RESULT_CHARS]


def err(message: Any) -> str:
    """An error tool_result payload — fed back so the model can recover."""
    return json.dumps({"success": False, "error": str(message)[:500]})
