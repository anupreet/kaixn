"""Persistence for generated playbooks — the durable, agent-readable knowledge.

A *playbook* is the latest generated knowledge bundle for a repo: its domain
model (Mermaid + entities), engineering principles, and a full templated
document (PRD / Tech Spec) per feature/area. Regenerating a repo replaces its
bundle (old docs cascade-deleted).

Two implementations mirror the norm-store split:
  * ``PgPlaybookStore``      — Postgres (production; KAIXN_DSN set)
  * ``InMemoryPlaybookStore``— offline / tests (resets on restart)

``from_env`` picks between them. Agents read a repo back via :meth:`get_playbook`
/ :meth:`get_doc` to evaluate a proposed PRD or a posted PR against what the
codebase actually is. The Pg store self-creates its tables on connect (the
startup migration runner only owns 001); ``migrations/002_playbook.sql`` is the
canonical record of the same schema.
"""

from __future__ import annotations

import json
import os

_DDL = """
CREATE TABLE IF NOT EXISTS playbook (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    repo        text UNIQUE NOT NULL,
    llm         boolean,
    mermaid     text,
    entities    jsonb NOT NULL DEFAULT '[]'::jsonb,
    principles  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS playbook_doc (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    playbook_id uuid NOT NULL REFERENCES playbook(id) ON DELETE CASCADE,
    repo        text NOT NULL,
    kind        text NOT NULL,                 -- 'prd' | 'spec'
    slug        text NOT NULL,
    title       text NOT NULL,
    summary     text NOT NULL DEFAULT '',
    markdown    text NOT NULL,
    principles  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (repo, kind, slug)
);
CREATE INDEX IF NOT EXISTS playbook_doc_repo_idx ON playbook_doc (repo);
"""


# --- in-memory (offline / tests) -------------------------------------------
class InMemoryPlaybookStore:
    """Dict-backed store. Same surface as the Pg one; resets on restart."""

    def __init__(self) -> None:
        self._pb: dict[str, dict] = {}                 # repo -> bundle meta
        self._docs: dict[tuple[str, str, str], dict] = {}  # (repo,kind,slug) -> doc

    def create_playbook(self, repo: str, *, llm: bool) -> str:
        for key in [k for k in self._docs if k[0] == repo]:
            del self._docs[key]
        self._pb[repo] = {"repo": repo, "llm": llm, "mermaid": None,
                          "entities": [], "principles": []}
        return repo                                    # repo doubles as the id

    def update_playbook(self, pid: str, *, mermaid=None, entities=None,
                        principles=None) -> None:
        pb = self._pb.setdefault(pid, {"repo": pid})
        if mermaid is not None:
            pb["mermaid"] = mermaid
        if entities is not None:
            pb["entities"] = entities
        if principles is not None:
            pb["principles"] = principles

    def save_doc(self, pid: str, *, repo: str, kind: str, slug: str, title: str,
                 summary: str, markdown: str, principles: list) -> None:
        self._docs[(repo, kind, slug)] = {
            "repo": repo, "kind": kind, "slug": slug, "title": title,
            "summary": summary, "markdown": markdown, "principles": principles}

    def get_doc(self, repo: str, kind: str, slug: str) -> dict | None:
        return self._docs.get((repo, kind, slug))

    def get_playbook(self, repo: str) -> dict | None:
        pb = self._pb.get(repo)
        if pb is None:
            return None
        docs = [{k: d[k] for k in ("kind", "slug", "title", "summary", "principles")}
                for (r, _, _), d in self._docs.items() if r == repo]
        return {**pb, "docs": docs}

    def list_repos(self) -> list[dict]:
        out = []
        for repo, pb in self._pb.items():
            n = sum(1 for (r, _, _) in self._docs if r == repo)
            out.append({"repo": repo, "n_docs": n, "llm": pb.get("llm")})
        return sorted(out, key=lambda r: r["repo"])


# --- Postgres (production) -------------------------------------------------
class PgPlaybookStore:
    """Postgres-backed store. ``conn`` is an autocommit psycopg connection."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self._conn.execute(_DDL)

    def create_playbook(self, repo: str, *, llm: bool) -> str:
        self._conn.execute("DELETE FROM playbook WHERE repo = %s", (repo,))
        return self._conn.execute(
            "INSERT INTO playbook (repo, llm) VALUES (%s, %s) RETURNING id::text",
            (repo, llm),
        ).fetchone()[0]

    def update_playbook(self, pid: str, *, mermaid=None, entities=None,
                        principles=None) -> None:
        self._conn.execute(
            """
            UPDATE playbook SET
              mermaid    = COALESCE(%s, mermaid),
              entities   = COALESCE(%s::jsonb, entities),
              principles = COALESCE(%s::jsonb, principles),
              updated_at = now()
            WHERE id = %s::uuid
            """,
            (mermaid,
             json.dumps(entities) if entities is not None else None,
             json.dumps(principles) if principles is not None else None,
             pid),
        )

    def save_doc(self, pid: str, *, repo: str, kind: str, slug: str, title: str,
                 summary: str, markdown: str, principles: list) -> None:
        self._conn.execute(
            """
            INSERT INTO playbook_doc
              (playbook_id, repo, kind, slug, title, summary, markdown, principles)
            VALUES (%s::uuid, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (repo, kind, slug) DO UPDATE SET
              title = EXCLUDED.title, summary = EXCLUDED.summary,
              markdown = EXCLUDED.markdown, principles = EXCLUDED.principles
            """,
            (pid, repo, kind, slug, title, summary, markdown,
             json.dumps(principles or [])),
        )

    def get_doc(self, repo: str, kind: str, slug: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT repo, kind, slug, title, summary, markdown, principles
            FROM playbook_doc WHERE repo = %s AND kind = %s AND slug = %s
            """,
            (repo, kind, slug),
        ).fetchone()
        if not row:
            return None
        return {"repo": row[0], "kind": row[1], "slug": row[2], "title": row[3],
                "summary": row[4], "markdown": row[5], "principles": row[6]}

    def get_playbook(self, repo: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id::text, repo, llm, mermaid, entities, principles "
            "FROM playbook WHERE repo = %s", (repo,),
        ).fetchone()
        if not row:
            return None
        docs = self._conn.execute(
            "SELECT kind, slug, title, summary, principles FROM playbook_doc "
            "WHERE repo = %s ORDER BY kind, title", (repo,),
        ).fetchall()
        return {
            "repo": row[1], "llm": row[2], "mermaid": row[3],
            "entities": row[4], "principles": row[5],
            "docs": [{"kind": d[0], "slug": d[1], "title": d[2],
                      "summary": d[3], "principles": d[4]} for d in docs],
        }

    def list_repos(self) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT p.repo, p.llm, p.updated_at::text, count(d.id) AS n
            FROM playbook p LEFT JOIN playbook_doc d ON d.repo = p.repo
            GROUP BY p.repo, p.llm, p.updated_at
            ORDER BY p.updated_at DESC
            """
        ).fetchall()
        return [{"repo": r[0], "llm": r[1], "updated_at": r[2], "n_docs": r[3]}
                for r in rows]


def from_env() -> InMemoryPlaybookStore | PgPlaybookStore:
    """Pg store when KAIXN_DSN is set (self-creates its tables), else in-memory."""
    dsn = os.getenv("KAIXN_DSN")
    if not dsn:
        return InMemoryPlaybookStore()
    import psycopg

    return PgPlaybookStore(psycopg.connect(dsn, autocommit=True))
