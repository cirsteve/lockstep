"""Acceptance tests for DirectionalSolution."""

from __future__ import annotations

import pytest

from lockstep.domains.trading.directional.solution import (
    DirectionalSolution,
    SandboxError,
)

SOURCE = (
    "def signal(window, state):\n"
    "    return {\"direction\": \"long\", \"size\": 0.5}\n"
)


def test_serialize_deserialize_roundtrips():
    sol = DirectionalSolution(source=SOURCE, parameters=b"some-fitted-bytes")
    blob = sol.serialize()
    back = DirectionalSolution.deserialize(blob)
    assert back == sol


def test_plaintext_commitment_stable_across_roundtrips():
    sol1 = DirectionalSolution(source=SOURCE, parameters=b"")
    sol2 = DirectionalSolution.deserialize(sol1.serialize())
    assert sol1.plaintext_commitment() == sol2.plaintext_commitment()


def test_instantiate_returns_callable_conforming_to_output_schema():
    sol = DirectionalSolution(source=SOURCE)
    signal = sol.instantiate()
    out = signal([], {})
    assert isinstance(out, dict)
    assert out.keys() == {"direction", "size"}
    assert out["direction"] in ("long", "short", "flat")
    assert isinstance(out["size"], (int, float))


def test_solver_attempting_import_os_is_rejected():
    bad_source = (
        "import os\n"
        "def signal(window, state):\n"
        "    return {\"direction\": \"flat\", \"size\": 0.0}\n"
    )
    sol = DirectionalSolution(source=bad_source)
    with pytest.raises(SandboxError, match="forbidden"):
        sol.instantiate()


def test_solver_must_define_signal():
    bad_source = "x = 1\n"
    sol = DirectionalSolution(source=bad_source)
    with pytest.raises(SandboxError, match="signal"):
        sol.instantiate()
