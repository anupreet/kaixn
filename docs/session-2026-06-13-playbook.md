# Session history — 2026-06-13 (playbook knowledge layer)

A full day building kaixn's **playbook** thread: from a 3-tab repo inspector into
a human-facing, eagerly-generated, **persisted knowledge layer** that agents can
read back to evaluate PRDs/PRs. Chronological, with the why behind each turn.

---

## Arc of the day

The day started mid-stream (continuing a prior handbook session) and moved through
four big phases:

1. **Real miner hardening** — verify-by-sampling, streaming, hang fix.
2. **Knowledge layer** — eager full PRD/Tech-Spec docs + domain graph, persisted to Postgres.
3. **UX** — stream-as-you-generate, Add/Explore, doc pages, balance + ordering fixes.
4. **Robustness** — server-side jobs (survive disconnect, fix concurrency race).

## Timeline (commit times, this thread bolded)

| time  | commit    | what |
|-------|-----------|------|
| 09:26 | `c6456b2` | seed engineering axis catalog (~75 axes) |
| 09:28 | `697492e` | hand-mined validation playbook for kaixn |
| 09:43 | `9a6bd76` | enforcement dimension + generation-grade axes |
| 10:21 | `563a68b` | mut-duplicate policy: deterministic→upsert |
| 10:45–11:16 | `4c47ec9`,`2854c87` | playbook eval-loop + guideline-coverage evals |
| 11:41–11:54 | `407b56e`,`0c15804`,`fbec569` | curate catalog · design-playbook workflow · rigor-for-free demo |
| 12:26 | `c36827c` | rewrite README around the handbook direction |
| **12:38** | **`2b6151f`** | **real miner: deterministic floor + live-API design pass** |
| **12:47** | **`fd0d9ae`** | **Playbook UI: add a repo → 3-tab webpage** |
| 12:50–13:00 | `5d67227`,`8dceb0c` | aws deploy scripts · update website |
| **13:12** | **`f5a48f5`** | **scrub `.env.save` secret from history + harden gitignore** |
| 13:35 | `32a47cf` | (user) marketing waitlist + verify-by-sampling |
| 13:41 | `23b6bfd` | merge origin/main (reconcile parallel work) |
| **14:02** | **`cb5c1bd`** | **stream LLM calls + bound timeout — fix the 31-min hang** |
| **14:32** | **`9a7ca82`** | **stream the playbook as it's generated (SSE)** |
| **15:05** | **`5c92b78`** | **eager knowledge layer: full PRD/Spec docs + domain + Add/Explore** |
| **15:11** | **`286014b`** | **reorder stream: PRDs/specs + docs first** |
| 15:12 | `0cc77e1` | (user) deploy guide + DevOps session history |
| **15:23** | **`bafa268`** | **balance features vs tech-specs (build_index): 23/1 → 13/13** |
| 15:36 | `79a6d12` | (user) raise ALB idle_timeout to 600s for SSE |
| **15:39** | **`ea5c4b4`** | **persist domain/principles robustly + ↻ refresh from Explore** |
| 15:51 | `6d2510f` | (user) marketing session notes |
| **15:56** | **`859f45d`** | **server-side jobs: survive disconnect, fix concurrency race** |
| **16:12** | **`63fd4e1`** | **build_index max_tokens 4096→8192 (stop silent offline fallback)** |

---

## Phase 0 — housekeeping (~13:12, `f5a48f5`)

> **Goal:** resume the handbook work cleanly and remove a committed secret without
> leaving it in history.
> **Driving prompt:** *"…session-2026-06-13-handbook.md ← continue work"* → on
> finding the live key, user chose **"Full scrub + history rewrite."**

- **Committed secret remediation.** `.env.save` held a live `ANTHROPIC_API_KEY`.
  Scrubbed from *all* git history via `git filter-repo`, hardened `.gitignore`
  (`.env.*` except `.env.example`), force-pushed the rewritten history. Verified
  the key was gone from every reachable commit. (User still must rotate the key —
  dangling objects on GitHub stay fetchable by SHA until GC.)
- **Discovered the user commits in parallel** on `main` (marketing waitlist,
  verify-by-sampling, deploy work). Lesson recorded in memory: always `git fetch`
  + check `git log`/`git diff HEAD` before assuming the tree is mine.

