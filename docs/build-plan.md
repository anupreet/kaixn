# kaixn — Build Plan

**Status:** active · **Owner:** anupreet · **Date:** 2026-06-12
**Source of truth for sequencing.** Implements `docs/prd.md` + `docs/tech-spec.md`;
decisions traced to `docs/prd-open-questions.md`.

---

## Surface build order (decided 2026-06-12)

1. **Handbook** ← building first
2. **Per-PR constitutional gate** (keystone EM behavior)
3. **PM living-PRD editor**
4. **Agents (MCP read + gated propose) + authority**
5. **Reconciler + coverage + connectors**

**Why Handbook first:** it's the hard dependency every other surface reads; it's
the lowest-risk, fastest-to-tangible surface; it de-risks the advisory notability
threshold (Q3) on a *read-only* surface before that same miner drives a *blocking*
PR gate; and it's the input (the norms) that the PM editor and PR gate compare
against. The per-PR gate is the keystone but wants a populated, trusted
constitution underneath it, so it goes second.

**Approach:** **offline-first** (in-memory store, no Postgres) to match the repo's
existing philosophy (`/api/connect` + bootstrap + UI already run keyless). The
Postgres migration (`002_tiered_trust`) lands once the shape is proven.

---

## Milestone 1 — Handbook MVP

**Goal:** point kaixn at a repo → it mines the constitution (advisory conventions +
governed decisions) → renders two books with tier badges, provenance, and supersede
timelines. Tangible proof the asset is real. Lead with the **Engineering** book.

### Steps (ordered)

- [ ] **1. Tier in the model.** Add `tier: advisory | governed` to
  `NormCandidate`/`NormRecord` (`types.py`); advisory norms born `active`.
  In-memory store only; Pg migration deferred. *Unblocks everything below.*

- [ ] **2. Advisory miner.** Start from the deterministic structural detectors in
  `codebase.py` (deps / layout / tooling) re-tagged `tier=advisory`; add 2–3
  **lexical regularity detectors** (naming casing, test-file mirroring,
  import/error-shape) computing `support = matches / sites`, emitting only above
  the **threshold**. *This is where the Q3 risk lives — prove it read-only here.*

- [ ] **3. Constitution population flow.** Wire mining into the connect-repo path:
  advisory (miner, auto-active) + governed (existing doc/code extraction → proposed
  queue via the write gate). Reuse `bootstrap`.

- [ ] **4. Handbook read API + UI.** Two books (Engineering = `technical`; Product =
  `product`+`product_design`+`ux`), grouped by scope subtree. Each entry:
  **tier badge** (observed-convention vs ratified-principle/decision), **provenance**
  (source commit/file), **supersede timeline**, **freshness** ("synced to commit X").
  Extend the FastAPI app + `static/`.

- [ ] **5. Flag-to-propose** (fast-follow). Opens a governed `modify`/`supersede`
  proposal into the review queue. The *read* is the core deliverable; this can slip
  to 4.1.

### Micro-decisions (defaults — tune against real output)
- **Advisory threshold:** 0.8 consistency.
- **Miner v1 detectors:** structural (free) + naming-casing + test-mirroring; LLM
  deep-path deferred.
- **Provenance granularity:** file-level evidence for advisory + commit sha for
  freshness; line ranges later.

### Done when
- Connecting this repo yields a populated Engineering handbook whose advisory
  entries are *true* (manual spot-check) and whose governed entries are the
  human-curated ones — with tier badges, provenance, and at least one supersede
  timeline rendered.
- Advisory-mining precision spot-checked; threshold feels right (no obvious noise).

---

## Tracking
- Decisions: `docs/prd-open-questions.md` (all 22 decided).
- This plan supersedes the milestone list in `prd.md §12` / `tech-spec.md §12` for
  *sequencing detail*; those remain the conceptual reference.
- Parked: doc-mining **progress indicator** (the original request) — revisit after M1.
