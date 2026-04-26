"""Unit tests for the SDK-agnostic helpers in storage_real.

The full conformance suite parameterized over Mock + Real adapters
arrives in a later commit (Day 3 §2.3). These tests cover the helper
classes in isolation so the retry, log, and cost-tracker logic is
verified before the SDK-touching method bodies land in Day 4.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from lockstep.errors import SubstrateError, TrustViolation
from lockstep.substrate.storage_real import (
    RealStorageAdapter,
    _CostTracker,
    _RetryBudget,
    _log_event,
    _with_retry,
)


class _FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_retry_budget_exponential_with_cap():
    budget = _RetryBudget(base_delay_seconds=0.5, max_delay_seconds=4.0)
    assert budget.delay_for_attempt(0) == 0.0
    assert budget.delay_for_attempt(1) == 0.5
    assert budget.delay_for_attempt(2) == 1.0
    assert budget.delay_for_attempt(3) == 2.0
    assert budget.delay_for_attempt(4) == 4.0  # 8.0 capped to 4.0
    assert budget.delay_for_attempt(10) == 4.0


def test_with_retry_returns_first_success_without_sleeping():
    clock = _FakeClock()
    calls = 0

    def fn() -> str:
        nonlocal calls
        calls += 1
        return "ok"

    result = _with_retry(
        fn, budget=_RetryBudget(), sleep=clock.sleep, monotonic=clock.monotonic
    )
    assert result == "ok"
    assert calls == 1
    assert clock.sleeps == []


def test_with_retry_recovers_from_transient_substrate_error():
    clock = _FakeClock()
    calls = 0

    def fn() -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise SubstrateError("transient")
        return "recovered"

    result = _with_retry(
        fn,
        budget=_RetryBudget(base_delay_seconds=0.5, max_delay_seconds=4.0),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    assert result == "recovered"
    assert calls == 3
    assert clock.sleeps == [0.5, 1.0]  # delays before attempts 1 and 2


def test_with_retry_exhausts_attempts_and_raises_last_substrate_error():
    clock = _FakeClock()
    errors = [SubstrateError(f"attempt {i}") for i in range(10)]
    seen = iter(errors)

    def fn() -> None:
        raise next(seen)

    with pytest.raises(SubstrateError, match="attempt 3"):
        _with_retry(
            fn,
            budget=_RetryBudget(max_attempts=4),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )


def test_with_retry_propagates_trust_violation_immediately():
    clock = _FakeClock()
    calls = 0

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise TrustViolation("byzantine evidence")

    with pytest.raises(TrustViolation):
        _with_retry(
            fn, budget=_RetryBudget(), sleep=clock.sleep, monotonic=clock.monotonic
        )
    assert calls == 1
    assert clock.sleeps == []


def test_with_retry_does_not_retry_unrelated_exceptions():
    clock = _FakeClock()
    calls = 0

    def fn() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("not a substrate problem")

    with pytest.raises(ValueError):
        _with_retry(
            fn, budget=_RetryBudget(), sleep=clock.sleep, monotonic=clock.monotonic
        )
    assert calls == 1


def test_with_retry_stops_when_wall_clock_budget_exhausted_before_next_sleep():
    # Budget too small to fit even the first backoff sleep.
    clock = _FakeClock()
    calls = 0

    def fn() -> None:
        nonlocal calls
        calls += 1
        # Burn the wall clock during the first attempt itself.
        clock.now += 100.0
        raise SubstrateError("slow upstream")

    with pytest.raises(SubstrateError, match="slow upstream"):
        _with_retry(
            fn,
            budget=_RetryBudget(wall_clock_seconds=30.0),
            sleep=clock.sleep,
            monotonic=clock.monotonic,
        )
    assert calls == 1
    assert clock.sleeps == []  # never slept; budget already gone


def test_log_event_appends_jsonl_and_creates_parent_dir(tmp_path: Path):
    log = tmp_path / "nested" / "substrate-storage.jsonl"
    _log_event(log, {"op": "upload", "bytes": 42, "status": "ok"})
    _log_event(log, {"op": "download", "bytes": 17, "status": "ok"})

    lines = log.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first == {"bytes": 42, "op": "upload", "status": "ok"}
    assert second == {"bytes": 17, "op": "download", "status": "ok"}


def test_log_event_uses_sorted_keys_for_canonical_serialization(tmp_path: Path):
    log = tmp_path / "log.jsonl"
    _log_event(log, {"z": 1, "a": 2, "m": 3})
    line = log.read_text().strip()
    assert line == '{"a":2,"m":3,"z":1}'


def test_cost_tracker_charges_within_budget():
    tracker = _CostTracker(budget=Decimal("10"))
    tracker.charge(Decimal("3"))
    tracker.charge(Decimal("4"))
    assert tracker.spent == Decimal("7")
    assert tracker.remaining() == Decimal("3")


def test_cost_tracker_raises_substrate_error_on_overdraw():
    tracker = _CostTracker(budget=Decimal("10"))
    tracker.charge(Decimal("8"))
    with pytest.raises(SubstrateError, match="token budget exhausted"):
        tracker.charge(Decimal("3"))
    # State unchanged after refused charge.
    assert tracker.spent == Decimal("8")


def test_cost_tracker_rejects_negative_charge():
    tracker = _CostTracker(budget=Decimal("10"))
    with pytest.raises(ValueError):
        tracker.charge(Decimal("-1"))


def test_real_storage_adapter_constructs_without_credentials():
    adapter = RealStorageAdapter(
        rpc_url="https://evmrpc-testnet.0g.ai",
        indexer_url="https://indexer-storage-testnet-turbo.0g.ai",
    )
    assert adapter.cost_spent() == Decimal("0")


def test_real_storage_adapter_authorize_attestation_normalizes_case():
    adapter = RealStorageAdapter(
        rpc_url="https://evmrpc-testnet.0g.ai",
        indexer_url="https://indexer-storage-testnet-turbo.0g.ai",
    )
    pubkey = "0x" + "AB" * 32
    adapter.authorize_attestation(pubkey)
    assert pubkey.lower() in adapter._authorized_attestations


def test_real_storage_adapter_methods_raise_not_implemented():
    adapter = RealStorageAdapter(
        rpc_url="https://evmrpc-testnet.0g.ai",
        indexer_url="https://indexer-storage-testnet-turbo.0g.ai",
    )
    bundle = b"x" * 32
    pubkey = "0x" + "ab" * 32
    plaintext_commitment = "0x" + "cd" * 32

    with pytest.raises(NotImplementedError, match="Day 4"):
        adapter.upload_encrypted_solution(
            bundle,
            plaintext_commitment=plaintext_commitment,
            recipient_pubkey=pubkey,
        )
    with pytest.raises(NotImplementedError, match="Day 4"):
        adapter.download_encrypted_solution("zg://anything")


def test_real_storage_adapter_accepts_token_budget_as_string_or_float():
    a1 = RealStorageAdapter(rpc_url="r", indexer_url="i", token_budget="50.5")
    a2 = RealStorageAdapter(rpc_url="r", indexer_url="i", token_budget=50.5)
    a3 = RealStorageAdapter(rpc_url="r", indexer_url="i", token_budget=Decimal("50.5"))
    assert a1._cost.budget == Decimal("50.5")
    assert a2._cost.budget == Decimal("50.5")
    assert a3._cost.budget == Decimal("50.5")
