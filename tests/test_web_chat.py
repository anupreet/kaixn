"""/api/chat endpoint wiring (no LLM — the streaming turn is covered elsewhere)."""

from fastapi.testclient import TestClient

from kaixn.web import app


def test_chat_route_registered():
    assert "/api/chat" in {r.path for r in app.routes}


def test_chat_rejects_empty_message():
    # the guard fires before the chat service / LLM is touched
    r = TestClient(app).post("/api/chat",
                             json={"repo_url": "https://github.com/x/y", "message": "   "})
    assert r.status_code == 400
