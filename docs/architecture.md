# kaixn — Architecture Specification (v0.2)

**Status:** draft · **Scope:** v0 foundation (memory model + provenance graph) through the conflict engine and plan synthesis
**Audience:** engineers building kaixn, and coding agents implementing against it
**Changes from v0.1:** the *plan* is promoted from a text column to a first-class structured artifact (a **Proposal** = an ordered set of typed **operations** against the graph and code). `spec`/`spec_claim` are retired; intent and plan are separated; conflict detection now runs on typed operations. See §0 for the full diff.

---

## 0. What changed and why (v0.1 → v0.2)

The v0.1 thesis was right, but the model was inverted: it atomized the **spec** into checkable claims (structured) and emitted the **plan** as `plan_text` (prose). The thesis says the opposite — the *spec is disposable input*, and the *plan is the structured artifact kaixn owns and reviews*, because **conflict is a property of change-against-state, and only the plan expresses change-against-state.** A spec asserts into a void and can't conflict with anything; a plan touches things that already have commitments, and that's where conflict becomes visible.

| v0.1 | v0.2 | Why |
|---|---|---|
| `spec` (intent_text + **plan_text**) | split into **`intent`** (disposable input) + **`proposal`** (owned, versioned plan) | intent and plan have different lifecycles; the plan is the artifact, not a text field |
| `spec_claim` (untyped requirement) | **`operation`** (typed change: `assert`/`modify`/`deprecate`/`supersede`/`implement`, with explicit target) | typing makes change-against-state explicit; a class of conflicts becomes structural, not LLM-dependent |
| `plan_text` (prose for the agent) | **operations + generated `agent_contract`** (a *rendering* of the structured plan) | the structured proposal is the source of truth; the markdown is a view |
| override → mint decision (special case) | override = **add a `supersede` operation to the same proposal** | resolution becomes a first-class op; case law accrues inside proposals |
| "engine reused on the constitution" (§6.4) | falls out for free — **norm operations are conflict-checked like any operation** | one abstraction, two surfaces |
| edges hang off `spec`/`spec_claim` | edges hang off **`operation`/`proposal`** | the change-against-state object is the natural edge-bearer and review surface |

**Naming note.** The owned artifact is called a **Proposal**, not a "plan." "Plan" is already overloaded in this market to mean an agent's execution step-list (Claude Code Plan Mode, Kiro `tasks.md`). A kaixn Proposal is a higher-order object: a typed change-proposal against persistent product state, authored *before* any execution step-list exists. The word should signal *diff-against-state*.

**Resolved fork (was §11 open).** v0.1 left open whether requirement-claims and plan-operations should coexist. **Resolved: they collapse into one `operation` model.** We keep the benefit of a cheap early conflict pass by *sequencing* — norm-facing conflict checks run before the expensive code-grounding step (§6) — not by maintaining two atomic representations.

---

## 1. Problem & thesis

Coding agents can now implement features from a written plan. The bottleneck is
no longer writing code — it's making sure the **plan** is right, consistent, and
faithful to everything the team has already decided. Today that check happens (if
at all) in PR review: late, expensive, and about the diff rather than the intent.

**Thesis:** move the reviewable artifact up the stack — from the PR/diff to the
**Proposal**. A product manager writes an *intent* (a spec-shaped ask); kaixn
synthesizes a **Proposal** — a structured set of typed operations against the
team's persisted constitution and codebase — validates it *before* a coding agent
writes a line, surfaces conflicts while they're cheap, and emits a grounded
contract the agent implements. **PR review becomes Proposal review.**

The spec is the input we don't fight for. The Proposal is the artifact we own. The
memory layer is what makes the Proposal possible — a spec needs no memory, a plan
requires it.

### Non-goals (v0)
- **Not a coding agent.** kaixn produces Proposals and points at code;
  implementation is delegated (Claude Code, etc.).
