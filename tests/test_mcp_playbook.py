"""MCP playbook tools — verify they shape the HTTP API responses (API mocked)."""

import kaixn.server as srv


def test_playbook_tools_wrap_the_api(monkeypatch):
    calls = []

    def fake_get(path, params=None):
        calls.append((path, params or {}))
        if path == "/api/repos":
            return {"repos": [{"repo": "github.com/x/y", "n_docs": 3}]}
        if path == "/api/playbook":
            return {"repo": params["repo"], "entities": [{"name": "Order"}],
                    "principles": [{"axis": "idempotency"}],
                    "docs": [{"kind": "prd", "slug": "checkout", "title": "Checkout"}]}
        if path == "/api/doc":
            return {"title": "Checkout", "markdown": "# Checkout\n..."}
        raise AssertionError(path)

    monkeypatch.setattr(srv, "_api_get", fake_get)

    assert srv.tool_list_indexed_repos()[0]["repo"] == "github.com/x/y"
    pb = srv.tool_get_playbook("github.com/x/y")
    assert pb["principles"][0]["axis"] == "idempotency"
    assert pb["docs"][0]["slug"] == "checkout"
    doc = srv.tool_get_doc("github.com/x/y", "prd", "checkout")
    assert doc["markdown"].startswith("# Checkout")
    assert ("/api/doc", {"repo": "github.com/x/y", "kind": "prd", "slug": "checkout"}) in calls


def test_playbook_tools_degrade_on_api_error(monkeypatch):
    def boom(path, params=None):
        raise RuntimeError("network down")

    monkeypatch.setattr(srv, "_api_get", boom)
    assert "error" in srv.tool_get_playbook("r")
    assert srv.tool_list_indexed_repos()[0]["error"]
