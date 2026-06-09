"""PgStore round-trip — runs ONLY when KAIXN_TEST_DSN points at a Postgres with
the 001 migration applied (pgvector + ltree). Skipped otherwise, so the offline
suite stays green. This is the verification path for the production store.

  createdb kaixn_test && psql kaixn_test -f migrations/001_init.sql
  KAIXN_TEST_DSN=postgresql:///kaixn_test pytest tests/test_pg.py
"""

from __future__ import annotations

import os

import pytest

DSN = os.getenv("KAIXN_TEST_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="KAIXN_TEST_DSN not set")


@pytest.fixture
def store():
    pytest.importorskip("psycopg")
    pytest.importorskip("pgvector")
    from kaixn.embedding import FakeEmbedder
    from kaixn.store import PgStore, pg_connect

    conn = pg_connect(DSN)
    conn.execute("TRUNCATE norm, edge CASCADE")
    return PgStore(conn, FakeEmbedder())


def test_add_get_and_supersede(store):
    from kaixn.types import NormCandidate

    old = store.add_norm(NormCandidate("Billing uses Adyen", "technical",
                                       "all.product.billing"))
    assert store.get(old.id).status == "active"

    new = store.add_norm(NormCandidate("Billing uses Stripe", "technical",
                                       "all.product.billing"))
    store.set_status(old.id, "superseded")
    store.add_edge(new.id, "norm", old.id, "norm", "supersedes")

    assert store.get(old.id).status == "superseded"
    assert store.supersede_chain(old.id) == [new.id]
    # superseded norm no longer an active neighbor
    assert all(n.id != old.id for n in store.neighbors(
        NormCandidate("card processing", "technical", "all.product.billing"),
        top_k=8))