- **Not a project-management tool.** It links to features/initiatives but doesn't
  replace Linear/Jira.
- **Not a doc store.** Prose lives in Notion/GitHub; kaixn stores *atomic,
  checkable* norms and *structured* Proposals.
- **The PM never authors operations by hand.** Operations are *generated* from
  intent and *reviewed* by the PM. Authoring in typed ops would kill adoption.

---

## 2. The three layers

| Layer | Holds | System of record |
|---|---|---|
| **1. Code** | the implementation | GitHub (kaixn points at it via `code_ref`) |
| **2. Memory / constitution** | principles + decisions, across 4 domains | kaixn |
| **3. Proposal** (intent + plan) | per-feature intent + the structured plan (operations) | kaixn |

The value is not the three stores; it's the **provenance edges** between them:

```
  Constitution (norms: principles + decisions)
        ▲  supersedes                    │ governs
        │                                ▼
  ─────────────────────────  depends_on / creates / amends / conflicts_with
        │                                ▲
  Proposal (intent + operations) ────────┘
        │ implements
        ▼
  Code (GitHub PR)
```

- **Forward provenance** — "why is this code like this?" → PR → Proposal →
  operation → norm.
- **Reverse impact** — "we want to change this norm → which Proposals/features are
  at risk?" The query no PR review can answer.

---

## 3. Domain model

### 3.1 Two kinds of norm (unchanged — this is the moat)

| | **Principle** | **Decision** |
|---|---|---|
| nature | durable, normative ("we never block the main thread") | specific choice ("billing uses Stripe, not Adyen") |
| cardinality | few | many, accruing |
| authority | constitution — **hard stop** on conflict | case law — a Proposal may legitimately revise it |
| context loading | **always loaded in full** | **retrieved** (scope + semantic) |
| change | rare, governed | supersede chains over time |

A Proposal conflicting with a **principle** is a hard stop. A Proposal conflicting
with a **decision** is often legitimate revision — which is expressed as a
`supersede` operation inside that same Proposal. Same UI, different semantics.

### 3.2 The Proposal and its operations (the v0.2 core)

A **Proposal** is the owned plan for a feature: an ordered, versioned set of
typed **operations**. It is the review surface ("Proposal review not PR review")
and the edge-bearer for all provenance.

An **operation** is a single typed change-against-state. Two kinds, one table:

| kind | op_type | target | meaning |
|---|---|---|---|
| **norm** | `assert` | — (mints a new norm) | introduce a principle/decision |
| **norm** | `modify` | active norm | change a norm's statement/scope |
| **norm** | `deprecate` | active norm | retire a norm |
| **norm** | `supersede` | active norm (→ new) | replace a decision with a revision (how case law accrues, incl. overrides) |
| **code** | `implement` | code location(s) | a grounded change to the codebase; `depends_on` norms it must respect |

Every operation — both kinds — is conflict-checked against the active norm set.
That is what makes the constitution-self-check (a `modify` that contradicts a
principle) and the feature-vs-constitution check (an `implement` that violates a
decision) the *same* mechanism (§6).

### 3.3 Fixed taxonomies (unchanged)

- **domain** (discipline): `product`, `technical`, `product_design`, `ux`
- **scope** (product area): hierarchical `ltree` rooted at `all`
  (e.g. `all.product.billing.subscriptions`). Orthogonal to domain. `all` = global.

### 3.4 Work hierarchy

`initiative` (epic) → `feature` → `proposal` (versioned) → `operation` (atomic).
Norms are minted/changed **only via operations**, so origin (whether an
initiative-level or feature-level Proposal minted a norm) is uniform in the graph.

### 3.5 Entities (canonical DDL in `migrations/001_init.sql`)

- `initiative`, `feature` — work hierarchy.
- `proposal` — belongs to a feature; versioned; carries `intent_text` (the raw PM
  ask it was synthesized from) and `status` (`draft`/`in_review`/`accepted`/`superseded`).
  Its `agent_contract` (generated markdown) is a *rendering*, not the source of truth.
