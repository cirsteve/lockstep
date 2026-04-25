"""Coin-flip evaluation contract.

Exists to prove the abstract interfaces hold under a domain that's nothing
like trading. If this works end-to-end, adding ML benchmarks or
optimization is a matter of writing a new Evaluation subclass with new
concrete types — not changing Layer 1 or Layer 2.
"""

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

from .dataset import CoinFlipDataset
from .grader import CoinFlipGrader
from .solution import CoinFlipSolution

_INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "features": {"type": "array", "items": {"type": "number"}},
    },
    "required": ["features"],
}

_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "prediction": {"type": "string", "enum": ["heads", "tails"]},
    },
    "required": ["prediction"],
}


class CoinFlipEvaluation(Evaluation[CoinFlipSolution, CoinFlipDataset]):
    """Concrete Evaluation for the coin-flip toy domain."""

    def __init__(self) -> None:
        self._evaluator = Evaluator.build(
            domain_name="coin_flip",
            domain_version="v1",
            solver_input_schema=_INPUT_SCHEMA,
            solver_output_schema=_OUTPUT_SCHEMA,
            invocation_model="batch",
            accepted_grader_versions=[CoinFlipGrader.version()],
            dataset_schema_version="v1",
            holdout_policy=HoldoutPolicy(
                public_fraction=0.5,
                rotation_cadence_days=None,
                rotation_governance="marketplace_admin",
            ),
            score_dimensions=[
                ScoreDimension(
                    key="accuracy",
                    description="Fraction of correct predictions over all examples.",
                    unit="ratio",
                    higher_is_better=True,
                    expected_range=(0.0, 1.0),
                ),
                ScoreDimension(
                    key="n_examples",
                    description="Number of examples the score was computed over.",
                    unit="count",
                    higher_is_better=False,
                ),
            ],
            rank_dimension="accuracy",
            dispute_policy=DisputePolicy(
                challenge_window_seconds=86_400,
                minimum_validators_for_consensus=1,
                resolution_method="replay",
            ),
            metadata={
                "purpose": "Toy domain proving the abstract interfaces hold for non-trading workloads.",
            },
        )
        register_evaluator(self._evaluator)

    @property
    def domain(self) -> str:
        return "coin_flip"

    def evaluator(self) -> Evaluator:
        return self._evaluator

    @property
    def solver_interface(self) -> SolverInterface:
        return SolverInterface(
            domain="coin_flip",
            version="v1",
            input_schema=_INPUT_SCHEMA,
            output_schema=_OUTPUT_SCHEMA,
            invocation_model="batch",
        )

    def grader(self) -> CoinFlipGrader:
        return CoinFlipGrader()

    def deserialize_solution(self, data: bytes) -> CoinFlipSolution:
        return CoinFlipSolution.deserialize(data)

    def load_dataset(self, commitment_uri: str, *, with_private: bool) -> CoinFlipDataset:
        # Real implementation pulls from 0G Storage and verifies Merkle
        # roots. Stubbed here; tests construct CoinFlipDataset directly.
        raise NotImplementedError("uses substrate.storage.load_dataset(commitment_uri)")

    def rank_score(self, receipt: Receipt) -> float:
        if receipt.full_score_vector is not None:
            return receipt.full_score_vector.get("accuracy", float("-inf"))
        return receipt.public_score_vector.get("accuracy", float("-inf"))

    def filter_dimensions(self) -> list[str]:
        return ["accuracy", "n_examples"]
