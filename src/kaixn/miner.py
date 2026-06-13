"""The deterministic miner — the no-LLM floor of the engineering handbook.

Scans a Python repo and computes, for each *deterministic* axis, the repo's value
with **exact** support: `matches / sites`, plus sample evidence and counterexamples.
This is the foundation the LLM propose/verify pass builds on (docs/engineering-
handbook-design.md §5); it runs offline, needs no API, and replaces the hand-
estimated support in the validation playbook with real counts.

A convention is recorded when its consistency ratio clears the threshold (the
anti-noise knob); below it the dimension is reported as having no convention — a
true answer, not a miss.

    python src/kaixn/miner.py <repo-root> [--threshold 0.8] [--out playbook.md]
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import re
from dataclasses import dataclass, field

_SKIP_DIRS = {".venv", "venv", ".git", "__pycache__", "build", "dist", "node_modules"}
_SNAKE = re.compile(r"[a-z_][a-z0-9_]*$")


@dataclass(slots=True)
class Site:
    path: str
    detail: str = ""


@dataclass(slots=True)
class Observation:
    """One axis's observed value on a repo, with exact deterministic support."""

    axis_id: str
    statement: str                       # rendered norm statement
    value: str
    n_match: int
    n_total: int
    sample_sites: list[Site] = field(default_factory=list)
    counterexamples: list[Site] = field(default_factory=list)
    tier: str = "advisory"
    method: str = "deterministic"

    @property
    def ratio(self) -> float:
        return self.n_match / self.n_total if self.n_total else 0.0

    def is_convention(self, threshold: float) -> bool:
        return self.n_total > 0 and self.ratio >= threshold


# --- repo walking ----------------------------------------------------------
def _py_files(root: pathlib.Path) -> list[pathlib.Path]:
    return [p for p in root.rglob("*.py")
            if not any(part in _SKIP_DIRS for part in p.parts)]


def _is_test(path: pathlib.Path) -> bool:
    return path.name.startswith("test_") or any(
        part in ("tests", "test") for part in path.parts)


def _parse(path: pathlib.Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, OSError):
        return None


