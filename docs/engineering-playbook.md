# kaixn — Engineering Playbook (validation run)

**Generated:** 2026-06-13 · **Repo:** `kaixn` @ `4ddb020` · **Miner:** hand-run of
`docs/axis-catalog.yaml` (Claude as miner) to **validate the axis model** before
building automation.
**Tier legend:** ▣ governed (principle/decision — normative) · ◌ advisory
(observed convention). **Support** is a hand estimate of consistency across sites.

> Validation question: *does instantiating the axis catalog on a real codebase
> produce something that reads like a true engineering handbook?* Verdict at the end.

---

## Style & form
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| naming-case | snake_case fns/vars, PascalCase classes, SCREAMING consts | ◌ | `DOMAINS`, `CONFIDENCE_FLOOR`, all modules | ~1.0 | |
| file-naming | snake_case modules | ◌ | `store.py`, `conflict.py` | 1.0 | |
| import-organization | `__future__` → stdlib → `kaixn.*`, grouped | ◌ | every module head | ~1.0 | |
| formatter | ruff | ◌ | `pyproject [dev]` | n/a | declared |
| docstring-style | module + public docstrings, prose-rationale | ◌ | every module opens with one | ~1.0 | unusually strong |
| type-annotations | full on public fns | ◌ | throughout | ~0.95 | |
| future-annotations | `from __future__ import annotations` | ◌ | all mined modules | ~0.95 | counter: `__init__.py` |
| comment-rationale | comments explain WHY | ◌ | `_vec` dumper note, edge-uuid-skip note, gate `ignore_norm_ids` | high | notable strength |

## Structure & architecture
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| module-responsibility | single, clear per module | ▣ | gate/conflict/engine/store split | high | |
| layering-direction | callers → `NormReader` seam, impl hidden | ▣ | engine/gate depend on protocol | high | |
| seam-pattern | `typing.Protocol` | ◌ | `NormReader`,`Synthesizer`,`Adjudicator`,`Grounder`,`NormExtractor` | high | |
| dependency-injection | constructor + env-switch | ◌ | `from_env`, `AppState` | high | |
| impl-pairing | in-memory/heuristic + Anthropic/Pg per seam | ◌ | `InMemoryStore`/`PgStore`, `Naive`/`Anthropic` | ~1.0 | signature pattern |
| offline-fallback | every LLM path has a deterministic twin | ▣ | `from_env`, `AppState`, `_ConsistentAdjudicator` | ~1.0 | core principle |
| package-layout | src-layout | ◌ | `src/kaixn/` | 1.0 | |
| config-management | env vars | ◌ | `KAIXN_DSN`,`KAIXN_EMBEDDER`,`ANTHROPIC_API_KEY` | 1.0 | |
| public-surface | leading-underscore privates | ◌ | `_vec`,`_parse_operations`,`_relevant` | high | |

## Correctness & logic
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| error-signaling | **layered**: domain returns results, boundaries raise | ▣ | `GateResult` vs `HTTPException`/`ValueError` in web/app | high | nuanced, not a single value |
| error-propagation | propagated with cause | ▣ | `raise … from e` in `connect_repo_url` | high | |
| input-validation | at boundary | ▣ | `normalize_repo_url` (strict), `normalize_scope` at construction | high | |
| none-handling | explicit Optional / `None` returns | ▣ | `get() -> … \| None` | high | |
| state-mutation | append-only (status flip + supersede edge) | ▣ | `resolution.commit_proposal`, store | ~1.0 | documented invariant |
| idempotency | partial — edges `ON CONFLICT DO NOTHING` | ▣ | `PgStore.add_edge` | medium | not universal |
| concurrency-model | sync | ▣ | no async in core | 1.0 | |
| resource-lifecycle | try/finally cleanup | ▣ | `connect_repo_url` tmpdir `rmtree` | high | |

## Data & persistence
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| data-access | reader-seam; SQL confined to `store.py` | ▣ | `NormReader`/`PgNormReader` | ~1.0 | no inline SQL in callers |
| query-construction | parameterized | ▣ | psycopg `%s` + `Vector` adapter | 1.0 | injection-safe |
| transaction-boundaries | autocommit | ▣ | `pg_connect(autocommit=True)` | 1.0 | deliberate choice |
| migration-strategy | numbered, append-only SQL | ▣ | `migrations/001_init.sql`, `apply_migrations.py` | 1.0 | |
| schema-sot | SQL migrations | ▣ | `migrations/` | 1.0 | |
| serialization | dataclasses internal, pydantic at API | ◌ | `types.py` vs `web.py` `BaseModel` | high | |
| time-handling | `timestamptz default now()`; little app logic | ▣ | schema | n/a | low population |

## API & interface
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| response-shape | bare dicts | ◌ | FastAPI route returns | high | |
| error-response | `HTTPException(detail=…)` | ◌ | `web.py` | high | |
| status-codes | semantic (400/404/502) | ◌ | `connect` route | high | |
| versioning | **none** | ▣ | no version in routes | — | honest gap |
| pagination | none | — | — | — | low population |

## Security
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| secrets-handling | env vars, no literals | ▣ | API keys via `os.getenv` | 1.0 | |
| injection-safety | parameterized → safe | ▣ | store SQL | 1.0 | |
| trust-boundary | strict at repo-clone | ▣ | `normalize_repo_url` regex | high | |
| authz-boundary | **none (v0.2)** | ▣ | no auth layer | — | honest gap; `auth` extra scaffolded |

