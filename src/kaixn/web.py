"""kaixn web app — REST API + single-page UI over the `Kaixn` service.

Run:  uvicorn kaixn.web:app --host 0.0.0.0 --port 8000
or:   kaixn-web   (console script)

The UI lets you paste a GitHub URL, bootstrap the constitution, write an intent,
review the synthesized Proposal + conflict report, commit it, and run a drift
review — the full loop, end to end.
"""

from __future__ import annotations

import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Best-effort: load ANTHROPIC_API_KEY etc. from .env for local runs (the LLM
# playbook passes need it; docker/prod inject env directly).
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from kaixn import playbook
from kaixn.app import Kaixn

_STATIC = pathlib.Path(__file__).parent / "static"

app = FastAPI(title="kaixn", description="Review the plan, not the PR.")
_service: Kaixn | None = None


def service() -> Kaixn:
    global _service
    if _service is None:
        _service = Kaixn.from_env()
    return _service


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


# -- UI ----------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(_STATIC / "playbook.html")


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
