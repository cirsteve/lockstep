"""Real 0G Storage adapter — wired to the TS storage service.

The adapter is a thin orchestrator: ``_StorageHttpClient`` talks to
``services/storage-ts/`` over HTTP, this class wraps each call in
``_with_retry``, charges ``_CostTracker`` from the on-chain receipt,
emits a structured log event, and runs defense-in-depth integrity
checks where there's a real trust anchor (``commitment.public_root``
on dataset loads, the receipt's enclave signature on
``download_receipt``).

Design notes:
- Construction must succeed without contacting the network — both
  ``_StorageHttpClient`` and ``Web3.HTTPProvider`` lazy-connect on first
  request. The factory can build the adapter from config without the
  TS service running.
- ``_RetryBudget`` and ``_with_retry`` only retry ``SubstrateError``;
  ``TrustViolation`` is byzantine evidence and propagates immediately.
- ``_CostTracker`` raises ``SubstrateError`` on budget exhaustion
  rather than silently draining the testnet faucet. Receipt-derived
  cost falls back to ``_UPLOAD_COST_BASELINE`` if the RPC fetch fails;
  silent zero would let a runaway loop drain the faucet.
- ``_log_event`` accepts a per-instance log path so parallel pytest
  workers don't race on a shared file.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from eth_typing import HexStr
from web3 import Web3

from lockstep.errors import SubstrateError, TrustViolation
from lockstep.evaluation.canonical import Bytes32Hex, canonical_json_bytes
from lockstep.evaluation.solution import DatasetCommitment, EncryptedSolution
from lockstep.substrate._storage_http import _StorageHttpClient

if TYPE_CHECKING:
    from lockstep.evaluation.receipt import Receipt


_LOG_PATH_DEFAULT = Path("logs/substrate-storage.jsonl")
_DEFAULT_TOKEN_BUDGET = Decimal("100")
_DEFAULT_SERVICE_URL = "http://localhost:7878"
# Measured 2026-04-26 baseline (~1.16 mG per upload). Charged when the
# on-chain receipt fetch fails so a runaway loop can't drain the faucet
# silently. See spec/day-04-LEARNINGS.md §D2 for the cost breakdown.
_UPLOAD_COST_BASELINE = Decimal("0.0012")


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
    """0G Galileo storage adapter wired to the TS storage service.

    Each public method is a thin orchestration over ``_StorageHttpClient``:
    wrap in ``_with_retry``, charge cost from the chain receipt, emit a
    log event. Defense-in-depth integrity checks run at the boundaries
    that have a real trust anchor (commitment roots on dataset loads,
    enclave signature on receipt download).
    """

    def __init__(
        self,
        *,
        rpc_url: str,
        indexer_url: str,
        service_url: str = _DEFAULT_SERVICE_URL,
        token_budget: Decimal | str | float = _DEFAULT_TOKEN_BUDGET,
        log_path: Path | None = None,
        retry_budget: _RetryBudget | None = None,
    ) -> None:
        # The signing key (LOCKSTEP_0G_PRIVATE_KEY) is read by the TS
        # storage service at boot, not here. The Python adapter never
        # touches the wallet directly — it relays bytes through the
        # service's HTTP surface. See services/storage-ts/README.md
        # "Trust boundary".
        self._rpc_url = rpc_url
        self._indexer_url = indexer_url
        self._service_url = service_url
        self._cost = _CostTracker(budget=Decimal(str(token_budget)))
        self._log_path = log_path or _LOG_PATH_DEFAULT
        self._retry_budget = retry_budget or _RetryBudget()
        self._authorized_attestations: set[str] = set()
        self._http = _StorageHttpClient(service_url)
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))

    def close(self) -> None:
        """Close the HTTP client. Tests use this; the demo flow leaks."""
        self._http.close()

    def authorize_attestation(self, pubkey: Bytes32Hex) -> None:
        """Register an attestation pubkey as authorized for full-dataset reads.

        **Test scaffolding, not part of the StorageAdapter Protocol.**
        Mirrors ``MockStorageAdapter.authorize_attestation``. Posts to
        the TS service so its in-memory gate is also populated, then
        adds to the local set so ``load_dataset_full`` can short-circuit
        unauthorized requests before the network call.

        In production the authorization comes from the ERC-7857 oracle
        re-encryption ceremony on the chain side — this method goes
        away once that flow lands (Day 5+).
        """
        def fn() -> None:
            self._http.authorize_attestation(pubkey)
        _with_retry(fn, budget=self._retry_budget)
        self._authorized_attestations.add(pubkey.lower())

    def cost_spent(self) -> Decimal:
        """Cumulative testnet token cost charged through this adapter instance."""
        return self._cost.spent

    # ---- internals ----

    def _fetch_cost_0g(self, tx_hash: str) -> Decimal:
        """Fetch transaction receipt + tx, return total spend in 0G.

        cost = gasUsed * effectiveGasPrice + tx.value (storage market fee).

        Falls back to ``_UPLOAD_COST_BASELINE`` on any failure — RPC
        unreachable, missing fields (legacy receipts may lack
        ``effectiveGasPrice``; we fall back to the tx's ``gasPrice``),
        decode errors, anything. Silent zero would let a runaway loop
        drain the faucet without registering the spend.
        """
        tx_hash_hex = HexStr(tx_hash)
        try:
            receipt = self._w3.eth.get_transaction_receipt(tx_hash_hex)
            tx = self._w3.eth.get_transaction(tx_hash_hex)
            gas_price = receipt.get("effectiveGasPrice") or tx.get("gasPrice")
            if gas_price is None:
                return _UPLOAD_COST_BASELINE
            cost_wei = int(receipt["gasUsed"]) * int(gas_price) + int(tx["value"])
            return Decimal(cost_wei) / Decimal(10**18)
        except Exception:
            return _UPLOAD_COST_BASELINE

    def _emit_log(
        self,
        op: str,
        *,
        uri: str = "",
        bytes_: int = 0,
        cost: Decimal = Decimal(0),
        latency_ms: float = 0.0,
        status: str = "ok",
    ) -> None:
        _log_event(
            self._log_path,
            {
                "ts": time.time(),
                "op": op,
                "uri": uri,
                "bytes": bytes_,
                "cost": str(cost),
                "latency_ms": round(latency_ms, 3),
                "status": status,
            },
        )

    def upload_encrypted_solution(
        self,
        bundle: bytes,
        *,
        plaintext_commitment: Bytes32Hex,
        recipient_pubkey: Bytes32Hex,
    ) -> EncryptedSolution:
        # Only the network call lives inside _with_retry. Cost charging
        # and log emission happen exactly once after the upload succeeds.
        # See lockstep/substrate/storage_real.py:_CostTracker docstring
        # for why this matters: _cost.charge raises SubstrateError on
        # budget exhaustion, and if that fired inside the retried fn()
        # we'd burn an extra paid upload per retry.
        t0 = time.monotonic()

        def fn() -> dict[str, Any]:
            return self._http.upload_encrypted_solution(
                bundle,
                plaintext_commitment=plaintext_commitment,
                recipient_pubkey=recipient_pubkey,
            )

        resp = _with_retry(fn, budget=self._retry_budget)
        cost = self._fetch_cost_0g(resp["tx_hash"])
        self._cost.charge(cost)
        self._emit_log(
            "upload_encrypted_solution",
            uri=resp["storage_uri"],
            bytes_=len(bundle),
            cost=cost,
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return EncryptedSolution(
            plaintext_commitment=plaintext_commitment,
            bundle_hash=resp["bundle_hash"],
            storage_uri=resp["storage_uri"],
            recipient_pubkey=recipient_pubkey,
        )

    def download_encrypted_solution(self, uri: str) -> bytes:
        def fn() -> bytes:
            t0 = time.monotonic()
            body = self._http.download_encrypted_solution(uri)
            self._emit_log(
                "download_encrypted_solution",
                uri=uri,
                bytes_=len(body),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
            return body

        return _with_retry(fn, budget=self._retry_budget)

    def upload_receipt(self, receipt: Receipt) -> str:
        # See `upload_encrypted_solution` for why cost-charge sits
        # outside `_with_retry`.
        t0 = time.monotonic()
        body = canonical_json_bytes(receipt.model_dump(mode="json"))

        def fn() -> dict[str, Any]:
            return self._http.upload_receipt(body)

        resp = _with_retry(fn, budget=self._retry_budget)
        cost = self._fetch_cost_0g(resp["tx_hash"])
        self._cost.charge(cost)
        self._emit_log(
            "upload_receipt",
            uri=resp["uri"],
            bytes_=len(body),
            cost=cost,
            latency_ms=(time.monotonic() - t0) * 1000,
        )
        return resp["uri"]

    def download_receipt(self, uri: str) -> Receipt:
        from lockstep.evaluation.receipt import Receipt as _Receipt

        def fn() -> Receipt:
            t0 = time.monotonic()
            body = self._http.download_receipt(uri)
            receipt = _Receipt.model_validate_json(body)
            if not receipt.enclave.verify_signature(
                receipt.canonical_signing_payload()
            ):
                raise TrustViolation(
                    f"receipt signature invalid for {uri} "
                    f"(pubkey {receipt.enclave.pubkey})"
                )
            self._emit_log(
                "download_receipt",
                uri=uri,
                bytes_=len(body),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
            return receipt

        return _with_retry(fn, budget=self._retry_budget)

    def upload_dataset(
        self,
        commitment: DatasetCommitment,
        public_payload: bytes,
        private_payload: bytes,
    ) -> None:
        # See `upload_encrypted_solution` for why cost-charge sits
        # outside `_with_retry`. The dataset case fires *two* paid
        # uploads; doubling them up via a budget-exhausted retry would
        # cost up to `2 * max_attempts` × baseline.
        t0 = time.monotonic()

        def fn() -> dict[str, Any]:
            return self._http.upload_dataset(
                public_root=commitment.public_root,
                private_root=commitment.private_root,
                public_payload=public_payload,
                private_payload=private_payload,
            )

        resp = _with_retry(fn, budget=self._retry_budget)
        cost = self._fetch_cost_0g(resp["public_tx_hash"]) + self._fetch_cost_0g(
            resp["private_tx_hash"]
        )
        self._cost.charge(cost)
        self._emit_log(
            "upload_dataset",
            uri=commitment.storage_uri,
            bytes_=len(public_payload) + len(private_payload),
            cost=cost,
            latency_ms=(time.monotonic() - t0) * 1000,
        )

    def load_dataset_public(self, commitment: DatasetCommitment) -> bytes:
        def fn() -> bytes:
            t0 = time.monotonic()
            body = self._http.load_dataset_public(commitment.public_root)
            actual_root = "0x" + hashlib.sha256(body).hexdigest()
            if actual_root != commitment.public_root.lower():
                raise TrustViolation(
                    f"public payload root mismatch for commitment "
                    f"{commitment.storage_uri}: expected "
                    f"{commitment.public_root}, got {actual_root}"
                )
            self._emit_log(
                "load_dataset_public",
                uri=commitment.storage_uri,
                bytes_=len(body),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
            return body

        return _with_retry(fn, budget=self._retry_budget)

    def load_dataset_full(
        self, commitment: DatasetCommitment, attestation_pubkey: Bytes32Hex
    ) -> bytes:
        # Local short-circuit: fail before the network call if the pubkey
        # was never authorized via this adapter. Mirrors Mock semantics
        # and saves one round-trip on the unauthorized path.
        if attestation_pubkey.lower() not in self._authorized_attestations:
            raise TrustViolation(
                f"attestation pubkey {attestation_pubkey} not authorized "
                "for full dataset access"
            )

        def fn() -> bytes:
            t0 = time.monotonic()
            # The TS service verifies sha256(public) == public_root and
            # sha256(private) == private_root before returning the
            # concatenated body. We don't get the split point in the
            # response, so per-side defense-in-depth is server-side only.
            body = self._http.load_dataset_full(
                public_root=commitment.public_root,
                private_root=commitment.private_root,
                attestation_pubkey=attestation_pubkey,
            )
            self._emit_log(
                "load_dataset_full",
                uri=commitment.storage_uri,
                bytes_=len(body),
                latency_ms=(time.monotonic() - t0) * 1000,
            )
            return body

        return _with_retry(fn, budget=self._retry_budget)


__all__ = ["RealStorageAdapter"]
