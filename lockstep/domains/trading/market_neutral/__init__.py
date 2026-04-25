"""Trading market-neutral domain — Layer 3 instantiation."""

from .dataset import (
    FUNDING_THRESHOLD,
    VALID_REGIMES,
    MarketNeutralDataset,
    classify_funding_regime,
    commitment_roots,
)
from .evaluation import TradingMarketNeutralEvaluation
from .grader import MarketNeutralGrader
from .solution import MarketNeutralSolution, SandboxError

__all__ = [
    "FUNDING_THRESHOLD",
    "VALID_REGIMES",
    "MarketNeutralDataset",
    "MarketNeutralGrader",
    "MarketNeutralSolution",
    "SandboxError",
    "TradingMarketNeutralEvaluation",
    "classify_funding_regime",
    "commitment_roots",
]
