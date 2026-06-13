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
from concurrent.futures import ThreadPoolExecutor, as_completed

from kaixn.app import normalize_repo_url
from kaixn.miner import (
    Observation,
    _source_blob,
    mine,
    mine_all,
    mine_semantic_iter,
)

# Concurrency for the eager full-document pass (one LLM call per feature/spec).
# Bounded so a big repo doesn't open dozens of sockets at once.
_DOC_WORKERS = int(os.getenv("KAIXN_DOC_WORKERS", "6"))


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


def _llm_call(prompt: str, *, model: str, max_tokens: int) -> str:
    """One bounded, streamed Anthropic call returning the raw text."""
    from anthropic import Anthropic

    client = Anthropic(max_retries=2, timeout=120.0)
    with client.messages.stream(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]) as stream:
        return "".join(t for t in stream.text_stream)


def _llm_json(prompt: str, *, model: str, max_tokens: int = 2048):
    raw = _llm_call(prompt, model=model, max_tokens=max_tokens)
    return json.loads(raw[raw.find("["): raw.rfind("]") + 1])


def _llm_obj(prompt: str, *, model: str, max_tokens: int = 2048) -> dict:
    """Like _llm_json but for a single JSON object reply."""
    raw = _llm_call(prompt, model=model, max_tokens=max_tokens)
    return json.loads(raw[raw.find("{"): raw.rfind("}") + 1])


def _llm_text(prompt: str, *, model: str, max_tokens: int = 4096) -> str:
    """Free-form text reply (e.g. a markdown document)."""
    return _llm_call(prompt, model=model, max_tokens=max_tokens).strip()


def slugify(name: str) -> str:
    """A short, URL-safe id for a feature/spec within a repo+kind."""
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return (s or "untitled")[:60]


# --- principle linking -----------------------------------------------------
def _axis_menu(principles: list[dict]) -> str:
    """A compact menu of mined axes for the model to tag features/specs against."""
    return "\n".join(f"  {p['axis']}: {p['value']}" for p in principles)


def _valid_links(item: dict, known: set[str]) -> list[str]:
    """Keep only principle links that name a real mined axis (drop hallucinations)."""
    return [a for a in (item.get("principles") or []) if a in known][:4]


# --- features / PRDs -------------------------------------------------------
def build_features(root: pathlib.Path, *, llm: bool, principles: list[dict],
                   model: str = "claude-sonnet-4-6") -> list[dict]:
    known = {p["axis"] for p in principles}
    if llm:
        docs = _read_docs(root)
        prompt = (
            "From this repo's README and docs, list the user-facing PRODUCT "
            "FEATURES it implements — what a PM would put in a PRD. For each give "
            "a short name, a one-line summary, and the doc/section it came from. "
            "Also tag each feature with `principles`: the axis ids from the menu "
            "below that the feature most relies on (0–3, only from the menu).\n\n"
            "PRINCIPLE MENU (axis: value):\n" + _axis_menu(principles) +
            '\n\nReply JSON: [{"name","summary","evidence","principles":["axis", ...]}].'
            "\n\nDOCS:\n" + docs)
        try:
            feats = _llm_json(prompt, model=model)
            for f in feats:
                f["principles"] = _valid_links(f, known)
            return feats
        except Exception:
            pass
    # offline fallback: README section headings (no principle links)
    readme = next((p for p in root.glob("README*")), None)
    feats: list[dict] = []
    if readme:
        for line in readme.read_text(errors="ignore").splitlines():
            m = re.match(r"#{2,3}\s+(.*)", line.strip())
            if m:
                feats.append({"name": m.group(1).strip(), "summary": "",
                              "evidence": readme.name, "principles": []})
    return feats[:20]


# --- tech specs ------------------------------------------------------------
def build_tech_specs(root: pathlib.Path, *, llm: bool, principles: list[dict],
                     model: str = "claude-sonnet-4-6") -> list[dict]:
    known = {p["axis"] for p in principles}
    if llm:
        blob = _source_blob(root, max_files=30, per_file=3000)
        prompt = (
            "From this source, extract the key TECHNICAL SPEC decisions a tech "
            "lead would document: the area, the decision made, why, and an "
            "evidence file. Cover stack/storage, interfaces/seams, data flow, "
            "concurrency, and integration points. Also tag each with `principles`: "
            "the axis ids from the menu below that the decision embodies (0–3, only "
            "from the menu).\n\nPRINCIPLE MENU (axis: value):\n" + _axis_menu(principles) +
            '\n\nReply JSON: [{"area","decision","rationale","evidence",'
            '"principles":["axis", ...]}].\n\nSOURCE:\n' + blob)
        try:
            specs = _llm_json(prompt, model=model, max_tokens=3072)
            for s in specs:
                s["principles"] = _valid_links(s, known)
            return specs
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
                          "rationale": "", "evidence": str(p.relative_to(root)),
                          "principles": []})
    return specs[:30]


