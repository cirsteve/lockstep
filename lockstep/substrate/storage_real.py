"""Real 0G Storage adapter — skeleton + SDK-agnostic helpers.

Day 3 ships the helpers and the constructor-level config plumbing
(retry budget, structured log writer, cost tracker, network endpoints).
The actual SDK-touching method bodies wait for Day 4, when wallet
credentials and the SDK choice (between the published Python SDK,
TS-via-subprocess, or direct JSON-RPC) can be exercised against
0G Galileo. Until then, the methods raise ``NotImplementedError`` and
the conformance tests for the real path skip cleanly.

Design notes:
- Construction must succeed without credentials so the factory can
  build the adapter from config. Method calls fail until Day 4.
- ``_RetryBudget`` and ``_with_retry`` only retry ``SubstrateError``;
  ``TrustViolation`` is byzantine evidence and propagates immediately.
- ``_CostTracker`` raises ``SubstrateError`` on budget exhaustion
  rather than silently draining the testnet faucet.
- ``_log_event`` accepts a per-instance log path so parallel pytest
  workers don't race on a shared file.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lockstep.errors import SubstrateError, TrustViolation
from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.solution import DatasetCommitment, EncryptedSolution

if TYPE_CHECKING:
    from lockstep.evaluation.receipt import Receipt


_LOG_PATH_DEFAULT = Path("logs/substrate-storage.jsonl")
_DEFAULT_TOKEN_BUDGET = Decimal("100")


@dataclass(frozen=True)
class _RetryBudget:
    """Retry policy for transient ``SubstrateError`` from the real adapter.

    The wall-clock budget covers the original attempt plus all retry
    sleeps, so a slow upstream consumes the budget even if no errors
    occur. Stop early if the budget is exhausted mid-backoff rather
    than oversleeping.
    """

    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 4.0
    max_attempts: int = 4  # 1 original + up to 3 retries
    wall_clock_seconds: float = 30.0

    def __post_init__(self) -> None:
        # Construction-time validation. With these guarantees, _with_retry
        # is provably entered at least once, so its "no attempt completed"
        # fallback is unreachable by construction.
        if self.max_attempts < 1:
            raise ValueError(
                f"max_attempts must be >= 1, got {self.max_attempts}"
            )
        if self.wall_clock_seconds <= 0:
            raise ValueError(
                f"wall_clock_seconds must be > 0, got {self.wall_clock_seconds}"
            )
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError(
                "base_delay_seconds and max_delay_seconds must be non-negative"
            )

    def delay_for_attempt(self, attempt_index: int) -> float:
        """Return the sleep before attempt N (0-indexed). Attempt 0 has no delay."""
        if attempt_index <= 0:
            return 0.0
        raw = self.base_delay_seconds * (2 ** (attempt_index - 1))
        return min(raw, self.max_delay_seconds)


def _with_retry(
    fn: Callable[[], Any],
    *,
    budget: _RetryBudget,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Any:
    """Call ``fn()`` with retries on ``SubstrateError``.

    ``TrustViolation`` (a ``SubstrateError`` subclass) propagates
    immediately without retry — it's evidence of byzantine behavior,
    not a transient failure. Non-SubstrateError exceptions also
    propagate without retry.

    The wall-clock budget is checked both before and after each backoff
    sleep: if the sleep itself consumes the remaining budget (or
    ``time.sleep`` oversleeps), the next attempt is skipped rather than
    fired past the deadline.

    ``sleep`` and ``monotonic`` are injected so tests can drive the
    retry loop deterministically.
    """
    deadline = monotonic() + budget.wall_clock_seconds
    last_error: SubstrateError | None = None
    for attempt in range(budget.max_attempts):
        delay = budget.delay_for_attempt(attempt)
        if delay > 0:
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleep(min(delay, remaining))
            # Re-check post-sleep — the sleep may have consumed the
            # remaining budget (or overslept slightly). Don't fire
            # another attempt past the deadline.
            if monotonic() >= deadline:
                break
        try:
            return fn()
        except TrustViolation:
            raise
        except SubstrateError as exc:
            last_error = exc
            if monotonic() >= deadline:
                break
    # Unreachable when _RetryBudget construction validation passes
    # (max_attempts >= 1 guarantees at least one fn() call), but kept
    # as a typed-non-None guarantee for the raise.
    raise last_error or SubstrateError(
        "retry budget exhausted before any attempt completed"
    )


def _log_event(log_path: Path, event: dict[str, Any]) -> None:
    """Append one JSONL event to ``log_path`` and fsync.

    Parent directory is created on demand. Schema fields used by the
    real adapter: ``ts``, ``op``, ``uri``, ``bytes``, ``cost``,
    ``latency_ms``, ``status``. Add fields freely; never remove.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, sort_keys=True, separators=(",", ":"))
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


