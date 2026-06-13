#!/usr/bin/env python3
"""Apply kaixn migrations to a Postgres database, idempotently.

The canonical schema (migrations/001_init.sql) declares `vector(1536)` for the
OpenAI embedder. The active embedder's dimension may differ (fake=64,
nomic-embed-text=768), so we rewrite the vector width to match
`get_embedder().dim` before executing — otherwise inserts fail with a dimension
mismatch. This keeps the Postgres path working end-to-end with *any* embedder.

Usage:
    KAIXN_DSN=postgresql://... python scripts/apply_migrations.py
    python scripts/apply_migrations.py "postgresql://user:pass@host/db"

Re-running is safe: if the `norm` table already exists we skip (the schema is
not versioned in-DB yet; drop the database to re-apply a changed schema).
"""

from __future__ import annotations

import os
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))


def main(argv: list[str]) -> int:
    dsn = (argv[0] if argv else os.getenv("KAIXN_DSN"))
    if not dsn:
        print("error: pass a DSN or set KAIXN_DSN", file=sys.stderr)
        return 2

    from kaixn.embedding import get_embedder

    dim = getattr(get_embedder(), "dim", 1536)
    sql = (ROOT / "migrations" / "001_init.sql").read_text()
    if dim != 1536:
        sql = re.sub(r"vector\(1536\)", f"vector({dim})", sql)
        print(f"• rewrote embedding width 1536 -> {dim} to match embedder")

    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT to_regclass('public.norm') IS NOT NULL"
        ).fetchone()[0]
        if exists:
            # The embedding column carries its dimension in atttypmod. If it
            # already matches the active embedder, we're done. If it differs
            # (someone swapped embedders) we can only recreate when the schema
            # holds no data — never silently drop real rows.
            cur_dim = conn.execute(
                "SELECT a.atttypmod FROM pg_attribute a "
                "JOIN pg_class c ON a.attrelid = c.oid "
                "JOIN pg_namespace n ON c.relnamespace = n.oid "
                "WHERE n.nspname = 'public' AND c.relname = 'norm' "
                "AND a.attname = 'embedding'"
            ).fetchone()[0]
            if cur_dim == dim:
                print("• schema already present (norm table exists) — skipping")
                return 0
            rows = conn.execute(
                "SELECT (SELECT count(*) FROM norm) + (SELECT count(*) FROM operation)"
            ).fetchone()[0]
            if rows:
                print(
                    f"error: embedding dim mismatch (db={cur_dim}, embedder={dim}) "
                    f"but {rows} row(s) present — refusing to drop data. Migrate the "
                    f"rows or drop the database manually.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"• embedding dim mismatch (db={cur_dim} -> {dim}) and schema is "
                f"empty — recreating public schema"
            )
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        conn.execute(sql)
        print("• migration 001 applied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
