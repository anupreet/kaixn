"""Build a reviewable playbook for a repo, in three sections:

  - Features / PRDs            — the product features the repo implements
  - Tech Specs                 — the key technical decisions / architecture
  - Engineering Design Principles — the miner output (deterministic + real-LLM
                                    design pass)

The design-principles section is the real miner (`kaixn.miner`). Features and
tech-specs are bounded LLM passes over the repo's docs + central source, each with
an offline heuristic fallback so the page renders with no API key (the repo's
offline-first contract).
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

from kaixn.app import normalize_repo_url
from kaixn.miner import Observation, _source_blob, mine_all


def _llm_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


# --- serialization ---------------------------------------------------------
def _obs_dict(o: Observation) -> dict:
    return {
        "axis": o.axis_id, "value": o.value, "statement": o.statement,
        "support": f"{o.n_match}/{o.n_total}", "ratio": round(o.ratio, 2),
        "tier": o.tier, "method": o.method,
        "convention": o.is_convention(0.8),
        "evidence": [s.path for s in o.sample_sites],
        "counterexamples": [f"{s.path}:{s.detail}" if s.detail else s.path
                            for s in o.counterexamples],
    }


# --- doc / source gathering ------------------------------------------------
def _read_docs(root: pathlib.Path, *, limit: int = 8, per: int = 6000) -> str:
    out: list[str] = []
    readme = next((p for p in root.glob("README*")), None)
    paths = ([readme] if readme else []) + sorted(root.glob("docs/*.md"))
    for p in paths[:limit]:
        try:
            out.append(f"# {p.name}\n" + p.read_text(errors="ignore")[:per])
        except OSError:
            continue
    return "\n\n".join(out)


def _llm_json(prompt: str, *, model: str, max_tokens: int = 2048):
    from anthropic import Anthropic

    msg = Anthropic().messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    raw = msg.content[0].text
    raw = raw[raw.find("["): raw.rfind("]") + 1]
    return json.loads(raw)


# --- features / PRDs -------------------------------------------------------
def build_features(root: pathlib.Path, *, llm: bool,
                   model: str = "claude-sonnet-4-6") -> list[dict]:
    if llm:
        docs = _read_docs(root)
        prompt = (
            "From this repo's README and docs, list the user-facing PRODUCT "
            "FEATURES it implements — what a PM would put in a PRD. For each give "
            "a short name, a one-line summary, and the doc/section it came from. "
            'Reply JSON: [{"name","summary","evidence"}].\n\n' + docs)
        try:
            return _llm_json(prompt, model=model)
        except Exception:
            pass
    # offline fallback: README section headings
    readme = next((p for p in root.glob("README*")), None)
    feats: list[dict] = []
    if readme:
        for line in readme.read_text(errors="ignore").splitlines():
            m = re.match(r"#{2,3}\s+(.*)", line.strip())
            if m:
                feats.append({"name": m.group(1).strip(),
                              "summary": "", "evidence": readme.name})
    return feats[:20]


# --- tech specs ------------------------------------------------------------
def build_tech_specs(root: pathlib.Path, *, llm: bool,
                     model: str = "claude-sonnet-4-6") -> list[dict]:
    if llm:
        blob = _source_blob(root, max_files=30, per_file=3000)
        prompt = (
            "From this source, extract the key TECHNICAL SPEC decisions a tech "
            "lead would document: the area, the decision made, why, and an "
            "evidence file. Cover stack/storage, interfaces/seams, data flow, "
            'concurrency, and integration points. Reply JSON: '
            '[{"area","decision","rationale","evidence"}].\n\n' + blob)
        try:
            return _llm_json(prompt, model=model, max_tokens=3072)
        except Exception:
            pass
    # offline fallback: module first-line docstrings as spec notes
    import ast
    specs: list[dict] = []
    for p in sorted(root.rglob("*.py")):
        if any(d in p.parts for d in (".venv", ".git", "__pycache__")):
            continue
        try:
            doc = ast.get_docstring(ast.parse(p.read_text(errors="ignore")))
        except (SyntaxError, OSError):
            doc = None
        if doc:
            specs.append({"area": p.stem,
                          "decision": doc.strip().splitlines()[0],
                          "rationale": "", "evidence": str(p.relative_to(root))})
    return specs[:30]


# --- top-level -------------------------------------------------------------
def build(root: str | pathlib.Path, *, llm: bool | None = None,
          model: str = "claude-sonnet-4-6") -> dict:
    root = pathlib.Path(root)
    use_llm = _llm_enabled() if llm is None else llm
    principles = [_obs_dict(o) for o in mine_all(root, llm=use_llm, model=model)]
    return {
        "features": build_features(root, llm=use_llm, model=model),
        "tech_specs": build_tech_specs(root, llm=use_llm, model=model),
        "principles": principles,
        "llm": use_llm,
    }


def build_from_url(repo_url: str, *, llm: bool | None = None,
                   model: str = "claude-sonnet-4-6") -> dict:
    """Clone a GitHub repo (shallow) and build its playbook."""
    url = normalize_repo_url(repo_url)
    tmp = tempfile.mkdtemp(prefix="kaixn-playbook-")
    try:
        subprocess.run(["git", "clone", "--depth", "1", url, tmp],
                       check=True, capture_output=True, text=True, timeout=180)
        out = build(tmp, llm=llm, model=model)
        out["repo"] = url
        return out
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"git clone failed: {e.stderr.strip()[:300]}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("git clone timed out") from e
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
