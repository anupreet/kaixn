"""Tests for the deterministic miner pass (offline, no API).

The semantic pass (`mine_semantic`) calls the real Anthropic API and is exercised
separately; these cover the deterministic detectors that form the no-LLM floor.
"""

from __future__ import annotations

import pathlib

from kaixn.miner import mine


def _write(root: pathlib.Path, name: str, content: str) -> None:
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def _by_axis(root: pathlib.Path) -> dict:
    return {o.axis_id: o for o in mine(root)}


def test_naming_case_counts_snake_vs_other(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "a.py",
           '"""doc."""\ndef good_name():\n    pass\ndef badName():\n    pass\n')
    nc = _by_axis(tmp_path)["naming-case"]
    assert nc.value == "snake_case"
    assert (nc.n_match, nc.n_total) == (1, 2)
    assert any("badName" in c.detail for c in nc.counterexamples)


def test_future_annotations_ratio(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "with_future.py", "from __future__ import annotations\nx = 1\n")
    _write(tmp_path, "without.py", "x = 1\n")
    fa = _by_axis(tmp_path)["future-annotations"]
    assert (fa.n_match, fa.n_total) == (1, 2)


def test_dataclass_slots_ratio(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "d.py",
           "from dataclasses import dataclass\n"
           "@dataclass(slots=True)\nclass A:\n    x: int\n"
           "@dataclass\nclass B:\n    y: int\n")
    ds = _by_axis(tmp_path)["dataclass-slots"]
    assert (ds.n_match, ds.n_total) == (1, 2)


def test_test_mirroring_whole_repo_population(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "src/pkg/mod.py", "x = 1\n")
    _write(tmp_path, "src/pkg/other.py", "y = 1\n")
    _write(tmp_path, "tests/test_mod.py", "x = 1\n")
    tm = _by_axis(tmp_path)["test-mirroring"]
    assert (tm.n_match, tm.n_total) == (1, 2)  # mod mirrored, other not


def test_production_axes_ignore_test_files(tmp_path: pathlib.Path) -> None:
    # an unannotated test function must not drag down type-annotations
    _write(tmp_path, "src/pkg/m.py",
           "def f(x: int) -> int:\n    return x\n")
    _write(tmp_path, "tests/test_m.py",
           "def test_f():\n    assert True\n")
    ta = _by_axis(tmp_path)["type-annotations"]
    assert ta.n_total == 1 and ta.n_match == 1  # only the source fn counted


def test_threshold_decides_convention(tmp_path: pathlib.Path) -> None:
    _write(tmp_path, "a.py", "from __future__ import annotations\nx = 1\n")
    fa = _by_axis(tmp_path)["future-annotations"]
    assert fa.is_convention(0.8) is True
    assert fa.is_convention(1.01) is False
