"""Tiny Merkle helper used by the mock storage adapter.

Production replaces this with whatever the substrate's chain commits to
on-chain (likely Keccak256 over a sparse Merkle tree). For the hackathon
we only need:

    1. Deterministic hashing of a leaf set into a single root.
    2. Recompute-and-compare on download.

Concatenated-pair hashing with sha256, with leaf duplication for odd
counts. Same shape every time so verification is deterministic.
"""

from __future__ import annotations

import hashlib


def _h(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def merkle_root(leaves: list[bytes]) -> str:
    """Compute the Merkle root of ``leaves``. Returns 0x-prefixed hex.

    Leaves are hashed first; pairs are concatenated and hashed again until
    one node remains. Odd counts duplicate the trailing leaf.
    """
    if not leaves:
        return "0x" + "00" * 32

    layer = [_h(leaf) for leaf in leaves]
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [_h(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
    return "0x" + layer[0].hex()