# --- combined index (balanced features + tech specs) -----------------------
def build_index(root: pathlib.Path, *, llm: bool, principles: list[dict],
                model: str = "claude-sonnet-4-6") -> tuple[list[dict], list[dict]]:
    """Partition the repo into PRODUCT FEATURES and TECHNICAL AREAS in ONE call,
    so the two stay balanced and distinct (separate calls let the model dump
    everything into 'features'). Returns (features, tech_specs).

    Offline → README headings (features) + module docstrings (specs)."""
    known = {p["axis"] for p in principles}
    if llm:
        ctx = _read_docs(root, limit=6) + "\n\n" + _source_blob(root, max_files=20, per_file=2000)
        prompt = (
            "Analyze THIS repository and split what it provides into two lists, "
            "at a senior PM/tech-lead altitude (group related capabilities — do NOT "
            "list every helper as its own item; aim for the ~8-14 most significant "
            "of each):\n"
            "  • features  — user-facing PRODUCT capabilities (what a PM writes a PRD "
            "for: what users/developers can DO with it).\n"
            "  • tech_specs — TECHNICAL areas a tech-lead specs (HOW it's built: "
            "architecture, stack/storage, core engine/algorithms, interfaces/seams, "
            "data flow, concurrency, extension points, integration).\n"
            "Every item must be grounded in the code/docs below. Tag each with "
            "`principles`: axis ids from the menu it relies on (0-3, only from the menu).\n\n"
            "PRINCIPLE MENU (axis: value):\n" + _axis_menu(principles) +
            '\n\nReply JSON object: {"features":[{"name","summary","evidence",'
            '"principles":[]}], "tech_specs":[{"area","decision","rationale",'
            '"evidence","principles":[]}]}.\n\nREPO:\n' + ctx)
        try:
            d = _llm_obj(prompt, model=model, max_tokens=4096)
            feats = d.get("features") or []
            specs = d.get("tech_specs") or []
            for f in feats:
                f["principles"] = _valid_links(f, known)
            for s in specs:
                s["principles"] = _valid_links(s, known)
            if feats or specs:
                return feats, specs
        except Exception:
            pass
    # offline fallback: reuse the single-list builders (no LLM inside)
    return (build_features(root, llm=False, principles=principles, model=model),
            build_tech_specs(root, llm=False, principles=principles, model=model))


# --- full templated documents (PRD / Tech Spec) ----------------------------
# Classic templates — the human-facing structure each generated doc follows, and
# the structure an agent can rely on when reading the knowledge back.
DOC_TEMPLATES: dict[str, list[str]] = {
    "prd": ["Overview", "Problem & Context", "Goals", "Non-Goals",
            "User Stories", "Functional Requirements", "UX & Key Flows",
            "Success Metrics", "Dependencies & Risks"],
    "spec": ["Context & Background", "Goals", "Non-Goals", "Proposed Design",
             "Data Model", "APIs & Interfaces", "Key Decisions & Trade-offs",
             "Sequencing & Rollout", "Risks & Open Questions"],
}
_DOC_KIND_NAME = {"prd": "Product Requirements Document (PRD)",
                  "spec": "Technical Specification"}


def build_doc(root: pathlib.Path, *, kind: str, title: str, summary: str = "",
              llm: bool, model: str = "claude-sonnet-4-6") -> str:
    """Generate ONE full, classically-templated document (markdown) for a feature
    (kind='prd') or technical area (kind='spec'), grounded in the repo.

    Offline → a section skeleton so the structure still persists."""
    sections = DOC_TEMPLATES[kind]
    if llm:
        ctx = (_read_docs(root) if kind == "prd"
               else _source_blob(root, max_files=25, per_file=3000))
        prompt = (
            f"Write a {_DOC_KIND_NAME[kind]} for "
            f"\"{title}\"" + (f" — {summary}" if summary else "") +
            " of THIS repository. Ground every statement in the actual code/docs "
            "below; do not invent capabilities the repo doesn't have. Be concrete "
            "and specific to this codebase (name real modules, types, endpoints). "
            "Use bullet lists and tables where natural.\n\nStart with a single H1 "
            "title line, then EXACTLY these H2 sections, in order:\n" +
            "\n".join(f"## {s}" for s in sections) +
            "\n\nREPO CONTEXT:\n" + ctx)
        try:
            return _llm_text(prompt, model=model, max_tokens=4096)
        except Exception:
            pass
    return (f"# {title}\n\n" + (f"> {summary}\n\n" if summary else "") +
            "\n\n".join(f"## {s}\n\n_Offline mode — connect an API key to "
                        "generate this section._" for s in sections))


