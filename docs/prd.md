# kaixn — Product Requirements Document (v1)

**Status:** draft · **Owner:** anupreet · **Date:** 2026-06-12
**Companion specs:** `docs/architecture.md` (v0.2 engine/data model) ·
`docs/bootstrap.md` (constitution mining) · `docs/prd-open-questions.md` (the
22 decisions this PRD is built on; read it for the *why* behind each call).

> One-line: **kaixn moves review up the stack for everyone — PMs review the
> impact of their intent, engineers review the constitutional delta of a PR, and
> agents read a living Product + Engineering handbook that always reflects the
> code.**

---

## 1. North star & the problem

Coding agents can implement features from a written plan. Two human bottlenecks
remain, and they are the reason features ship slow and wrong:

1. **PMs write PRDs disconnected from the truth of the system.** They don't know
   all the paths a change touches, so requirements take many review iterations to
   become comprehensive. The gaps surface late — in eng questions, in QA, in prod.
2. **Engineers review agent-generated *code* against those PRDs.** That is the
   wrong altitude. The valuable human judgment is whether the *technical spec* is
   right and whether an **engineering pattern needs to evolve** — not reading diffs
   line by line.

kaixn fixes both by owning a **constitution** — the team's principles and
decisions, product and technical — and surfacing it at the two moments that
matter: when a PM forms intent, and when a PR lands. Same engine, two moments,
one shared source of truth that agents also read.

**Three first-class users: PM, EM, and agents.** This is a rich, deep product —
not a thin memory layer.

### Non-goals
- **Not a coding agent.** kaixn produces grounded contracts and reviews; agents
  (Claude Code, etc.) implement.
- **Not a PM/issue tracker.** Links to Linear/Jira; doesn't replace them.
- **Not a doc store.** Prose lives in Notion/GitHub; kaixn stores *atomic,
  checkable* norms and *structured* proposals.
- **Not a code-correctness reviewer (v1).** kaixn reviews the *constitutional*
  delta (patterns, decisions, completeness), which over time absorbs the
  pattern-checking part of code review. Bug-finding coexists during transition.

---

## 2. Users & jobs-to-be-done

| User | Job | Surface |
|---|---|---|
| **PM** | "Make my intent comprehensive and grounded before engineering starts." | **PM surface** — living PRD editor with inline impact/gaps/conflicts |
| **EM / Engineer** | "Tell me what *patterns* a PR introduces or evolves, and what it leaves incomplete — let me edit the spec, not the diff." | **Engineering surface** — per-PR constitutional review gate |
| **Anyone** | "Show me, credibly, what this codebase decided — product and technical — and why." | **Handbook** — two living books, read by all |
| **Agents** | "Give me the patterns, style, and decisions to follow; let me record what I learn." | **MCP** read access + gated proposals |

---

## 3. The constitution (the asset everything reads)

A **norm** is one atomic, checkable claim. Two orthogonal classifications:

**Kind** (unchanged from v0.2 §3.1):
- **Principle** — durable, normative ("we never block the main thread"). Few.
  Hard stop on conflict. Always loaded.
- **Decision** — a specific choice ("billing uses Stripe, not Adyen"). Many,
  accruing. Revisable via `supersede`. Retrieved by scope + semantics.

**Tier** (new in v1 — the trust model, Q2/Q2a):
- **Advisory** — a regularity the code *already exhibits* (style, conventions,
  idioms). **Descriptive.** Mined from code above a consistency threshold, born
  active, **applied by agents without a human gate**, never conflict-adjudicated.
  Cheap to be wrong; self-correcting as the code changes. *State-as-observed.*
- **Governed** — a commitment that *constrains future code* (principles +
  consequential decisions). **Prescriptive.** Human-promoted, conflict-gated,
  append-only. *Change-against-state* — exactly what the v0.2 engine already gates.

The descriptive/prescriptive cut is the boundary: *observed → advisory/auto;
intended change → governed/gated.* "We want to migrate to snake_case but the code
isn't there yet" is prescriptive → governed (a human ratifies before agents
enforce). Once ratified, agents follow it.

