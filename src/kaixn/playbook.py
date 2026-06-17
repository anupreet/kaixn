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
import logging
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

log = logging.getLogger(__name__)

from kaixn.app import normalize_repo_url
from kaixn.miner import (
    _SKIP_DIRS,
    Observation,
    _is_test,
    _py_files,
    _rel,
    _repo_tree,
    _source_blob,
    _source_files,
    mine,
    mine_all,
    mine_semantic_iter,
)

# Concurrency for the eager full-document pass (one LLM call per feature/spec).
# Bounded so a big repo doesn't open dozens of sockets at once.
_DOC_WORKERS = int(os.getenv("KAIXN_DOC_WORKERS", "6"))


def _llm_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _source_coverage(root: pathlib.Path) -> int:
    """Count of (non-test) source files the model would actually see. A grounded
    doc/domain pass over a repo with ~0 of these can only invent, so the callers
    refuse the LLM rather than fabricate (the JS auth-spec / Svelte-bleed bug)."""
    return sum(1 for p in _source_files(root) if not _is_test(p))


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


class LLMParseError(RuntimeError):
    """A structured LLM call returned text that wouldn't parse as JSON even after
    a repair retry. Callers catch this to flag degraded output rather than
    silently shipping an offline fallback under ``llm:true`` (the split-brain
    bug: a polished doc body over a README/file-stem list, with no signal)."""


def _extract_json(raw: str, opener: str, closer: str):
    """Slice the outermost ``opener..closer`` span and ``json.loads`` it,
    tolerating ```json fences and surrounding prose."""
    return json.loads(raw[raw.find(opener): raw.rfind(closer) + 1])


def _llm_structured(prompt: str, *, model: str, max_tokens: int,
                    opener: str, closer: str):
    """A structured (JSON) LLM call with ONE repair retry. Returns parsed JSON or
    raises :class:`LLMParseError`. The retry hands the malformed reply back and
    demands pure JSON — this is what stops a single truncated/fenced reply from
    collapsing the whole index to the offline fallback."""
    raw = _llm_call(prompt, model=model, max_tokens=max_tokens)
    try:
        return _extract_json(raw, opener, closer)
    except (json.JSONDecodeError, ValueError):
        pass
    repair = (
        "Your previous reply could not be parsed as JSON. Reply with ONLY the "
        f"JSON value (start with `{opener}`, end with `{closer}`) — no prose, no "
        "markdown fences, no trailing commas.\n\nPREVIOUS REPLY:\n" + raw[:8000])
    raw2 = _llm_call(repair, model=model, max_tokens=max_tokens)
    try:
        return _extract_json(raw2, opener, closer)
    except (json.JSONDecodeError, ValueError) as e:
        raise LLMParseError(str(e)[:200]) from e


def _llm_json(prompt: str, *, model: str, max_tokens: int = 2048):
    return _llm_structured(prompt, model=model, max_tokens=max_tokens,
                           opener="[", closer="]")


def _llm_obj(prompt: str, *, model: str, max_tokens: int = 2048) -> dict:
    """Like :func:`_llm_json` but for a single JSON object reply."""
    return _llm_structured(prompt, model=model, max_tokens=max_tokens,
                           opener="{", closer="}")


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


# --- offline fallbacks -----------------------------------------------------
def _humanize(stem: str) -> str:
    """`playbook_store` / `apply-migrations` → `Playbook Store` / `Apply Migrations`."""
    return re.sub(r"[_\-]+", " ", stem).strip().title()


# README headings that are documentation scaffolding, NOT product features. The
# offline fallback must never surface these as "PRDs" (the Docs/Status bug).
_NON_FEATURE_HEADINGS = {
    "docs", "documentation", "status", "overview", "about", "installation",
    "install", "setup", "getting started", "quick start", "quickstart", "usage",
    "license", "licence", "contributing", "contributors", "acknowledgements",
    "acknowledgments", "roadmap", "faq", "changelog", "prerequisites", "notes",
    "configuration", "configuration reference", "tear down", "teardown", "build",
    "building", "testing", "tests", "development", "table of contents",
    "references", "credits", "design principles", "how it works",
    "rigor for free", "run the engine", "what you get", "verifying a deploy",
}

# HTTP route declarations across common frameworks (FastAPI/Flask `@app.get(...)`,
# Express `router.post(...)`) — real "what you can DO" capabilities for the floor.
_ROUTE_RE = re.compile(
    r"""(?:@\w+|app|router|api)\.(get|post|put|patch|delete)\(\s*["']([^"']+)["']""",
    re.IGNORECASE)


def _offline_features(root: pathlib.Path) -> list[dict]:
    """No-LLM features: HTTP endpoints (real capabilities) + README headings with
    documentation scaffolding filtered out. Never emits 'Docs'/'Status'."""
    feats: list[dict] = []
    seen: set[str] = set()
    for p in _source_files(root):
        if _is_test(p):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for m in _ROUTE_RE.finditer(text):
            name = f"{m.group(1).upper()} {m.group(2).strip()}"
            if name not in seen:
                seen.add(name)
                feats.append({"name": name, "summary": "HTTP endpoint",
                              "evidence": _rel(p, root), "principles": []})
    readme = next((p for p in root.glob("README*")), None)
    if readme:
        for line in readme.read_text(errors="ignore").splitlines():
            m = re.match(r"#{2,3}\s+(.*)", line.strip())
            if not m:
                continue
            name = m.group(1).strip()
            key = re.sub(r"[^a-z ]", "", name.lower()).strip()
            if key and key not in _NON_FEATURE_HEADINGS and name not in seen:
                seen.add(name)
                feats.append({"name": name, "summary": "",
                              "evidence": readme.name, "principles": []})
    return feats[:20]


