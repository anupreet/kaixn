<div align="center">
  <img src="assets/logo-lockup.svg" alt="kaixn" width="200">
  <p><i>Review the plan, not the PR.</i></p>
</div>

A product development layer for the agentic world. The reviewable artifact
moves up the stack — from the PR/diff to the **Proposal**. Product managers
write an *intent*; kaixn synthesizes a **Proposal** (a structured set of typed
operations against the team's persisted constitution and codebase), validates it
against everything already decided *before* a coding agent writes a line, and
emits a grounded contract the agent implements. **PR review becomes Proposal
review.**

## Run it (web UI, end to end)

A FastAPI app serves a UI where you paste a **GitHub URL**, mine its docs into a
draft constitution, write an intent, review the synthesized **Proposal** +
conflict report, commit it, and run a drift review — the whole loop in the browser.

**Local, with Postgres, in one command:**

```bash
cp .env.example .env          # optionally add ANTHROPIC_API_KEY / OPENAI_API_KEY
docker compose up --build     # web + pgvector Postgres
open http://localhost:8000
```

With no API keys it runs in deterministic **offline mode** (fake embedder +
structural checks). Add `ANTHROPIC_API_KEY` for LLM synthesis/adjudication, and
set `KAIXN_EMBEDDER=openai` (+ `OPENAI_API_KEY`) for real semantic retrieval.

**Without Docker** (in-memory store, no DB):

```bash
pip install -e '.[web]'
kaixn-web                     # http://localhost:8000
```

Point it at your own Postgres by setting `KAIXN_DSN` and applying the schema:
`python scripts/apply_migrations.py "$KAIXN_DSN"`.

**Deploy to AWS:** ECS Fargate + ALB + RDS Postgres in one stack — see
[`deploy/README.md`](deploy/README.md).

### HTTP API

`GET /api/status` · `POST /api/connect {repo_url}` · `GET /api/norms` ·
`POST /api/norms/{id}/promote` · `POST /api/proposals {intent}` ·
`POST /api/proposals/{id}/resolve` · `POST /api/proposals/{id}/commit` ·
`POST /api/proposals/{id}/review`. Interactive docs at `/docs`.

## The three layers

1. **Code** — lives in GitHub (the implementation; kaixn only points at it).
2. **Memory / constitution** — persisted **principles** (durable, normative) and
   **decisions** (specific, supersedable) across four domains: product,
   technical, product-design, ux. Persist across features.
3. **Proposal** — per-feature intent + the structured plan (typed operations)
   that a coding agent consumes.

The value isn't the three stores — it's the **provenance edges** between them:
forward ("why is this code like this?" → Proposal → operation → norm) and
reverse ("we want to change this norm → which Proposals are at risk?").

## Core model (v0.2)

The plan, not the spec, is the structured artifact kaixn owns — because
**conflict is a property of change-against-state, and only a plan expresses
change-against-state.** A Proposal is an ordered set of typed **operations**:

| kind | op_type | meaning |
|---|---|---|
| norm | `assert` / `modify` / `deprecate` / `supersede` | change the constitution |
| code | `implement` | a grounded change to the codebase |

Typing lets a whole class of conflicts be caught *structurally* (deterministic,
before any LLM runs); the LLM only adjudicates the semantic cases.

## Layout

- `docs/architecture.md` — the full spec (v0.2). Start here.
- `migrations/001_init.sql` — canonical data model (Postgres + pgvector + ltree):
  norms, proposals, operations, verdicts, and one generic `edge` graph table.
  Append-only / versioned; `hnsw` vector indexes; generated `tsvector`; CHECK
  constraints enforce op typing.
- `queries/read_paths.sql` — the five queries the schema exists to serve:
  `active_norms`, `impact_of`, `provenance`, `supersede_chain`, `proposal_review`.
- `docs/bootstrap.md` — mining an existing product's principles into the
  constitution on day one.

## Design principles 

- **Atomic records** — one claim per norm, one change per operation; checkable.
- **No write-only memory** — every record sits on a defined read path.
- **Append-only** — supersede, never mutate; the timeline stays queryable.
- **Conflict engine is the product** — needs an eval harness from day one, and
  it gets reused to keep the constitution self-consistent.

## Build sequence

1. ✅ Memory model + provenance graph (schema, read paths, bootstrap design).
2. Memory API + write gate.
3. Proposal + operation model (intent → synthesized typed operations).
4. Conflict engine + eval harness (structural checks first, then adjudicate).
5. Resolution loop (edit-intent / override-as-`supersede`).
6. Code grounding + agent_contract render + GitHub integration.
7. Bootstrap pipeline.
8. Drift reconciliation.
