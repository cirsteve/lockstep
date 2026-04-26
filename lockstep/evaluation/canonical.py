"""
Shared protocol-level primitives: canonical JSON serialization and the
hex-string type aliases that flow through every Layer 2 schema.

These names sit in their own module because they're imported across
receipt.py, solution.py, evaluator.py, and any substrate adapter that
needs to verify a hash. Keeping them together avoids cross-module imports
of underscore-prefixed names and keeps the protocol surface honest about
what is and isn't private.

Treat the output of ``canonical_json_bytes`` as part of the on-chain
protocol surface. Any change to its serialization rules invalidates every
receipt and every evaluator hash produced before the change. Bump
coordinated with a grader-version bump.
"""

from __future__ import annotations

import json
from typing import Any

Bytes32Hex = str
"""0x-prefixed 64-character hex string. Represents a bytes32 on-chain;
hex-encoded for Python ergonomics and converted at the substrate boundary."""

Address = str
"""0x-prefixed 40-character hex string. Ethereum-style address."""

ZERO_BYTES32: Bytes32Hex = "0x" + "00" * 32


def canonical_json_bytes(obj: Any) -> bytes:
    """
    Deterministic JSON serialization for canonical signing and hashing.

    Rules:
    - Keys sorted lexicographically at every depth
    - No whitespace
    - UTF-8 encoded output
    - NaN and infinities rejected
    - Integers and floats both serialize natively; the schema enforces
      that score values are finite floats so JSON's float ambiguity is
      bounded.
    """
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
