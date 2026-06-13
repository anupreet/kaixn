# kaixn PRD — open questions to resolve

Working list of decisions to nail before drafting the full PRD. The PRD adds
**three role surfaces** (PM, EM, shared Handbook) over the v0.2 architecture
(`docs/architecture.md`), with **agents as a first-class third user** (consumer of
the spec/patterns/style + a learning source). Each question notes *why it matters*
and a *leaning*. Mark `DECIDED:` inline when we settle one.

Legend: `[ ]` open · `[x]` decided · `[~]` partially settled

---

## North star (the problem we solve)

Move the reviewable artifact up the stack for **both** roles:

1. **PM problem** — PRDs are disconnected from the truth of the system. PMs don't
   know all the paths, so requirements take many iterations to become
   comprehensive. kaixn grounds intent against the constitution + codebase so
   requirements converge fast and completely.
2. **Engineer problem** — engineers review *agent-generated code* against PRDs (wrong
   altitude). They should review/edit the **technical spec** and step in only where
   **engineering patterns need to evolve**.
3. **Agents** — consume the technical spec + patterns + style guidance to implement,
   and feed learning back.

**Three first-class users: PM, EM, agents.** We are building a rich, deep app — not
a thin memory layer.

---

## A. Foundational / reconciliation

- [x] **Q1 — Memory-layer doc fate. DECIDED:** Drop the "Learning Memory Layer"
  doc *as a product*, but **promote learning to a core capability and agents to a
  first-class user.** Style guidance, conventions, idioms are concepts agents
  follow directly. Keep: learnable, agent-consumable guidance + MCP access. Drop:
  success-rate auto-migration, Redis hot/cold split, workflow-with-success-rate as
  the unit.

- [x] **Q2 — Trust model. DECIDED: tiered trust.**
  - **Advisory / auto-learned** — style, conventions, idioms. Mined from code,
    applied by agents directly, *no human gate*. Shown in the handbook.
  - **Governed** — principles + consequential decisions. Human-promoted,
    conflict-gated, append-only.

- [x] **Q2a — Advisory/governed boundary. DECIDED: descriptive vs prescriptive.**
  - **Descriptive** = a regularity the code *already exhibits* (code is the
    evidence) → **advisory/auto.** Agent following it just matches existing
    reality; cheap + self-correcting.
  - **Prescriptive** = a commitment that *constrains future code* / contradicts
    current reality (a change-against-state) → **governed/gated.**
  Maps onto v0.2 exactly: **governed = change-against-state** (the existing
  conflict engine — assert/modify/supersede, gated). **Advisory = state-as-observed**
  (a new lightweight lane, never conflict-adjudicated). Edge case confirmed:
  "migrate to snake_case but code isn't there yet" is prescriptive → governed
  (correct — a human ratifies before agents enforce).

## B. Surface 3 — Handbook (Product + Engineering)

- [x] **Q3 — Notability threshold. DECIDED: splits by tier (see Q2a).**
  - *Advisory notability* = a **code consistency threshold** — if N% of the
    relevant code does X, X is a real convention worth recording. The code votes;
    this is the knob against the write-only-sink risk.
  - *Governed notability* = a human/agent deems it a consequential commitment and
    enters it through the governed flow (never auto-mined-and-trusted).
  *Open sub-knob:* the exact N% / how "relevant code" is scoped — tune later.

- [x] **Q4 — Derivation source. DECIDED (falls out of Q2a/Q3):** Advisory
  conventions are **mined from code** (consistency-thresholded). Governed
  norms come from **intents/docs + human/agent assertion**, conflict-gated. The
  technical handbook is therefore code-derived (advisory) + human-ratified
  (governed); the product handbook is intent/docs-derived + governed.
  *Open:* whether docs *also* feed advisory candidates (vs code only) — minor.

- [x] **Q5 — Freshness / staleness contract. DECIDED:** Primary mechanism is the
  **per-PR constitutional review gate** (see Q11) — drift is *event-driven*, not
  periodic. Advisory norms re-mine incrementally from the PR diff; governed
  contradictions surface as blocking/required review items on the PR. Periodic
  full pass demoted to a **backstop** (catches direct-to-main, re-evaluates when
  the constitution itself changes). Handbook shows per-norm freshness/drift badge
  + "synced to commit X".

- [x] **Q6 — Handbook shape & interaction. DECIDED:** Two books — **Product**
  (`product`+`product_design`+`ux`) and **Engineering** (`technical`) — each
  organized by **scope (ltree product-area tree)** for natural sections. Every
  entry: statement, rationale, **tier badge** (observed-convention vs
  ratified-principle/decision), provenance link (source commit/proposal),
  **supersede timeline** (the Q14 evolution view). Interaction: **read by anyone**,
  **not directly editable** (append-only). Anyone can **flag / "request change"**,
  which opens a governed `modify`/`supersede` proposal into the review queue.
  Agents read via MCP.

## C. Surface 1 — PM (concept → engineering blast radius)

The PM surface is the **mirror of the Eng per-PR gate** (Q11): same conflict
engine, surfaced **pre-code** (on intent) instead of **at-PR** (on diff). The
PM's "doesn't know all the paths" problem = the **`gap` verdict pointed forward**.

- [x] **Q7 — Cross-domain coupling. DECIDED:** the bridge is **scope (ltree), not
  domain**. A product concept in `all.product.billing` pulls technical norms under
  `all.product.billing.*`. (§6 step 2's "in domain" retrieval is amended to
  scope-governed across domains.)

