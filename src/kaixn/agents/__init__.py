"""kaixn's PM copilot — a conversational layer over the constitution engine.

Built on the standalone ``agentkit`` framework: this package supplies only the
product-specific pieces — the read-only tools over kaixn's engine
(``agents.tools``), the system-prompt composition (``agents.prompts``), and the
chat session/service wiring (``agents.copilot``). The tool-calling loop itself
lives in ``agentkit`` and stays reusable.
"""

from kaixn.agents.copilot import ChatService

__all__ = ["ChatService"]