## Performance
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| indexing | hnsw + gist(scope) + gin(tsv) + btree | ▣ | `001_init.sql` | 1.0 | strong |
| complexity | bounded; in-mem neighbors O(n) linear | ▣ | `InMemoryNormReader.neighbors` | — | counter: POC linear scan (noted in code) |
| blocking-io | sync subprocess w/ timeout | ▣ | git clone | n/a | |
| caching | none explicit | ◌ | — | — | |

## Reliability & ops
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| graceful-degradation | offline mode IS the degradation story | ▣ | keyless fallbacks everywhere | high | strength |
| timeouts | explicit on clone only | ▣ | `timeout=120` | partial | |
| feature-flags | env-switch | ◌ | embedder/llm selection | high | |
| logging-pattern | **sparse / none** | ◌ | no structured logging in core | — | honest gap |
| observability | none | ◌ | — | — | honest gap |
| retry-backoff | none | ▣ | single-attempt clone | — | gap |

## Testing
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| test-mirroring | `tests/test_<module>.py` | ▣ | bootstrap/conflict/engine/eval/gate/resolution/review/scope/pg | ~0.7 | counter: `codebase`,`grounding`,`llm`,`extract`,`server` lack direct tests |
| test-doubles | fakes, not mocks | ◌ | `InMemoryStore`, fake embedder, `NaiveSynthesizer` | high | |
| test-determinism | offline/deterministic; Pg test gated on DSN | ▣ | `test_pg` skips without `KAIXN_TEST_DSN` | high | strength |
| edge-case-tests | present | ▣ | `test_scope` etc. | medium | |
| **tests-first** | ⚠ **unmeasurable** | ▣ | squashed initial commit → no granular history | — | **needs real git history** |
| coverage-expectation | none declared | ▣ | no gate | — | gap |

## Dependencies
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| dependency-choice | fastapi, anthropic, pgvector, psycopg (+openai/mcp optional) | ▣ | `pyproject` | 1.0 | |
| version-pinning | lower-bound ranges | ◌ | `pyproject` | 1.0 | |
| optional-deps | extras (`web`,`server`,`anthropic`) | ◌ | `pyproject` | 1.0 | strong split |

## Documentation
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| module-docstrings | always, rationale-rich | ◌ | every module | ~1.0 | strength |
| adr-for-decisions | docs-driven (`architecture.md`, `bootstrap.md`, now prd/tech-spec) | ▣ | `docs/` | high | strong culture |
| evidence-provenance | every extracted candidate carries evidence; "no provenance, no candidate" | ▣ | `Extracted.evidence`, `extract.py` | ~1.0 | core principle |
| readme-maintained | current | ◌ | README updated this session | — | |

## Maintainability
| Axis | Value | Tier | Evidence | Support | Notes |
|---|---|---|---|---|---|
| atomic-records | one-claim-per-norm / one-change-per-op | ▣ | gate atomicity split; documented invariant | ~1.0 | core principle |
| function-complexity | small, focused | ▣ | throughout | high | |
| consistency | high cross-module consistency | ▣ | uniform module shape | high | |
| duplication | mostly DRY | ▣ | — | medium | **counter:** `_ConsistentAdjudicator` duplicated in `app.py` **and** `server.py` |
| dead-code | clean | ◌ | — | — | |

---

## Validation verdict

**The core assumption holds.** Instantiating the catalog produced a document that
reads like a genuine engineering handbook for kaixn — and, notably, it surfaced
things a static doc wouldn't:

**What worked**
- **Conventions came out true and specific** — the Protocol-seam + impl-pairing +
  offline-fallback triad, append-only state, evidence-provenance, parameterized SQL,
  reader-seam data access. These are exactly what a senior reviewer would write down.
- **Tiering landed correctly** — descriptive conventions (◌) vs normative principles
  (▣) separated cleanly along the catalog's pre-assigned tiers.
- **Honest gaps, not hallucinations** — it flagged *no logging/observability*, *no
  authz*, *no API versioning*, *no retry/coverage gate* as real absences rather than
  inventing conventions. "No convention" is a true answer.
- **Counterexamples earned trust** — duplicated `_ConsistentAdjudicator`, untested
  modules, the POC linear-scan — the kind of finding that makes a handbook credible.

**What the run taught us (feeds the build)**
1. **History axes are real and currently blind** — `tests-first` was *unmeasurable*
   on a squashed initial commit. Confirms the **git-history source** is mandatory,
   not optional, and needs real history (or PR stream) to light up.
2. **Some axes resist a single value** — `error-signaling` is *layered* (domain
   returns results, boundaries raise). The value-space needs to allow
   **context-qualified values** ("by-layer"), not just one winner.
3. **Low-population axes should be suppressed, not shown empty** — money/time,
   pagination → relevance-gating must hide axes whose population is absent (else the
   handbook reads padded).
4. **Support without real counts is the weak link** — these ratios were hand
   estimates. The deterministic ① pass (exact counts) and the sampled verify ③ are
   what make support trustworthy; that's the first thing to automate.

**Conclusion:** the axis model → engineering playbook mapping is **validated**.
Proceed to automate, in order: deterministic ① pass (exact support + populations) →
git-history extractors → semantic propose/verify. The catalog refinements above
(context-qualified values, relevance-gating) go into `axis-catalog.yaml` next.