def _rel(path: pathlib.Path, root: pathlib.Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# --- detectors -------------------------------------------------------------
# Each detector takes (root, files, parsed) and returns an Observation or None
# (None = the axis has no population in this repo → relevance-gated out).

def _functions(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def detect_naming_case(root, files, parsed) -> Observation | None:
    match = total = 0
    sites: list[Site] = []
    counter: list[Site] = []
    for path, tree in parsed.items():
        for fn in _functions(tree):
            name = fn.name
            if name.startswith("__") and name.endswith("__"):
                continue
            total += 1
            if _SNAKE.fullmatch(name):
                match += 1
                if len(sites) < 3:
                    sites.append(Site(_rel(path, root), name))
            else:
                counter.append(Site(_rel(path, root), name))
    if not total:
        return None
    return Observation("naming-case",
                       "Functions and variables use snake_case naming.",
                       "snake_case", match, total, sites, counter[:5])


def detect_future_annotations(root, files, parsed) -> Observation | None:
    match = 0
    sites: list[Site] = []
    counter: list[Site] = []
    total = len(parsed)
    for path, tree in parsed.items():
        has = any(isinstance(n, ast.ImportFrom) and n.module == "__future__"
                  and any(a.name == "annotations" for a in n.names)
                  for n in tree.body)
        if has:
            match += 1
            if len(sites) < 3:
                sites.append(Site(_rel(path, root)))
        else:
            counter.append(Site(_rel(path, root)))
    if not total:
        return None
    return Observation("future-annotations",
                       "Every module declares `from __future__ import annotations`.",
                       "all-modules", match, total, sites, counter[:5])


def detect_module_docstring(root, files, parsed) -> Observation | None:
    match = 0
    sites: list[Site] = []
    counter: list[Site] = []
    total = len(parsed)
    for path, tree in parsed.items():
        if ast.get_docstring(tree):
            match += 1
            if len(sites) < 3:
                sites.append(Site(_rel(path, root)))
        else:
            counter.append(Site(_rel(path, root)))
    if not total:
        return None
    return Observation("module-docstrings",
                       "Modules open with an explanatory docstring.",
                       "present", match, total, sites, counter[:5])


def detect_type_hints(root, files, parsed) -> Observation | None:
    """Public functions are fully type-annotated (return + all non-self args)."""
    match = total = 0
    sites: list[Site] = []
    counter: list[Site] = []
    for path, tree in parsed.items():
        for fn in _functions(tree):
            if fn.name.startswith("_"):
                continue
            args = [a for a in fn.args.args if a.arg not in ("self", "cls")]
            total += 1
            annotated = (fn.returns is not None
                         and all(a.annotation is not None for a in args))
            if annotated:
                match += 1
                if len(sites) < 3:
                    sites.append(Site(_rel(path, root), fn.name))
            else:
                counter.append(Site(_rel(path, root), fn.name))
    if not total:
        return None
    return Observation("type-annotations",
                       "Public functions are fully type-annotated.",
                       "full", match, total, sites, counter[:5])


def detect_dataclass_slots(root, files, parsed) -> Observation | None:
    """Among @dataclass uses, how many set slots=True."""
    match = total = 0
    sites: list[Site] = []
    counter: list[Site] = []
    for path, tree in parsed.items():
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for dec in node.decorator_list:
                is_dc = (isinstance(dec, ast.Name) and dec.id == "dataclass") or \
                        (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Name)
                         and dec.func.id == "dataclass")
                if not is_dc:
                    continue
                total += 1
                slotted = isinstance(dec, ast.Call) and any(
                    k.arg == "slots" and isinstance(k.value, ast.Constant)
                    and k.value.value is True for k in dec.keywords)
                if slotted:
                    match += 1
                    if len(sites) < 3:
                        sites.append(Site(_rel(path, root), node.name))
                else:
                    counter.append(Site(_rel(path, root), node.name))
    if not total:
        return None
    return Observation("dataclass-slots",
                       "Value types are `@dataclass(slots=True)`.",
                       "slots=True", match, total, sites, counter[:5])


def detect_test_mirroring(root, files, parsed) -> Observation | None:
    """Each source module has a mirrored tests/test_<name>.py."""
    test_names = {p.name for p in files if "test" in p.parts[-1] or
                  any(part in ("tests", "test") for part in p.parts)}
    src_modules = [p for p in files
                   if not any(part in ("tests", "test") for part in p.parts)
                   and p.name != "__init__.py"
                   and not p.name.startswith("test_")]
    if not src_modules:
        return None
    match = 0
    sites: list[Site] = []
    counter: list[Site] = []
    for p in src_modules:
        expected = f"test_{p.stem}.py"
        if expected in test_names:
            match += 1
            if len(sites) < 3:
                sites.append(Site(_rel(p, root), expected))
        else:
            counter.append(Site(_rel(p, root), f"no {expected}"))
    return Observation("test-mirroring",
                       "Each source module has a mirrored test file.",
                       "mirrored", match, len(src_modules), sites, counter[:5],
                       tier="governed")


def detect_private_surface(root, files, parsed) -> Observation | None:
    """Module-level helpers use a leading underscore for privacy."""
    # heuristic: of module-level defs that are not exported via __all__ and are
    # 'helper-ish', report the leading-underscore convention prevalence.
    match = total = 0
    sites: list[Site] = []
    for path, tree in parsed.items():
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                total += 1
                if node.name.startswith("_"):
                    match += 1
                    if len(sites) < 3:
                        sites.append(Site(_rel(path, root), node.name))
    if not total:
        return None
    # this is descriptive prevalence, not a "should be 100%" axis → report value
    val = "leading-underscore-private" if match else "no-private-helpers"
    return Observation("public-surface",
                       "Private module helpers use a leading underscore.",
                       val, match, total, sites)


DETECTORS = [
    detect_naming_case,
    detect_future_annotations,
    detect_module_docstring,
    detect_type_hints,
    detect_dataclass_slots,
    detect_test_mirroring,
    detect_private_surface,
]


# --- pipeline --------------------------------------------------------------
def mine(root: str | pathlib.Path) -> list[Observation]:
    """Run every deterministic detector over the repo. Returns observations
    (with exact support); callers apply the threshold to decide conventions.

    Production-quality axes measure over *source* files; cross-cutting axes
    (test-mirroring) see the whole repo (the per-axis population from the design)."""
    root = pathlib.Path(root)
    all_files = _py_files(root)
    all_parsed = {p: t for p in all_files if (t := _parse(p)) is not None}
    source_files = [p for p in all_files if not _is_test(p)]
    source_parsed = {p: t for p, t in all_parsed.items() if not _is_test(p)}
    out: list[Observation] = []
    for det in DETECTORS:
        if det is detect_test_mirroring:
            obs = det(root, all_files, all_parsed)          # whole-repo population
        else:
            obs = det(root, source_files, source_parsed)    # source-only population
        if obs is not None:
            out.append(obs)
    return out


# --- semantic pass (REAL Anthropic API) ------------------------------------
# The design/architecture tier — the moat. These cannot be counted by an AST;
# they require a model that understands intent. This calls the real API.
DESIGN_AXES: list[tuple[str, str]] = [
    ("layering-direction",  "Which way do dependencies point — callers toward a seam, layered, or tangled?"),
    ("seam-pattern",        "How are swap points / interfaces defined (typing.Protocol, ABC, duck-typed)?"),
    ("dependency-injection", "How are dependencies provided (constructor, global, factory, env-switch)?"),
    ("offline-fallback",    "Does every LLM/external path have a deterministic fallback?"),
    ("error-signaling",     "How are failures signaled (raise typed exceptions, return-result, error-code)?"),
    ("input-validation",    "Where is untrusted input validated (at a boundary, scattered, none)?"),
    ("state-mutation",      "Is state mutated in place or append-only/immutable?"),
    ("data-access",         "How is data fetched (a reader/repository seam vs inline SQL vs ORM)?"),
    ("concurrency-model",   "What is the concurrency model and is it race-safe (sync/async/threaded)?"),
    ("trust-boundary",      "Is external input validated/normalized at trust boundaries?"),
]


def _source_blob(root: pathlib.Path, *, max_files: int, per_file: int) -> str:
    """A bounded, central-first view of the source for the model to read."""
    files = [p for p in _py_files(root) if not _is_test(p)]
    # central-first: bigger files tend to be the load-bearing modules
    files.sort(key=lambda p: p.stat().st_size, reverse=True)
    chunks = []
    for p in files[:max_files]:
        try:
            chunks.append(f"# {_rel(p, root)}\n"
                          + p.read_text(encoding="utf-8", errors="ignore")[:per_file])
        except OSError:
            continue
    return "\n\n".join(chunks)


def _anthropic_json(prompt: str, *, model: str, max_tokens: int):
    """One real Anthropic call returning the first JSON array in the reply."""
    import json

    from anthropic import Anthropic

    msg = Anthropic().messages.create(
        model=model, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}])
    raw = msg.content[0].text
    return json.loads(raw[raw.find("["): raw.rfind("]") + 1])


