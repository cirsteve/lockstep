"""Acceptance tests for the reference strategies bundled in examples/."""

from __future__ import annotations

import json
import pathlib

from lockstep.domains.trading.directional import (
    DirectionalDataset,
    DirectionalGrader,
    DirectionalSolution,
)
from lockstep.domains.trading.market_neutral import (
    MarketNeutralDataset,
    MarketNeutralGrader,
    MarketNeutralSolution,
)
from lockstep.evaluation.solution import DatasetCommitment

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DIR_DIR = REPO_ROOT / "examples" / "strategies" / "directional"
MN_DIR = REPO_ROOT / "examples" / "strategies" / "market_neutral"

DIRECTIONAL_CANONICAL_JSON = (
    REPO_ROOT
    / "lockstep"
    / "domains"
    / "trading"
    / "directional"
    / "canonical_dataset.json"
)
MARKET_NEUTRAL_CANONICAL_JSON = (
    REPO_ROOT
    / "lockstep"
    / "domains"
    / "trading"
    / "market_neutral"
    / "canonical_dataset.json"
)

DIRECTIONAL_NAMES = ("ma_crossover", "momentum", "mean_reversion")
MARKET_NEUTRAL_NAMES = ("naive_funding", "basis_divergence")

# Spec-required spread thresholds against the real canonical datasets
# (Day 4 §B.2). Tighter than the synthetic-data thresholds the suite
# uses for smoke tests below; if a future change collapses the spread,
# the assertion fails loudly.
DIRECTIONAL_SPREAD_FLOOR = 0.30
MARKET_NEUTRAL_SPREAD_FLOOR = 0.05


def _load(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_directional_canonical() -> DirectionalDataset:
    payload = json.loads(DIRECTIONAL_CANONICAL_JSON.read_text(encoding="utf-8"))
    dataset = DirectionalDataset(
        commitment=DatasetCommitment(**payload["commitment"]),
        public_bars=tuple(payload["public_bars"]),
        private_bars=tuple(payload["private_bars"]),
        walk_forward_windows=tuple(tuple(w) for w in payload["walk_forward_windows"]),
    )
    # Catch accidental edits / corruption of the committed JSON early —
    # a tampered file would silently grade against bytes that don't
    # match the recorded Merkle roots.
    assert dataset.verify_integrity(), (
        "directional canonical_dataset.json failed verify_integrity(); "
        "regenerate via scripts/datasets/build_directional.py"
    )
    return dataset


def _load_market_neutral_canonical() -> MarketNeutralDataset:
    payload = json.loads(MARKET_NEUTRAL_CANONICAL_JSON.read_text(encoding="utf-8"))
    dataset = MarketNeutralDataset(
        commitment=DatasetCommitment(**payload["commitment"]),
        public_bars=tuple(payload["public_bars"]),
        private_bars=tuple(payload["private_bars"]),
        walk_forward_windows=tuple(tuple(w) for w in payload["walk_forward_windows"]),
    )
    assert dataset.verify_integrity(), (
        "market-neutral canonical_dataset.json failed verify_integrity(); "
        "regenerate via scripts/datasets/build_market_neutral.py"
    )
    return dataset


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


def test_directional_strategies_hit_spread_floor_against_real_dataset() -> None:
    """Spec §B.2: max-min on `worst_regime_sharpe` across the three
    directional strategies must exceed 0.30 when graded against the
    real canonical dataset (committed at
    lockstep/domains/trading/directional/canonical_dataset.json).
    Asserting against real data — not synthetic — so a parameter
    regression that collapses the spread fails loudly here."""
    ds = _load_directional_canonical()
    grader = DirectionalGrader()
    scores: dict[str, float] = {}
    for name in DIRECTIONAL_NAMES:
        sol = DirectionalSolution(source=_load(DIR_DIR / f"{name}.py"))
        sv = grader.grade(sol, ds).full_score_vector
        assert sv is not None
        scores[name] = sv["worst_regime_sharpe"]
    spread = max(scores.values()) - min(scores.values())
    assert spread > DIRECTIONAL_SPREAD_FLOOR, (
        f"directional worst_regime_sharpe spread {spread:+.4f} below "
        f"floor {DIRECTIONAL_SPREAD_FLOOR}: {scores}"
    )


def test_market_neutral_strategies_hit_spread_floor_against_real_dataset() -> None:
    """Spec §B.2: max-min on `net_market_neutral_pnl` across the two
    market-neutral strategies must exceed 0.05 when graded against
    the real canonical dataset (committed at
    lockstep/domains/trading/market_neutral/canonical_dataset.json)."""
    ds = _load_market_neutral_canonical()
    grader = MarketNeutralGrader()
    scores: dict[str, float] = {}
    for name in MARKET_NEUTRAL_NAMES:
        sol = MarketNeutralSolution(source=_load(MN_DIR / f"{name}.py"))
        sv = grader.grade(sol, ds).full_score_vector
        assert sv is not None
        scores[name] = sv["net_market_neutral_pnl"]
    spread = max(scores.values()) - min(scores.values())
    assert spread > MARKET_NEUTRAL_SPREAD_FLOOR, (
        f"market-neutral net_market_neutral_pnl spread {spread:+.4f} below "
        f"floor {MARKET_NEUTRAL_SPREAD_FLOOR}: {scores}"
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
