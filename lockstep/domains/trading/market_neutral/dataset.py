"""MarketNeutralDataset — joined funding + spot/perp basis at hourly resolution.

Each row is a ``Bar``::

    {
        "timestamp":     int,
        "funding_rate":  float,    # perp funding (per 8hr by convention)
        "spot_close":    float,
        "perp_close":    float,
        "basis":         float,    # perp_close - spot_close
        "regime":        str,      # "funding_positive" | "funding_negative" | "funding_neutral"
    }

Funding regime threshold is ``|funding_rate| > 0.0001`` per 8-hour window
(i.e., 1 bp). Bars below threshold are ``funding_neutral``; above with
positive sign are ``funding_positive``; above with negative sign are
``funding_negative``.
"""

from __future__ import annotations

import hashlib

from pydantic import ConfigDict

from lockstep.evaluation.canonical import canonical_json_bytes
from lockstep.evaluation.solution import DatasetPayload

VALID_REGIMES = ("funding_positive", "funding_negative", "funding_neutral")
FUNDING_THRESHOLD = 0.0001


def classify_funding_regime(rate: float) -> str:
    """Map a funding rate to one of the three funding regimes."""
    if abs(rate) <= FUNDING_THRESHOLD:
        return "funding_neutral"
    return "funding_positive" if rate > 0 else "funding_negative"


class MarketNeutralDataset(DatasetPayload):
    """Hourly joined funding + spot/perp basis with funding-regime labels."""

    model_config = ConfigDict(frozen=True)

    public_bars: tuple[dict, ...]
    private_bars: tuple[dict, ...]
    walk_forward_windows: tuple[tuple[int, int], ...]

    def verify_integrity(self) -> bool:
        public_root = _hash_bars(self.public_bars)
        if public_root != self.commitment.public_root:
            return False
        private_root = _hash_bars(self.private_bars)
        if private_root != self.commitment.private_root:
            return False
        return True

    def public_view(self) -> MarketNeutralDataset:
        return MarketNeutralDataset(
            commitment=self.commitment,
            public_bars=self.public_bars,
            private_bars=(),
            walk_forward_windows=self.walk_forward_windows,
        )

    def has_private_data(self) -> bool:
        return len(self.private_bars) > 0

    def all_bars(self) -> tuple[dict, ...]:
        return self.public_bars + self.private_bars


def _hash_bars(bars: tuple[dict, ...]) -> str:
    if not bars:
        return "0x" + "00" * 32
    payload = canonical_json_bytes(list(bars))
    return "0x" + hashlib.sha256(payload).hexdigest()


def commitment_roots(
    public_bars: tuple[dict, ...], private_bars: tuple[dict, ...]
) -> tuple[str, str, str]:
    """Compute (public_root, private_root, merkle_root) for a bar split."""
    pub = _hash_bars(public_bars)
    priv = _hash_bars(private_bars)
    combined = hashlib.sha256(
        bytes.fromhex(pub[2:]) + bytes.fromhex(priv[2:])
    ).hexdigest()
    return pub, priv, "0x" + combined


__all__ = [
    "FUNDING_THRESHOLD",
    "VALID_REGIMES",
    "MarketNeutralDataset",
    "classify_funding_regime",
    "commitment_roots",
]
