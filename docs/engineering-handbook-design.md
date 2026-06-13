# Engineering Handbook — Design (build-ready)

**Status:** active · **Owner:** anupreet · **Date:** 2026-06-12
**Implements:** `docs/build-plan.md` M1 · concepts in `docs/tech-spec.md`.
**Purpose:** make the miner + handbook mechanical to build — the *axis model*, the
*catalog & its sources*, the *record shapes*, the *complete memory footprint*, the
*mining mechanism*, and *storage*.

The Engineering handbook = the `technical`-domain constitution, rendered. Its
entries are produced by three extractor types (**deterministic**, **LLM-semantic**,
**governed/human-ratified**) but share one record shape and one organizing idea:
**axes**.

---

## 1. The axis model (the core idea)

We do **not** scan for a list of named conventions — that's open-ended and unstable.
We scan along **axes**: a dimension along which code *could* vary but, in a given
codebase, consistently doesn't.

> **A convention = a low-variance choice along a known axis.** We measure the
> *distribution* over an axis across its relevant sites; where it concentrates
> (low entropy) → a norm. Where it's scattered → *no norm* (a true answer, not a miss).

We seed the **axes**; the code reveals the **value**. We never need to know
"snake_case" up front — only to look at the *naming-case* axis.

### 1.1 Two levels of memory

| Level | What | Scope | Grows by |
|---|---|---|---|
| **Axis registry** | *what to look for* — the dimensions | **global, cross-repo** | catalog import + meta-discovery |
| **Norms (handbook)** | this repo's *observed/ratified value* on each axis | **per-repo** | mining + ratification |

