-- kaixn — product development layer
-- Migration 001: memory model + provenance graph  (v0.2)
--
-- v0.2 core change: the *plan* is the owned, structured artifact, not the spec.
--   * `spec` / `spec_claim` are retired.
--   * intent (disposable input) lives on `proposal`; the plan is an ordered set
--     of typed `operation`s (change-against-state).
--   * conflict is adjudicated per (operation × norm) into a `verdict` table.
--
-- One Postgres, three jobs:
--   relational  -> norms / proposals / operations with lifecycle
--   graph       -> generic `edge` table (provenance, supersede, impact)
--   retrieval   -> pgvector (semantic) + tsvector (lexical) hybrid
--
-- Conventions:
--   * Append-only / versioned. Nothing is mutated in place; superseded records
--     get status='superseded' and a `supersedes` edge from the replacement.
--   * `domain` = discipline (product / technical / product_design / ux) [fixed]
--   * `scope`  = product area as an ltree path rooted at `all`. `all` = global.
--     domain and scope are orthogonal.

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;   -- pgvector (>= 0.5 for hnsw)
CREATE EXTENSION IF NOT EXISTS ltree;
CREATE EXTENSION IF NOT EXISTS pgcrypto; -- gen_random_uuid()

-- Embedding width: 1536 = OpenAI text-embedding-3-small. Change here (and the
-- two vector() columns) if you swap embedders, e.g. 768 for nomic-embed-text.

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------
CREATE TYPE norm_kind        AS ENUM ('principle', 'decision');
CREATE TYPE domain           AS ENUM ('product', 'technical', 'product_design', 'ux');
CREATE TYPE norm_status      AS ENUM ('proposed', 'active', 'superseded', 'deprecated');
CREATE TYPE example_polarity AS ENUM ('positive', 'negative');
CREATE TYPE work_status      AS ENUM ('active', 'shipped', 'archived');

-- Proposal + operation lifecycle (the v0.2 core)
CREATE TYPE prop_status AS ENUM ('draft', 'in_review', 'accepted', 'superseded');
CREATE TYPE op_kind     AS ENUM ('norm', 'code');
CREATE TYPE op_type     AS ENUM ('assert', 'modify', 'deprecate', 'supersede', 'implement');
CREATE TYPE op_status   AS ENUM
  ('proposed', 'accepted', 'rejected', 'applied', 'conflict', 'needs_grounding');
CREATE TYPE verdict_t   AS ENUM ('consistent', 'conflict', 'tension', 'gap');

-- Node + edge taxonomy for the provenance graph
CREATE TYPE node_type AS ENUM
  ('initiative', 'feature', 'proposal', 'operation', 'norm', 'code_ref');

CREATE TYPE edge_rel AS ENUM (
  'supersedes',     -- norm      -> norm       (case-law evolution)
  'depends_on',     -- operation -> norm       (relied on / must respect this norm)
  'creates',        -- operation -> norm       (an `assert` minted this norm)
  'amends',         -- operation -> norm       (a `modify` changed this norm)
  'conflicts_with', -- operation -> norm       (recorded conflict + resolution)
  'implements',     -- code_ref  -> proposal   (PR fulfils the Proposal)
  'relates_to'      -- norm      -> norm       (soft link)
);

