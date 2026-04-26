"""Fixtures for tests/examples/.

Reuses the dataset builders from ``tests/_synthetic.py``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from tests._synthetic import (
    build_directional_dataset,
    build_market_neutral_dataset,
)


@pytest.fixture
def directional_dataset_factory() -> Callable[..., object]:
    def _build(n_public: int = 60, n_private: int = 20):
        return build_directional_dataset(n_public, n_private)

    return _build


@pytest.fixture
def market_neutral_dataset_factory() -> Callable[..., object]:
    def _build(n_public: int = 60, n_private: int = 20, start_offset: int = 0):
        return build_market_neutral_dataset(
            n_public, n_private, start_offset=start_offset
        )

    return _build