## Phase 1 — miner: verify-by-sampling + the hang fix (12:38–14:02)

> **Goal:** make the design-tier support *trustworthy* (real ratios, not the
> model's self-reported consistency), and make the real-API miner reliable.
> **Driving prompt:** user chose to build **"1 and 2"** (deepen tabs 1–2 +
> verify-by-sampling); the hang fix came from *"still running?"* → diagnosis.

- **verify-by-sampling** (`miner.mine_semantic`): PROPOSE (value + a `relevant_files`
  population) → VERIFY (independently classify a sample of those files
  follows/violates) → a *real* support ratio + counterexamples; method
  `llm-verified` vs `llm` (when the population can't be sampled).
- **Caught a 31-minute hang.** The semantic pass used `Anthropic().messages.create()`
  non-streaming; a large `max_tokens` call stalled the socket indefinitely. Fix
  (`cb5c1bd`): bounded **streaming** client (`max_retries=2, timeout=120s`) in
  `_anthropic_json`/`_llm_json`; bumped propose `max_tokens` 4096→8192 (the bigger
  `relevant_files` responses truncated JSON). Clean live run ~3 min.
- Live-validated on `encode/starlette`: 8 design axes `llm-verified`; later runs
  surfaced real violations (`data-access` 83%, `offline-fallback` 67%).
- Known caveat: proposer picks the verify population → sample skews adherent.

## Phase 2 — stream the playbook as it's generated (14:32, `9a7ca82`)

> **Goal:** kill the dead ~3-min spinner — show progress as the playbook is built.
> **Driving prompt:** *"can we show the playbook as it is generated — the UX is
> terrible as we wait"* (then tested with *"running with …/encode/starlette"*).

Turned the one ~3-min spinner into a live-filling page over **SSE**. Refactored
`mine_semantic` into `_semantic_propose` + `_observe_axis` + a `mine_semantic_iter`
generator so design cards stream one at a time. `POST /api/playbook/stream`
(StreamingResponse); client consumes via fetch+ReadableStream.

## Phase 3 — the knowledge layer (15:05, `5c92b78` — the big pivot)

> **Goal:** turn the technical inspector into the **human interface** + the durable
> knowledge agents read back to evaluate a proposed PRD / posted PR.
> **Driving prompts (three, in sequence):**
> 1. *"The UX looks very technical — the goal is that this is the human interface,
>    so each PRD/Tech Spec should follow the classic template… generate and render
>    them when clicked. We want a domain-driven-design graph… extract the key
>    objects and how they interact."*
> 2. *"We also don't want lazy — since this is the knowledge tools/agents use —
>    like when a new PRD is proposed or a PR is posted we want to evaluate it."*
> 3. *"We also want to query repos we've already indexed… the root page should be
>    add or explore."*

User reframed the goal: **this is the human interface, and the knowledge agents
read to evaluate a new PRD or PR.** That drove three decisions (asked + locked):

- **Full templated docs**, generated **eagerly for all items** (not lazy).
- **Persisted to Postgres.**
- **DDD graph** as a **Mermaid** class diagram; docs open at their **own URLs**.

Built:
- `playbook.build_doc()` — full classic **PRD** / **Tech Spec** markdown per item,
  grounded in repo. `build_domain()` — Mermaid `classDiagram` + entities (AST
  offline fallback).
- `build_stream` generates **all docs concurrently** (6-worker pool), streaming a
  `doc` event per completion.
- `playbook_store.py` (`PgPlaybookStore` / `InMemoryPlaybookStore`, `from_env`) +
  `migrations/002_playbook.sql`; self-creating tables. Bundle (domain, principles)
  + every full doc, keyed by repo.
- Endpoints: `POST /api/playbook/stream` (persists as it streams), `GET /api/repos`
  (Explore), `GET /api/playbook?repo` (agent surface), `GET /api/doc`, `GET /doc`.
- UI: **Add or Explore** root (browse indexed repos), PRDs/Tech-Specs as a clean
  clickable list → each opens at `/doc?...`, **Domain Model** tab (Mermaid), new
  `doc.html` (marked + mermaid, polls while generating).

## Phase 4 — the fixes that came from real runs (15:11–16:12)

> **Goal:** make the knowledge layer actually usable on real repos — content shows
> where expected, survives interruption, and regenerates on demand.
> **Driving prompts:** *"I am seeing empty for all"* (ordering) · *"why is the
> domain model for kaixn still empty?"* (robust persistence) · *"could we also add
> the ability to rerun/refresh on a URL from the repos page?"* · *"yes lets run
> these as jobs"* (after I flagged the browser-disconnect fragility). The balance
> fix (23/1 → 13/13) and the `build_index` truncation fix came from my own test runs.

- **DevOps (user, in parallel):** raised ALB idle timeout to 600s for SSE
  (`79a6d12`); rewrote deploy guide.
- **Ordering** (`286014b`): the default PRDs tab sat empty because the slow ~2-min
  design pass streamed first onto a tab the user wasn't on. Reordered:
  conventions → lists → docs → principles → domain. ("empty for all" complaint.)
- **Balance** (`bafa268`): two independent extraction calls let the model dump
  everything into features (starlette: **23 features / 1 tech-spec**). Replaced
  with one combined `build_index` call that partitions at senior altitude → **13/13**.
- **Robust persistence + refresh** (`ea5c4b4`): domain/principles were written only
  on their late events, so an interrupted run saved neither (the empty Domain Model
  the user hit). Fix: emit + persist **domain up-front**, persist conventions and
  each design principle **incrementally**. Added **↻ refresh/regenerate** on Explore
  rows and the repo view. Validated by interrupting right after the domain step —
  domain still persisted.
- **Server-side jobs** (`859f45d`): the FK violation
  (`playbook_doc_playbook_id_fkey`) was two generations racing on one repo
  (B's `create_playbook` deletes A's row → A's `save_doc` dangles). And any run died
  when the browser SSE dropped. Fix: `playbook_jobs.JobManager` runs each generation
  in a **background thread**, **one job per repo** (dedup). New endpoints:
  `POST /api/playbook/generate` (start/reuse, return now), `GET /api/playbook/events`
  (replay + live tail, reconnect-safe), `GET /api/playbook/jobs`. `from_env` makes
  the in-memory store a process-wide singleton; Pg gives each caller its own
  connection. UI: `run()` starts a job then subscribes with auto-reconnect+replay;
  Explore shows a live "● generating…" badge. **Validated:** a job started with
  *no client ever subscribed* filled the DB to completion (domain + 36 docs +
  17 principles).
- **`build_index` truncation** (`63fd4e1`): on larger repos the balanced object
  overflowed 4096 tokens → JSON parse failed → silent offline fallback (the
  6-PRD/30-spec module-docstring shape). Raised to 8192.

---

## Final architecture (playbook thread)

```
Browser ──POST /api/playbook/generate──▶ JobManager (1 thread/repo, dedup)
        ◀─GET /api/playbook/events?repo─  └─ build_stream_from_url (clone, shallow)
                 (SSE replay + tail)          ├─ mine() conventions  (instant, AST)
                                              ├─ build_domain()       → persist mermaid+entities
                                              ├─ build_index()        → features + tech_specs (balanced)
                                              ├─ ThreadPool: build_doc() × all items → persist each
                                              └─ mine_semantic_iter()  → persist each principle
Postgres: playbook(repo, mermaid, entities, principles) + playbook_doc(repo,kind,slug,markdown,…)
Read/agent surface: GET /api/repos, /api/playbook?repo, /api/doc, /doc page
```

Key modules: `src/kaixn/{miner,playbook,playbook_store,playbook_jobs,web}.py`,
`src/kaixn/static/{playbook,doc}.html`, `migrations/002_playbook.sql`.

## Open items / follow-ups

1. **Confirm the `build_index` 8192 fix** on kaixn (a regen was running at session
   end; earlier kaixn showed the 6/30 offline shape — verify it's now ~13/13).
2. **Agent evaluation surface** — the *why* behind eager+persist: an endpoint/MCP
   that, given a proposed PRD or a PR diff, retrieves the bundle and evaluates it
   (maps onto the existing gate/review machinery).
3. **Rotate the exposed Anthropic key** (still outstanding from the morning scrub).
4. **Verify-by-sampling population bias** — seed the sample with non-evidence files.
5. **Enforcement-honesty backlog** — ruff/mypy/CI so `invariant` labels are true.

## Validation summary (what was proven live this session)

- verify-by-sampling produces real ratios + counterexamples (live API).
- SSE streams incrementally (timestamped frames), no buffering.
- Persistence works (Pg bundle + docs); doc fetch returns a real grounded PRD.
- Jobs survive a fully-disconnected client (DB filled to 36 docs with no subscriber).
- Concurrency dedup: a duplicate `/generate` reused the in-flight job (no FK race).
- Robust domain persistence: interrupting right after the domain step still saved it.

---

## Appendix — the LLM prompts in the pipeline

The actual prompts driving each generation step (in `src/kaixn/{miner,playbook}.py`),
all run against `claude-sonnet-4-6` via a bounded streaming client.

**1. Design-axis PROPOSE** (`miner._semantic_propose`, max_tokens 8192):
> "You are mining a codebase's ARCHITECTURE/DESIGN. For each axis below, read the
> source and report the repo's ACTUAL value. Also list `relevant_files` (up to 8
> repo-relative paths): the files where this axis is DECIDED (include both files
> that follow and any that don't — the population we sample to verify). Be honest:
> set applies=false if the dimension is irrelevant to this repo. … Reply JSON array
> `[{axis,applies,value,evidence,relevant_files,consistency,tier,rationale}]`."
> (Axes: layering-direction, seam-pattern, dependency-injection, offline-fallback,
> error-signaling, input-validation, state-mutation, data-access, concurrency-model,
> trust-boundary.)

**2. VERIFY-by-sampling** (`miner._verify_axis`, per axis, max_tokens 1536):
> "Convention under test — axis '{axis}': the repo's stated value is \"{value}\".
> For EACH file below, decide INDEPENDENTLY whether it FOLLOWS the convention,
> VIOLATES it, or the convention is NOT_APPLICABLE to that file. Judge only what the
> file shows. Reply JSON `[{path,verdict:follows|violates|n_a,note}]`." → ratio =
> follows / (follows+violates).

**3. Combined index — features vs tech-specs** (`playbook.build_index`, max_tokens 8192):
> "Analyze THIS repository and split what it provides into two lists, at a senior
> PM/tech-lead altitude (group related capabilities — do NOT list every helper as
> its own item; aim for the ~8-14 most significant of each): • features — user-facing
> PRODUCT capabilities (what a PM writes a PRD for). • tech_specs — TECHNICAL areas a
> tech-lead specs (HOW it's built: architecture, stack/storage, core engine, seams,
> data flow, concurrency, extension points). Tag each with `principles` from the menu.
> Reply JSON object `{features:[{name,summary,evidence,principles}], tech_specs:[{area,
> decision,rationale,evidence,principles}]}`."

**4. Full document** (`playbook.build_doc`, per item, max_tokens 4096):
> "Write a {Product Requirements Document (PRD) | Technical Specification} for
> \"{title}\" of THIS repository. Ground every statement in the actual code/docs
> below; do not invent capabilities the repo doesn't have. Be concrete and specific
> (name real modules, types, endpoints). Start with a single H1 title, then EXACTLY
> these H2 sections, in order: {template}."
> PRD template: Overview · Problem & Context · Goals · Non-Goals · User Stories ·
> Functional Requirements · UX & Key Flows · Success Metrics · Dependencies & Risks.
> Spec template: Context & Background · Goals · Non-Goals · Proposed Design ·
> Data Model · APIs & Interfaces · Key Decisions & Trade-offs · Sequencing & Rollout ·
> Risks & Open Questions.

**5. Domain model** (`playbook.build_domain`, max_tokens 2048):
> "Extract the DOMAIN MODEL of this codebase: the key domain objects (entities,
> aggregates, value objects, services) and how they interact. Output a Mermaid
> `classDiagram`: declare each key class with its 2-5 most important fields, and the
> relationships between them — association (-->), inheritance (<|--), composition
> (*--), dependency (..>) — each with a short label. Keep to the ~15 most important
> objects. Reply JSON `{mermaid, entities:[{name,description}]}`."

Deterministic floor (no LLM): `miner.mine()` AST detectors compute exact support
for naming-case, future-annotations, module-docstrings, type-annotations,
dataclass-slots, test-mirroring, public-surface.