**Domain** (discipline, unchanged): `product`, `technical`, `product_design`,
`ux`. **Scope** (product area, unchanged): ltree rooted at `all`, orthogonal to
domain — and the bridge that lets a *product* concept reach *technical* norms.

---

## 4. The three surfaces

### 4.1 Handbook — the living Product + Engineering books

Two books, each the always-current read surface anyone can review:
- **Product handbook** — domains `product` + `product_design` + `ux`.
- **Engineering handbook** — domain `technical`.

Within each, organized by **scope** (the ltree product-area tree gives natural
sections: billing, auth, …). Every entry shows: statement, rationale, **tier
badge** (observed-convention vs ratified-principle/decision), **provenance** link
(source commit or proposal), **supersede timeline** (a pattern's evolution at a
glance), and a **freshness/drift badge** ("synced to commit `abc123`").

**Interaction:** read by anyone; **not directly editable** (append-only holds).
Anyone can **flag / "request change"**, which opens a governed `modify`/`supersede`
proposal into the review queue. Agents read it via MCP.

**Derivation & freshness (Q4/Q5):** advisory entries are mined from code and
re-mined incrementally on every PR; governed entries come from intent/docs +
ratified proposals and only change via governed operations. Drift between code and
a governed norm is surfaced at the PR (§4.3), with a periodic full pass as backstop.

**Credibility guard (Q3):** advisory norms require a **code consistency threshold**
— if N% of the relevant code does X, X is recorded. The code votes; this is what
keeps the technical handbook from becoming a write-only sink.

### 4.2 PM surface — intent → engineering blast radius

A **living PRD editor**: the PM writes intent and kaixn surfaces, inline and live,
three things against the constitution:

1. **Conflicts** — intent contradicts a governed decision → resolve (edit intent,
   or propose a `supersede`).
2. **Gaps (the killer feature)** — governed norms in the touched scope the intent
   is *silent on*: "you haven't addressed the refund-policy decision / the
   error-state principle." **This is kaixn surfacing the paths the PM didn't know.**
   Comprehensiveness becomes instant instead of an N-iteration discovery.
3. **Blast radius** — predictive **`impact_of`** on the norms the concept touches:
   which existing features/code are at risk (depth: norm → features → code), before
   any grounding.

**Cross-domain (Q7):** the product→technical bridge is **scope overlap**, not
domain — a concept in `all.product.billing` pulls technical norms under
`all.product.billing.*`.

**Presentation (Q9):** layered — a plain-language *consequence* summary generated
from the touched technical norms, with **drill-down to the raw norms** for ground
truth. The PM stays at altitude without the truth being hidden.

**Output:** a grounded, comprehensive **Proposal** + generated `agent_contract`
the agent implements.

### 4.3 Engineering surface — the per-PR constitutional review gate (keystone)

Every PR is analyzed by kaixn; the engineer reviews the **constitutional delta**
the PR implies — *not the diff*. Because the review rides on the commit, the
constitution can never silently drift from code. Three checks → one delta:

1. **vs the proposal** it claims to implement (forward-drift: did the code honor
   the `agent_contract`?).
2. **vs governed norms** — conflict / gap / tension.
3. **vs advisory conventions** — follows / reinforces / **establishes a new
   regularity** worth recording.

**Tiered enforcement (maps to the trust tiers):**
- Governed **conflict** (contradicts a principle) → **blocking**; resolve by
  changing code or ratifying a `supersede` (pattern evolution) inline.
- Governed **gap / tension** → **review required, overridable with rationale**
  (the rationale becomes a verdict label → eval data).
- **Advisory** (new/changed convention) → **informational, auto-recorded, never
  blocks**. Future agent work picks it up.

A clean PR sails through; engineers stop only where a pattern genuinely conflicts
or must evolve — exactly the job-to-be-done.

**Two entry modes converge here:** an *agent-authored* PR (proposal known → fast
"did it honor the contract + introduce nothing unsanctioned") and a *human-authored*
PR (delta inferred cold from the diff).