- [x] **Q8 — Blast-radius. DECIDED:** predictive **`impact_of`** on the norms a
  concept would touch, *before grounding*; depth = norm → features → code.

- [x] **Q9 — PM-facing presentation. DECIDED: layered.** Plain-language
  *consequence* summary (generated from touched technical norms) + **drill-down to
  raw norms** for ground truth.

- [x] **Q10 — Authoring surface. DECIDED:** a **living PRD editor in kaixn** that
  surfaces impact / gaps / conflicts **inline as the PM writes**, converging to a
  grounded comprehensive Proposal. Not a separate chat, not a static form.

### The full loop (Groups C + D are one product)

```
PM intent ─▶ [PM surface] ground·gap-fill·resolve ─▶ Proposal + agent_contract
                                                            │
                                              agent implements ─▶ PR
                                                            │
                                  [Eng surface] per-PR constitutional review gate
                                                            │ merge
                                          constitution updated ─▶ Handbook
                                              (read by PM, EM, agents)
```
One engine, two moments (pre-code / at-PR), one constitution everyone reads.

## D. Surface 2 — Engineering (patterns introduced / evolved + completeness)

- [x] **Q11 — What feeds the Eng surface? DECIDED: the per-PR constitutional
  review gate (the keystone).** Every PR is analyzed by kaixn; the engineer reviews
  the **constitutional delta** the PR implies, not the diff. The review rides on
  the commit → the constitution never silently drifts from code. Three checks per
  PR → one delta:
  1. **vs the proposal** it claims to implement (forward-drift: did code honor the
     `agent_contract`?),
  2. **vs governed norms** (conflict / gap / tension),
  3. **vs advisory conventions** (follows / reinforces / establishes a new
     regularity worth recording).
  **Tiered enforcement** (maps to trust tiers): governed conflict → **blocking**;
  governed gap/tension → **review required, overridable w/ rationale** (→ eval
  label); advisory → **informational, auto-recorded, never blocks**. Two entry
  modes converge here: agent-authored PR (proposal known, fast check) and
  human-authored PR (delta inferred cold).

- [x] **Q12 — Flow B status. DECIDED:** first-class, **event-driven** flow (per-PR),
  not a background job. The per-PR gate IS the Engineering review surface.

- [x] **Q13 — GitHub integration. DECIDED:** PR webhook → analyze net diff → post
  constitutional delta back as a check + comment. `code_ref` + `implements` edges.

- [x] **Q11a — Review granularity. DECIDED:** PR net-diff is the unit;
  direct-to-`main` via push-analysis fallback. (Not per-commit — too noisy.)

- [x] **Q11b — System of record. DECIDED:** PR comment is where engineers act
  (approve/override in GitHub); kaixn holds the full constitutional view + is the
  record. Comment-first for adoption.

- [x] **Q11c — Blocking policy. DECIDED:** kaixn is a **required status check**,
  tiered — only governed *conflicts* hard-block merge; gaps/tension are
  required-but-overridable (w/ rationale); advisory never blocks. This is what
  makes code↔constitution sync *guaranteed*.

- [x] **Q14 — "Evolution of old patterns" view. DECIDED (absorbed into Q6):**
  `supersede_chain` rendered as a per-norm timeline on each handbook entry; a
  pattern's evolution is visible at the norm and surfaced in the per-PR delta when
  a PR proposes the next supersede.

## E. Cross-cutting engine

- [x] **Q15 — "Completeness" — of what against what? DECIDED:** two named
  notions — `gap` (per-change, conflict engine, built) + `coverage` (handbook-vs-
  code, reconciler, now delivered via the per-PR gate — Q11). Two engines, one
  shared store.
  Per-proposal gap recall
  (is this feature complete against the principles it touches) vs. handbook-vs-
  codebase completeness (does the constitution cover the code)? These are
  different engines on different inputs.
  *Why:* the word "completeness" in the Eng-surface ask is ambiguous and decides
  whether the PRD specs one engine or two. **Resolve early.**

- [x] **Q16 — Authority model. DECIDED (working): by domain × tier.** Advisory →
  no authority (auto). Governed *decisions* → domain owner (product domains → PM,
  `technical` → EM). Governed *principles* → higher bar (explicit ratification by
  the domain owner). Resolves v0.2 Open Decision #4.

- [x] **Q17 — Adjudication LLM. DECIDED (working):** single strong model +
  adversarial self-verify on governed conflicts/gaps. Panel deferred as an
  eval-driven optimization.

- [x] **Q18 — Evals. DECIDED (working): three labeled sets** — (1) `op × norm →
  verdict` (v0.2 §7), (2) synthesis (intent → correct ops), (3) per-PR delta (diff
  → correct constitutional delta); + advisory-mining precision check.

## F. Stack / infra (lower priority, still track)

- [x] **Q19 — App-layer stack. DECIDED:** ratify the repo — FastAPI/Python +
  Postgres + pgvector + ltree.

- [x] **Q20 — Embedding model. DECIDED:** default OpenAI 1536, pluggable to local
  768 (already in `embedding.py`).

- [x] **Q21 — `implement` granularity. DECIDED:** per logical change, carrying a
  list of target locations.

- [x] **Q22 — Connectors priority. DECIDED:** repo → docs (Notion/Confluence) →
  Linear.

---

## Parked feature requests (not PRD questions, don't lose them)

- Doc-mining **progress indicator** for repo connect (SSE vs staged spinner vs
  polled job) — raised before the PRD pivot; revisit after PRD.
