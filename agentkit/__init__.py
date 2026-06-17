"""agentkit — a small, domain-agnostic agent framework.

A reusable tool-calling loop over the Anthropic SDK, a `Tool` abstraction, and a
prompt-composition helper. It carries NO product knowledge: callers supply the
system prompt, the tools, and an opaque ``ctx`` that the tools close over. kaixn's
PM copilot (``kaixn.agents``) is one consumer; the framework stays reusable.

The design follows goal-oriented prompt-engineering + tool-calling principles:
tools are sources of context (not a fixed sequence), the loop converges on its
own (iteration / repeat-call / timeout guards), and turns stream so a UI can
render tokens and tool steps live.
"""

from agentkit.agent import Agent
from agentkit.prompt import block, compose
from agentkit.tools import Tool, anthropic_tool_defs, err, ok

__all__ = ["Agent", "Tool", "anthropic_tool_defs", "ok", "err", "compose", "block"]