# --- domain model (DDD graph) ----------------------------------------------
def _clean_mermaid(text: str) -> str:
    """Strip code fences / prose around a mermaid diagram and ensure it declares
    a diagram type."""
    t = (text or "").strip()
    if "```" in t:                                   # pull out a fenced block
        parts = t.split("```")
        for p in parts:
            p = p.strip()
            if p.startswith("mermaid"):
                t = p[len("mermaid"):].strip(); break
            if p.startswith(("classDiagram", "graph", "flowchart")):
                t = p; break
    if not t.startswith(("classDiagram", "graph", "flowchart")):
        t = "classDiagram\n" + t
    return t


def _domain_offline(root: pathlib.Path) -> dict:
    """No-LLM domain map: classes + inheritance/association edges from the AST."""
    import ast
    names: dict[str, list[str]] = {}      # class -> field annotations (type names)
    bases: dict[str, list[str]] = {}
    for p in (x for x in root.rglob("*.py")
              if not any(d in x.parts for d in (".venv", ".git", "__pycache__", "tests"))):
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            fields: list[str] = []
            for b in node.body:
                if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
                    ann = getattr(b.annotation, "id", None) or getattr(
                        getattr(b.annotation, "value", None), "id", None)
                    fields.append(b.target.id + (f": {ann}" if ann else ""))
            names[node.name] = fields[:5]
            bases[node.name] = [b.id for b in node.bases if isinstance(b, ast.Name)]
    keep = list(names)[:20]
    lines = ["classDiagram"]
    for c in keep:
        body = "".join(f"  +{f}\n" for f in names[c])
        lines.append(f"class {c} {{\n{body}}}" if body else f"class {c}")
    for c in keep:
        for b in bases.get(c, []):
            if b in names:
                lines.append(f"{b} <|-- {c}")
    entities = [{"name": c, "description": ", ".join(names[c]) or "—"} for c in keep]
    return {"mermaid": "\n".join(lines), "entities": entities}


def build_domain(root: pathlib.Path, *, llm: bool,
                 model: str = "claude-sonnet-4-6") -> dict:
    """Extract the domain model — key objects and how they interact — as a Mermaid
    classDiagram plus an entity list. Offline → an AST-derived class graph."""
    if llm:
        blob = _source_blob(root, max_files=30, per_file=2500)
        prompt = (
            "Extract the DOMAIN MODEL of this codebase: the key domain objects "
            "(entities, aggregates, value objects, and services) and how they "
            "interact. Output a Mermaid `classDiagram`: declare each key class with "
            "its 2-5 most important fields, and the relationships between them — "
            "association (-->), inheritance (<|--), composition (*--), dependency "
            "(..>) — each with a short label. Keep to the ~15 most important "
            "objects. Ground strictly in the code below.\n\n"
            'Reply JSON: {"mermaid": "classDiagram\\n...", '
            '"entities": [{"name","description"}]}.\n\nSOURCE:\n' + blob)
        try:
            d = _llm_obj(prompt, model=model, max_tokens=2048)
            d["mermaid"] = _clean_mermaid(d.get("mermaid", ""))
            d.setdefault("entities", [])
            return d
        except Exception:
            pass
    return _domain_offline(root)


# --- top-level -------------------------------------------------------------
def build(root: str | pathlib.Path, *, llm: bool | None = None,
          model: str = "claude-sonnet-4-6") -> dict:
    root = pathlib.Path(root)
    use_llm = _llm_enabled() if llm is None else llm
    principles = [_obs_dict(o) for o in mine_all(root, llm=use_llm, model=model)]
    features, specs = build_index(root, llm=use_llm, principles=principles, model=model)
    return {
        "features": features,
        "tech_specs": specs,
        "principles": principles,
        "llm": use_llm,
    }


def build_from_url(repo_url: str, *, llm: bool | None = None,
                   model: str = "claude-sonnet-4-6") -> dict:
    """Clone a GitHub repo (shallow) and build its playbook."""
    url = normalize_repo_url(repo_url)
    tmp = _clone(url)
    try:
        out = build(tmp, llm=llm, model=model)
        out["repo"] = url
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- streaming (progressive UI) --------------------------------------------
def _clone(url: str) -> str:
    """Shallow-clone ``url`` into a temp dir (caller removes it). Raises
    RuntimeError on clone failure/timeout so the web layer can map it to 502."""
    tmp = tempfile.mkdtemp(prefix="kaixn-playbook-")
    try:
        subprocess.run(["git", "clone", "--depth", "1", url, tmp],
                       check=True, capture_output=True, text=True, timeout=180)
    except subprocess.CalledProcessError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"git clone failed: {e.stderr.strip()[:300]}") from e
    except subprocess.TimeoutExpired as e:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("git clone timed out") from e
    return tmp


