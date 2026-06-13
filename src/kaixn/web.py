"""kaixn web app — REST API + single-page UI over the `Kaixn` service.

Run:  uvicorn kaixn.web:app --host 0.0.0.0 --port 8000
or:   kaixn-web   (console script)

The UI lets you paste a GitHub URL, bootstrap the constitution, write an intent,
review the synthesized Proposal + conflict report, commit it, and run a drift
review — the full loop, end to end.
"""

from __future__ import annotations

import os
import pathlib

from fastapi import FastAPI, HTTPException, Request
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

from kaixn import playbook, waitlist
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
    in-memory). Single instance reused across requests."""
    global _pstore
    if _pstore is None:
        from kaixn import playbook_store as ps

        _pstore = ps.from_env()
    return _pstore


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


@app.post("/api/playbook/stream")
def playbook_stream(body: PlaybookBody) -> StreamingResponse:
    """Server-Sent Events: stream the playbook section-by-section as it's built
    AND persist it as the durable, agent-readable knowledge for the repo.

    Persistence happens here (single-threaded, as events arrive): the bundle row
    is created on `meta`, domain+principles written on `domain`, and each full
    document saved on its `doc` event — so a doc is queryable the moment it lands.
    Each SSE line is `data: {json}\\n\\n`; errors arrive as an `error` event (we
    can't change the HTTP status once 200 + headers have been sent)."""
    import json

    store = playbook_store()

    def gen():
        pid = None
        repo = body.repo_url
        principles: list = []
        try:
            for ev in playbook.build_stream_from_url(body.repo_url, llm=body.llm):
                e = ev.get("event")
                if e == "meta":
                    repo = ev["repo"]
                    pid = store.create_playbook(repo, llm=bool(ev.get("llm")))
                elif e == "conventions":
                    principles = list(ev.get("items", []))
                elif e == "principle":
                    principles.append(ev["item"])
                elif e == "domain" and pid is not None:
                    d = ev.get("domain", {})
                    store.update_playbook(pid, mermaid=d.get("mermaid"),
                                          entities=d.get("entities", []),
                                          principles=principles)
                elif e == "doc" and pid is not None:
                    store.save_doc(pid, repo=repo, kind=ev["kind"], slug=ev["slug"],
                                   title=ev["title"], summary=ev.get("summary", ""),
                                   markdown=ev["markdown"],
                                   principles=ev.get("principles", []))
                    ev = {k: v for k, v in ev.items() if k != "markdown"}  # keep SSE light
                yield f"data: {json.dumps(ev)}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'event': 'error', 'status': 400, 'detail': str(e)})}\n\n"
        except RuntimeError as e:
            yield f"data: {json.dumps({'event': 'error', 'status': 502, 'detail': str(e)})}\n\n"
        except Exception as e:  # noqa: BLE001 — surface anything else to the client
            yield f"data: {json.dumps({'event': 'error', 'status': 500, 'detail': str(e)[:300]})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


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