**Integration (Q11a–c, Q13):** PR **net-diff** is the unit (direct-to-`main` via
push fallback). kaixn is a **required status check** (only governed conflicts
hard-block). The **PR comment** is where engineers act; **kaixn holds the full
constitutional view and is the record.**

### 4.4 Agents — consumer & learner

- **Read (MCP):** agents pull scope-relevant governed norms + advisory conventions
  + the `agent_contract` — the patterns, style, and decisions to follow.
- **Learn:** advisory conventions the agent's own merged code reinforces are mined
  automatically (no gate). Anything prescriptive an agent wants to introduce is a
  **gated proposal** into the review queue — never auto-trusted.

---

## 5. The full loop

```
PM intent ─▶ [PM surface] ground · gap-fill · resolve ─▶ Proposal + agent_contract
                                                                  │
                                                    agent implements ─▶ PR
                                                                  │
                                  [Eng surface] per-PR constitutional review gate
                                                                  │ merge
                                          constitution updated ─▶ Handbook
                                              (read by PM, EM, agents)
```

**One conflict engine, two moments** (pre-code for the PM, at-PR for the EM),
**one constitution** everyone — including agents — reads.

---

## 6. The engines

Two engines over one shared norm store + provenance graph (Q15):

- **Conflict engine (`gap`)** — per-change completeness. Adjudicates an
  `operation × norm` pair → `consistent` / `conflict` / `tension` / `gap`. Powers
  both the PM surface (forward gaps) and the per-PR governed checks. *Already built*
  (v0.2 §6, repo `conflict.py` / `engine.py` / `gate.py`).
- **Reconciler (`coverage`)** — handbook-vs-codebase completeness: *uncodified
  code* (regularities with no norm) and *orphaned norms* (norms with no code).
  Delivered **continuously via the per-PR gate**, periodic pass as backstop. (repo
  `review.py` `DriftReviewer` is the seed.)

**Unification:** the per-PR constitutional delta is itself a **code-derived
Proposal** — the same `operation` model, the same conflict engine, the same review
surface as the PM flow; only the synthesis *input* differs (a diff vs an intent).
Flow A (intent-down) and Flow B (code-up) share the entire data + engine layer.

**Adjudication (Q17):** single strong model + adversarial self-verify on governed
conflicts/gaps. Panel deferred as an eval-driven optimization.

---

## 7. Trust, authority & enforcement

| | Advisory | Governed (decision) | Governed (principle) |
|---|---|---|---|
| origin | mined from code (descriptive) | intent/docs + assertion (prescriptive) | same |
| gate | none (auto, born active) | domain owner ratifies | higher bar, domain owner |
| authority (Q16) | — | product → PM, `technical` → EM | as decision, stricter |
| on PR conflict | informational | required, overridable | **blocking** |
| change | re-mined as code moves | `supersede` (case law) | rare, governed |

---

## 8. Data model

Reuse the v0.2 schema (`migrations/001_init.sql`); **additions for v1:**

- `norm.tier` — `advisory | governed`. Advisory norms skip the `proposed→active`
  gate (born `active`); governed follow the v0.2 lifecycle.
- `norm.support` — evidence/consistency metric for advisory (the N% + sample of
  code locations that vote for it); `code_ref`-linked.
- `proposal.origin` — `intent | pr`. The per-PR delta is a `pr`-origin proposal.
- `code_ref` on operations/edges already exists; the per-PR gate writes
  `implements` (code_ref→proposal) and `conflicts_with` edges as today.

Everything else — `operation` (typed, kind×op_type), `verdict`, the generic `edge`
graph, the five read paths (`active_norms`, `impact_of`, `provenance`,
`supersede_chain`, `proposal_review`) — is unchanged. The handbook is
`active_norms` partitioned by domain; the PM blast-radius is `impact_of` run
predictively.

---

## 9. Integrations

- **GitHub (Q13):** PR webhook → analyze net-diff → post a check + comment;
  push-analysis fallback for direct-to-`main`. Required status check, tiered
  blocking.
