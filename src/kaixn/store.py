"""Read access to the constitution for the write gate / conflict engine.

`NormReader` is the seam: the gate depends on it, the in-memory impl makes the
POC and tests run with no Postgres, and `PgNormReader` is the production path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from kaixn.embedding import Embedder
from kaixn.types import NormCandidate, NormRecord


@dataclass(slots=True)
class Edge:
    src_id: str
    src_type: str
    dst_id: str
    dst_type: str
    rel_type: str
    metadata: dict[str, Any] = field(default_factory=dict)


def scope_governs(norm_scope: str, target_scope: str) -> bool:
    """ltree `@>` semantics: is norm_scope an ancestor-or-equal of target?

    A norm at all.product.billing governs all.product.billing.subscriptions;
    a norm at `all` governs everything.
    """
    if norm_scope in ("all", target_scope):
        return True
    return target_scope.startswith(norm_scope + ".")


class NormReader(Protocol):
    def neighbors(self, candidate: NormCandidate, *, top_k: int) -> list[NormRecord]:
        """Most similar active norms in the candidate's domain."""

    def active_principles(self, domain: str, scope: str) -> list[NormRecord]:
        """All active principles whose scope governs `scope` (always checked)."""

    def get(self, norm_id: str) -> NormRecord | None:
        """Fetch a norm by id (any status), or None — for target resolution."""


class InMemoryNormReader:
    """Backs the POC and tests. Similarity computed in Python."""

    def __init__(self, embedder: Embedder) -> None:
        self._embedder = embedder
        self._norms: list[NormRecord] = []

    def add(self, record: NormRecord) -> NormRecord:
        if record.embedding is None:
            record.embedding = self._embedder.embed([record.statement])[0]
        self._norms.append(record)
        return record

    def neighbors(self, candidate: NormCandidate, *, top_k: int) -> list[NormRecord]:
        from kaixn.similarity import cosine, jaccard

        if candidate.embedding is None:
            candidate.embedding = self._embedder.embed([candidate.statement])[0]
        scored = []
        for n in self._norms:
            if n.status != "active" or n.domain != candidate.domain:
                continue
            score = max(cosine(candidate.embedding, n.embedding),
                        jaccard(candidate.statement, n.statement))
            scored.append((score, n))
        scored.sort(key=lambda s: s[0], reverse=True)
        return [n for _, n in scored[:top_k]]

    def active_principles(self, domain: str, scope: str) -> list[NormRecord]:
        return [n for n in self._norms
                if n.status == "active" and n.kind == "principle"
                and n.domain == domain and scope_governs(n.scope, scope)]

    def get(self, norm_id: str) -> NormRecord | None:
        return next((n for n in self._norms if n.id == norm_id), None)


class InMemoryStore(InMemoryNormReader):
    """Read + write + edges, for the POC. Mirrors what PgStore will do over the
    v0.2 schema (append-only norms, status flips, generic edge rows)."""

    def __init__(self, embedder: Embedder) -> None:
        super().__init__(embedder)
        self.edges: list[Edge] = []
        self._counter = 0

    def next_id(self) -> str:
        self._counter += 1
        return f"norm-{self._counter:04d}"

    def add_norm(self, candidate: NormCandidate, *, status: str = "active") -> NormRecord:
        rec = NormRecord(
            id=self.next_id(), statement=candidate.statement,
            domain=candidate.domain, scope=candidate.scope, kind=candidate.kind,
            rationale=candidate.rationale, status=status,
            embedding=candidate.embedding,
        )
        if rec.embedding is None:
            rec.embedding = self._embedder.embed([rec.statement])[0]
        self._norms.append(rec)
        return rec

    def set_status(self, norm_id: str, status: str) -> None:
        n = self.get(norm_id)
        if n is not None:
            n.status = status

    def add_edge(self, src_id: str, src_type: str, dst_id: str, dst_type: str,
                 rel_type: str, metadata: dict | None = None) -> Edge:
        e = Edge(src_id, src_type, dst_id, dst_type, rel_type, metadata or {})
        self.edges.append(e)
        return e

    def supersede_chain(self, norm_id: str) -> list[str]:
        """Norm ids that (transitively) superseded this one — newest last."""
        chain: list[str] = []
        cur = norm_id
        seen = {cur}
        while True:
            nxt = next((e.src_id for e in self.edges
                        if e.rel_type == "supersedes" and e.dst_id == cur
                        and e.src_id not in seen), None)
            if nxt is None:
                break
            chain.append(nxt)
            seen.add(nxt)
            cur = nxt
        return chain


