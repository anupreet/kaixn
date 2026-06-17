-- kaixn — generated knowledge layer
-- Migration 002: persisted playbooks (PRDs, tech specs, domain model, principles)
--
-- The playbook is the durable, agent-readable knowledge generated from a repo:
-- a domain model + engineering principles + one full templated document per
-- feature/area. Agents read it back to evaluate a proposed PRD or a posted PR.
--
-- NOTE: PgPlaybookStore self-creates these tables (CREATE TABLE IF NOT EXISTS)
-- on connect, because the startup migration runner only owns 001. This file is
-- the canonical record of that schema; keep the two in sync.
--
-- One bundle per repo (repo UNIQUE); regenerating replaces it and the docs
-- cascade-delete.

BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

CREATE TABLE IF NOT EXISTS playbook (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo        text UNIQUE NOT NULL,
    llm         boolean,
    mermaid     text,                       -- domain model (Mermaid classDiagram)
    entities    jsonb NOT NULL DEFAULT '[]'::jsonb,
    principles  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS playbook_doc (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    playbook_id uuid NOT NULL REFERENCES playbook(id) ON DELETE CASCADE,
    repo        text NOT NULL,
    kind        text NOT NULL,              -- 'prd' | 'spec'
    slug        text NOT NULL,
    title       text NOT NULL,
    summary     text NOT NULL DEFAULT '',
    markdown    text NOT NULL,
    principles  jsonb NOT NULL DEFAULT '[]'::jsonb,
    grp         text NOT NULL DEFAULT '',       -- the area this nests under ('' = top level)
    seq         integer NOT NULL DEFAULT 0,     -- display order within the kind
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repo, kind, slug)
);

CREATE INDEX IF NOT EXISTS playbook_doc_repo_idx ON playbook_doc (repo);
-- additive columns for the nested view (idempotent on pre-existing tables)
ALTER TABLE playbook_doc ADD COLUMN IF NOT EXISTS grp text NOT NULL DEFAULT '';
ALTER TABLE playbook_doc ADD COLUMN IF NOT EXISTS seq integer NOT NULL DEFAULT 0;

COMMIT;
