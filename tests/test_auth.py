"""GitHub-auth session signing + the chat gate (flag-gated, no real OAuth)."""

from fastapi.testclient import TestClient

from kaixn import auth
from kaixn.web import app

_USER = {"login": "octocat", "id": 583231, "avatar_url": "https://x/a.png"}


def test_session_roundtrip_and_tamper():
    tok = auth.make_session(_USER)
    body = auth.read_session(tok)
    assert body and body["login"] == "octocat" and body["id"] == 583231
    assert auth.read_session(tok[:-3] + "zzz") is None     # bad signature
    assert auth.read_session("garbage") is None
    assert auth.read_session(None) is None


def test_session_expiry():
    assert auth.read_session(auth.make_session(_USER, ttl=-1)) is None


def test_auth_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KAIXN_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("KAIXN_GITHUB_CLIENT_SECRET", raising=False)
    assert auth.auth_enabled() is False
    me = TestClient(app).get("/api/auth/me").json()
    assert me == {"enabled": False, "authed": True}        # open when unconfigured


def _enable(monkeypatch):
    monkeypatch.setenv("KAIXN_GITHUB_CLIENT_ID", "cid")
    monkeypatch.setenv("KAIXN_GITHUB_CLIENT_SECRET", "csecret")
    monkeypatch.setenv("KAIXN_SESSION_SECRET", "test-signing-key")


def test_chat_gated_when_enabled(monkeypatch):
    _enable(monkeypatch)
    assert auth.auth_enabled() is True
    c = TestClient(app)
    assert c.get("/api/auth/me").json() == {"enabled": True, "authed": False}
    # no session cookie → 401 before any LLM/engine work
    r = c.post("/api/chat", json={"repo_url": "https://github.com/x/y", "message": "hi"})
    assert r.status_code == 401


def test_login_redirects_to_github(monkeypatch):
    _enable(monkeypatch)
    r = TestClient(app).get("/api/auth/github/login", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"].startswith("https://github.com/login/oauth/authorize")
    assert "client_id=cid" in r.headers["location"]