def _offline_specs(root: pathlib.Path) -> list[dict]:
    """No-LLM specs: Python-module docstrings as decisions, skipping tests and
    package dunders (the test_*/__init__ bug). Non-Python repos → one area per
    top source dir. Humanized names, never raw file stems."""
    import ast
    specs: list[dict] = []
    for p in sorted(_py_files(root)):
        if _is_test(p) or p.name == "__init__.py":
            continue
        try:
            doc = ast.get_docstring(ast.parse(p.read_text(errors="ignore")))
        except (SyntaxError, OSError):
            doc = None
        if doc:
            specs.append({"area": _humanize(p.stem),
                          "decision": doc.strip().splitlines()[0],
                          "rationale": "", "evidence": _rel(p, root),
                          "principles": []})
    if not specs:                       # non-Python repo → group by top source dir
        from collections import defaultdict
        groups: dict[str, list[str]] = defaultdict(list)
        for p in _source_files(root):
            if _is_test(p):
                continue
            parts = pathlib.Path(_rel(p, root)).parts
            groups["/".join(parts[:-1][:2]) or parts[0]].append(parts[-1])
        for key, files in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            specs.append({"area": _humanize(key.replace("/", " ")) or "Root",
                          "decision": f"{len(files)} source files: "
                                      + ", ".join(sorted(files)[:6]),
                          "rationale": "", "evidence": key, "principles": []})
    return specs[:15]


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
        except Exception as e:                       # noqa: BLE001
            log.warning("build_features LLM pass failed, using offline floor: %s", e)
    return _offline_features(root)


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
        except Exception as e:                       # noqa: BLE001
            log.warning("build_tech_specs LLM pass failed, using offline floor: %s", e)
    return _offline_specs(root)


# --- combined index (balanced features + tech specs) -----------------------
def build_index(root: pathlib.Path, *, llm: bool, principles: list[dict],
                model: str = "claude-sonnet-4-6") -> tuple[list[dict], list[dict], bool]:
    """Partition the repo into PRODUCT FEATURES and TECHNICAL AREAS in ONE call,
    so the two stay balanced and distinct (separate calls let the model dump
    everything into 'features'). Returns ``(features, tech_specs, llm_index)``.

    ``llm_index`` is True only when the LLM pass actually produced the lists; it
    is False both in offline mode and — critically — when the LLM pass was
    *requested but fell back* to the offline floor, so the caller never ships a
    README/file-stem list under ``llm:true`` with no signal (the split-brain bug).

    Offline → API endpoints + filtered README headings (features) + module
    docstrings (specs)."""
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
            "`principles`: axis ids from the menu it relies on (0-3, only from the menu). "
            "ALSO give each item a `group`: the higher-level AREA it nests under — "
            "organize all items into ~3-6 areas and reuse the SAME area string for "
            "siblings (e.g. 'Meeting Intelligence', 'Storage & Retrieval'). List items "
            "so grouped siblings are adjacent.\n\n"
            "PRINCIPLE MENU (axis: value):\n" + _axis_menu(principles) +
            '\n\nReply JSON object: {"features":[{"name","summary","evidence","group",'
            '"principles":[]}], "tech_specs":[{"area","decision","rationale",'
            '"evidence","group","principles":[]}]}.\n\nREPO:\n' + ctx)
        try:
            # 8192: a balanced object (~13 features + ~13 specs, each with prose)
            # overflows 4096 and truncates. _llm_obj now strips fences + repairs
            # once before raising, so a single bad reply no longer silently
            # collapses to the offline floor.
            d = _llm_obj(prompt, model=model, max_tokens=8192)
            feats = d.get("features") or []
            specs = d.get("tech_specs") or []
            for f in feats:
                f["principles"] = _valid_links(f, known)
            for s in specs:
                s["principles"] = _valid_links(s, known)
            if feats or specs:
                return feats, specs, True
            log.warning("build_index LLM pass returned empty lists; offline floor")
        except Exception as e:                       # noqa: BLE001
            log.warning("build_index LLM pass failed (%s); offline floor", e)
    # offline fallback: reuse the single-list builders (no LLM inside)
    return (build_features(root, llm=False, principles=principles, model=model),
            build_tech_specs(root, llm=False, principles=principles, model=model),
            False)


# --- full templated documents (PRD / Tech Spec) ----------------------------
# Two DISTINCT audiences, not two specs:
#   • PRD  — customer-facing: who it's for, what they can do, the experience
#            (a user-journey diagram + a low-fi wireframe). No implementation.
#   • Spec — engineer-facing and SELF-CONTAINED: leads with a plain-language
#            description of what the component is and why (so the reader never has
#            to open the PRD), then the technical design + a runtime sequence
#            diagram. No forced "Non-Goals" filler.
DOC_TEMPLATES: dict[str, list[str]] = {
    "prd": ["What You Can Do", "Who It's For", "User Stories",
            "The Experience", "Wireframe", "What Success Feels Like"],
    "spec": ["Overview", "Runtime Flow", "Data & Interfaces",
             "Key Decisions & Trade-offs", "Risks & Open Questions"],
}
# Shared writing rules. No "Non-Goals"/"Out of Scope" — it read as forced filler.
_DENSITY_RULES = (
    "WRITING RULES:\n"
    "• Prefer tables and tight bullets to paragraphs; never restate the same point "
    "across sections or restate the section title.\n"
    "• Do NOT add a 'Non-Goals' or 'Out of Scope' section anywhere — it is forced "
    "filler here.\n")

