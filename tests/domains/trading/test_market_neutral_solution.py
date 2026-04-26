"""Acceptance tests for MarketNeutralSolution."""

from __future__ import annotations

import pytest

from lockstep.domains.trading.market_neutral.solution import (
    MarketNeutralSolution,
    SandboxError,
)

SOURCE = (
    "def signal(funding_window, basis_window, state):\n"
    "    return {\n"
    "        \"spot\": {\"direction\": \"long\", \"size\": 1.0},\n"
    "        \"perp\": {\"direction\": \"short\", \"size\": 1.0},\n"
    "    }\n"
)


def test_serialize_deserialize_roundtrips():
    sol = MarketNeutralSolution(source=SOURCE, parameters=b"fitted")
    back = MarketNeutralSolution.deserialize(sol.serialize())
    assert back == sol


def test_deserialize_rejects_trailing_bytes():
    sol = MarketNeutralSolution(source=SOURCE, parameters=b"")
    blob = sol.serialize() + b"\x00trailing"
    with pytest.raises(ValueError, match="trailing bytes"):
        MarketNeutralSolution.deserialize(blob)


def test_plaintext_commitment_stable_across_roundtrips():
    sol1 = MarketNeutralSolution(source=SOURCE)
    sol2 = MarketNeutralSolution.deserialize(sol1.serialize())
    assert sol1.plaintext_commitment() == sol2.plaintext_commitment()


def test_instantiate_returns_callable_with_paired_legs():
    sol = MarketNeutralSolution(source=SOURCE)
    signal = sol.instantiate()
    out = signal([], [], {})
    assert isinstance(out, dict)
    assert set(out.keys()) == {"spot", "perp"}
    for leg in out.values():
        assert isinstance(leg, dict)
        assert set(leg.keys()) == {"direction", "size"}
        assert leg["direction"] in ("long", "short", "flat")


def test_solver_attempting_import_os_is_rejected():
    bad = "import os\n" + SOURCE
    sol = MarketNeutralSolution(source=bad)
    with pytest.raises(SandboxError, match="import"):
        sol.instantiate()


def test_solver_attempting_from_import_is_rejected():
    bad = "from os import path\n" + SOURCE
    sol = MarketNeutralSolution(source=bad)
    with pytest.raises(SandboxError, match="import"):
        sol.instantiate()
