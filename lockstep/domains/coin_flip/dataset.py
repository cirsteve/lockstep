"""Coin-flip dataset: feature vectors paired with true outcomes.

Public portion lets the producer develop. Private portion is sealed; the
grader uses both and produces both public-only and full score vectors.
"""

from __future__ import annotations

from pydantic import ConfigDict

from lockstep.evaluation.solution import DatasetPayload


class CoinFlipDataset(DatasetPayload):
    """Coin-flip dataset split into public and private examples."""

    model_config = ConfigDict(frozen=True)

    public_examples: tuple[tuple[tuple[float, ...], bool], ...]
    private_examples: tuple[tuple[tuple[float, ...], bool], ...]

    def verify_integrity(self) -> bool:
        # Real implementation hashes examples against commitment roots.
        # Substrate's storage adapter handles integrity at the bytes level.
        return True

    def public_view(self) -> CoinFlipDataset:
        return CoinFlipDataset(
            commitment=self.commitment,
            public_examples=self.public_examples,
            private_examples=(),
        )

    def has_private_data(self) -> bool:
        return len(self.private_examples) > 0