- `operation` — belongs to a proposal; typed (§3.2); `statement` + `rationale`
  (quote from intent); `order`; `status`; embedded + full-text indexed. Replaces
  `spec_claim`.
- `norm` — `principle|decision`; atomic `statement` + `rationale`; `domain`,
  `scope`, `status`, `version`; embedded + full-text indexed.
- `norm_example` — positive/negative worked examples (gold for the engine).
- `code_ref` — pointer into GitHub (PR or commit).
- `verdict` — one row per `(operation × norm)` adjudication: `verdict_type`,
  `evidence` (cited spans), `proposed_resolution`. The conflict report and the
  eval signal. (Promoted to a real table in v0.2.)
- `edge` — one generic provenance-graph table.

### 3.6 Invariants

1. **Atomicity** — one normative claim per `norm`; one change per `operation`.
   Both sides of a conflict check are atomic.
2. **Append-only / versioned** — nothing mutates in place. Supersession =
   `status='superseded'` + a `supersedes` edge from the replacement. Proposals are
   versioned; accepting a new version supersedes the prior. History stays queryable.
3. **No write-only memory** — every record sits on a defined read path (§5).
4. **Provenance in the graph** — "which Proposal minted this norm" is an edge.
5. **PM never authors operations** — operations are system-generated, human-reviewed.

---

## 4. The provenance graph

One generic `edge(src, dst, rel_type, metadata)` table; traversed with recursive
CTEs. No dedicated graph DB at this scale.

| rel_type | from → to | meaning |
|---|---|---|
| `supersedes` | norm → norm | case-law evolution (via a `supersede` operation) |
| `depends_on` | operation → norm | this operation relied on / must respect this norm |
| `creates` | operation → norm | an `assert` minted this norm |
| `amends` | operation → norm | a `modify` changed this norm |
| `conflicts_with` | operation → norm | recorded conflict + resolution (in `metadata`) |
| `implements` | code_ref → proposal | PR fulfils the Proposal |
| `relates_to` | norm → norm | soft link |

All change-bearing edges now originate at an **operation** (or `code_ref`), making
the operation the unit of provenance.

---

## 5. Read paths (the contract `queries/read_paths.sql`)

The schema exists to serve exactly these. Any column serving no read path is removed.

1. **`active_norms(domain, scope)`** — the set the conflict engine checks against:
   active norms whose scope governs the target (`scope @> target`).
2. **`impact_of(norm_id)`** — reverse impact: operations/proposals depending on a
   norm + the code implementing them.
3. **`provenance(code_ref)`** — forward: code → proposal → operations → norms.
4. **`supersede_chain(norm_id)`** — full lineage of a decision.
5. **`proposal_review(proposal_id)`** *(new)* — render a Proposal as a
   change-against-state review: every operation, its target, its verdicts, and its
   resolution. **This is the review surface** that replaces PR review.

---

## 6. The conflict engine + plan synthesis (the product)

A naive "dump all norms + intent into one prompt, ask for conflicts" fails the way
LLM judges always fail: sycophantic rubber-stamp or hallucinated conflicts, with no
way to tell which. The engine is a **decomposed, typed, retrieve-then-adjudicate**
pipeline, and it produces the Proposal as it goes.