# PRD — customer-facing. The whole point is to NOT read like the tech spec.
_PRD_RULES = (
    "AUDIENCE — write for a CUSTOMER / PM in plain language about VALUE and "
    "EXPERIENCE. Describe what users can DO and what they SEE — NEVER how it's "
    "built: no modules, data models, schemas, function names, endpoints, or code "
    "(those live in the Tech Spec). Keep it crisp.\n"
    "• 'User Stories' — 3-6 bullets, each `As a <role>, I want <capability>, so "
    "that <outcome>`, with 1-2 user-facing acceptance criteria.\n"
    "• 'The Experience' — the journey the user takes. Include exactly ONE Mermaid "
    "`journey` diagram in a ```mermaid block: a `title`, `section`s, and steps "
    "`Step name: <1-5 score>: Actor`.\n"
    "• 'Wireframe' — a LOW-FIDELITY sketch of the primary screen in a plain ``` "
    "code block (box-drawing/ASCII). If the product has NO GUI, sketch the real "
    "interaction instead: a CLI session, an API request→response, or device "
    "behaviour.\n")

# Spec — engineer-facing, self-contained, with the runtime sequence diagram.
_SPEC_RULES = (
    "AUDIENCE — write for an ENGINEER, and make the spec SELF-CONTAINED: the "
    "'Overview' must describe, in plain language, WHAT this component is, the "
    "problem it solves, and where it sits in the system — enough that the reader "
    "never needs the PRD — then the rest goes deep on the design. Be complete, not "
    "padded.\n"
    "• The 'Runtime Flow' section MUST contain exactly ONE Mermaid sequence diagram "
    "inside a ```mermaid fenced block whose first line is `sequenceDiagram`. "
    "Participants are REAL modules/classes/functions from the source below; arrows "
    "are REAL calls labelled with the real function name; include the `alt`/`opt` "
    "branches that exist in the code. In diagram text use commas, NOT semicolons "
    "(a `;` breaks Mermaid rendering).\n")


# UI surfaces fed to the PRD so the wireframe is grounded in the real screens
# (HTML/JSX/templates aren't all in _SOURCE_EXTS, so glob them directly).
_UI_EXTS = {".html", ".htm", ".jsx", ".tsx", ".vue", ".svelte", ".astro"}


def _ui_blob(root: pathlib.Path, *, max_files: int = 6, per_file: int = 2500) -> str:
    """A bounded view of the UI/template files — context for the PRD wireframe."""
    files = [p for p in root.rglob("*")
             if p.suffix.lower() in _UI_EXTS and p.is_file()
             and not any(d in p.parts for d in _SKIP_DIRS) and not _is_test(p)]
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    out: list[str] = []
    for p in files[:max_files]:
        try:
            out.append(f"# {_rel(p, root)}\n"
                       + p.read_text(encoding="utf-8", errors="ignore")[:per_file])
        except OSError:
            continue
    return "\n\n".join(out)


def _insufficient_source_doc(title: str, summary: str, sections: list[str]) -> str:
    """An honest placeholder for a spec whose repo has ~no readable source. A
    grounding tool must say 'I can't see the code' rather than invent a spec (the
    fabricated JS auth spec / Svelte-bleed failure)."""
    return (f"# {title}\n\n" + (f"> {summary}\n\n" if summary else "") +
            "> ⚠ **Insufficient source coverage.** No readable source files were "
            "found for this area, so a grounded specification cannot be generated "
            "without inventing it. Check the clone, or connect a repo whose source "
            "is in a supported language.\n")


def build_doc(root: pathlib.Path, *, kind: str, title: str, summary: str = "",
              llm: bool, model: str = "claude-sonnet-4-6") -> str:
    """Generate ONE full, classically-templated document (markdown) for a feature
    (kind='prd') or technical area (kind='spec'), grounded in the repo.

    Specs carry a required Mermaid ``sequenceDiagram`` and refuse to generate when
    there's no source to ground them. Offline → a section skeleton."""
    sections = DOC_TEMPLATES[kind]
    if llm:
        if kind == "spec":
            if _source_coverage(root) == 0:        # no code → don't fabricate
                return _insufficient_source_doc(title, summary, sections)
            # repo tree anchors the model to THIS repo (stops free-association onto
            # another project); per_file=8000 stops big modules being truncated
            # into invented/"truncated signature" claims.
            ctx = ("REPO FILES:\n" + _repo_tree(root) + "\n\nSOURCE:\n"
                   + _source_blob(root, max_files=25, per_file=8000))
            intro = (
                f'Write a Technical Specification for "{title}"'
                + (f" — {summary}" if summary else "")
                + " of THIS repository. Ground EVERY statement in the code below; "
                  "never invent types or signatures that aren't present — if "
                  "something isn't in the context, say so. Name real modules, "
                  "types, and endpoints.\n\n")
            rules = _DENSITY_RULES + _SPEC_RULES
        else:
            ctx = _read_docs(root)
            ui = _ui_blob(root)
            if ui:                                 # ground the wireframe, don't spec the code
                ctx += ("\n\nUI / SURFACE FILES (to ground the wireframe — describe "
                        "what the user SEES, not this code):\n" + ui)
            if not ctx.strip() and _source_coverage(root) == 0:
                return _insufficient_source_doc(title, summary, sections)
            intro = (
                f'Write a Product Requirements Document for "{title}"'
                + (f" — {summary}" if summary else "")
                + " of THIS product. Ground the users, capabilities, and experience "
                  "in the material below; don't invent capabilities it doesn't "
                  "have.\n\n")
            rules = _DENSITY_RULES + _PRD_RULES
        prompt = (intro + rules
                  + "\nStart with a single H1 title line, then EXACTLY these H2 "
                    "sections, in order:\n" + "\n".join(f"## {s}" for s in sections)
                  + "\n\nREPO CONTEXT:\n" + ctx)
        try:
            return _sanitize_doc_mermaid(_llm_text(prompt, model=model, max_tokens=4096))
        except Exception as e:                       # noqa: BLE001
            log.warning("build_doc LLM pass failed for %r (%s); offline skeleton", title, e)
    return (f"# {title}\n\n" + (f"> {summary}\n\n" if summary else "") +
            "\n\n".join(f"## {s}\n\n_Offline mode — connect an API key to "
                        "generate this section._" for s in sections))


