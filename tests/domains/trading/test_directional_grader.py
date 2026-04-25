"""Acceptance tests for DirectionalGrader."""

from __future__ import annotations

import hashlib
import inspect

from lockstep.domains.trading.directional.grader import DirectionalGrader
from lockstep.domains.trading.directional.solution import DirectionalSolution


def test_grading_same_solution_twice_produces_identical_score_vector(
    directional_dataset_factory, directional_oracle_solver_source
):
    ds = directional_dataset_factory()
    sol = DirectionalSolution(source=directional_oracle_solver_source)
    grader = DirectionalGrader()
    a = grader.grade(sol, ds)
    b = grader.grade(sol, ds)
    assert a == b
    # Bit-equal floats — same dict equality but verify per-key for clarity
    for key in a.public_score_vector:
        assert a.public_score_vector[key] == b.public_score_vector[key]
    if a.full_score_vector is not None:
        for key in a.full_score_vector:
            assert a.full_score_vector[key] == b.full_score_vector[key]


def test_grade_public_subset_of_grade_full_execution(
    directional_dataset_factory, directional_oracle_solver_source
):
    ds = directional_dataset_factory()
    sol = DirectionalSolution(source=directional_oracle_solver_source)
    grader = DirectionalGrader()

    public_only = grader.grade(sol, ds.public_view())
    full = grader.grade(sol, ds)

    # Public-only dataset has no private data → no full score
    assert public_only.full_score_vector is None

    # Full grade has both vectors
    assert full.full_score_vector is not None
    assert full.public_score_vector is not None

    # Public score vectors are computed over the same public bars and
    # should match — both computed from public_bars only.
    assert full.public_score_vector == public_only.public_score_vector


def test_flat_solution_yields_zero_pnl_and_zero_sharpe(
    directional_dataset_factory, directional_flat_solver_source
):
    ds = directional_dataset_factory()
    sol = DirectionalSolution(source=directional_flat_solver_source)
    grader = DirectionalGrader()
    result = grader.grade(sol, ds)
    sv = result.full_score_vector or result.public_score_vector

    assert sv["aggregate_sharpe"] == 0.0
    assert sv["worst_regime_sharpe"] == 0.0
    assert sv["bull_sharpe"] == 0.0
    assert sv["bear_sharpe"] == 0.0
    assert sv["chop_sharpe"] == 0.0
    assert sv["vol_spike_sharpe"] == 0.0
    assert sv["trade_count"] == 0.0
    assert sv["max_drawdown"] == 0.0


def test_oracle_solution_produces_high_sharpe_in_aligned_regimes(
    directional_dataset_factory, directional_oracle_solver_source
):
    ds = directional_dataset_factory()
    sol = DirectionalSolution(source=directional_oracle_solver_source)
    grader = DirectionalGrader()
    result = grader.grade(sol, ds)
    sv = result.full_score_vector

    assert sv is not None
    # The oracle goes long in bull and short in bear — those should be
    # strongly positive Sharpe; chop and vol_spike are flat (0.0).
    assert sv["bull_sharpe"] > 0
    assert sv["bear_sharpe"] > 0
    assert sv["aggregate_sharpe"] > 0


def test_grader_version_matches_inspect_getsource_hash():
    expected = (
        "0x"
        + hashlib.sha256(
            inspect.getsource(DirectionalGrader).encode("utf-8")
        ).hexdigest()
    )
    assert DirectionalGrader.version() == expected