-- ---------------------------------------------------------------------------
-- Work hierarchy:  initiative (epic) -> feature -> proposal -> operation
-- ---------------------------------------------------------------------------
CREATE TABLE initiative (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  title       text NOT NULL,
  description text NOT NULL DEFAULT '',
  owner       text NOT NULL DEFAULT '',
  status      work_status NOT NULL DEFAULT 'active',
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE feature (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  initiative_id uuid REFERENCES initiative(id) ON DELETE SET NULL,
  title         text NOT NULL,
  owner         text NOT NULL DEFAULT '',
  status        work_status NOT NULL DEFAULT 'active',
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_feature_initiative ON feature(initiative_id);

-- ---------------------------------------------------------------------------
-- The constitution + case law:  norm (principle | decision)
-- Provenance ("which operation minted this") lives in `edge` via creates/amends.
-- ---------------------------------------------------------------------------
CREATE TABLE norm (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  kind       norm_kind   NOT NULL,
  domain     domain      NOT NULL,
  statement  text        NOT NULL,          -- ONE atomic normative claim
  rationale  text        NOT NULL DEFAULT '',
  scope      ltree       NOT NULL DEFAULT 'all',
  status     norm_status NOT NULL DEFAULT 'proposed',
  version    int         NOT NULL DEFAULT 1,
  author     text        NOT NULL DEFAULT '',
  embedding  vector(1536),
  search_tsv tsvector GENERATED ALWAYS AS
             (to_tsvector('english',
                coalesce(statement, '') || ' ' || coalesce(rationale, ''))) STORED,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_norm_domain_status ON norm(domain, status);
CREATE INDEX idx_norm_scope         ON norm USING gist (scope);
CREATE INDEX idx_norm_vec           ON norm USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_norm_tsv           ON norm USING gin (search_tsv);

CREATE TABLE norm_example (
  id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  norm_id  uuid NOT NULL REFERENCES norm(id) ON DELETE CASCADE,
  polarity example_polarity NOT NULL,
  text     text NOT NULL
);
CREATE INDEX idx_norm_example_norm ON norm_example(norm_id);

-- ---------------------------------------------------------------------------
-- Proposal (the owned plan) — intent + a generated agent_contract (a rendering)
-- ---------------------------------------------------------------------------
CREATE TABLE proposal (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  feature_id     uuid NOT NULL REFERENCES feature(id) ON DELETE CASCADE,
  version        int  NOT NULL DEFAULT 1,
  intent_text    text NOT NULL DEFAULT '',   -- the raw PM ask it was synthesized from
  agent_contract text NOT NULL DEFAULT '',   -- rendered markdown view of operations
  status         prop_status NOT NULL DEFAULT 'draft',
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (feature_id, version)
);
CREATE INDEX idx_proposal_feature ON proposal(feature_id);

-- ---------------------------------------------------------------------------
-- Operation — a single typed change-against-state. Replaces spec_claim.
-- The PM never authors these; they are synthesized from intent and reviewed.
-- ---------------------------------------------------------------------------
CREATE TABLE operation (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  proposal_id uuid NOT NULL REFERENCES proposal(id) ON DELETE CASCADE,
  kind        op_kind   NOT NULL,
  op_type     op_type   NOT NULL,
  statement   text      NOT NULL,
  rationale   text      NOT NULL DEFAULT '',   -- quote/justification from intent
  ord         int       NOT NULL DEFAULT 0,
  status      op_status NOT NULL DEFAULT 'proposed',

  -- norm-op fields
  target_norm_id   uuid REFERENCES norm(id) ON DELETE SET NULL,  -- modify/deprecate/supersede
  produced_norm_id uuid REFERENCES norm(id) ON DELETE SET NULL,  -- set when assert/supersede mints

  -- code-op fields
  target_location     text NOT NULL DEFAULT '',
  before_state        text NOT NULL DEFAULT '',
  after_state         text NOT NULL DEFAULT '',
  acceptance_criteria text NOT NULL DEFAULT '',

  embedding  vector(1536),
  search_tsv tsvector GENERATED ALWAYS AS
             (to_tsvector('english',
                coalesce(statement, '') || ' ' || coalesce(rationale, ''))) STORED,
  created_at timestamptz NOT NULL DEFAULT now(),

  -- Typing coherence: norm-ops vs code-ops use disjoint op_types.
  CONSTRAINT op_kind_type_coherent CHECK (
    (kind = 'norm' AND op_type IN ('assert', 'modify', 'deprecate', 'supersede'))
    OR (kind = 'code' AND op_type = 'implement')
  ),
  -- modify/deprecate/supersede must name a target; assert must not.
  CONSTRAINT op_target_present CHECK (
    (op_type IN ('modify', 'deprecate', 'supersede') AND target_norm_id IS NOT NULL)
    OR (op_type NOT IN ('modify', 'deprecate', 'supersede'))
  ),
  CONSTRAINT op_assert_no_target CHECK (
    op_type <> 'assert' OR target_norm_id IS NULL
  )
);
CREATE INDEX idx_operation_proposal ON operation(proposal_id);
CREATE INDEX idx_operation_status   ON operation(status);
CREATE INDEX idx_operation_vec      ON operation USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_operation_tsv      ON operation USING gin (search_tsv);

-- ---------------------------------------------------------------------------
-- Code layer pointer (source of truth stays in GitHub)
-- ---------------------------------------------------------------------------
CREATE TABLE code_ref (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  repo       text NOT NULL,
  ref_type   text NOT NULL CHECK (ref_type IN ('pr', 'commit')),
  ref        text NOT NULL,   -- PR number or commit sha
  path       text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Verdict — one row per (operation × norm) adjudication.
-- The conflict report AND the eval signal.
-- ---------------------------------------------------------------------------
CREATE TABLE verdict (
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operation_id        uuid NOT NULL REFERENCES operation(id) ON DELETE CASCADE,
  norm_id             uuid NOT NULL REFERENCES norm(id) ON DELETE CASCADE,
  verdict             verdict_t NOT NULL,
  evidence            jsonb NOT NULL DEFAULT '{}',  -- cited spans (op clause + norm)
  proposed_resolution text NOT NULL DEFAULT '',
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_verdict_operation ON verdict(operation_id);
CREATE INDEX idx_verdict_norm      ON verdict(norm_id);

-- ---------------------------------------------------------------------------
-- The provenance graph — one generic edge table
-- ---------------------------------------------------------------------------
CREATE TABLE edge (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  src_id     uuid NOT NULL,
  src_type   node_type NOT NULL,
  dst_id     uuid NOT NULL,
  dst_type   node_type NOT NULL,
  rel_type   edge_rel NOT NULL,
  metadata   jsonb NOT NULL DEFAULT '{}',  -- e.g. {resolution, confidence, decided_by}
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (src_id, dst_id, rel_type)
);
CREATE INDEX idx_edge_src ON edge(src_id, rel_type);
CREATE INDEX idx_edge_dst ON edge(dst_id, rel_type);
CREATE INDEX idx_edge_rel ON edge(rel_type);

COMMIT;
