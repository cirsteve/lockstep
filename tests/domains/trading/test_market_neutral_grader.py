"""Acceptance tests for MarketNeutralGrader."""

from __future__ import annotations

import hashlib
import inspect

from lockstep.domains.trading.market_neutral.grader import MarketNeutralGrader
from lockstep.domains.trading.market_neutral.solution import MarketNeutralSolution


def test_grading_same_solution_twice_is_bit_equal(
    market_neutral_dataset_factory, market_neutral_capture_solver_source
):
    ds = market_neutral_dataset_factory()
    sol = MarketNeutralSolution(source=market_neutral_capture_solver_source)
    grader = MarketNeutralGrader()
    a = grader.grade(sol, ds)
    b = grader.grade(sol, ds)
    assert a == b


def test_public_grade_is_subset_of_full_grade_execution(
    market_neutral_dataset_factory, market_neutral_capture_solver_source
):
    ds = market_neutral_dataset_factory()
    sol = MarketNeutralSolution(source=market_neutral_capture_solver_source)
    grader = MarketNeutralGrader()
    public_only = grader.grade(sol, ds.public_view())
    full = grader.grade(sol, ds)
    assert public_only.full_score_vector is None
    assert full.full_score_vector is not None
    assert full.public_score_vector == public_only.public_score_vector


def test_flat_solution_yields_zero_pnl(
    market_neutral_dataset_factory, market_neutral_flat_solver_source
):
    ds = market_neutral_dataset_factory()
    sol = MarketNeutralSolution(source=market_neutral_flat_solver_source)
    result = MarketNeutralGrader().grade(sol, ds)
    sv = result.full_score_vector or result.public_score_vector
    assert sv["net_market_neutral_pnl"] == 0.0
    assert sv["funding_capture_rate"] == 0.0
    assert sv["trade_count"] == 0.0
    assert sv["directional_exposure_max"] == 0.0


def test_capture_solver_is_market_neutral(
    market_neutral_dataset_factory, market_neutral_capture_solver_source
):
    """Spot+perp paired sizing keeps directional_exposure_max small."""
    ds = market_neutral_dataset_factory()
    sol = MarketNeutralSolution(source=market_neutral_capture_solver_source)
    grader = MarketNeutralGrader()
    sv = grader.grade(sol, ds).full_score_vector
    assert sv is not None
    # Paired sizing means net direction is zero whenever positions taken
    assert sv["directional_exposure_max"] < 0.1


def test_grader_version_matches_inspect_getsource_hash():
    expected = (
        "0x"
        + hashlib.sha256(
            inspect.getsource(MarketNeutralGrader).encode("utf-8")
        ).hexdigest()
    )
    assert MarketNeutralGrader.version() == expected
