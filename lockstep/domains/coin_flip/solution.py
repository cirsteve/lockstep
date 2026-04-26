"""Coin-flip solution: a deterministic predictor expressed as a rule string.

The rule string is the canonical form. It serializes deterministically
(it's just bytes), and its hash is the plaintext_commitment.

Rule grammar:
    "always_heads"
    "always_tails"
    "match:<feature_index>"            predict heads iff features[i] >= 0.5
    "anti:<feature_index>"             predict heads iff features[i] <  0.5
    "majority:<i>,<j>,<k>"             predict heads iff sum >= len/2
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import ConfigDict

from lockstep.evaluation.solution import SolutionPayload


class CoinFlipSolution(SolutionPayload):
    """A coin-flip prediction strategy expressed as a rule string."""

    model_config = ConfigDict(frozen=True)

    rule: str

    def serialize(self) -> bytes:
        return self.rule.encode("utf-8")

    @classmethod
    def deserialize(cls, data: bytes) -> CoinFlipSolution:
        return cls(rule=data.decode("utf-8"))

    def instantiate(self) -> Callable[[tuple[float, ...]], bool]:
        """Return a predictor function bound to this rule."""
        return self.evaluate_one

    def evaluate_one(self, features: tuple[float, ...]) -> bool:
        """Apply the rule to one feature vector. True for heads, False for tails."""
        rule = self.rule
        if rule == "always_heads":
            return True
        if rule == "always_tails":
            return False
        if rule.startswith("match:"):
            i = int(rule.split(":", 1)[1])
            return features[i] >= 0.5
        if rule.startswith("anti:"):
            i = int(rule.split(":", 1)[1])
            return features[i] < 0.5
        if rule.startswith("majority:"):
            indices = [int(x) for x in rule.split(":", 1)[1].split(",")]
            count = sum(1 for i in indices if features[i] >= 0.5)
            return count * 2 >= len(indices)
        raise ValueError(f"unknown rule: {rule!r}")