def _verify_axis(root: pathlib.Path, axis_id: str, value: str,
                 candidates: list[str], *, model: str, sample_n: int
                 ) -> tuple[int, int, list[Site]] | None:
    """Verify a proposed design axis by SAMPLING real sites and classifying each
    independently as follows / violates / not-applicable.

    Returns (n_follow, n_decided, counterexamples) — a *real* support ratio, the
    design's verify-by-sampling (docs/engineering-handbook-design.md §5). Returns
    None when no candidate file actually exists (nothing to sample → can't verify;
    caller falls back to the propose-time self-report)."""
    seen: list[pathlib.Path] = []
    for c in sorted(dict.fromkeys(candidates)):           # de-dup, deterministic
        p = (root / c)
        if p.is_file() and p not in seen:
            seen.append(p)
        if len(seen) >= sample_n:
            break
    if not seen:
        return None
    blob = "\n\n".join(
        f"# {_rel(p, root)}\n" + p.read_text(encoding='utf-8', errors='ignore')[:3500]
        for p in seen)
    prompt = (
        f"Convention under test — axis '{axis_id}': the repo's stated value is "
        f"\"{value}\".\nFor EACH file below, decide INDEPENDENTLY whether it "
        "FOLLOWS the convention, VIOLATES it, or the convention is NOT_APPLICABLE "
        "to that file. Judge only what the file shows.\nReply JSON, one object per "
        'file: [{"path","verdict":"follows|violates|n_a","note"}].\n\nFILES:\n'
        + blob)
    try:
        verdicts = _anthropic_json(prompt, model=model, max_tokens=1536)
    except Exception:
        return None
    follow = decided = 0
    counter: list[Site] = []
    for v in verdicts:
        verdict = str(v.get("verdict", "")).lower()
        if verdict == "follows":
            follow += 1
            decided += 1
        elif verdict == "violates":
            decided += 1
            counter.append(Site(str(v.get("path", "?")), str(v.get("note", ""))[:80]))
    if decided == 0:
        return None
    return follow, decided, counter[:5]


