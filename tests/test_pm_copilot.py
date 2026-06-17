"""PM copilot backend tests — tools over a fake engine + a scripted chat turn."""

from kaixn.agents.copilot import ChatSession
from kaixn.agents.prompts import build_system_prompt
from kaixn.agents.tools import CheckFeatureConflicts, PMContext, SearchNorms, all_tools


class _Norm:
    def __init__(self, **kw):
        self.id = kw.get("id", "n1"); self.kind = kw.get("kind", "principle")
        self.domain = kw.get("domain", "product"); self.scope = kw.get("scope", "all")
        self.statement = kw.get("statement", "s"); self.rationale = kw.get("rationale", "")


class _Store:
    def neighbors(self, candidate, *, top_k):
        return [_Norm(id="N7", statement="Billing changes must emit an audit trail")]

    def active_principles(self, domain, scope):
        return [_Norm(id="P1", kind="principle", scope=scope)]


class _Service:
    """Minimal Kaixn stand-in for the tools."""
    store = _Store()

    def synthesize(self, intent, feature_id=None):
        return {"proposal_id": "prop_abc", "intent": intent,
                "operations": [{"op_type": "assert", "statement": intent, "status": "ok"}],
                "conflict_report": {"blocked": True, "counts": {"conflict": 1},
                    "findings": [{"verdict": "conflict", "norm": "no async in billing",
                                  "norm_kind": "principle", "on": intent,
                                  "resolution": "make it sync or supersede"}]}}

    def get_proposal(self, pid):
        return {"proposal_id": pid}


def test_tools_wrap_the_engine():
    ctx = PMContext(service=_Service(), repo="r")
    norms = SearchNorms().execute({"query": "billing", "domain": "product"}, ctx)
    assert norms[0]["id"] == "N7" and "audit" in norms[0]["statement"]
    res = CheckFeatureConflicts().execute({"feature_description": "skip audit on billing"}, ctx)
    assert res["proposal_id"] == "prop_abc"
    assert res["conflict_report"]["findings"][0]["verdict"] == "conflict"
    assert {t.name for t in all_tools()} == {
        "search_norms", "get_governing_principles", "read_playbook_doc",
        "check_feature_conflicts", "get_proposal"}


def test_system_prompt_composition():
    sp = build_system_prompt(repo="github.com/x/y", persona="platform_pm",
                             repo_context="Domain objects: A, B.")
    for header in ("### IDENTITY", "### TOOLS", "### COLLISION & CLARIFICATION BEHAVIOR",
                   "### GUARDRAILS", "### REPO"):
        assert header in sp
    assert "github.com/x/y" in sp and "platform product manager" in sp


# --- scripted chat turn (fake Anthropic client) ---
class _Block:
    def __init__(self, type, **kw):
        self.type = type; self.__dict__.update(kw)


class _CM:
    def __init__(self, deltas, content):
        self._d, self._c = deltas, content

    def __enter__(self): return self
    def __exit__(self, *a): return False
    @property
    def text_stream(self): return iter(self._d)
    def get_final_message(self): return type("F", (), {"content": self._c})()


def _fake_client(turns):
    state = {"i": 0}

    class _M:
        def stream(self, **kw):
            t = turns[state["i"]]; state["i"] += 1
            return _CM(t["text"], t["content"])

    return type("C", (), {"messages": _M()})()


def test_chat_turn_surfaces_collision():
    turns = [
        {"text": ["Let me check that against billing decisions. "],
         "content": [_Block("text", text="Let me check. "),
                     _Block("tool_use", name="check_feature_conflicts",
                            input={"feature_description": "skip audit on billing"}, id="t1")]},
        {"text": ["That collides with the audit principle — supersede it or comply?"],
         "content": [_Block("text", text="That collides with the audit principle.")]},
    ]
    sess = ChatSession(service=_Service(), repo="r", persona=None, session_id="s1",
                       client_factory=lambda: _fake_client(turns))
    events = list(sess.stream("can we skip the audit log on billing edits?"))
    done = events[-1]
    assert done["type"] == "done"
    assert done["proposal_id"] == "prop_abc"
    assert done["conflict_findings"][0]["verdict"] == "conflict"
    assert any(e["type"] == "step" and e["tool"] == "check_feature_conflicts" for e in events)
    assert len(sess.history) == 2 and sess.history[0]["role"] == "user"
