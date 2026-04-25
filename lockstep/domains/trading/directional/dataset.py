"""DirectionalDataset — OHLCV bars + regime labels + walk-forward windows.

Each row in the dataset is a ``Bar``::

    {
        "timestamp": int,      # epoch seconds, hourly
        "asset":     str,      # "BTC" | "ETH" | "SOL"
        "open":      float,
        "high":      float,
        "low":       float,
        "close":     float,
        "volume":    float,
        "regime":    str,      # "bull" | "bear" | "chop" | "vol_spike"
    }

Walk-forward windows are ``(in_sample_end_idx, out_of_sample_end_idx)``
tuples, each describing one walk-forward step. The grader fits on
``[0:in_sample_end]`` and runs out-of-sample on
``[in_sample_end:out_of_sample_end]``.

The hackathon scope keeps the in-sample step a no-op for parameterless
solvers (the streaming solver already accumulates state). The window
boundaries are kept on the dataset so that future stateful-fit graders
have somewhere to read them.
"""

from __future__ import annotations

import hashlib

from pydantic import ConfigDict

from lockstep.evaluation.canonical import canonical_json_bytes
from lockstep.evaluation.solution import DatasetPayload

VALID_REGIMES = ("bull", "bear", "chop", "vol_spike")


class DirectionalDataset(DatasetPayload):
    """OHLCV bars at hourly resolution for BTC/ETH/SOL with regime labels."""

    model_config = ConfigDict(frozen=True)

    public_bars: tuple[dict, ...]
    private_bars: tuple[dict, ...]
    walk_forward_windows: tuple[tuple[int, int], ...]

    def verify_integrity(self) -> bool:
        """Recompute Merkle-style root over the bars and compare to commitment.

        For determinism the bars are serialized through ``canonical_json_bytes``
        before hashing — Pydantic-loaded values that come back through JSON
        round-trips would otherwise produce different byte sequences in
        edge cases (key order, float rounding inside dict values).
        """
        public_root = _hash_bars(self.public_bars)
        if public_root != self.commitment.public_root:
            return False
        private_root = _hash_bars(self.private_bars)
        if private_root != self.commitment.private_root:
            return False
        return True

    def public_view(self) -> DirectionalDataset:
        return DirectionalDataset(
            commitment=self.commitment,
            public_bars=self.public_bars,
            private_bars=(),
            walk_forward_windows=self.walk_forward_windows,
        )

    def has_private_data(self) -> bool:
        return len(self.private_bars) > 0

    def all_bars(self) -> tuple[dict, ...]:
        """Public + private bars in chronological order."""
        return self.public_bars + self.private_bars


def _hash_bars(bars: tuple[dict, ...]) -> str:
    """Deterministic hash over a tuple of bar dicts."""
    if not bars:
        return "0x" + "00" * 32
    payload = canonical_json_bytes(list(bars))
    return "0x" + hashlib.sha256(payload).hexdigest()


def commitment_roots(
    public_bars: tuple[dict, ...], private_bars: tuple[dict, ...]
) -> tuple[str, str, str]:
    """Compute (public_root, private_root, merkle_root) for a bar split.

    Helper used by tests and the demo script that build datasets in
    process. The combined merkle_root is sha256(public_root || private_root).
    """
    pub = _hash_bars(public_bars)
    priv = _hash_bars(private_bars)
    combined = hashlib.sha256(
        bytes.fromhex(pub[2:]) + bytes.fromhex(priv[2:])
    ).hexdigest()
    return pub, priv, "0x" + combined


__all__ = [
    "VALID_REGIMES",
    "DirectionalDataset",
    "commitment_roots",
]
