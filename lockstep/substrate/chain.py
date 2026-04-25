"""Chain adapter — vendor-agnostic Protocol + in-memory Mock.

Production binding (Day 3+): 0G Chain. Receipts ride into ERC-7857 iNFT
metadata; ``authorize_usage`` corresponds to the iNFT's
``authorizeUsage()`` call; evaluator registration writes to a registry
contract. The Mock keeps everything in process dicts.

Tx hashes are sha256 over a counter + timestamp so they look like real
ones to the eye and are unique within a process, but they're not derived
from the operations they reference.
"""

from __future__ import annotations

import hashlib
import time
from typing import Protocol

from lockstep.evaluation.canonical import Address, Bytes32Hex
from lockstep.evaluation.evaluator import Evaluator
from lockstep.evaluation.receipt import Receipt


class ChainError(RuntimeError):
    """Raised when an on-chain operation references something that doesn't exist."""


class ChainAdapter(Protocol):
    def register_evaluator_onchain(self, evaluator: Evaluator) -> Bytes32Hex: ...

    def mint_inft(self, receipt: Receipt, owner: Address) -> int: ...

    def read_inft_metadata(self, token_id: int) -> Receipt: ...

    def authorize_usage(
        self, token_id: int, executor: Address, signature: bytes
    ) -> Bytes32Hex: ...

    def submit_challenge(
        self, receipt_id: Bytes32Hex, divergent_receipt: Receipt
    ) -> Bytes32Hex: ...

    def read_evaluator(self, evaluator_id: Bytes32Hex) -> Evaluator | None: ...


class MockChainAdapter:
    """In-memory chain state with realistic-looking tx hashes."""

    def __init__(self) -> None:
        self._evaluators: dict[Bytes32Hex, Evaluator] = {}
        self._next_token_id = 1
        self._tokens: dict[int, Receipt] = {}
        self._token_owners: dict[int, Address] = {}
        self._authorizations: dict[int, list[tuple[Address, bytes]]] = {}
        self._challenges: dict[Bytes32Hex, list[Receipt]] = {}
        self._tx_counter = 0

    def _new_tx_hash(self) -> Bytes32Hex:
        self._tx_counter += 1
        seed = f"{self._tx_counter}:{time.time_ns()}".encode()
        return "0x" + hashlib.sha256(seed).hexdigest()

    def register_evaluator_onchain(self, evaluator: Evaluator) -> Bytes32Hex:
        self._evaluators[evaluator.evaluator_id] = evaluator
        return self._new_tx_hash()

    def read_evaluator(self, evaluator_id: Bytes32Hex) -> Evaluator | None:
        return self._evaluators.get(evaluator_id)

    def mint_inft(self, receipt: Receipt, owner: Address) -> int:
        token_id = self._next_token_id
        self._next_token_id += 1
        self._tokens[token_id] = receipt
        self._token_owners[token_id] = owner
        return token_id

    def read_inft_metadata(self, token_id: int) -> Receipt:
        if token_id not in self._tokens:
            raise ChainError(f"unknown token_id: {token_id}")
        return self._tokens[token_id]

    def authorize_usage(
        self, token_id: int, executor: Address, signature: bytes
    ) -> Bytes32Hex:
        if token_id not in self._tokens:
            raise ChainError(f"unknown token_id: {token_id}")
        self._authorizations.setdefault(token_id, []).append((executor, signature))
        return self._new_tx_hash()

    def is_authorized(self, token_id: int, executor: Address) -> bool:
        """Test helper. Real ChainAdapter would query an event log."""
        return any(addr == executor for addr, _ in self._authorizations.get(token_id, []))

    def submit_challenge(
        self, receipt_id: Bytes32Hex, divergent_receipt: Receipt
    ) -> Bytes32Hex:
        self._challenges.setdefault(receipt_id, []).append(divergent_receipt)
        return self._new_tx_hash()

    def challenge_count(self, receipt_id: Bytes32Hex) -> int:
        """Test helper."""
        return len(self._challenges.get(receipt_id, []))

    def list_token_ids(self) -> list[int]:
        """Test/demo helper. Real adapter would query indexed events."""
        return sorted(self._tokens.keys())
