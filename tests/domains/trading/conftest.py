"""Shared fixtures for trading-domain tests.

Synthetic data builders live in ``tests/_synthetic.py``; this conftest
just wires them as pytest fixtures.
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
    def _build(n_public: int = 40, n_private: int = 10):
        return build_directional_dataset(n_public, n_private)

    return _build


@pytest.fixture
def market_neutral_dataset_factory() -> Callable[..., object]:
    def _build(n_public: int = 60, n_private: int = 20, start_offset: int = 0):
        return build_market_neutral_dataset(
            n_public, n_private, start_offset=start_offset
        )

    return _build


@pytest.fixture
def directional_oracle_solver_source() -> str:
    """Solver that goes long in bull, short in bear, flat otherwise."""
    return (
        "def signal(window, state):\n"
        "    if not window:\n"
        "        return {\"direction\": \"flat\", \"size\": 0.0}\n"
        "    last = window[-1]\n"
        "    regime = last.get(\"regime\", \"chop\")\n"
        "    if regime == \"bull\":\n"
        "        return {\"direction\": \"long\", \"size\": 1.0}\n"
        "    if regime == \"bear\":\n"
        "        return {\"direction\": \"short\", \"size\": 1.0}\n"
        "    return {\"direction\": \"flat\", \"size\": 0.0}\n"
    )


@pytest.fixture
def directional_flat_solver_source() -> str:
    return (
        "def signal(window, state):\n"
        "    return {\"direction\": \"flat\", \"size\": 0.0}\n"
    )


@pytest.fixture
def market_neutral_capture_solver_source() -> str:
    """Long spot + short perp when funding > 0; reversed when funding < 0; flat otherwise."""
    return (
        "def signal(funding_window, basis_window, state):\n"
        "    if not funding_window:\n"
        "        return {\"spot\": {\"direction\": \"flat\", \"size\": 0.0},\n"
        "                \"perp\": {\"direction\": \"flat\", \"size\": 0.0}}\n"
        "    last = funding_window[-1]\n"
        "    rate = last.get(\"funding_rate\", 0.0)\n"
        "    if rate > 0.0001:\n"
        "        return {\"spot\": {\"direction\": \"long\", \"size\": 1.0},\n"
        "                \"perp\": {\"direction\": \"short\", \"size\": 1.0}}\n"
        "    if rate < -0.0001:\n"
        "        return {\"spot\": {\"direction\": \"short\", \"size\": 1.0},\n"
        "                \"perp\": {\"direction\": \"long\", \"size\": 1.0}}\n"
        "    return {\"spot\": {\"direction\": \"flat\", \"size\": 0.0},\n"
        "            \"perp\": {\"direction\": \"flat\", \"size\": 0.0}}\n"
    )


@pytest.fixture
def market_neutral_flat_solver_source() -> str:
    return (
        "def signal(funding_window, basis_window, state):\n"
        "    return {\"spot\": {\"direction\": \"flat\", \"size\": 0.0},\n"
        "            \"perp\": {\"direction\": \"flat\", \"size\": 0.0}}\n"
    )