- **Embeddings (Q20):** OpenAI 1536 default, pluggable to local 768 (`embedding.py`).
- **Connectors priority (Q22):** **repo** (advisory mining + bootstrap) → **docs**
  (Notion/Confluence) → **Linear**.
- **MCP (`server.py`):** agent read access to norms + contract.
- **Stack (Q19):** FastAPI/Python + Postgres + pgvector + ltree.

---

## 10. Evals (non-negotiable — the engine is the product)

Three labeled sets + one check (Q18, extends v0.2 §7):
1. `operation × norm → verdict` — per-verdict precision/recall, special attention
   to **`gap` recall**.
2. **Synthesis** — intent → correct typed/targeted operations (garbage ops produce
   confident wrong checks).
3. **Per-PR delta** — diff → correct constitutional delta.
4. **Advisory mining precision** — a recorded convention must actually hold in code.

The structural layer (typing checks) is scored separately at ~100% precision. Every
engine/prompt/model change runs the sets as a regression gate. Production
resolutions (human-confirmed verdicts, PR overrides-with-rationale) grow the sets.

---

## 11. What exists vs. what we build

**Exists in the repo (reuse):** norm/operation/proposal types (`types.py`),
in-memory + Postgres stores (`store.py`), write gate (`gate.py`), conflict engine
(`conflict.py` / `engine.py`), bootstrap + promotion (`bootstrap.py`), resolution
(`resolution.py`), drift reviewer (`review.py`), code grounding (`grounding.py`),
codebase extraction (`codebase.py` / `extract.py`), eval harness (`eval.py`), MCP
server (`server.py`), web app + connect/synthesize/commit/review API (`web.py` /
`app.py`), migrations + read paths (`migrations/`, `queries/`).

**New for v1:**
1. **Tiered trust** — `norm.tier`, advisory lane (auto-active, code-evidenced),
   consistency-threshold miner.
2. **Per-PR constitutional gate** — GitHub webhook, diff→delta synthesis
   (`pr`-origin proposal), check + comment, required-status enforcement.
3. **Handbook UI** — two books, scoped sections, tier badges, supersede timelines,
   flag-to-propose; freshness/drift badges.
4. **PM living-PRD editor** — inline impact/gaps/conflicts, predictive blast-radius,
   layered plain-language presentation.
5. **Scope-bridged cross-domain retrieval** — amend §6 step-2 retrieval.
6. **Reconciler `coverage`** — uncodified-code / orphaned-norm metrics.
7. **Evals 2–4** + advisory precision check.

---

## 12. Milestones

1. **Tiered constitution + handbook (read).** `norm.tier`, advisory miner,
   handbook UI over `active_norms`. *Makes the asset visible.*
2. **Per-PR gate (the keystone).** GitHub app, diff→delta, check + comment,
   tiered blocking. *Delivers the EM value + the sync guarantee.*
3. **PM living-PRD editor.** Intent → impact/gaps/conflicts, scope-bridged
   retrieval, blast-radius. *Delivers the PM value.*
4. **Agents.** MCP read of norms/contract; gated agent proposals.
5. **Reconciler + coverage**, evals 2–4, connectors (docs → Linear).

Each milestone ships its slice of the eval set first.

---

## 13. Open knobs (resolve as conflicts arise)

- Advisory consistency threshold N% and how "relevant code" is scoped (Q3 sub-knob).
- Whether docs also feed *advisory* candidates or code-only (Q4 sub-knob).
- Panel adjudication if single-model `gap` recall underperforms (Q17).
- `implement` granularity tuning — per logical change is the default (Q21).

---

## 14. Risks

- **Advisory noise** → write-only sink. Mitigation: consistency threshold + advisory
  precision eval; advisory never blocks, so noise is low-cost.
- **PR gate false-blocks** → engineer distrust. Mitigation: only governed
  *conflicts* hard-block; everything else overridable; precision-gated rollout.
- **Synthesis garbage** → confident wrong reviews. Mitigation: synthesis eval (set 2),
  structural checks first.
- **Adoption** — engineers must accept reviewing the delta, not the diff. Mitigation:
  meet them in the PR comment; start as augmentation, not replacement.