# --- overview (the root of the nested PRD / Tech-Spec tree) ----------------
# title, H2 sections, and a guiding instruction per kind. The overview is the
# big picture; the nested docs hold the detail. It carries a Mermaid flowchart
# that wires the AREAS together ("how it fits together").
_OVERVIEW: dict[str, tuple[str, list[str], str]] = {
    "prd": ("Product Overview",
            ["What This Product Is", "Who It's For", "The Capabilities",
             "How It Fits Together"],
            "Write a high-level PRODUCT OVERVIEW — the big picture a PM or customer "
            "needs before drilling into individual features. Plain language about "
            "value and experience; NO implementation detail."),
    "spec": ("Architecture Overview",
             ["System Overview", "Key Components", "How It Fits Together",
              "Cross-Cutting Concerns"],
             "Write a high-level ARCHITECTURE OVERVIEW — the big picture an engineer "
             "needs before drilling into individual specs: the major components and "
             "how control/data flows between them. Ground every claim in the code."),
}


def build_overview(root: pathlib.Path, *, kind: str, areas: list[dict],
                   llm: bool, model: str = "claude-sonnet-4-6") -> str:
    """Generate the root OVERVIEW doc for a kind. ``areas`` is ``[{group, items}]``
    — the nested docs this overview maps. The 'How It Fits Together' section
    carries a Mermaid flowchart wiring the areas together."""
    title, sections, guide = _OVERVIEW[kind]
    areas_txt = "\n".join(f"  • {a['group']}: " + ", ".join(a["items"])
                          for a in areas if a.get("group"))
    if llm and (kind == "prd" or _source_coverage(root) > 0):
        ctx = (_read_docs(root) + "\n\n" + _ui_blob(root) if kind == "prd"
               else _source_blob(root, max_files=22, per_file=4000))
        prompt = (
            guide + " of THIS repository.\n\n" + _DENSITY_RULES +
            "• 'How It Fits Together' MUST contain exactly ONE Mermaid `flowchart` "
            "in a ```mermaid block (first line `flowchart TD`) that wires the AREAS "
            "below into how the system/journey flows. Use commas, not semicolons.\n\n"
            "AREAS (the nested docs this overview maps — reference them, don't "
            "restate them):\n" + (areas_txt or "(none surfaced)") +
            "\n\nStart with a single H1 title line, then EXACTLY these H2 sections, "
            "in order:\n" + "\n".join(f"## {s}" for s in sections) +
            "\n\nREPO CONTEXT:\n" + ctx)
        try:
            return _sanitize_doc_mermaid(_llm_text(prompt, model=model, max_tokens=3072))
        except Exception as e:                       # noqa: BLE001
            log.warning("build_overview(%s) failed (%s); skeleton", kind, e)
    body = "\n".join(f"- **{a['group']}** — {', '.join(a['items'])}"
                     for a in areas if a.get("group"))
    return f"# {title}\n\n" + (body or "_Offline mode — connect an API key._")


# --- domain model (DDD graph) ----------------------------------------------
# Every Mermaid diagram type we accept. Order matters only for the prefix check.
# (Previously hard-coded to class/graph/flowchart, which silently corrupted a
# sequenceDiagram by prepending `classDiagram\n` — see _spec sequence diagrams.)
MERMAID_KINDS = ("classDiagram", "sequenceDiagram", "stateDiagram-v2",
                 "stateDiagram", "erDiagram", "flowchart", "graph", "journey",
                 "gantt", "pie", "mindmap")


def _sanitize_mermaid(code: str) -> str:
    """Fix render-breakers in generated Mermaid. ``;`` is a Mermaid statement
    separator: a model writing `A->>B: parse sig; check window` splits into two
    statements, the second arrow-less → "Syntax error in text" and the diagram
    fails to render. We never use ``;`` structurally, so fold it to ``,``."""
    return (code or "").replace(";", ",")


_MERMAID_FENCE_RE = re.compile(r"(```\s*mermaid[^\n]*\n)(.*?)(```)", re.S | re.I)


