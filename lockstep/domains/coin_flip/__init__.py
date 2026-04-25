"""Coin-flip toy domain — abstraction proof for non-trading workloads."""

from .dataset import CoinFlipDataset
from .evaluation import CoinFlipEvaluation
from .grader import CoinFlipGrader
from .solution import CoinFlipSolution

__all__ = [
    "CoinFlipDataset",
    "CoinFlipEvaluation",
    "CoinFlipGrader",
    "CoinFlipSolution",
]
