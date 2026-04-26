"""Coin-flip grader: accuracy on public + accuracy on full.

Trivial accuracy grader. Counts correct predictions; returns the rate.

Determinism: integer arithmetic (correct count, total count), final
division to float at the end with a single rounding step. No
hardware-dependent operations.
"""

from __future__ import annotations

import hashlib
import inspect

from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.evaluation import Grader

from .dataset import CoinFlipDataset
from .solution import CoinFlipSolution


class CoinFlipGrader(Grader[CoinFlipSolution, CoinFlipDataset]):
    """Counts correct predictions; returns the rate as a float."""

    @classmethod
    def version(cls) -> Bytes32Hex:
        source = inspect.getsource(cls).encode("utf-8")
        return "0x" + hashlib.sha256(source).hexdigest()

    def _accuracy(
        self,
        solution: CoinFlipSolution,
        examples: tuple[tuple[tuple[float, ...], bool], ...],
    ) -> float:
        if not examples:
            return 0.0
        correct = 0
        total = 0
        for features, outcome in examples:
            prediction = solution.evaluate_one(features)
            if prediction == outcome:
                correct += 1
            total += 1
        return correct / total

    def grade_public(
        self,
        solution: CoinFlipSolution,
        public_dataset: CoinFlipDataset,
    ) -> dict[str, float]:
        return {
            "accuracy": self._accuracy(solution, public_dataset.public_examples),
            "n_examples": float(len(public_dataset.public_examples)),
        }

    def grade_full(
        self,
        solution: CoinFlipSolution,
        full_dataset: CoinFlipDataset,
    ) -> dict[str, float]:
        all_examples = full_dataset.public_examples + full_dataset.private_examples
        return {
            "accuracy": self._accuracy(solution, all_examples),
            "n_examples": float(len(all_examples)),
        }
