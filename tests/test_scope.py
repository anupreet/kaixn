"""scope normalization — invalid ltree characters from LLM extractors must be
folded so `scope::ltree` casts never raise (regression for the connect_repo
bootstrap crash: 'ltree syntax error')."""

from __future__ import annotations

import pytest

from kaixn.types import NormCandidate, Operation, OpKind, OpType, normalize_scope


@pytest.mark.parametrize("raw, expected", [
    ("all", "all"),
    ("all.product.billing", "all.product.billing"),       # valid → unchanged
    ("all.product design", "all.product_design"),         # space
    ("all.user/auth", "all.user_auth"),                   # slash
    ("all.billing@v2!", "all.billing_v2"),                # punctuation + trim
    ("product.billing", "all.product.billing"),           # rooted at all
    ("all.product.user-management", "all.product.user-management"),  # hyphen kept
    ("", "all"),
    (None, "all"),
    ("...", "all"),                                        # all-empty labels
    ("all..billing", "all.billing"),                      # empty middle label
])
def test_normalize_scope(raw, expected):
    assert normalize_scope(raw) == expected


def test_candidate_normalizes_on_construction():
    assert NormCandidate("s", "product", "all.user experience").scope == "all.user_experience"


def test_operation_normalizes_on_construction():
    op = Operation(OpKind.NORM, OpType.ASSERT, "s", scope="all.a/b")
    assert op.scope == "all.a_b"