A norm is an **instance of an axis** ("on the `data-access` axis this repo chose the
reader-seam, support 0.95"). The handbook is the **per-repo projection of the axis
registry**. The registry is reusable across every codebase kaixn ever sees.

---

## 2. The axis catalog — what to look for & where it comes from

A senior reviewer's rubric is **hundreds** of axes. We don't author them — we
**source** them, organized under 12 durable categories.

### 2.1 Categories (the stable skeleton)
Style & form · Structure & architecture · Correctness & logic · Data & persistence ·
API & interface · Security · Performance · Reliability & ops · Testing ·
Dependencies / supply-chain · Documentation · Maintainability.

### 2.2 Five sources (snapshot is not enough)
Some axes are invisible in a code snapshot (e.g. *tests-first* — the final tree looks
identical regardless of order). So the catalog draws on:

| Source | Captures | Example axes |
|---|---|---|
| **Code snapshot** (AST/semantic) | structure, naming, patterns, seams | data-access, layering, error-handling |
| **Git history** (temporal) | *practices* a snapshot can't show | tests-first, migration introduction, change size, churn |
| **Process artifacts** | declared rules | CONTRIBUTING, PR template, CI gates, commit-msg, CODEOWNERS |
| **Rule catalogs** (lint/SAST/OWASP) | the universe of quality/security axes | injection, N+1, complexity, parameterization |
| **Review history** (PRs + kaixn overrides) | what *this team* actually enforces | the specialization signal |

### 2.3 Sourcing strategy (how it becomes comprehensive & self-maintaining)
`catalog = import(rule taxonomies) ▸ specialize(team lint config + PR/override history)
▸ grow(anchored meta-discovery)`
- **Import** — linter/SAST/OWASP rule taxonomies are the codified reviewer rubric
  already enumerated & categorized (e.g. `ruff` ~800 rules); map axes onto them.
- **Specialize** — the team's lint/CI config + past PR comments + kaixn overrides tell
  us *which* axes this team cares about, and surface team-specific ones.
- **Meta-discover** — the LLM may propose a *new axis* it sees strongly; lands as a
  *candidate axis* for review, not an instant convention. The catalog grows deliberately.

### 2.4 Two kinds of axis → two tiers
- **Convention axes** (style, structure, data/API shape) — *descriptive*, value is a
  choice. Observed → **advisory**.
- **Principle/quality axes** (correctness, security, performance, reliability) —
  *normative*, there's a right stance. Become **governed principles**; a violation is
  a **finding**, not "no convention."

The catalog does **double duty**: the handbook's schema (what we remember) **and** the
per-PR gate's checklist (what we check a diff against). The reviewer's rubric *is* the
constitution's table of contents.

### 2.5 Enforcement — invariant vs convention (the zero-comment requirement)

Tier (advisory/governed) is *trust*. It does **not** tell you whether generated code
will actually obey a norm. That requires a second, orthogonal property: **how is a
violation mechanically caught?**

> **Litmus:** for any axis we expect generated code to honor — *"how would a linter,
> type-checker, or test catch a violation?"* If the only answer is "a human reviewer
> would," the norm is **advisory in practice**, regardless of its tier label, and
> generated code *will* violate it.

So every axis carries:
- `enforce`: `mypy-strict` | `ruff:<rule>` | `custom-check:<name>` | `test:<pattern>` | `human`
- `enforceable` (derived): **invariant** (mechanical) vs **convention** (judgment).

**The constitution compiles to a gate.** Each invariant emits its enforcement
artifact — a strict-mypy config, a selected/﻿custom ruff rule, a check, or a test —
and the per-PR gate runs *those mechanically*. The **LLM is reserved for true
conventions** that need judgment, never for re-checking what a linter should catch.
This is the mechanism behind zero-comment generation: the agent reads the handbook
*and* its output must pass the compiled gate.

**Honesty rule:** a `governed` axis whose only `enforce` is `human` is flagged
`enforceable: convention` and is a **backlog item** — either build the check
(custom ruff/mypy plugin/test) or stop pretending it's enforced. The validation run
found eight such axes (error-signaling, input-validation, none-handling,
state-mutation, idempotency, resource-lifecycle, data-access, trust-boundary); each
needs a mechanical path or an honest downgrade.

**Granularity follows from this.** A coarse axis ("full type annotations") isn't
enforceable; its rule-bearing children are ("`Any` banned except justified
`# type: ignore` → `ruff:ANN401` + `mypy-strict`"). Generation-grade axes carry a
concrete **`rule`** (what the agent must do) plus **`enforce`** — see the
generation-grade section of `axis-catalog.yaml`.

---

## 3. Record shapes

### 3.1 Axis (registry — global)
```
axis:
  id, category, name
  population:   how to find the relevant sites (the denominator)
  value_space:  what we distinguish (e.g. {raise, return-result, error-code})
  extractor:    deterministic | history | llm
  source:       catalog | history | process | meta
  status:       active | candidate | retired
```

### 3.2 Norm (handbook entry — per-repo, instance of an axis)

| field | type | source | written by | mutable |
|---|---|---|---|---|
| `id` | uuid | system | store | no |
| `axis_id` | uuid → axis | classifier | miner | no |
| `statement` | text (one atomic claim, handbook voice) | detector / LLM / human | miner / extractor / PM | no — change = supersede |
| `kind` | `principle` \| `decision` | classifier | miner / LLM | no |
| `tier` | `advisory` \| `governed` | mining vs assertion | system | no — promote = new governed norm |
| `domain` | `technical` | classifier | miner | no |
| `scope` | `ltree` (code-area → product-area) | path→scope inference | LLM (heuristic fallback) | no |
| `status` | `proposed`\|`active`\|`superseded`\|`deprecated` | lifecycle | system | **yes** (status only) |
| `rationale` | text | LLM / human | extractor | no |
| `support` | jsonb — §3.3 (advisory) | verify pass | miner | **yes** (counters bump) |
| `confidence` | float (governed candidate) | extractor | extractor | no |
| `embedding` | vector(1536) | embedder | store | no |
| `version` | int | lifecycle | system | yes |
| `created_at`/`updated_at`/`last_verified_at` | timestamptz | system | store | yes |

Tier × kind are orthogonal and both apply to advisory (observed *rule* vs observed
*choice*) and governed (ratified invariant vs ratified choice).

### 3.3 Support evidence (advisory `support` jsonb) — *how the code votes*
```jsonc
{
  "method":   "deterministic" | "history" | "llm_verified",
  "axis":     "import_future_annotations",
  "ratio":    0.95, "n_follow": 19, "n_total": 20,
  "sample_sites":    [{"path": "src/kaixn/store.py", "line": 7}],
  "counterexamples": [{"path": "src/kaixn/__init__.py"}],   // first-class: honesty + drift seed
  "commit_sha": "1504d3d", "last_verified_at": "2026-06-12T19:00:00Z"
}
```

---

## 4. What must be remembered (complete footprint)

**Globally:** the **axis registry** (§3.1) — reusable across repos.
**Per entry**, five things:
1. **claim + classification** — the `norm` row, linked to its `axis_id`.
2. **evidence** — `support` (advisory, incl. counterexamples) or `confidence`+source (governed).
3. **provenance edges** — `evidences` (code_ref→norm), `creates` (operation→norm),
   `depends_on` (operation→norm).
4. **lineage** — `supersedes` edges → the evolution timeline.
5. **identity + freshness** — `embedding` (dedup/idempotency key) + `commit_sha` /
   `last_verified_at`.

If a field serves none of {render · badge · retrieval · drift/coverage · provenance}
it is not remembered (no-write-only-memory, applied to ourselves).

---

## 5. Mining mechanism — scan-all → propose-from-sample → verify-by-sample

A convention is a property of the *population*, not of any one file. So mining is a
corpus + sampling problem, **not** a map-over-files loop. **We do not send each file
to the LLM.**

```
repo @ commit
  │
  ├─ ① SCAN (every file, deterministic — no LLM)
  │     • exact distributions for deterministic axes
  │     • git-history axes (tests-first, migration style, change size)
  │     • REPO SKELETON (modules, signatures, imports, docstrings, dir tree)
  │     • POPULATIONS per axis (the denominators)
  │
  ├─ ② PROPOSE (LLM — sees a COMPRESSED view, not every file)
  │     small repo:  skeleton-of-all + full source of CENTRAL files (import-degree)
  │     large repo:  map-reduce — group by dir/role → summarize → reduce to candidates
  │     guided by the SEMANTIC AXES as a rubric → values + cited sites
  │
  └─ ③ VERIFY (LLM-judge — sampled per candidate, never scan)
        deterministic filter → population → sample K sites → follow? + counterexamples
        keep iff ratio ≥ threshold
                    │
  candidates ─ identity match (embedding/lexical) ─┬─ exists → bump support + freshness
                                                   └─ new → write gate (atomicity+dedup,
                                                            NO conflict stage for advisory)
                                                          → add_norm(tier=advisory, active)
                                                          → evidences edge (code_ref→norm)
  governed candidates (LLM extract) → status=proposed → EM review queue
```

**Cost scales with #conventions, not #files.** Deterministic runs on everything (≈free);
LLM proposes on a bounded, prioritized budget; verify samples. **Relevance-gating:** skip
an axis whose population is empty (no API → no API axes). **Per-PR:** mine only the
diff's files — verify touched sites against existing conventions; propose only if the
diff repeats a new pattern.

---

## 6. Storage — Postgres + pgvector + ltree (not a separate vector DB)

The data is **relational + graph + lifecycle**, with semantics as one *index*:
- norms/axes with status lifecycle + exact filters (tier/domain/kind);
- a provenance **graph** traversed by recursive CTEs (supersede chains, impact_of);
- hierarchical **scope** via ltree `@>`/`<@`;
- **append-only, transactional** writes (the invariant);
- **hybrid** retrieval: filter → vector (pgvector hnsw) → tsvector, in one query.

pgvector **is** the semantic database, in-process. A dedicated vector store
(Pinecone/Chroma/Qdrant) is the wrong *primary* home: no joins/recursion, weak
lifecycle/transactions, no ltree, and a second store to keep in sync — the exact drift
we exist to prevent. (This is why we superseded the memory-layer doc's Chroma+Redis
design; that fits a bag-of-vectors, not graph-shaped lifecycle data.) Revisit only at
millions-of-vectors scale, and even then a vector DB is a *secondary index*, never the
system of record. Mining's working-set embeddings are transient (in-memory scratch).

---

## 7. Idempotency & freshness
- **Identity key:** statement embedding (cosine ≥ 0.86) OR lexical Jaccard ≥ 0.60,
  within `(axis_id, scope-family)`. Match → update support/freshness; else new norm.
  Re-mining is idempotent.
- **Freshness:** every mine stamps `commit_sha`; handbook badge shows "synced to `<sha>`".
  A convention whose ratio decays below threshold → `deprecated` + a coverage flag.
- **Incremental:** bootstrap = full mine; per-PR = diff files only.

---

## 8. LLM usage map
| Uses LLM | Deterministic |
|---|---|
| semantic-axis value **proposal** | deterministic-axis distributions (exact) |
| **support verification** (sampled + counterexamples) | git-history axes |
| atomicity split + handbook-voice phrasing | embedding/lexical dedup + identity |
| scope inference (code path → product area) | freshness stamping, lifecycle |
| consequence classification (advisory → governance candidate) | provenance edge writing |
| governed principle/decision extraction (code + docs) | population filtering (denominators) |

---

## 9. Seeded starter axis catalog (grounded in this repo)

Initial registry to code Phase ① + ② against. `det`=deterministic, `hist`=git-history,
`llm`=semantic. This-repo value shown where known.

| Category | Axis | extractor | this-repo value |
|---|---|---|---|
| Style | naming-case (func/var) | det | snake_case |
| Style | module preamble `from __future__ import annotations` | det | present ~all modules |
| Style | module docstring present | det | yes |
| Style | type annotations on public fns | det | yes |
| Structure | value-types as `@dataclass(slots=True)` | det | yes |
| Structure | seams as `typing.Protocol` | llm | `NormReader`/`Synthesizer`/`Adjudicator`… |
| Structure | layering direction (callers→seam, no inline impl) | llm | reads via `NormReader` |
| Structure | offline/fallback policy (every LLM path has a det twin) | llm | yes (`from_env`/`AppState`) |
| Correctness | error-signaling (raise vs result) | llm | result-object (`GateResult`) |
| Correctness | boundary validation | llm | strict at remote-fetch boundary |
| Correctness | append-only state (no in-place mutation) | llm | yes (supersede + status flip) |
| Data | data-access seam (no inline SQL in callers) | llm | `NormReader`/`PgNormReader` |
| Data | migration style (numbered, append-only SQL) | det+hist | `migrations/001…` |
| Data | query parameterization | catalog/llm | parameterized (psycopg) |
| API | exposed-as-MCP | det | `server.py` |
| Security | secrets via env, not literals | det/catalog | `ANTHROPIC_API_KEY` env |
| Security | injection-safe queries | catalog | parameterized |
| Testing | tests-first | hist | (measure from commit ordering) |
| Testing | test-mirroring (`tests/test_<m>.py`) | det | yes |
| Testing | offline determinism in tests | llm | yes (fakes, no keys) |
| Deps | runtime dependency choices | det | fastapi, anthropic, pgvector… |
| Deps | tooling (ruff, pytest) | det | yes |
| Docs | evidence-span on every extracted candidate | llm | yes (`Extracted.evidence`) |
| Maintainability | one-claim-per-record atomicity | llm | yes (the gate enforces) |

(~24 seeded; the registry grows to hundreds via §2.3.)

---

## 10. Open knobs (defaults set; tune on real output)
- Threshold **0.8** (but show the ratio in the UI — transparency over a hidden cutoff).
- LLM propose file budget; verify sample size K.
- Re-verify cadence for stale advisory norms.
- Scope-inference mapping (code dirs → product areas) — seed manually, refine.
- Which rule catalogs to import first (ruff + OWASP are the obvious seeds).
