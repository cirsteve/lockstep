"""TradingMarketNeutralEvaluation — Layer 3 contract for market-neutral strategies."""

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

from .dataset import MarketNeutralDataset
from .grader import MarketNeutralGrader
from .solution import MarketNeutralSolution

_INPUT_SCHEMA = {
    "type": "object",
    "description": (
        "Streaming solver input: funding_window is the funding-rate history "
        "through bar t-1; basis_window is the joined spot+perp history "
        "through bar t-1; state is a mutable scratchpad."
    ),
    "properties": {
        "funding_window": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "integer"},
                    "funding_rate": {"type": "number"},
                    "regime": {
                        "type": "string",
                        "enum": [
                            "funding_positive",
                            "funding_negative",
                            "funding_neutral",
                        ],
                    },
                },
                "required": ["timestamp", "funding_rate"],
            },
        },
        "basis_window": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "timestamp": {"type": "integer"},
                    "spot_close": {"type": "number"},
                    "perp_close": {"type": "number"},
                    "basis": {"type": "number"},
                },
                "required": ["timestamp", "spot_close", "perp_close", "basis"],
            },
        },
        "state": {"type": "object"},
    },
    "required": ["funding_window", "basis_window", "state"],
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "spot": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["long", "short", "flat"]},
                "size": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["direction", "size"],
        },
        "perp": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["long", "short", "flat"]},
                "size": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            },
            "required": ["direction", "size"],
        },
    },
    "required": ["spot", "perp"],
}


_SCORE_DIMENSIONS: list[ScoreDimension] = [
    ScoreDimension(
        key="funding_capture_rate",
        description="Fraction of theoretical funding actually captured (signed).",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="basis_dislocation_max",
        description="Max |basis| observed while holding any leg.",
        unit="ratio",
        higher_is_better=False,
    ),
    ScoreDimension(
        key="net_market_neutral_pnl",
        description="Net P&L after slippage as fraction of notional. Rank metric.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="funding_positive_sharpe",
        description="Sharpe restricted to bars where funding rate was positive.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="funding_negative_sharpe",
        description="Sharpe restricted to bars where funding rate was negative.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="funding_neutral_sharpe",
        description="Sharpe restricted to bars where funding rate was below threshold.",
        unit="ratio",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="trade_count",
        description="Number of bars where any leg's signed position changed.",
        unit="count",
        higher_is_better=True,
    ),
    ScoreDimension(
        key="directional_exposure_max",
        description="Max |spot_pos + perp_pos| — neutrality discipline check.",
        unit="ratio",
        higher_is_better=False,
    ),
]


EVALUATOR = Evaluator.build(
    domain_name="trading_market_neutral",
    domain_version="v1",
    solver_input_schema=_INPUT_SCHEMA,
    solver_output_schema=_OUTPUT_SCHEMA,
    invocation_model="streaming",
    accepted_grader_versions=[MarketNeutralGrader.version()],
    dataset_schema_version="v1",
    holdout_policy=HoldoutPolicy(
        public_fraction=0.8,
        rotation_cadence_days=None,
        rotation_governance="marketplace_admin",
    ),
    score_dimensions=_SCORE_DIMENSIONS,
    rank_dimension="net_market_neutral_pnl",
    dispute_policy=DisputePolicy(
        challenge_window_seconds=86_400,
        minimum_validators_for_consensus=1,
        resolution_method="replay",
    ),
    metadata={
        "purpose": "Market-neutral funding-rate capture on Hyperliquid + Binance.",
        "annualization": "hourly bars → sqrt(24*365)",
        "funding_threshold": "|rate| > 0.0001 per 8hr",
    },
)
register_evaluator(EVALUATOR)


class TradingMarketNeutralEvaluation(Evaluation[MarketNeutralSolution, MarketNeutralDataset]):
    """Layer 3 contract for the trading-market-neutral domain."""

    @property
    def domain(self) -> str:
        return "trading_market_neutral"

    def evaluator(self) -> Evaluator:
        return EVALUATOR

    @property
    def solver_interface(self) -> SolverInterface:
        return SolverInterface(
            domain="trading_market_neutral",
            version="v1",
            input_schema=_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            invocation_model="streaming",
        )

    def grader(self) -> MarketNeutralGrader:
        return MarketNeutralGrader()

    def deserialize_solution(self, data: bytes) -> MarketNeutralSolution:
        return MarketNeutralSolution.deserialize(data)

    def load_dataset(self, commitment_uri: str, *, with_private: bool) -> MarketNeutralDataset:
        raise NotImplementedError("uses substrate.storage.load_dataset_*(commitment)")

    def rank_score(self, receipt: Receipt) -> float:
        if receipt.full_score_vector is not None:
            return receipt.full_score_vector.get("net_market_neutral_pnl", float("-inf"))
        return receipt.public_score_vector.get("net_market_neutral_pnl", float("-inf"))

    def filter_dimensions(self) -> list[str]:
        return [
            "net_market_neutral_pnl",
            "funding_capture_rate",
            "basis_dislocation_max",
            "funding_positive_sharpe",
            "funding_negative_sharpe",
            "funding_neutral_sharpe",
            "trade_count",
            "directional_exposure_max",
        ]
