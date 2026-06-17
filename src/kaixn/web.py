"""kaixn web app — REST API + single-page UI over the `Kaixn` service.

Run:  uvicorn kaixn.web:app --host 0.0.0.0 --port 8000
or:   kaixn-web   (console script)

The UI lets you paste a GitHub URL, bootstrap the constitution, write an intent,
review the synthesized Proposal + conflict report, commit it, and run a drift
review — the full loop, end to end.
"""

from __future__ import annotations

import json
import os
import pathlib

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Best-effort: load ANTHROPIC_API_KEY etc. from .env for local runs (the LLM
# playbook passes need it; docker/prod inject env directly).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from kaixn import auth, playbook, waitlist
from kaixn.app import Kaixn

_STATIC = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="kaixn", description="Review the plan, not the PR.")

# The marketing site posts waitlist signups cross-origin (kaixn.com ->
# app.kaixn.com), so allow those origins for the AJAX path. Override/extend with
# KAIXN_CORS_ORIGINS (comma-separated) if domains change.
_DEFAULT_ORIGINS = [
    "https://kaixn.com", "https://www.kaixn.com", "https://kaixn.webflow.io",
]
_cors = os.getenv("KAIXN_CORS_ORIGINS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",")] if _cors else _DEFAULT_ORIGINS,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)

_service: Kaixn | None = None
_pstore = None


def service() -> Kaixn:
    global _service
    if _service is None:
        _service = Kaixn.from_env()
    return _service


def playbook_store():
    """Lazily-built persistence for generated playbooks (Pg if KAIXN_DSN, else
    in-memory). Single instance reused across READ requests; jobs make their own
    (see playbook_store.from_env)."""
    global _pstore
    if _pstore is None:
        from kaixn import playbook_store as ps

        _pstore = ps.from_env()
    return _pstore


_jobs = None


def jobs():
    """The process-wide generation job manager. Jobs each build their own store
    (own Pg connection / the shared in-memory singleton)."""
    global _jobs
    if _jobs is None:
        from kaixn import playbook_store as ps
        from kaixn.playbook_jobs import JobManager

        _jobs = JobManager(store_factory=ps.from_env,
                           generate=playbook.build_stream_from_url)
    return _jobs


_chat = None


def chat_service():
    """Process-wide PM-copilot chat service over the shared Kaixn engine."""
    global _chat
    if _chat is None:
        from kaixn.agents import ChatService

        _chat = ChatService(service())
    return _chat


# -- request models ----------------------------------------------------------
class ConnectBody(BaseModel):
    repo_url: str
    max_files: int = 50


class IntentBody(BaseModel):
    intent: str
    feature_id: str | None = None


class OverrideBody(BaseModel):
    finding_index: int
    new_decision: str


class ReviewBody(BaseModel):
    files: list[str] = []
    changes: list[str] = []
    summary: str = ""


class PlaybookBody(BaseModel):
    repo_url: str
    llm: bool | None = None     # None → auto (on iff ANTHROPIC_API_KEY present)


class ChatBody(BaseModel):
    repo_url: str
    message: str
    session_id: str | None = None   # None → new session
    persona: str | None = None      # persona slug; default generalist_pm


# -- API ---------------------------------------------------------------------
@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.get("/api/status")
def status() -> dict:
    return service().status()


@app.post("/api/connect")
def connect(body: ConnectBody) -> dict:
    try:
        return service().connect_repo_url(body.repo_url, max_files=body.max_files)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/api/norms")
def norms(status: str | None = None) -> dict:
    return {"norms": service().list_norms(status=status)}


@app.post("/api/norms/{norm_id}/promote")
def promote(norm_id: str) -> dict:
    return service().promote_norm(norm_id)


@app.post("/api/proposals")
def synthesize(body: IntentBody) -> dict:
    if not body.intent.strip():
        raise HTTPException(status_code=400, detail="intent is required")
    return service().synthesize(body.intent, feature_id=body.feature_id)


@app.get("/api/proposals/{proposal_id}")
def get_proposal(proposal_id: str) -> dict:
    p = service().get_proposal(proposal_id)
    if p is None:
        raise HTTPException(status_code=404, detail="unknown proposal")
    return p


@app.post("/api/proposals/{proposal_id}/resolve")
def resolve(proposal_id: str, body: OverrideBody) -> dict:
    return service().resolve_override(proposal_id, body.finding_index,
                                      body.new_decision)


@app.post("/api/proposals/{proposal_id}/commit")
def commit(proposal_id: str) -> dict:
    return service().commit(proposal_id)


@app.post("/api/proposals/{proposal_id}/review")
def review(proposal_id: str, body: ReviewBody) -> dict:
    return service().review(proposal_id, files=body.files, changes=body.changes,
                            summary=body.summary)


@app.post("/api/playbook")
def playbook_endpoint(body: PlaybookBody) -> dict:
    """Clone a repo and build its reviewable playbook (features / tech-specs /
    engineering design principles)."""
    try:
        return playbook.build_from_url(body.repo_url, llm=body.llm)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


@app.post("/api/playbook/generate")
def playbook_generate(body: PlaybookBody) -> dict:
    """Start (or reuse) a server-side generation JOB for a repo and return
    immediately. Generation runs in a background thread and persists as it goes —
    it survives the client disconnecting. One job per repo (a second request for
    a repo already generating returns the in-flight job, avoiding a racing run
    that would corrupt persistence). Subscribe to progress via /events."""
    try:
        repo = playbook.normalize_repo_url(body.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job = jobs().start(repo, body.llm)
    return {"repo": repo, "status": job.status}


@app.get("/api/playbook/events")
def playbook_events(repo: str) -> StreamingResponse:
    """SSE stream for a repo's generation job: full replay of what's happened so
    far, then live tail until done. Reconnect-safe — re-subscribing replays the
    whole log so the client can rebuild state."""
    repo = playbook.normalize_repo_url(repo)
    job = jobs().get(repo)
    if job is None:
        raise HTTPException(status_code=404, detail="no generation job for this repo")
    return StreamingResponse(jobs().subscribe(job),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


@app.get("/api/playbook/jobs")
def playbook_jobs_status() -> dict:
    """Repos currently generating — lets Explore show a live 'generating…' badge."""
    return {"running": jobs().running_repos()}


@app.post("/api/playbook/stream")
def playbook_stream(body: PlaybookBody) -> StreamingResponse:
    """Back-compat one-shot: start the job and stream it in a single request.
    (The decoupled flow is POST /generate then GET /events.)"""
    try:
        repo = playbook.normalize_repo_url(body.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    job = jobs().start(repo, body.llm)
    return StreamingResponse(jobs().subscribe(job),
                             media_type="text/event-stream", headers=_SSE_HEADERS)


# -- GitHub auth (gate before chat; flag-gated on the OAuth credentials) ------
_SESSION_COOKIE = "kaixn_session"
_STATE_COOKIE = "kaixn_oauth_state"


def _public_base(request: Request) -> str:
    """The externally-visible origin (behind the ALB we must trust X-Forwarded-*),
    so the OAuth redirect_uri matches what's registered in the GitHub OAuth App."""
    env = os.getenv("KAIXN_PUBLIC_URL")
    if env:
        return env.rstrip("/")
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def _secure(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto") or request.url.scheme) == "https"


def _redirect_uri(request: Request) -> str:
    return _public_base(request) + "/api/auth/github/callback"


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict:
    """Who's signed in. When auth is disabled (no OAuth creds), reports the app as
    open so the UI shows the composer without a sign-in step."""
    if not auth.auth_enabled():
        return {"enabled": False, "authed": True}
    sess = auth.read_session(request.cookies.get(_SESSION_COOKIE))
    if not sess:
        return {"enabled": True, "authed": False}
    return {"enabled": True, "authed": True, "login": sess.get("login"),
            "avatar": sess.get("avatar")}


@app.get("/api/auth/github/login")
def auth_login(request: Request) -> RedirectResponse:
    if not auth.auth_enabled():
        raise HTTPException(status_code=503, detail="GitHub auth is not configured")
    state = auth.new_state()
    resp = RedirectResponse(auth.authorize_url(_redirect_uri(request), state), status_code=302)
    resp.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True,
                    secure=_secure(request), samesite="lax", path="/")
    return resp


@app.get("/api/auth/github/callback")
def auth_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
    if not auth.auth_enabled():
        raise HTTPException(status_code=503, detail="GitHub auth is not configured")
    home = _public_base(request) + "/"
    if not code or not state or state != request.cookies.get(_STATE_COOKIE):
        return RedirectResponse(home + "?auth=failed", status_code=302)
    try:
        user = auth.exchange_code(code, _redirect_uri(request))
    except Exception:  # noqa: BLE001
        return RedirectResponse(home + "?auth=failed", status_code=302)
    resp = RedirectResponse(home + "?auth=ok", status_code=302)
    resp.set_cookie(_SESSION_COOKIE, auth.make_session(user), max_age=7 * 86400,
                    httponly=True, secure=_secure(request), samesite="lax", path="/")
    resp.delete_cookie(_STATE_COOKIE, path="/")
    return resp


@app.post("/api/auth/logout")
def auth_logout() -> Response:
    resp = Response(status_code=204)
    resp.delete_cookie(_SESSION_COOKIE, path="/")
    return resp


# -- PM copilot chat ---------------------------------------------------------
@app.post("/api/chat")
def chat(body: ChatBody, request: Request) -> StreamingResponse:
    """One PM-copilot turn, streamed as SSE. Frames: `token` (answer deltas),
    `step` (tool activity), then a terminal `done` carrying the answer, the
    session id, any collision findings, and the proposal_id (if a conflict check
    ran). Reuses the SSE pattern from the playbook job manager.

    Gated: when GitHub auth is enabled, a valid session is required."""
    if auth.auth_enabled() and not auth.read_session(request.cookies.get(_SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="sign in with GitHub to chat")
    if not body.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    try:
        repo = playbook.normalize_repo_url(body.repo_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session = chat_service().session(repo, body.session_id, body.persona)

    def _frames():
        try:
            for ev in session.stream(body.message):
                yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n"
        except Exception as e:  # noqa: BLE001 — surface to the client, end the stream
            yield f"event: error\ndata: {json.dumps({'detail': str(e)[:300]})}\n\n"

    return StreamingResponse(_frames(), media_type="text/event-stream", headers=_SSE_HEADERS)


# -- read the persisted knowledge (humans via /doc, agents via the API) -------
@app.get("/api/repos")
def list_repos() -> dict:
    """Every indexed repo — backs the Explore view and agent discovery."""
    return {"repos": playbook_store().list_repos()}


@app.get("/api/playbook")
def get_playbook(repo: str) -> dict:
    """The full knowledge bundle for a repo (domain + principles + doc index).

    This is the agent-readable surface: read it back to evaluate a proposed PRD
    or a posted PR against what the codebase actually is."""
    pb = playbook_store().get_playbook(playbook.normalize_repo_url(repo))
    if pb is None:
        raise HTTPException(status_code=404, detail="repo not indexed")
    return pb


@app.delete("/api/playbook")
def delete_playbook(repo: str) -> dict:
    """Remove a repo's playbook (and its documents, via cascade) from the index."""
    removed = playbook_store().delete_playbook(playbook.normalize_repo_url(repo))
    if not removed:
        raise HTTPException(status_code=404, detail="repo not indexed")
    return {"ok": True, "repo": repo}


@app.get("/api/doc")
def get_doc(repo: str, kind: str, slug: str) -> dict:
    """One generated document (full markdown). 404 if it hasn't been generated
    yet — the doc page polls until it appears."""
    doc = playbook_store().get_doc(playbook.normalize_repo_url(repo), kind, slug)
    if doc is None:
        raise HTTPException(status_code=404, detail="not generated yet")
    return doc


# -- waitlist (public marketing site posts here) -----------------------------
class WaitlistBody(BaseModel):
    email: str
    source: str = "marketing"


@app.post("/api/waitlist", response_model=None)
async def join_waitlist(request: Request) -> dict | RedirectResponse:
    """Capture a marketing-site signup.

    Accepts JSON (AJAX fetch from the site) or form-encoded (native form POST).
    JSON → returns {ok, email}; form post → 303-redirects to a thank-you so the
    browser lands somewhere sensible after a full-page submit.
    """
    ctype = request.headers.get("content-type", "")
    is_form = ctype.startswith("application/x-www-form-urlencoded")
    if is_form:
        # Parse urlencoded from the raw body — avoids a python-multipart dep.
        # (Webflow custom-action forms submit as urlencoded.)
        from urllib.parse import parse_qs

        body = (await request.body()).decode("utf-8", "replace")
        email = (parse_qs(body).get("email", [""]) or [""])[0]
    else:
        try:
            data = await request.json()
        except Exception:
            data = {}
        email = str(data.get("email", ""))
    source = "marketing"

    try:
        result = waitlist.add(email, source=source)
    except ValueError as e:
        if is_form:
            return RedirectResponse("https://kaixn.com/?waitlist=invalid", status_code=303)
        raise HTTPException(status_code=400, detail=str(e)) from e

    if is_form:
        return RedirectResponse("https://kaixn.com/?waitlist=ok", status_code=303)
    return result


# -- UI ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "playbook.html")


@app.get("/doc")
def doc_page() -> FileResponse:
    """A generated PRD / Tech Spec at its own shareable URL (params in the query
    string: ?repo=&kind=&slug=). The page fetches /api/doc and renders it."""
    return FileResponse(_STATIC / "doc.html")


@app.get("/classic")
def classic() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


def main() -> None:
    import os

    import uvicorn

    uvicorn.run("kaixn.web:app", host="0.0.0.0",
                port=int(os.getenv("PORT", "8000")), reload=False)


if __name__ == "__main__":
    main()