@dataclass
class _CostTracker:
    """Per-process accumulator with a hard budget cap.

    All amounts are ``Decimal`` to match the determinism rules in
    CLAUDE.md (no float arithmetic where precision matters).
    Exceeding the cap raises ``SubstrateError`` so a runaway loop
    cannot drain the testnet faucet between commits.
    """

    budget: Decimal
    spent: Decimal = field(default_factory=lambda: Decimal("0"))

    def charge(self, amount: Decimal) -> None:
        """Charge ``amount`` to the running total, or raise if it would exceed budget."""
        if amount < 0:
            raise ValueError(f"cost must be non-negative, got {amount}")
        new_total = self.spent + amount
        if new_total > self.budget:
            raise SubstrateError(
                f"token budget exhausted: would spend {new_total}, cap is {self.budget}"
            )
        self.spent = new_total

    def remaining(self) -> Decimal:
        return self.budget - self.spent


class RealStorageAdapter:
    """0G Galileo storage adapter — Day 3 skeleton.

    Construction succeeds without credentials. Method calls raise
    ``NotImplementedError`` until Day 4 wires the SDK and credentials.
    The factory can therefore build this from config, the conformance
    suite can construct it for parameterized tests (the real-path
    cases skip when ``LOCKSTEP_TEST_REAL_STORAGE`` is unset), and the
    Day 4 PR fills in the method bodies without changing the surface.
    """

    def __init__(
        self,
        *,
        rpc_url: str,
        indexer_url: str,
        signer_key: str | None = None,
        token_budget: Decimal | str | float = _DEFAULT_TOKEN_BUDGET,
        log_path: Path | None = None,
        retry_budget: _RetryBudget | None = None,
    ) -> None:
        self._rpc_url = rpc_url
        self._indexer_url = indexer_url
        self._signer_key = signer_key
        self._cost = _CostTracker(budget=Decimal(str(token_budget)))
        self._log_path = log_path or _LOG_PATH_DEFAULT
        self._retry_budget = retry_budget or _RetryBudget()
        self._authorized_attestations: set[str] = set()

    def authorize_attestation(self, pubkey: Bytes32Hex) -> None:
        """Register an attestation pubkey as authorized for full-dataset reads.

        **Test scaffolding, not part of the StorageAdapter Protocol.**
        Mirrors ``MockStorageAdapter.authorize_attestation`` so the
        conformance suite and demo can construct either adapter and
        seed an authorized pubkey before calling ``load_dataset_full``.
        In production the authorization comes from the ERC-7857 oracle
        re-encryption ceremony on the chain side — this method goes
        away once that flow lands (Day 5+).
        """
        self._authorized_attestations.add(pubkey.lower())

    def cost_spent(self) -> Decimal:
        """Cumulative testnet token cost charged through this adapter instance."""
        return self._cost.spent

    def upload_encrypted_solution(
        self,
        bundle: bytes,
        *,
        plaintext_commitment: Bytes32Hex,
        recipient_pubkey: Bytes32Hex,
    ) -> EncryptedSolution:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )

    def download_encrypted_solution(self, uri: str) -> bytes:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )

    def upload_receipt(self, receipt: Receipt) -> str:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )

    def download_receipt(self, uri: str) -> Receipt:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )

    def upload_dataset(
        self,
        commitment: DatasetCommitment,
        public_payload: bytes,
        private_payload: bytes,
    ) -> None:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )

    def load_dataset_public(self, commitment: DatasetCommitment) -> bytes:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )

    def load_dataset_full(
        self, commitment: DatasetCommitment, attestation_pubkey: Bytes32Hex
    ) -> bytes:
        raise NotImplementedError(
            "RealStorageAdapter pending SDK choice + Galileo credentials — Day 4 PR"
        )


__all__ = ["RealStorageAdapter"]
