"""MarketNeutralSolution — Python-source strategy for market-neutral trading.

Solver contract
---------------
The submitted source string must define a top-level function::

    def signal(funding_window, basis_window, state) -> dict[str, dict]:
        # funding_window: list[FundingBar] — perp funding history through t-1
        # basis_window:   list[BasisBar]   — joined spot+perp through t-1
        # state:          dict             — strategy-private mutable scratchpad
        # returns: {"spot": {"direction": ..., "size": ...},
        #          "perp": {"direction": ..., "size": ...}}

A ``FundingBar`` carries ``timestamp``, ``funding_rate``, ``regime``
(one of ``"funding_positive"``, ``"funding_negative"``,
``"funding_neutral"``). A ``BasisBar`` carries ``timestamp``,
``spot_close``, ``perp_close``, and ``basis``.

Each leg's ``size`` is in [0, 1] meaning fraction of notional. The
grader penalizes net directional exposure (|spot_pos + perp_pos|)
through the ``directional_exposure_max`` metric.

Sandboxing
----------
``instantiate()`` runs the source through ``exec`` with a constrained
``builtins`` mapping. **This is not a security boundary.** It catches
accidental ``import os`` in reference strategies; it does not contain
adversarial code. Production replaces this with subprocess + seccomp,
WASM, or gVisor isolation.
"""

from __future__ import annotations

import ast
import struct
from collections.abc import Callable
from typing import Any

from pydantic import ConfigDict

from lockstep.evaluation.solution import SolutionPayload

_ALLOWED_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "dict": dict,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "int": int,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "range": range,
    "reversed": reversed,
    "round": round,
    "set": set,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "zip": zip,
    "True": True,
    "False": False,
    "None": None,
}

# Identifiers a legitimate solver doesn't need. Substring check is a
# cheap surface filter; AST parsing below catches all import forms.
_FORBIDDEN_SUBSTRINGS = (
    "__import__",
    "open(",
    "compile(",
    "exec(",
    "eval(",
    "globals(",
    "locals(",
    "__builtins__",
    "__class__",
    "__subclasses__",
    "__bases__",
    "__mro__",
    "__getattribute__",
)


class SandboxError(RuntimeError):
    """Raised when a solver source is rejected or fails to instantiate."""


class MarketNeutralSolution(SolutionPayload):
    """Python-source market-neutral strategy submission."""

    model_config = ConfigDict(frozen=True)

    source: str
    parameters: bytes = b""

    def serialize(self) -> bytes:
        src = self.source.encode("utf-8")
        return (
            struct.pack(">I", len(src))
            + src
            + struct.pack(">I", len(self.parameters))
            + self.parameters
        )

    @classmethod
    def deserialize(cls, data: bytes) -> MarketNeutralSolution:
        if len(data) < 4:
            raise ValueError("MarketNeutralSolution.deserialize: data too short")
        src_len = struct.unpack(">I", data[:4])[0]
        offset = 4
        if len(data) < offset + src_len + 4:
            raise ValueError("MarketNeutralSolution.deserialize: truncated source")
        source = data[offset : offset + src_len].decode("utf-8")
        offset += src_len
        param_len = struct.unpack(">I", data[offset : offset + 4])[0]
        offset += 4
        if len(data) < offset + param_len:
            raise ValueError("MarketNeutralSolution.deserialize: truncated parameters")
        parameters = data[offset : offset + param_len]
        return cls(source=source, parameters=parameters)

    def instantiate(self) -> Callable[[list[dict], list[dict], dict], dict]:
        for token in _FORBIDDEN_SUBSTRINGS:
            if token in self.source:
                raise SandboxError(
                    f"solver source contains forbidden token: {token!r}"
                )

        try:
            tree = ast.parse(self.source, "<solver>", "exec")
        except SyntaxError as exc:
            raise SandboxError(f"solver source failed to parse: {exc}") from exc

        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise SandboxError(
                    "solver source contains an import statement; "
                    "imports are not permitted in the sandbox"
                )

        sandbox_globals: dict[str, Any] = {
            "__builtins__": _ALLOWED_BUILTINS,
            "parameters": self.parameters,
        }
        try:
            exec(compile(tree, "<solver>", "exec"), sandbox_globals)  # noqa: S102 — sandbox surface
        except Exception as exc:
            raise SandboxError(f"solver source failed to compile: {exc}") from exc

        signal = sandbox_globals.get("signal")
        if not callable(signal):
            raise SandboxError("solver source must define a callable named 'signal'")
        return signal