class PgNormReader:
    """Production reader over the v0.2 schema (pgvector + ltree).

    `conn` is a psycopg connection. Embeddings are passed as pgvector params.
    """

    def __init__(self, conn, embedder: Embedder) -> None:
        self._conn = conn
        self._embedder = embedder

    def neighbors(self, candidate: NormCandidate, *, top_k: int) -> list[NormRecord]:
        if candidate.embedding is None:
            candidate.embedding = self._embedder.embed([candidate.statement])[0]
        rows = self._conn.execute(
            """
            SELECT id::text, statement, domain::text, scope::text,
                   kind::text, rationale
            FROM   norm
            WHERE  status = 'active' AND domain = %s
            ORDER  BY embedding <=> %s        -- cosine distance (pgvector)
            LIMIT  %s
            """,
            (candidate.domain, candidate.embedding, top_k),
        ).fetchall()
        return [NormRecord(*r) for r in rows]

    def active_principles(self, domain: str, scope: str) -> list[NormRecord]:
        rows = self._conn.execute(
            """
            SELECT id::text, statement, domain::text, scope::text,
                   kind::text, rationale, status::text
            FROM   norm
            WHERE  status = 'active' AND kind = 'principle'
              AND  domain = %s AND scope @> %s::ltree
            """,
            (domain, scope),
        ).fetchall()
        return [NormRecord(*r) for r in rows]

    def get(self, norm_id: str) -> NormRecord | None:
        row = self._conn.execute(
            """
            SELECT id::text, statement, domain::text, scope::text,
                   kind::text, rationale, status::text
            FROM   norm WHERE id = %s::uuid
            """,
            (norm_id,),
        ).fetchone()
        return NormRecord(*row) if row else None


def pg_connect(dsn: str):
    """Open a psycopg connection with the pgvector adapter registered."""
    import psycopg
    from pgvector.psycopg import register_vector

    conn = psycopg.connect(dsn, autocommit=True)
    register_vector(conn)
    return conn


class PgStore(PgNormReader):
    """Writable store over the v0.2 schema. Append-only norms, status flips,
    generic edges — the production mirror of InMemoryStore.

    NOTE: not exercised in the offline test suite (needs a live Postgres with
    the 001 migration applied + pgvector/ltree). See `tests/test_pg.py`, which
    runs only when KAIXN_TEST_DSN is set.
    """

    def add_norm(self, candidate: NormCandidate, *, status: str = "active") -> NormRecord:
        if candidate.embedding is None:
            candidate.embedding = self._embedder.embed([candidate.statement])[0]
        row = self._conn.execute(
            """
            INSERT INTO norm (kind, domain, statement, rationale, scope,
                              status, embedding)
            VALUES (%s, %s, %s, %s, %s::ltree, %s, %s)
            RETURNING id::text, statement, domain::text, scope::text,
                      kind::text, rationale, status::text
            """,
            (candidate.kind, candidate.domain, candidate.statement,
             candidate.rationale, candidate.scope, status, candidate.embedding),
        ).fetchone()
        rec = NormRecord(*row)
        rec.embedding = candidate.embedding
        return rec

    def set_status(self, norm_id: str, status: str) -> None:
        self._conn.execute("UPDATE norm SET status = %s WHERE id = %s::uuid",
                           (status, norm_id))

    def add_edge(self, src_id: str, src_type: str, dst_id: str, dst_type: str,
                 rel_type: str, metadata: dict | None = None) -> None:
        import json

        self._conn.execute(
            """
            INSERT INTO edge (src_id, src_type, dst_id, dst_type, rel_type, metadata)
            VALUES (%s::uuid, %s, %s::uuid, %s, %s, %s)
            ON CONFLICT (src_id, dst_id, rel_type) DO NOTHING
            """,
            (src_id, src_type, dst_id, dst_type, rel_type,
             json.dumps(metadata or {})),
        )

    def supersede_chain(self, norm_id: str) -> list[str]:
        rows = self._conn.execute(
            """
            WITH RECURSIVE fwd AS (
              SELECT src_id, 1 AS depth FROM edge
              WHERE dst_id = %s::uuid AND rel_type = 'supersedes'
              UNION ALL
              SELECT e.src_id, f.depth + 1 FROM edge e
              JOIN fwd f ON e.dst_id = f.src_id
              WHERE e.rel_type = 'supersedes'
            )
            SELECT src_id::text FROM fwd ORDER BY depth
            """,
            (norm_id,),
        ).fetchall()
        return [r[0] for r in rows]