def _sanitize_doc_mermaid(markdown: str) -> str:
    """Sanitize every ```mermaid block embedded in a generated document (the spec
    sequence diagrams), so a stray ``;`` doesn't break in-browser rendering."""
    return _MERMAID_FENCE_RE.sub(
        lambda m: m.group(1) + _sanitize_mermaid(m.group(2)) + m.group(3), markdown)


def _clean_mermaid(text: str, *, default_kind: str = "classDiagram") -> str:
    """Strip code fences / prose around a mermaid diagram and ensure it declares
    a known diagram type. ``default_kind`` is only prepended when the text starts
    with no recognizable kind (never override a real ``sequenceDiagram`` etc.)."""
    t = (text or "").strip()
    if "```" in t:                                   # pull out a fenced block
        for p in (x.strip() for x in t.split("```")):
            if p.startswith("mermaid"):
                t = p[len("mermaid"):].strip(); break
            if p.startswith(MERMAID_KINDS):
                t = p; break
    if not t.startswith(MERMAID_KINDS):
        t = f"{default_kind}\n" + t
    return _sanitize_mermaid(t)


def _ann_type_names(node) -> list[str]:
    """Identifier names referenced in a type annotation AST — unwraps Optional[X],
    list[X], dict[K, V], A | B, and `Forward` string refs to their inner names."""
    import ast
    out: list[str] = []
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out.append(n.id)
        elif isinstance(n, ast.Attribute):
            out.append(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value.strip("'\" "))     # forward-ref string annotation
    return out


# A domain extraction is (fields, bases, refs, docs) keyed by type name:
#   fields[T] = ["name: Type", ...]   bases[T] = [local base names]
#   refs[T]   = {referenced local type names}   docs[T] = one-line role/description
_Domain = tuple[dict[str, list[str]], dict[str, list[str]], dict[str, set[str]], dict[str, str]]


def _domain_ast(root: pathlib.Path) -> _Domain:
    """Python domain via the AST: classes, local bases, typed-field/ctor refs, and
    the class docstring's first line (so a field-less service isn't described '—')."""
    import ast
    fields: dict[str, list[str]] = {}
    bases: dict[str, list[str]] = {}
    refs: dict[str, set[str]] = {}
    docs: dict[str, str] = {}
    for p in _py_files(root):
        if _is_test(p):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8", errors="ignore"))
        except (SyntaxError, OSError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            fs: list[str] = []
            ref: set[str] = set()
            for b in node.body:
                if isinstance(b, ast.AnnAssign) and isinstance(b.target, ast.Name):
                    tn = _ann_type_names(b.annotation)
                    fs.append(b.target.id + (f": {tn[0]}" if tn else ""))
                    ref.update(tn)
                elif isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef)) and b.name == "__init__":
                    for a in b.args.args:
                        if a.annotation is not None:
                            ref.update(_ann_type_names(a.annotation))
            doc = (ast.get_docstring(node) or "").strip().splitlines()
            fields[node.name] = fs[:5]
            bases[node.name] = [b.id for b in node.bases if isinstance(b, ast.Name)]
            refs[node.name] = ref
            docs[node.name] = doc[0][:80] if doc else ""
    return fields, bases, refs, docs


# Type declarations across curly-brace languages (TS/JS/Go/Rust/Java/C#/C++/Swift…).
_DECL_RE = re.compile(
    r"\b(?:export\s+)?(?:abstract\s+)?"
    r"(?:class|interface|struct|enum|trait|protocol)\s+([A-Z]\w+)", re.MULTILINE)
_GO_TYPE_RE = re.compile(r"\btype\s+([A-Z]\w+)\s+(?:struct|interface)\b")
_TS_TYPE_RE = re.compile(r"\btype\s+([A-Z]\w+)\s*=")
# inheritance: `class X extends Y`, `class X implements Y`, `X: Y`, Rust `impl T for X`.
_EXTENDS_RE = re.compile(r"\b([A-Z]\w+)\s+(?:extends|implements|:)\s+([A-Z]\w+)")
_IMPL_FOR_RE = re.compile(r"\bimpl\s+([A-Z]\w+)\s+for\s+([A-Z]\w+)")


def _domain_regex(root: pathlib.Path) -> _Domain:
    """Language-agnostic domain via regex for non-Python repos (the AST path is
    Python-only, which is why JS/Go/Rust repos used to ship an empty diagram).
    Nodes = declared types; edges = extends/implements + co-mention within a
    type's neighbourhood. A heuristic floor — the LLM path is primary."""
    fields: dict[str, list[str]] = {}
    bases: dict[str, list[str]] = {}
    refs: dict[str, set[str]] = {}
    blocks: dict[str, str] = {}            # type -> ~window of text after its decl
    for p in _source_files(root):
        if _is_test(p) or p.suffix == ".py":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        decls = sorted((m.start(), m.group(1))
                       for rx in (_DECL_RE, _GO_TYPE_RE, _TS_TYPE_RE)
                       for m in rx.finditer(text))
        for i, (pos, name) in enumerate(decls):
            fields.setdefault(name, [])
            bases.setdefault(name, [])
            refs.setdefault(name, set())
            # bound the block at the NEXT declaration so co-mention doesn't bleed
            # across types into a fully-connected hairball.
            end = decls[i + 1][0] if i + 1 < len(decls) else pos + 1200
            blocks[name] = text[pos:min(end, pos + 1200)]
        for m in _EXTENDS_RE.finditer(text):
            child, parent = m.group(1), m.group(2)
            bases.setdefault(child, []).append(parent)
        for m in _IMPL_FOR_RE.finditer(text):     # Rust: impl Trait for Type
            trait, typ = m.group(1), m.group(2)
            bases.setdefault(typ, []).append(trait)
    local = set(fields)
    bases = {k: [b for b in v if b in local] for k, v in bases.items()}
    for name, block in blocks.items():            # co-mention → association
        for other in re.findall(r"\b([A-Z]\w+)\b", block[len(name):]):
            if other in local and other != name and other not in bases.get(name, []):
                refs[name].add(other)             # not a base → don't dup inheritance
    return fields, bases, refs, {}


