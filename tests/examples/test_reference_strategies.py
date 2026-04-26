"""Acceptance tests for the reference strategies bundled in examples/."""

from __future__ import annotations

import pathlib

from lockstep.domains.trading.directional import (
    DirectionalGrader,
    DirectionalSolution,
)
from lockstep.domains.trading.market_neutral import (
    MarketNeutralGrader,
    MarketNeutralSolution,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR_DIR = REPO_ROOT / "examples" / "strategies" / "directional"
MN_DIR = REPO_ROOT / "examples" / "strategies" / "market_neutral"

DIRECTIONAL_NAMES = ("ma_crossover", "momentum", "mean_reversion")
MARKET_NEUTRAL_NAMES = ("naive_funding", "basis_divergence")


def _load(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_strategies_load_and_grade_without_raising(
    directional_dataset_factory, market_neutral_dataset_factory
):
    dir_ds = directional_dataset_factory(n_public=60, n_private=20)
    mn_ds = market_neutral_dataset_factory(n_public=60, n_private=20)

    for name in DIRECTIONAL_NAMES:
        sol = DirectionalSolution(source=_load(DIR_DIR / f"{name}.py"))
        result = DirectionalGrader().grade(sol, dir_ds)
        assert result.full_score_vector is not None

    for name in MARKET_NEUTRAL_NAMES:
        sol = MarketNeutralSolution(source=_load(MN_DIR / f"{name}.py"))
        result = MarketNeutralGrader().grade(sol, mn_ds)
        assert result.full_score_vector is not None


def test_directional_strategies_have_non_trivial_rank_score_spread(
    directional_dataset_factory,
):
    ds = directional_dataset_factory(n_public=60, n_private=20)
    grader = DirectionalGrader()
    scores = []
    for name in DIRECTIONAL_NAMES:
        sol = DirectionalSolution(source=_load(DIR_DIR / f"{name}.py"))
        result = grader.grade(sol, ds)
        sv = result.full_score_vector
        assert sv is not None
        scores.append(sv["worst_regime_sharpe"])
    assert max(scores) - min(scores) > 1e-3, (
        f"directional rank scores too tight: {scores}"
    )


def test_market_neutral_strategies_have_non_trivial_rank_score_spread(
    market_neutral_dataset_factory,
):
    ds = market_neutral_dataset_factory(n_public=60, n_private=20)
    grader = MarketNeutralGrader()
    scores = []
    for name in MARKET_NEUTRAL_NAMES:
        sol = MarketNeutralSolution(source=_load(MN_DIR / f"{name}.py"))
        result = grader.grade(sol, ds)
        sv = result.full_score_vector
        assert sv is not None
        scores.append(sv["net_market_neutral_pnl"])
    assert max(scores) - min(scores) > 1e-6, (
        f"market-neutral rank scores too tight: {scores}"
    )


def test_directional_strategies_produce_non_zero_trade_count(
    directional_dataset_factory,
):
    ds = directional_dataset_factory(n_public=60, n_private=20)
    grader = DirectionalGrader()
    for name in DIRECTIONAL_NAMES:
        sol = DirectionalSolution(source=_load(DIR_DIR / f"{name}.py"))
        sv = grader.grade(sol, ds).full_score_vector
        assert sv is not None
        assert sv["trade_count"] > 0, f"{name} never traded"


def test_market_neutral_strategies_stay_neutral(market_neutral_dataset_factory):
    """``directional_exposure_max`` must stay below 0.1 — paired sizing works."""
    ds = market_neutral_dataset_factory(n_public=60, n_private=20)
    grader = MarketNeutralGrader()
    for name in MARKET_NEUTRAL_NAMES:
        sol = MarketNeutralSolution(source=_load(MN_DIR / f"{name}.py"))
        sv = grader.grade(sol, ds).full_score_vector
        assert sv is not None
        assert sv["directional_exposure_max"] < 0.1, (
            f"{name} drifted directional: {sv['directional_exposure_max']}"
        )
