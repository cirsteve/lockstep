"""Synthetic dataset builders shared by trading-domain tests and example tests.

Plain Python module (not a conftest) so it can be imported from any test
file regardless of pytest's conftest hierarchy. Fixtures are defined in
the conftest closest to the tests that use them; they delegate the
data-building work here.
"""

from __future__ import annotations

import math

from lockstep.domains.trading.directional.dataset import (
    DirectionalDataset,
)
from lockstep.domains.trading.directional.dataset import (
    commitment_roots as dir_roots,
)
from lockstep.domains.trading.market_neutral.dataset import (
    MarketNeutralDataset,
    classify_funding_regime,
)
from lockstep.domains.trading.market_neutral.dataset import (
    commitment_roots as mn_roots,
)
from lockstep.evaluation.solution import DatasetCommitment


def _bar(
    ts: int,
    asset: str,
    close: float,
    *,
    regime: str = "bull",
    high_offset: float = 0.5,
    low_offset: float = 0.5,
    volume: float = 1000.0,
) -> dict:
    return {
        "timestamp": ts,
        "asset": asset,
        "open": close,
        "high": close + high_offset,
        "low": close - low_offset,
        "close": close,
        "volume": volume,
        "regime": regime,
    }


def build_directional_series(
    n_public: int = 40,
    n_private: int = 10,
    block_size: int = 5,
) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    regimes = ["bull", "bear", "chop", "vol_spike"]
    bars: list[dict] = []
    base = 50_000.0
    total = n_public + n_private
    for i in range(total):
        regime = regimes[(i // block_size) % len(regimes)]
        if regime == "bull":
            drift = 0.005
        elif regime == "bear":
            drift = -0.005
        elif regime == "chop":
            drift = 0.0005 * math.sin(i * 0.7)
        else:
            drift = 0.01 * (1 if i % 2 == 0 else -1)
        base = base * (1.0 + drift)
        bars.append(
            _bar(
                ts=1_700_000_000 + i * 3600,
                asset="BTC",
                close=round(base, 4),
                regime=regime,
            )
        )
    return tuple(bars[:n_public]), tuple(bars[n_public:])


def build_directional_dataset(
    n_public: int = 40, n_private: int = 10
) -> DirectionalDataset:
    public, private = build_directional_series(n_public, n_private)
    pub_root, priv_root, merkle = dir_roots(public, private)
    commitment = DatasetCommitment(
        domain="trading_directional",
        merkle_root=merkle,
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="mock://dataset/trading_directional/test",
        schema_version="v1",
    )
    return DirectionalDataset(
        commitment=commitment,
        public_bars=public,
        private_bars=private,
        walk_forward_windows=((n_public // 2, n_public),),
    )


def build_market_neutral_series(
    n_public: int = 60, n_private: int = 20
) -> tuple[tuple[dict, ...], tuple[dict, ...]]:
    bars: list[dict] = []
    spot = 100.0
    total = n_public + n_private
    for i in range(total):
        block = (i // 10) % 6
        if block in (0, 1):
            rate = 0.001
        elif block in (3, 4):
            rate = -0.001
        else:
            rate = 0.00005
        regime = classify_funding_regime(rate)
        spot = spot * 1.0001
        if rate > 0:
            basis = 0.5 + 0.1 * (i % 3)
        elif rate < 0:
            basis = -(0.5 + 0.1 * (i % 3))
        else:
            basis = 0.05 * (1 if i % 2 == 0 else -1)
        perp = spot + basis
        bars.append(
            {
                "timestamp": 1_700_000_000 + i * 3600,
                "funding_rate": rate,
                "spot_close": round(spot, 6),
                "perp_close": round(perp, 6),
                "basis": round(basis, 6),
                "regime": regime,
            }
        )
    return tuple(bars[:n_public]), tuple(bars[n_public:])


def build_market_neutral_dataset(
    n_public: int = 60, n_private: int = 20
) -> MarketNeutralDataset:
    public, private = build_market_neutral_series(n_public, n_private)
    pub_root, priv_root, merkle = mn_roots(public, private)
    commitment = DatasetCommitment(
        domain="trading_market_neutral",
        merkle_root=merkle,
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="mock://dataset/trading_market_neutral/test",
        schema_version="v1",
    )
    return MarketNeutralDataset(
        commitment=commitment,
        public_bars=public,
        private_bars=private,
        walk_forward_windows=((n_public // 2, n_public),),
    )