def build_stream(root: str | pathlib.Path, *, llm: bool | None = None,
                 model: str = "claude-sonnet-4-6"):
    """Generator that yields the playbook in pieces, with the human-facing
    documents FIRST (they're the point of this interface), then the slower
    analysis:

      meta → conventions (instant) → domain → feature/spec LISTS → one `doc` per
      generated PRD/Tech Spec (concurrent) → `principle` per verified design axis
      → done

    Ordering rationale: PRDs are the default view, so the lists must appear within
    seconds and their docs stream next — not after the ~2-min design pass. Domain
    is one cheap call, emitted (and persisted) up-front so it survives even if the
    long doc phase is interrupted; the slow design principles fill their tab last
    but are persisted incrementally. Each yielded value is a JSON event dict."""
    root = pathlib.Path(root)
    use_llm = _llm_enabled() if llm is None else llm
    yield {"event": "meta", "llm": use_llm}

    # 1) deterministic conventions — instant, no API (seeds principle links)
    principles: list[dict] = [_obs_dict(o) for o in mine(root)]
    yield {"event": "conventions", "items": principles}

    # 2) domain model (DDD graph) — one cheap call, persisted up-front so it's
    #    never lost to an interrupted doc phase.
    if use_llm:
        yield {"event": "status", "step": "mapping the domain model…"}
    yield {"event": "domain", "domain": build_domain(root, llm=use_llm, model=model)}

    # 3) feature + tech-spec LISTS — emit immediately (stable slugs) so the
    #    default PRDs/Tech-Specs tabs populate within seconds.
    yield {"event": "status", "step": "extracting features & tech specs…"}
    features, specs = build_index(root, llm=use_llm, principles=principles, model=model)
    items = _with_slugs("prd", [{"title": f.get("name", ""), **f} for f in features]) \
        + _with_slugs("spec", [{"title": s.get("area", ""), **s} for s in specs])
    yield {"event": "features", "items": [i for i in items if i["kind"] == "prd"]}
    yield {"event": "tech_specs", "items": [i for i in items if i["kind"] == "spec"]}

    # 4) full templated documents — generated CONCURRENTLY (the heavy part: one
    #    LLM call per item). Emit as each lands so its row flips to "ready".
    yield {"event": "status", "step": f"generating {len(items)} full documents…"}

    def _one(item: dict) -> dict:
        md = build_doc(root, kind=item["kind"], title=item["title"],
                       summary=item.get("summary", ""), llm=use_llm, model=model)
        return {"event": "doc", "kind": item["kind"], "slug": item["slug"],
                "title": item["title"], "summary": item.get("summary", ""),
                "principles": item.get("principles", []), "markdown": md}

    with ThreadPoolExecutor(max_workers=_DOC_WORKERS) as pool:
        futures = {pool.submit(_one, it): it for it in items}
        for fut in as_completed(futures):
            try:
                yield fut.result()
            except Exception as e:                       # one doc failed — keep going
                it = futures[fut]
                yield {"event": "doc_error", "kind": it["kind"], "slug": it["slug"],
                       "title": it["title"], "detail": str(e)[:160]}

    # 5) design/architecture principles — propose + verify-by-sampling (the slow
    #    pass), streamed + persisted one card at a time into the Principles tab.
    if use_llm:
        yield {"event": "status", "step": "analyzing architecture & design (LLM)…"}
        try:
            for o in mine_semantic_iter(root, model=model):
                d = _obs_dict(o)
                principles.append(d)
                yield {"event": "principle", "item": d}
        except Exception as e:                       # real API down mid-stream
            yield {"event": "status", "step": f"design pass skipped: {str(e)[:120]}"}
    yield {"event": "done"}


def _with_slugs(kind: str, items: list[dict]) -> list[dict]:
    """Attach a unique, URL-safe slug + kind to each item."""
    seen: dict[str, int] = {}
    out: list[dict] = []
    for it in items:
        base = slugify(it.get("title", ""))
        n = seen.get(base, 0)
        seen[base] = n + 1
        slug = base if n == 0 else f"{base}-{n + 1}"
        out.append({**it, "kind": kind, "slug": slug})
    return out


def build_stream_from_url(repo_url: str, *, llm: bool | None = None,
                          model: str = "claude-sonnet-4-6"):
    """Clone a repo and stream its playbook (see :func:`build_stream`)."""
    url = normalize_repo_url(repo_url)
    yield {"event": "status", "step": "cloning the repo…"}
    tmp = _clone(url)
    try:
        for ev in build_stream(tmp, llm=llm, model=model):
            if ev.get("event") == "meta":
                ev["repo"] = url
            yield ev
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