# Heuristic infra markers — names that signal a persistence/transport/plumbing
# type rather than a domain concept. Conservative (precision over recall) so we
# don't drop real domain types; note `Record` is intentionally absent (NormRecord
# IS domain). The LLM path classifies far better; this is the offline floor.
_INFRA_SUFFIX = ("Store", "Repository", "Repo", "Dao", "Config", "Settings",
                 "Options", "Dto", "Request", "Response", "Client", "Adapter",
                 "Factory", "Builder", "Manager", "Runner", "Worker", "Job",
                 "Mapper", "Serializer", "Controller", "Middleware", "Router",
                 "Error", "Exception", "Result", "Report")
_INFRA_PREFIX = ("Pg", "Postgres", "Sql", "Sqlite", "Mongo", "Redis", "InMemory",
                 "Http", "Grpc", "Mock", "Fake", "Stub")


def _is_infra(name: str) -> bool:
    """A type whose NAME marks it as plumbing (persistence impl, DTO, config,
    client, result wrapper…) — excluded from the DDD model."""
    return name.endswith(_INFRA_SUFFIX) or name.startswith(_INFRA_PREFIX)


def _ddd_kind(name: str, flds: list[str], bs: list[str]) -> str:
    """Coarse DDD stereotype for the offline floor (the LLM path tags precisely)."""
    bset = set(bs)
    if any("Enum" in b for b in bs):
        return "Value Object"                          # enums are value objects
    if {"Protocol", "ABC"} & bset:
        return ("Repository" if name.endswith(("Reader", "Repository", "Store"))
                else "Service")
    if any(f.split(":")[0].strip() in ("id", "uuid", "pk") for f in flds):
        return "Entity"
    if not flds:
        return "Service"                               # behaviour, no state
    return "Value Object" if len(flds) <= 4 else "Entity"


def _render_domain(dom: _Domain, *, cap: int = 16, ddd: bool = True) -> dict:
    """Shared renderer: optionally drop infrastructure (``ddd``), derive edges,
    collapse pure-leaf implementations under their base (so 3 near-identical
    `*Embedder` boxes become one annotated node), rank by connectivity, and emit a
    Mermaid classDiagram (with DDD stereotypes) + entity list."""
    fields, bases, refs, docs = dom
    if ddd:                                            # keep the domain, drop plumbing
        fields = {k: v for k, v in fields.items() if not _is_infra(k)}
    kinds = {c: _ddd_kind(c, fields[c], bases.get(c, [])) for c in fields}
    local = set(fields)

    # collapse polymorphic impls into one annotated node (so 3 near-identical
    # `*Embedder` boxes don't clutter the model). Two ways an impl attaches:
    #   (a) explicit inheritance — a local base (TS `extends`, Python `class X(Base)`);
    #   (b) structural — a field-less orphan NAMED `<Something>Base` (the duck-typed
    #       Protocol/ABC pattern, where FakeEmbedder never inherits Embedder).
    referenced = {r for s in refs.values() for r in s}
    children: dict[str, list[str]] = {}
    for c in fields:
        for base in bases.get(c, []):
            children.setdefault(base, []).append(c)

    def _is_leaf(k: str) -> bool:
        return (not fields.get(k) and k not in referenced and not children.get(k))

    collapsed: set[str] = set()
    impls: dict[str, list[str]] = {}
    for base in fields:                           # (a) inheritance-based
        leaves = [k for k in children.get(base, []) if _is_leaf(k)]
        # (b) name-suffix-based: orphan field-less `*Base` with no base of its own
        leaves += [k for k in fields if k != base and k.endswith(base)
                   and len(k) > len(base) and _is_leaf(k) and not bases.get(k)
                   and k not in leaves]
        if len(leaves) >= 2:
            collapsed.update(leaves)
            impls[base] = leaves

    edges: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for c in fields:
        if c in collapsed:
            continue
        for base in bases.get(c, []):
            if base in local and base not in collapsed and (e := ("inh", base, c)) not in seen:
                seen.add(e); edges.append(e)
        for r in refs.get(c, ()):
            if r in local and r != c and r not in collapsed and (e := ("assoc", c, r)) not in seen:
                seen.add(e); edges.append(e)

    nodes = [c for c in fields if c not in collapsed]
    degree: dict[str, int] = {c: 0 for c in nodes}
    for _, a, b in edges:
        if a in degree: degree[a] += 1
        if b in degree: degree[b] += 1
    keep = sorted(nodes, key=lambda c: (-degree[c], c))[:cap]
    keepset = set(keep)

    def _describe(c: str) -> str:
        parts = list(fields.get(c, []))
        if c in impls:
            parts = [*parts, "impls: " + ", ".join(_short_impl(k, c) for k in impls[c])]
        return ", ".join(parts) or docs.get(c) or (
            "implements " + ", ".join(bases[c]) if bases.get(c) else "—")

    lines = ["classDiagram"]
    for c in keep:
        body = f"  <<{kinds[c]}>>\n" if kinds.get(c) else ""
        body += "".join(f"  +{f}\n" for f in fields.get(c, []))
        if c in impls:
            body += f"  +«{len(impls[c])} impls»\n"
        lines.append(f"class {c} {{\n{body}}}")
    for kind, a, b in edges:
        if a in keepset and b in keepset:
            lines.append(f"{a} <|-- {b}" if kind == "inh" else f"{a} --> {b}")
    entities = [{"name": c, "kind": kinds.get(c, ""), "description": _describe(c)}
                for c in keep]
    return {"mermaid": "\n".join(lines), "entities": entities}