def mine_semantic(root: str | pathlib.Path, *, model: str = "claude-sonnet-4-6",
                  max_files: int = 40, per_file: int = 4000,
                  verify: bool = True, sample_n: int = 6) -> list[Observation]:
    """Evaluate the design/architecture axes with the REAL Anthropic API.

    Two passes (when ``verify``): (1) PROPOSE — read the source and report each
    axis's value plus the *population* of files where the axis is decided; then
    (2) VERIFY-BY-SAMPLING — independently classify a sample of those files as
    follows/violates, yielding a *real* support ratio instead of the model's
    self-reported consistency (method ``llm-verified``). Axes whose population
    can't be sampled keep the propose-time estimate (method ``llm``).

    Raises if the key/SDK is absent — there is no fake fallback for the semantic
    pass (an LLM judge has no deterministic twin)."""
    import os

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY not set — the semantic pass needs the real API")

    root = pathlib.Path(root)
    blob = _source_blob(root, max_files=max_files, per_file=per_file)
    axis_lines = "\n".join(f"  {aid}: {q}" for aid, q in DESIGN_AXES)
    prompt = (
        "You are mining a codebase's ARCHITECTURE/DESIGN. For each axis below, read "
        "the source and report the repo's ACTUAL value. Also list `relevant_files` "
        "(up to 8 repo-relative paths): the files where this axis is DECIDED (include "
        "both files that follow and any that don't — the population we sample to verify). "
        "Be honest: set applies=false if the dimension is irrelevant to this repo."
        "\n\nAXES:\n" + axis_lines +
        "\n\nReply with a JSON array, one object per axis, in order: "
        '[{"axis","applies","value","evidence":["path", ...],'
        '"relevant_files":["path", ...],'
        '"consistency":"high|medium|low","tier":"advisory|governed","rationale"}]'
        "\n\nSOURCE:\n" + blob
    )
    out: list[Observation] = []
    for d in _anthropic_json(prompt, model=model, max_tokens=8192):
        if not d.get("applies", True):
            continue
        cons = {"high": (10, 10), "medium": (7, 10), "low": (4, 10)}.get(
            str(d.get("consistency", "medium")).lower(), (7, 10))
        n_match, n_total = cons
        method = "llm"
        counter: list[Site] = []
        if verify:
            pop = list(d.get("relevant_files") or []) + list(d.get("evidence") or [])
            res = _verify_axis(root, d["axis"], d.get("value", ""), pop,
                               model=model, sample_n=sample_n)
            if res is not None:
                n_match, n_total, counter = res
                method = "llm-verified"
        out.append(Observation(
            axis_id=d["axis"],
            statement=d.get("rationale", d.get("value", "")),
            value=d.get("value", ""),
            n_match=n_match, n_total=n_total,
            sample_sites=[Site(p) for p in (d.get("evidence") or [])[:4]],
            counterexamples=counter,
            tier=d.get("tier", "governed"),
            method=method,
        ))
    return out


def mine_all(root: str | pathlib.Path, *, llm: bool = False,
             model: str = "claude-sonnet-4-6", verify: bool = True
             ) -> list[Observation]:
    """Deterministic floor + (optionally) the real-API semantic pass.

    When ``verify`` (default), the semantic axes are verified by sampling real
    sites for an honest support ratio; pass ``verify=False`` for a single, cheaper
    propose-only call."""
    obs = mine(root)
    if llm:
        obs += mine_semantic(root, model=model, verify=verify)
    return obs


def render_playbook(root: pathlib.Path, observations: list[Observation],
                    *, threshold: float) -> str:
    has_llm = any(o.method == "llm" for o in observations)
    note = "exact deterministic support" + (
        " + real-LLM design pass" if has_llm else " · no LLM")
    lines = [f"# Engineering playbook — {root.name or root}", "",
             f"> {len(observations)} axes · {note} · threshold {threshold:.0%}.", "",
             "| axis | value | support | tier | method | conv? | evidence / counterexamples |",
             "|---|---|---|---|---|---|---|"]
    for o in sorted(observations, key=lambda x: (x.method, -x.ratio)):
        conv = "✅" if o.is_convention(threshold) else "—"
        ev = o.counterexamples[:3] if o.counterexamples else o.sample_sites[:3]
        evs = "; ".join(f"`{c.path}:{c.detail}`" if c.detail else f"`{c.path}`"
                        for c in ev) or "none"
        lines.append(f"| {o.axis_id} | {o.value} | {o.n_match}/{o.n_total} "
                     f"({o.ratio:.0%}) | {o.tier} | {o.method} | {conv} | {evs} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="kaixn engineering-handbook miner")
    ap.add_argument("root", help="repo root to mine")
    ap.add_argument("--threshold", type=float, default=0.8)
    ap.add_argument("--llm", action="store_true",
                    help="also run the real-API semantic/design pass")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip verify-by-sampling (propose-only; cheaper, less honest)")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--out", default=None, help="write playbook markdown here")
    args = ap.parse_args()

    # best-effort: load ANTHROPIC_API_KEY etc. from .env for local runs
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    root = pathlib.Path(args.root)
    obs = mine_all(root, llm=args.llm, model=args.model, verify=not args.no_verify)
    report = render_playbook(root, obs, threshold=args.threshold)
    if args.out:
        pathlib.Path(args.out).write_text(report)
        print(f"wrote {args.out}")
    print(report)


if __name__ == "__main__":
    main()