```
intent_text ──▶ synthesize operations (typed, targeted)        [LLM]
                      │
                      ▼
        ┌── for each operation ──────────────────────────────┐
        │                                                     │
        │  1. STRUCTURAL CHECK (typing — deterministic)       │
        │     modify/deprecate/supersede → target active?     │
        │        else → conflict (stale target)               │
        │     assert → duplicate of an active norm?           │
        │        else → conflict (merge suggested)            │
        │     implement → code target resolvable?             │
        │        else → needs-grounding (defer)               │
        │                                                     │
        │  2. RETRIEVE relevant norms                          │
        │     all active principles in domain                  │
        │     + scoped decisions (ltree scope × pgvector ×     │
        │       tsvector hybrid)                               │
        │                                                     │
        │  3. ADJUDICATE (operation × norm), independent per   │
        │     pair [LLM, adversarial-verify], norm_example     │
        │     rows as few-shot anchors → verdict + evidence    │
        └──────────────────────┬──────────────────────────────┘
                               ▼
              aggregate → Proposal conflict report (§5.5)
                               │
                ┌──────────────┴───────────────┐
          edit intent                     resolve in place:
          (re-synthesize ops)             add a `supersede` norm-operation
                                          to THIS proposal (override),
                                          recording a conflicts_with edge
                               │
                               ▼   (only accepted operations)
              GROUND code operations  [LLM + repo access]
              target_location / before_state / after_state /
              acceptance_criteria
                               │
                               ▼
              render agent_contract (markdown) ──▶ coding agent
              commit edges: creates / amends / depends_on / conflicts_with
```

