"""agentkit loop tests — a scripted fake Anthropic client (no network)."""

from agentkit import Agent, anthropic_tool_defs, block, compose


class _Block:
    def __init__(self, type, **kw):
        self.type = type
        self.__dict__.update(kw)


class _Final:
    def __init__(self, content):
        self.content = content


class _StreamCM:
    def __init__(self, deltas, content):
        self._deltas, self._content = deltas, content

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @property
    def text_stream(self):
        return iter(self._deltas)

    def get_final_message(self):
        return _Final(self._content)


class _FakeClient:
    """Scripted turns: each turn = {"text": [deltas], "content": [blocks]}."""

    def __init__(self, turns):
        self._turns, self._i = turns, 0

        class _M:
            def stream(_self, **kwargs):
                turn = self._turns[self._i]
                self._i += 1
                return _StreamCM(turn["text"], turn["content"])

        self.messages = _M()


class _EchoTool:
    name = "echo"
    description = "Echo the input back."
    input_schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}

    def __init__(self):
        self.calls = []

    def execute(self, args, ctx):
        self.calls.append(args)
        return {"echoed": args.get("x"), "ctx": ctx}


def test_prompt_helpers():
    assert block("identity", "you are X").startswith("### IDENTITY\n")
    assert compose("a", "", None, "b") == "a\n\nb"
    defs = anthropic_tool_defs([_EchoTool()])
    assert defs[0]["name"] == "echo" and "input_schema" in defs[0]


def test_loop_dispatches_tool_then_answers():
    tool = _EchoTool()
    turns = [
        {"text": ["Let me check. "],
         "content": [_Block("text", text="Let me check. "),
                     _Block("tool_use", name="echo", input={"x": "hi"}, id="t1")]},
        {"text": ["Done: hi"], "content": [_Block("text", text="Done: hi")]},
    ]
    agent = Agent(model="m", system="sys", tools=[tool], ctx={"who": "pm"},
                  client_factory=lambda: _FakeClient(turns))
    events = list(agent.stream([{"role": "user", "content": "hello"}]))
    kinds = [e["type"] for e in events]
    assert "token" in kinds and "step" in kinds
    final = next(e for e in events if e["type"] == "final")
    assert final["text"] == "Done: hi"
    assert tool.calls == [{"x": "hi"}]          # tool ran with the model's args
    assert "echo" in final["captured"]          # result captured for the caller
    assert '"who": "pm"' in final["captured"]["echo"]  # ctx threaded to the tool


def test_convergence_on_repeated_tool_call():
    same = {"text": ["thinking "],
            "content": [_Block("text", text="thinking "),
                        _Block("tool_use", name="echo", input={"x": "a"}, id="t")]}
    agent = Agent(model="m", system="", tools=[_EchoTool()],
                  client_factory=lambda: _FakeClient([same, dict(same), dict(same)]))
    events = list(agent.stream([{"role": "user", "content": "go"}]))
    # second identical tool-call signature → loop stops (no infinite loop)
    assert events[-1]["type"] == "final"
    assert sum(1 for e in events if e["type"] == "step" and e["status"] == "running") == 1


def test_unknown_tool_feeds_error_not_crash():
    turns = [
        {"text": [], "content": [_Block("tool_use", name="missing", input={}, id="t1")]},
        {"text": ["recovered"], "content": [_Block("text", text="recovered")]},
    ]
    agent = Agent(model="m", system="", tools=[_EchoTool()],
                  client_factory=lambda: _FakeClient(turns))
    final = list(agent.stream([{"role": "user", "content": "x"}]))[-1]
    assert final["type"] == "final" and final["text"] == "recovered"
