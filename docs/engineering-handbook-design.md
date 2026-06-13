# Engineering Handbook — Design (build-ready)

**Status:** active · **Owner:** anupreet · **Date:** 2026-06-12
**Implements:** `docs/build-plan.md` M1 · concepts in `docs/tech-spec.md`.
**Purpose:** nail the *shape*, the *what-exists-vs-what's-learned* taxonomy, and the
*complete memory footprint* so writing the miner + handbook is mechanical.

The Engineering handbook = the `technical`-domain constitution, rendered. Its
entries come from three sources with different trust and different mechanics:
**exists** (deterministic, read from code), **learned** (LLM, semantic, verified),
**governed** (human-ratified). All three produce the *same record shape*; they
differ in `tier`, how `support`/`confidence` is computed, and who may change them.

---

## 1. The shape — a handbook entry (`norm`)

One row per atomic claim. Every field, its source, and whether it can change.

| field | type | source | written by | mutable |
|---|---|---|---|---|
| `id` | uuid | system | store | no |
| `statement` | text (one atomic claim, handbook voice) | detector / LLM / human | miner / extractor / PM | no — change = supersede |
| `kind` | `principle` \| `decision` | classifier | miner / LLM | no |
| `tier` | `advisory` \| `governed` | mining (advisory) vs assertion (governed) | system | no — promote = new governed norm |
| `domain` | `technical` (this book) | classifier | miner / LLM | no |
| `scope` | `ltree` (code-area → product-area) | path→scope inference | LLM (heuristic fallback) | no |
| `status` | `proposed`\|`active`\|`superseded`\|`deprecated` | lifecycle | system | **yes** (status only) |
| `rationale` | text (why / the pattern's purpose) | LLM / human | extractor | no |
| `support` | jsonb — see §2 (advisory only) | verify pass | miner | **yes** (counters bump) |
| `confidence` | float 0–1 (governed candidate only) | extractor | extractor | no |
| `embedding` | vector(1536) | embedder | store | no |
| `version` | int | lifecycle | system | yes |
| `created_at` / `updated_at` / `last_verified_at` | timestamptz | system | store | yes |

**Tier × kind are orthogonal** and both apply to advisory:
- advisory + principle → an observed *rule* ("every module declares `from __future__ import annotations`")
- advisory + decision → an observed *choice* ("the project depends on FastAPI")
- governed + principle → a ratified invariant ("memory is append-only")
- governed + decision → a ratified choice ("the store is Postgres + pgvector + ltree")

---

## 2. Support evidence shape (advisory `support` jsonb)

This is *how the code votes* — the thing that lets an advisory norm skip the human
gate. It must be rich enough to (a) render the badge, (b) drive freshness, (c) feed
coverage/drift, (d) make re-mining idempotent.

```jsonc
{
  "method":   "deterministic" | "llm_verified",
  "detector": "import_future_annotations",     // which detector produced it
  "ratio":    0.95,                              // n_follow / n_total
  "n_follow": 19,
  "n_total":  20,
  "sample_sites":   [{"path": "src/kaixn/store.py", "line": 7}],   // evidence (votes for)
  "counterexamples":[{"path": "src/kaixn/__init__.py"}],           // sites that violate (honesty + drift)
  "commit_sha": "4d3331f",                       // freshness anchor
  "last_verified_at": "2026-06-12T19:00:00Z"
}
```

**Counterexamples are first-class** — they keep the handbook honest ("95%, here are
the 5% that don't") and they are the seed of coverage drift when the ratio decays.

---

## 3. What exists vs. what's learned vs. what's governed

Concrete, grounded in **this repo** so the detectors have real targets.

### 3.1 EXISTS — deterministic detectors (exact support, no LLM)

Read directly from structure; `support` is an exact count.

| Convention (statement) | kind | detector | source signal in this repo |
|---|---|---|---|
| The project depends on `<dep>` as a runtime dependency | decision | `pyproject_deps` | `pyproject.toml [project.dependencies]` |
| The project targets Python `<spec>` | decision | `pyproject_runtime` | `requires-python` |
| Code is linted/formatted with ruff | principle | `tooling` | dev deps |
| Automated tests run under pytest | principle | `tooling` | dev deps |
| DB schema is managed via versioned SQL migrations | decision | `layout` | `migrations/*.sql` |
| The system is exposed to agents as an MCP server | decision | `layout` | `server.py` |
| Every module ships a mirrored test suite | principle | `test_mirror` | `tests/test_<m>.py` per `src/kaixn/<m>.py` |
| Every module declares `from __future__ import annotations` | principle | `import_future` | present in ~all `.py` |
| Value types are `@dataclass(slots=True)` | principle | `dataclass_slots` | pervasive in `types.py` etc. |
| Modules open with a module docstring | principle | `module_docstring` | pervasive |

These are cheap, run on **every** file, and give precise ratios. They are the
broad floor of the Engineering handbook.

### 3.2 LEARNED — LLM semantic detectors (propose-then-verify)

Intent an AST can't see. **Propose** (LLM reads code → candidate + cited sites),
then **verify** (LLM-judge samples relevant sites → real ratio + counterexamples),
emit only if `ratio ≥ threshold`. `method = "llm_verified"`.

Real patterns this repo exhibits (the targets):
- Store/LLM seams are `typing.Protocol` interfaces with an InMemory/heuristic impl
  **and** an Anthropic/Pg impl (`NormReader`, `Synthesizer`, `Adjudicator`,
  `Grounder`, `NormExtractor`).
- Every LLM-backed component has a **deterministic offline fallback** selected by
  `ANTHROPIC_API_KEY` (`app.from_env`, `server.AppState`).
- The **write gate never writes** — it returns a `GateResult`; the caller commits.
- Norms are **append-only**: supersession is a status flip + `supersedes` edge,
  never in-place mutation.
- **No provenance, no candidate** — every extracted candidate carries an evidence span.
- Conflict adjudication is **decomposed per (operation × norm)**, never one bulk prompt.
- **Structural/deterministic checks run before any LLM** adjudication.

These are the conventions that make the handbook worth reading. The verify pass is
what keeps them *descriptive* (true across the code) rather than asserted.

### 3.3 GOVERNED — human-ratified (LLM extracts candidates → EM promotes)

Prescriptive commitments. LLM (`AnthropicCodebaseExtractor` + doc extraction)
proposes; lands `status=proposed`; **EM ratifies**. Never auto-active.

- The §3.6 invariants as principles: atomicity, append-only/versioned, no
  write-only memory, provenance-in-graph, PM-never-authors-operations.
- Stack decisions: store = Postgres + pgvector + ltree; embeddings default 1536-dim.
- "Offline mode stays first-class" as an engineering principle.

**Promotion bridge:** a consequence-classifier (LLM) flags advisory norms that look
governance-worthy (e.g. a dependency = a real decision) → surfaces a *"promote to
governed?"* suggestion. Default stays advisory; the important ones get human ownership.

---

## 4. What must be remembered (the complete footprint)

For each handbook entry, persist **five things** so nothing is lost:

1. **The claim + classification** — the `norm` row (§1).
2. **The evidence** — `support` (advisory, §2) or `confidence` + source (governed):
   *how we know it*, including counterexamples.
3. **Provenance edges** — *what produced it*:
   - `evidences`: `code_ref(commit) → norm` (advisory — the commit/files that vote)
   - `creates`: `operation → norm` (governed — the op that minted it)
   - `depends_on`: `operation → norm` (who relies on it — feeds impact/coverage)
4. **Lineage** — `supersedes` edges → the supersede timeline (pattern evolution).
5. **Identity + freshness** — the `embedding` (dedup/idempotency key) and
   `commit_sha` / `last_verified_at` (freshness badge + re-verify scheduling).

If a field serves none of: render an entry · compute a badge · enable retrieval ·
support drift/coverage · prove provenance — it is not remembered (the
no-write-only-memory invariant, applied to ourselves).

---

## 5. Mining pipeline (flow)

```
repo @ commit ─┬─▶ deterministic detectors ─▶ candidates (exact support)
               │
               └─▶ LLM propose (bounded budget) ─▶ candidates + cited sites
                                                      │
                                          LLM verify (sampled) ─▶ ratio + counterexamples
                                                      │
                         keep if ratio ≥ threshold ◀──┘
                                   │
   all candidates ─▶ identity match (embedding/lexical) ─┬─ existing? ─▶ bump support + freshness
                                                         └─ new? ─▶ write gate (atomicity split + dedup,
                                                                     NO conflict stage for advisory)
                                                                     ─▶ add_norm(tier=advisory, status=active)
                                                                     ─▶ evidences edge (code_ref → norm)
   governed candidates (LLM extract) ─▶ status=proposed ─▶ EM review queue
```

Advisory **skips the conflict/consistency gate stage** (it's descriptive, not a
change-against-state) but **keeps atomicity + dedup** so the store stays clean.

---

## 6. Identity, idempotency & freshness

- **Identity key:** statement embedding (cosine ≥ 0.86) OR lexical Jaccard ≥ 0.60,
  within `(domain, tier, scope-family)`. Match → same norm: update `support`,
  `commit_sha`, `last_verified_at`. No match → new norm. *Re-mining is idempotent.*
- **Freshness:** every mine stamps `commit_sha`; the handbook badge shows "synced to
  `<sha>`". A norm not re-confirmed by the latest mine → ratio recomputed; decay
  below threshold → status `deprecated` + a coverage flag (the convention weakened).
- **Incremental:** bootstrap = full mine; per-PR = mine the diff's files, update only
  affected conventions (Flow B).

---

## 7. LLM usage map (where "necessary" means necessary)

| Uses LLM | Deterministic |
|---|---|
| semantic convention **proposal** | dependency / runtime / tooling / layout detection |
| **support verification** (sampled judge + counterexamples) | exact support counts for syntactic detectors |
| atomicity split + handbook-voice phrasing | embedding / lexical dedup + identity |
| scope inference (code path → product-area ltree) | freshness stamping |
| consequence classification (advisory → governance candidate) | lifecycle / status transitions |
| governed principle/decision extraction from code + docs | provenance edge writing |

Cost discipline: deterministic runs on everything; LLM proposes on a **bounded,
prioritized file budget**; verify **samples** rather than scans; strong model to
propose, cheaper to verify.

---

## 8. Open knobs (defaults set; tune on real output)
- Threshold: **0.8** (but show the ratio in the UI — transparency over a hidden cutoff).
- LLM file budget for propose: start small, prioritized by deterministic interest.
- Re-verify cadence for stale advisory norms.
- Scope inference mapping table (code dirs → product areas) — seed manually, refine.