### 6.1 Verdict types (unchanged — `gap` is the crown jewel)
- `consistent` — operation agrees with the norm.
- `conflict` — direct contradiction (hard stop for principles).
- `tension` — partial/contextual friction; needs a human call.
- `gap` — **conflict by omission**: the Proposal is silent on something a norm
  *requires* (e.g. "principle UX-07 requires error states; no operation defines
  one"). Most real bugs live here, and `gap` is meaningless in a spec world — only
  a *change-against-state* can fail to address what existing state requires.

### 6.2 Why typing matters (the v0.2 win)
Structural checks (step 1) catch a whole class of conflicts **before any LLM runs**:
a `modify` against a superseded norm, an `assert` duplicating an active norm, a
`supersede` targeting something already replaced. The LLM judge (step 3) only
handles the semantic cases. This cuts cost, cuts hallucinated verdicts, and gives a
deterministic floor under the engine.

### 6.3 Sequencing = cheap early pass
Norm-facing checks (steps 1–3) run on **all** operations first. Expensive code
**grounding** runs only on *accepted* operations, after conflicts resolve. So a
Proposal that contradicts a principle is killed before a token is spent grounding it
to the repo — the benefit the old "claims-first" fork was reaching for, without a
second atomic representation.

### 6.4 Resolution = a first-class operation
Overriding a decision is not a side-channel: it appends a `supersede` norm-operation
to the same Proposal, mints the new decision on accept, writes a `supersedes` edge,
and records the `conflicts_with` resolution in `metadata`. Case law accrues *inside*
Proposals, fully in the operation model.

### 6.5 One engine, two surfaces
Because norm-operations are adjudicated exactly like code-operations, the engine that
checks a feature against the constitution **is** the engine that keeps the
constitution internally consistent. Build once.

---

## 7. Eval harness (non-negotiable, week one)

The conflict engine is the one capability that *is* the product, so it cannot ship on
vibes.

- **Dataset:** labeled `(operation, norm, expected_verdict)` cases, seeded by hand and
  grown from production resolutions (every human-confirmed verdict is a new label).
- **Metrics:** precision/recall **per verdict type**. Over-alerting → alarm fatigue →
  ignored → dead product. Under-alerting → misses the thing it exists to catch →
  worthless. Track both, with separate attention to **`gap` recall** (the hardest and
  most valuable).
- **Separately score the structural layer** (§6.2) — it should be ~100% precision; any
  miss is a bug, not a model error.
- **Gate:** engine/prompt/model changes run against the set before rollout (regression
  gate).

> Note (carried from v0.1 review): v0.2 adds a new upstream failure surface —
> **operation synthesis** (intent → correct typed/targeted operations). Garbage
> operations produce confident conflict checks on the wrong things, so synthesis
> needs its own eval, not just the `(operation, norm, verdict)` set.

---

## 8. Bootstrap

Mining an existing product's principles into `status='proposed'` norm candidates for
human promotion. Full design in `docs/bootstrap.md`. Every candidate carries an
**evidence span**, passes atomicity + dedup + self-consistency, and is **never
auto-trusted** — a curator promotes `proposed → active`. (Promotion itself runs through
the conflict engine as `assert` operations, so a candidate that contradicts an existing
norm is caught at bootstrap.)

---

## 9. Drift reconciliation

Memory rots against reality: implementations silently violate Proposals; Proposals
silently violate principles; principles go stale. A periodic job walks
`code_ref → proposal → operation → norm` provenance and flags:
- code that diverged from the Proposal it claims to implement,
- norms with no active dependents (candidates for deprecation),
- decisions contradicted by newer merged code (propose a `supersede`).
Without this loop the memory becomes confidently wrong — the failure mode we explicitly
designed against.

---

## 10. Components & stack

| Component | Responsibility | Stack (proposed) |
|---|---|---|
| **Store** | norms, proposals, operations, graph, retrieval | Postgres + pgvector + ltree |
| **Memory API** | CRUD + read paths + write gate (atomicity/dedup/consistency) | _app layer (TBD)_ |
| **Proposal engine** | synthesize ops → structural check → retrieve → adjudicate → report → ground → render | _app layer_ + LLM |
| **Eval harness** | labeled set, scoring, regression gate | _app layer_ |
| **Bootstrap** | ingest → extract → lint → review queue | _app layer_ + LLM |
| **Reconciler** | drift detection over provenance | scheduled job |
| **GitHub integration** | `code_ref` + `implements` edges from PRs | webhook/poller |

Embeddings default to `vector(1536)` (OpenAI `text-embedding-3-small`); swap to 768 for
a fully-local `nomic-embed-text` (decide before data exists).

---

## 11. Schema sketch

The canonical, full-quality DDL lives in `migrations/001_init.sql` (generated
`tsvector` columns, `hnsw` vector indexes, FK `ON DELETE` rules, and CHECK
constraints enforcing op-kind/op-type coherence and target presence). The doc no
longer carries an inline sketch to avoid drift between the two.

---

## 12. Open decisions

1. **App-layer stack** — language/framework for the API, engine, jobs (not yet chosen).
2. **Embedding model** — cloud (1536) vs local (768); affects standalone story.
3. **LLM for adjudication** — single strong model vs panel; cost vs precision.
4. **Authority model** — who may accept an `assert` (principle) vs a `supersede`
   (decision override); principles likely need higher authority than decisions.
5. **Intent authoring surface** — where the PM actually writes the intent (kaixn UI?
   editor? chat?). *(reframed from "spec authoring surface")*
6. **Ingestion connectors** for bootstrap (repo, Notion, Linear) — priority order.
7. **Operation granularity for `implement`** — one operation per file/symbol vs per
   logical change; affects grounding precision and agent-contract size.

*Resolved in v0.2:* claims-vs-operations (collapsed into one `operation` model, §0);
plan representation (structured Proposal is source of truth, `agent_contract` is a
rendering).

---

## 13. Build sequence

1. ✅ **Memory model + provenance graph** — schema, read paths, bootstrap design.
2. **Memory API + write gate** — make atomicity/dedup/consistency real for `norm`.
3. **Proposal + operation model** — the v0.2 core: intent → synthesized typed
   operations; the `proposal_review` read path as the review surface.
4. **Conflict engine + eval harness** — together; structural checks first, then
   retrieve→adjudicate. The engine is meaningless without the eval.
5. **Resolution loop** — edit-intent and override-as-`supersede`-operation.
6. **Code grounding + agent_contract render + GitHub integration** — close the loop
   to code; `implements` edges from PRs.
7. **Bootstrap pipeline** — onboard a real product's constitution.
8. **Drift reconciliation** — keep it honest.
