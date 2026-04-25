"""TradingDirectionalEvaluation — Layer 3 contract for directional strategies."""

from __future__ import annotations

from lockstep.evaluation.evaluation import Evaluation, SolverInterface
from lockstep.evaluation.evaluator import (
    DisputePolicy,
    Evaluator,
    HoldoutPolicy,
    ScoreDimension,
    register_evaluator,
)
from lockstep.evaluation.receipt import Receipt

from .dataset import DirectionalDataset
from .grader import DirectionalGrader
from .solution import DirectionalSolution

_INPUT_SCHEMA = {
    "type": "object",
    "description": (
        "Streaming solver input: window is OHLCV history through bar t-1; "
        "state is a mutable scratchpad."
    ),
    "properties": {
        "window": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "integer"},
                    "asset": {"type": "string", "enum": ["BTC", "ETH", "SOL"]},
                    "open": {"type": "number"},
                    "high": {"type": "number"},
                    "low": {"type": "number"},
                    "close": {"type": "number"},
                    "volume": {"type": "number"},
                    "regime": {
                        "type": "string",
                        "enum": ["bull", "bear", "chop", "vol_spike"],
                    },
                },
                "required": ["timestamp", "asset", "close"],
            },
        },
        "state": {"type": "object"},
    },
    "required": ["window", "state"],
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "direction": {"type": "string", "enum": ["long", "short", "flat"]},
        "size": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["direction", "size"],
}


_SCORE_DIMENSIONS: list[ScoreDimension] = [
    ScoreDimension(
        key="worst_regime_sharpe",
        description=(
            "Lowest annualized Sharpe across the four regime buckets. "
            "Rank metric — punishes strategies that only work in one regime."
        ),
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="aggregate_sharpe",
        description="Annualized Sharpe over the full out-of-sample series.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="max_drawdown",
        description="Peak-to-trough drawdown on cumulative P&L (non-negative magnitude).",
        unit="ratio",
        higher_is_better=False,
    ),
    ScoreDimension(
        key="bull_sharpe",
        description="Annualized Sharpe restricted to bull-regime bars.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="bear_sharpe",
        description="Annualized Sharpe restricted to bear-regime bars.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="chop_sharpe",
        description="Annualized Sharpe restricted to chop-regime bars.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="vol_spike_sharpe",
        description="Annualized Sharpe restricted to vol-spike-regime bars.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="trade_count",
        description="Number of position changes across the evaluation period.",
        unit="count",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="win_rate",
        description="Fraction of position bars with positive net P&L after slippage.",
        unit="ratio",
        higher_is_better=True,
    ),
]


class TradingDirectionalEvaluation(Evaluation[DirectionalSolution, DirectionalDataset]):
    """Layer 3 contract for the trading-directional domain."""

    def __init__(self) -> None:
        self._evaluator = Evaluator.build(
            domain_name="trading_directional",
            domain_version="v1",
            solver_input_schema=_INPUT_SCHEMA,
            solver_output_schema=_OUTPUT_SCHEMA,
            invocation_model="streaming",
            accepted_grader_versions=[DirectionalGrader.version()],
            dataset_schema_version="v1",
            holdout_policy=HoldoutPolicy(
                public_fraction=0.8,
                rotation_cadence_days=None,
                rotation_governance="marketplace_admin",
            ),
            score_dimensions=_SCORE_DIMENSIONS,
            rank_dimension="worst_regime_sharpe",
            dispute_policy=DisputePolicy(
                challenge_window_seconds=86_400,
                minimum_validators_for_consensus=1,
                resolution_method="replay",
            ),
            metadata={
                "purpose": "Directional perp strategies on BTC/ETH/SOL hourly bars.",
                "annualization": "hourly bars → sqrt(24*365)",
            },
        )
        register_evaluator(self._evaluator)

    @property
    def domain(self) -> str:
        return "trading_directional"

    def evaluator(self) -> Evaluator:
        return self._evaluator

    @property
    def solver_interface(self) -> SolverInterface:
        return SolverInterface(
            domain="trading_directional",
            version="v1",
            input_schema=_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            invocation_model="streaming",
        )

    def grader(self) -> DirectionalGrader:
        return DirectionalGrader()

    def deserialize_solution(self, data: bytes) -> DirectionalSolution:
        return DirectionalSolution.deserialize(data)

    def load_dataset(self, commitment_uri: str, *, with_private: bool) -> DirectionalDataset:
        # Real implementation pulls bytes via the substrate.storage adapter.
        raise NotImplementedError("uses substrate.storage.load_dataset_*(commitment)")

    def rank_score(self, receipt: Receipt) -> float:
        if receipt.full_score_vector is not None:
            return receipt.full_score_vector.get("worst_regime_sharpe", float("-inf"))
        return receipt.public_score_vector.get("worst_regime_sharpe", float("-inf"))

    def filter_dimensions(self) -> list[str]:
        return [
            "worst_regime_sharpe",
            "aggregate_sharpe",
            "max_drawdown",
            "bull_sharpe",
            "bear_sharpe",
            "chop_sharpe",
            "vol_spike_sharpe",
            "trade_count",
            "win_rate",
        ]