def _short_impl(name: str, base: str) -> str:
    """`OpenAIEmbedder` under base `Embedder` → `OpenAI` (drop the base suffix)."""
    return name[: -len(base)] if name.endswith(base) and len(name) > len(base) else name


def _domain_offline(root: pathlib.Path) -> dict:
    """No-LLM domain map. Python → AST; otherwise a language-agnostic regex pass.
    Both feed the shared renderer (collapse + ranking), so the result reads as a
    connected model, not a row of standalone boxes — for ANY language."""
    ast_dom = _domain_ast(root)
    if len(ast_dom[0]) >= 4:                       # enough Python classes to model
        return _render_domain(ast_dom)
    rx_dom = _domain_regex(root)
    # whichever extractor found more types wins (handles mixed + non-Python repos)
    return _render_domain(rx_dom if len(rx_dom[0]) > len(ast_dom[0]) else ast_dom)


_EDGE_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*(?:<\|--|--\|>|\.\.\|>|\*--|o--|\.\.>|-->|--)\s*([A-Za-z_]\w*)")


def _prune_orphans(mermaid: str) -> str:
    """Drop class boxes that participate in NO relationship, so a strong-nodes /
    weak-edges LLM reply contributes its connected core instead of being discarded
    wholesale to the offline graph (and instead of shipping standalone boxes)."""
    lines = mermaid.splitlines()
    connected: set[str] = set()
    for ln in lines:
        if (m := _EDGE_RE.match(ln)):
            connected.update((m.group(1), m.group(2)))
    if not connected:
        return mermaid
    out: list[str] = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        block_open = re.match(r"\s*class\s+([A-Za-z_]\w*)\s*\{", ln)
        if block_open:
            if "}" in ln:                              # single-line block
                if block_open.group(1) in connected:
                    out.append(ln)
                i += 1
                continue
            block = [ln]; i += 1
            while i < len(lines) and "}" not in lines[i]:
                block.append(lines[i]); i += 1
            if i < len(lines):
                block.append(lines[i]); i += 1
            if block_open.group(1) in connected:
                out.extend(block)
            continue
        bare = re.match(r"\s*class\s+([A-Za-z_]\w*)\s*$", ln)
        if bare and bare.group(1) not in connected:
            i += 1
            continue
        out.append(ln); i += 1
    return "\n".join(out)


def build_domain(root: pathlib.Path, *, llm: bool,
                 model: str = "claude-sonnet-4-6") -> dict:
    """Extract the domain model — key objects and how they interact — as a Mermaid
    classDiagram plus an entity list. Offline → a language-agnostic class graph.

    Refuses the LLM call when there's no source to ground it (a doc tool must fail
    loud, not invent a domain for a repo it can't see)."""
    if llm and _source_coverage(root) > 0:
        blob = _source_blob(root, max_files=30, per_file=2500)
        prompt = (
            "Model the BUSINESS DOMAIN of this codebase as a DOMAIN-DRIVEN DESIGN "
            "model — the ubiquitous language a domain expert would recognize, NOT "
            "a class or database diagram. Capture the DOMAIN, not the plumbing.\n\n"
            "INCLUDE only domain concepts, each tagged with its DDD stereotype:\n"
            "  • <<Aggregate Root>> — an entity that owns a consistency boundary\n"
            "  • <<Entity>>         — has identity + a lifecycle\n"
            "  • <<Value Object>>   — immutable descriptor, no identity (incl. enums)\n"
            "  • <<Service>>        — domain logic spanning entities\n"
            "  • <<Repository>>     — the persistence SEAM (interface), if it is a "
            "genuine domain concept\n"
            "EXCLUDE infrastructure & plumbing ENTIRELY: persistence/ORM "
            "implementations and DB-row mappings, DTOs / request-response / "
            "serialization shapes, config & settings objects, clients, adapters, "
            "factories, job/worker runners, controllers, generic result/response "
            "wrappers, and utilities. If a type exists only to talk to a database, a "
            "framework, or the wire, it is NOT domain — leave it out. Aim for the "
            "~8-14 concepts that actually carry the domain.\n\n"
            "Output a Mermaid `classDiagram`. Put the stereotype as the FIRST line "
            "inside each class body, e.g. `class Proposal {\\n  <<Aggregate Root>>\\n"
            "  +id\\n}`. Show the relationships that matter: aggregate composition "
            "`Root *-- Member : owns`, references between aggregates `A --> B : "
            "refs`, service dependencies `Service ..> Repository : reads`. Every "
            "concept connects to at least one other. Ground strictly in the code "
            "below.\n\n"
            'Reply JSON: {"mermaid": "classDiagram\\n  ...", "entities": '
            '[{"name","kind","description"}]} where `kind` is the stereotype '
            "(Aggregate Root / Entity / Value Object / Service / Repository)."
            "\n\nSOURCE:\n" + blob)
        try:
            d = _llm_obj(prompt, model=model, max_tokens=4096)
            mermaid = _prune_orphans(_clean_mermaid(d.get("mermaid", "")))
            # Keep the LLM diagram only if it has real relationships; otherwise the
            # offline graph (which derives connections deterministically) is better
            # than a row of standalone boxes.
            if any(rel in mermaid for rel in ("<|--", "-->", "*--", "..>", "--|>", "o--")):
                d["mermaid"] = mermaid
                d.setdefault("entities", [])
                return d
            log.warning("build_domain LLM diagram had no edges; offline graph")
        except Exception as e:                       # noqa: BLE001
            log.warning("build_domain LLM pass failed (%s); offline graph", e)
    return _domain_offline(root)


