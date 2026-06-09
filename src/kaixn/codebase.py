"""Constitution-from-source-code.

The dogfood lesson: prose docs are a bad source (they discuss, they don't rule).
Source code is the ground truth of decisions *actually made* — dependencies,
schema management, runtime targets, tooling, architecture. This extractor mines
those signals into norm candidates with file-level evidence, so the constitution
(and the generated handbook/ADRs) is grounded in what the code really does.

`CodebaseExtractor` is deterministic and runs offline. `AnthropicCodebaseExtractor`
(the deep path) reads source and synthesizes architectural principles/ADRs an
AST can't see.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

from kaixn.extract import Extracted
from kaixn.types import NormCandidate


def _d(statement: str, evidence: str, *, domain: str = "technical",
       kind: str = "decision", scope: str = "all", confidence: float = 0.9,
       flags: list[str] | None = None) -> Extracted:
    return Extracted(
        candidate=NormCandidate(statement=statement, domain=domain, scope=scope,
                                kind=kind),
        evidence=evidence, source=evidence.split(":")[0],
        confidence=confidence, flags=flags or [],
    )


class CodebaseExtractor:
    """Mine decisions/principles from real source-code signals."""

    def extract_repo(self, root: str) -> list[Extracted]:
        root_path = pathlib.Path(root)
        out: list[Extracted] = []
        out += self._from_pyproject(root_path)
        out += self._from_layout(root_path)
        return out

    # -- pyproject: dependencies, runtime, license, tooling ----------------
    def _from_pyproject(self, root: pathlib.Path) -> list[Extracted]:
        pp = root / "pyproject.toml"
        if not pp.exists():
            return []
        data = tomllib.loads(pp.read_text())
        proj = data.get("project", {})
        out: list[Extracted] = []

        for dep in proj.get("dependencies", []):
            name = re.split(r"[<>=!~ \[]", dep, 1)[0]
            out.append(_d(f"The project depends on `{name}` as a core runtime dependency.",
                          f"pyproject.toml:dependencies → {dep}"))

        for extra, deps in proj.get("optional-dependencies", {}).items():
            if extra == "dev":
                continue
            names = ", ".join(re.split(r"[<>=!~ \[]", d, 1)[0] for d in deps)
            # ambiguous: is an *optional* extra a binding architectural decision,
            # or just an available capability? the connecting human decides.
            out.append(_d(
                f"Optional capability `{extra}` is provided by: {names}.",
                f"pyproject.toml:optional-dependencies.{extra}",
                confidence=0.5, flags=["binding"]))

        if rp := proj.get("requires-python"):
            out.append(_d(f"The project targets Python {rp}.",
                          "pyproject.toml:requires-python", confidence=0.95))
        if lic := proj.get("license", {}).get("text"):
            # ambiguous domain: product norm or legal/compliance?
            out.append(_d(f"The project is licensed under {lic}.",
                          "pyproject.toml:license", domain="product",
                          confidence=0.6, flags=["domain"]))

        dev = proj.get("optional-dependencies", {}).get("dev", [])
        joined = " ".join(dev)
        if "ruff" in joined:
            out.append(_d("Code is linted/formatted with ruff.",
                          "pyproject.toml:dev → ruff", kind="principle"))
        if "pytest" in joined:
            out.append(_d("Automated tests run under pytest.",
                          "pyproject.toml:dev → pytest", kind="principle"))
        return out

    # -- repository layout: structural decisions ---------------------------
    def _from_layout(self, root: pathlib.Path) -> list[Extracted]:
        out: list[Extracted] = []
        if list(root.glob("migrations/*.sql")):
            out.append(_d("Database schema is managed via versioned SQL migrations.",
                          "migrations/*.sql"))
        if (root / "src" / "kaixn" / "server.py").exists():
            out.append(_d("The system is exposed to agents as an MCP server.",
                          "src/kaixn/server.py"))
        if (root / "tests").is_dir():
            out.append(_d("Every module ships with a mirrored test suite.",
                          "tests/", kind="principle"))
        if list(root.glob(".github/workflows/*")):
            out.append(_d("CI runs via GitHub Actions.", ".github/workflows/"))
        return out


class AnthropicCodebaseExtractor:
    """Deep path: read source and synthesize architectural principles + ADRs the
    AST can't infer (invariants, patterns, intentional constraints)."""

    def __init__(self, model: str = "claude-sonnet-4-6", max_files: int = 60) -> None:
        self.model = model
        self.max_files = max_files

    def extract_repo(self, root: str) -> list[Extracted]:
        import json

        from anthropic import Anthropic

        files = sorted(pathlib.Path(root).rglob("*.py"))[: self.max_files]
        blob = "\n\n".join(f"# {p}\n{p.read_text(errors='ignore')[:4000]}"
                           for p in files)
        prompt = (
            "Read this codebase and extract the engineering DECISIONS and "
            "PRINCIPLES it embodies — the kind you'd put in ADRs and an "
            "engineering handbook. Each atomic, with the file as evidence. "
            "domain ∈ {product, technical, product_design, ux}. Reply JSON "
            'array: [{"statement","kind","domain","scope","evidence"}].\n\n' + blob
        )
        client = Anthropic()
        msg = client.messages.create(model=self.model, max_tokens=4096,
                                     messages=[{"role": "user", "content": prompt}])
        raw = msg.content[0].text
        raw = raw[raw.find("["): raw.rfind("]") + 1]
        return [Extracted(
            candidate=NormCandidate(statement=d["statement"], domain=d["domain"],
                                    scope=d.get("scope", "all"), kind=d["kind"]),
            evidence=d.get("evidence", ""), source=d.get("evidence", "").split(":")[0],
        ) for d in json.loads(raw)]