# --- top-level -------------------------------------------------------------
def build(root: str | pathlib.Path, *, llm: bool | None = None,
          model: str = "claude-sonnet-4-6") -> dict:
    root = pathlib.Path(root)
    use_llm = _llm_enabled() if llm is None else llm
    principles = [_obs_dict(o) for o in mine_all(root, llm=use_llm, model=model)]
    features, specs, llm_index = build_index(root, llm=use_llm, principles=principles, model=model)
    return {
        "features": features,
        "tech_specs": specs,
        "principles": principles,
        "llm": use_llm,
        "llm_index": llm_index,
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
    domain = build_domain(root, llm=use_llm, model=model)
    # Validation gate: a repo with source must yield a connected, non-empty model.
    # If not, say so (an empty `classDiagram` silently asserts nothing connects).
    has_edges = any(r in domain.get("mermaid", "")
                    for r in ("<|--", "-->", "*--", "..>", "--|>", "o--"))
    if _source_coverage(root) > 0 and not (has_edges and domain.get("entities")):
        yield {"event": "status", "step": "⚠ domain model unavailable — no "
               "connected entities could be extracted for this repo's language."}
    yield {"event": "domain", "domain": domain}

    # 3) feature + tech-spec LISTS — emit immediately (stable slugs) so the
    #    default PRDs/Tech-Specs tabs populate within seconds.
    yield {"event": "status", "step": "extracting features & tech specs…"}
    features, specs, llm_index = build_index(root, llm=use_llm, principles=principles, model=model)
    if use_llm and not llm_index:        # LLM requested but the index fell back
        yield {"event": "status", "step": "⚠ feature/spec extraction degraded to "
               "the offline floor (LLM index failed) — names may be approximate."}
    feat_items = _nest("prd", _with_slugs("prd", [{"title": f.get("name", ""), **f} for f in features]))
    spec_items = _nest("spec", _with_slugs("spec", [{"title": s.get("area", ""), **s} for s in specs]))
    # an Overview doc roots each kind's tree (seq 0, pinned at the top of the nav)
    ov_prd = {"kind": "prd", "slug": "overview", "title": "Product Overview",
              "summary": "the whole product at a glance", "grp": "", "seq": 0,
              "overview": True, "areas": _areas(feat_items), "principles": []}
    ov_spec = {"kind": "spec", "slug": "overview", "title": "Architecture Overview",
               "summary": "the whole system at a glance", "grp": "", "seq": 0,
               "overview": True, "areas": _areas(spec_items), "principles": []}
    items = [ov_prd, *feat_items, ov_spec, *spec_items]
    yield {"event": "features", "items": [i for i in items if i["kind"] == "prd"]}
    yield {"event": "tech_specs", "items": [i for i in items if i["kind"] == "spec"]}

    # 4) full templated documents — generated CONCURRENTLY (the heavy part: one
    #    LLM call per item). Emit as each lands so its row flips to "ready".
    yield {"event": "status", "step": f"generating {len(items)} full documents…"}

    def _one(item: dict) -> dict:
        if item.get("overview"):
            md = build_overview(root, kind=item["kind"], areas=item.get("areas", []),
                                 llm=use_llm, model=model)
        else:
            md = build_doc(root, kind=item["kind"], title=item["title"],
                           summary=item.get("summary", ""), llm=use_llm, model=model)
        return {"event": "doc", "kind": item["kind"], "slug": item["slug"],
                "title": item["title"], "summary": item.get("summary", ""),
                "principles": item.get("principles", []),
                "grp": item.get("grp", ""), "seq": item.get("seq", 0), "markdown": md}

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


def _nest(kind: str, items: list[dict]) -> list[dict]:
    """Attach ``grp`` (the area an item nests under) and ``seq`` (display order).
    seq starts at 1 — the Overview doc takes seq 0 at the top of the tree."""
    for i, it in enumerate(items):
        it["grp"] = (it.get("group") or "").strip()
        it["seq"] = i + 1
    return items


def _areas(items: list[dict]) -> list[dict]:
    """Group items into ``[{group, items:[title, ...]}]`` preserving first-seen
    order — the map the Overview doc references."""
    order: list[str] = []
    by: dict[str, list[str]] = {}
    for it in items:
        g = it.get("grp") or ""
        if not g:
            continue
        if g not in by:
            by[g] = []; order.append(g)
        by[g].append(it.get("title", ""))
    return [{"group": g, "items": by[g]} for g in order]


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
