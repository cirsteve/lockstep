"""Unit tests for ``RealStorageAdapter`` via mocked HTTP.

These tests exercise the wiring between the adapter's public methods and
``_StorageHttpClient`` without needing the live TS service. The full
end-to-end conformance suite (``test_storage_conformance.py``) covers
the same surface against running infrastructure when
``LOCKSTEP_TEST_REAL_STORAGE=1`` is set.

What's verified here:

- 502 from the service triggers ``_with_retry``: multiple attempts fire,
  the eventual error is a ``SubstrateError``.
- 422 propagates as ``TrustViolation`` immediately — exactly one attempt.
- ``_CostTracker`` accumulates from receipt-derived (or baseline-fallback)
  cost on every upload path.
- ``_log_event`` writes one structured JSONL line per call, with the
  expected fields populated.
- Defense-in-depth on ``load_dataset_public``: a 200 with bytes whose
  sha256 doesn't match ``commitment.public_root`` raises
  ``TrustViolation`` even though the service responded with success.
- Defense-in-depth on ``download_receipt``: a receipt whose enclave
  signature doesn't verify raises ``TrustViolation``.
- ``authorize_attestation`` POSTs to the service before seeding the
  local set.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from lockstep.domains.coin_flip.evaluation import CoinFlipEvaluation
from lockstep.errors import SubstrateError, TrustViolation
from lockstep.evaluation.canonical import canonical_json_bytes
from lockstep.evaluation.evaluation import GraderResult
from lockstep.evaluation.receipt import Receipt, ReceiptKind
from lockstep.evaluation.solution import DatasetCommitment
from lockstep.substrate.attestation import MockAttestationAdapter
from lockstep.substrate.storage_real import (
    _UPLOAD_COST_BASELINE,
    RealStorageAdapter,
    _RetryBudget,
)

SERVICE_URL = "http://test-service:7878"


def _make_adapter(tmp_path: Path) -> RealStorageAdapter:
    """Adapter pointed at a fake service URL that respx intercepts.

    Tight retry budget so tests that exhaust retries finish in <1s.
    The Web3 provider points at an unreachable RPC so ``_fetch_cost_0g``
    falls back to ``_UPLOAD_COST_BASELINE`` — that's the deterministic
    cost we assert against.
    """
    return RealStorageAdapter(
        rpc_url="http://test-rpc:8545",
        indexer_url="http://test-indexer:5678",
        service_url=SERVICE_URL,
        log_path=tmp_path / "storage.jsonl",
        retry_budget=_RetryBudget(
            base_delay_seconds=0.0,
            max_delay_seconds=0.0,
            max_attempts=3,
            wall_clock_seconds=10.0,
        ),
    )


def _read_log(log_path: Path) -> list[dict[str, Any]]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text().splitlines() if line]


def _ok_upload_solution_response(bundle: bytes) -> dict[str, Any]:
    bundle_hash = "0x" + hashlib.sha256(bundle).hexdigest()
    root_hash = "0x" + "aa" * 32
    return {
        "plaintext_commitment": "0x" + "cc" * 32,
        "bundle_hash": bundle_hash,
        "storage_uri": f"zg://{root_hash}",
        "recipient_pubkey": "0x" + "bb" * 32,
        "encryption_scheme": "x25519-chacha20poly1305-mock",
        "tx_hash": "0x" + "11" * 32,
        "root_hash": root_hash,
        "tx_seq": 50000,
        "size_bytes": len(bundle),
    }


# ---------------------------------------------------------------------------
# upload_encrypted_solution
# ---------------------------------------------------------------------------


@respx.mock
def test_upload_encrypted_solution_charges_baseline_cost_when_receipt_unreachable(
    tmp_path: Path,
) -> None:
    bundle = b"encrypted-bundle"
    pubkey = "0x" + "bb" * 32
    plaintext_commitment = "0x" + "cc" * 32

    respx.post(f"{SERVICE_URL}/upload-encrypted-solution").mock(
        return_value=httpx.Response(200, json=_ok_upload_solution_response(bundle))
    )

    adapter = _make_adapter(tmp_path)
    enc = adapter.upload_encrypted_solution(
        bundle,
        plaintext_commitment=plaintext_commitment,
        recipient_pubkey=pubkey,
    )

    assert enc.bundle_hash == "0x" + hashlib.sha256(bundle).hexdigest()
    assert enc.recipient_pubkey == pubkey
    assert enc.plaintext_commitment == plaintext_commitment
    # web3 RPC unreachable → _fetch_cost_0g falls back to baseline.
    assert adapter.cost_spent() == _UPLOAD_COST_BASELINE

    events = _read_log(tmp_path / "storage.jsonl")
    assert len(events) == 1
    assert events[0]["op"] == "upload_encrypted_solution"
    assert events[0]["bytes"] == len(bundle)
    assert events[0]["cost"] == str(_UPLOAD_COST_BASELINE)
    assert events[0]["status"] == "ok"


@respx.mock
def test_upload_encrypted_solution_502_retries_until_budget_exhausted(
    tmp_path: Path,
) -> None:
    route = respx.post(f"{SERVICE_URL}/upload-encrypted-solution").mock(
        return_value=httpx.Response(
            502, json={"error": "upload_failed", "detail": "indexer unreachable"}
        )
    )

    adapter = _make_adapter(tmp_path)
    with pytest.raises(SubstrateError, match="upload_failed"):
        adapter.upload_encrypted_solution(
            b"bundle",
            plaintext_commitment="0x" + "cc" * 32,
            recipient_pubkey="0x" + "bb" * 32,
        )

    # max_attempts=3 in the test budget.
    assert route.call_count == 3
    # No cost charged since every attempt failed before the cost-tracker line.
    assert adapter.cost_spent() == Decimal("0")


@respx.mock
def test_upload_encrypted_solution_422_raises_trust_violation_without_retry(
    tmp_path: Path,
) -> None:
    route = respx.post(f"{SERVICE_URL}/upload-encrypted-solution").mock(
        return_value=httpx.Response(
            422,
            json={
                "error": "public_root_mismatch",
                "detail": "sha256 mismatch",
            },
        )
    )

    adapter = _make_adapter(tmp_path)
    with pytest.raises(TrustViolation, match="public_root_mismatch"):
        adapter.upload_encrypted_solution(
            b"bundle",
            plaintext_commitment="0x" + "cc" * 32,
            recipient_pubkey="0x" + "bb" * 32,
        )

    # 422 propagates immediately — exactly one attempt.
    assert route.call_count == 1


# ---------------------------------------------------------------------------
# upload_dataset
# ---------------------------------------------------------------------------


@respx.mock
def test_upload_dataset_charges_cost_for_both_halves(tmp_path: Path) -> None:
    public_payload = b"public-data"
    private_payload = b"private-data"
    public_root = "0x" + hashlib.sha256(public_payload).hexdigest()
    private_root = "0x" + hashlib.sha256(private_payload).hexdigest()

    respx.post(f"{SERVICE_URL}/upload-dataset").mock(
        return_value=httpx.Response(
            200,
            json={
                "public_storage_uri": "zg://0x" + "11" * 32,
                "private_storage_uri": "zg://0x" + "22" * 32,
                "public_root_hash": "0x" + "11" * 32,
                "private_root_hash": "0x" + "22" * 32,
                "public_tx_hash": "0x" + "aa" * 32,
                "private_tx_hash": "0x" + "bb" * 32,
                "public_tx_seq": 100,
                "private_tx_seq": 101,
                "public_size_bytes": len(public_payload),
                "private_size_bytes": len(private_payload),
            },
        )
    )

    adapter = _make_adapter(tmp_path)
    commitment = DatasetCommitment(
        domain="conformance",
        merkle_root="0x" + "ff" * 32,
        public_root=public_root,
        private_root=private_root,
        storage_uri="mock://dataset/test",
        schema_version="v1",
    )
    adapter.upload_dataset(commitment, public_payload, private_payload)

    # Two upload paths inside one call → 2× baseline.
    assert adapter.cost_spent() == _UPLOAD_COST_BASELINE * 2

    events = _read_log(tmp_path / "storage.jsonl")
    assert len(events) == 1
    assert events[0]["op"] == "upload_dataset"
    assert events[0]["bytes"] == len(public_payload) + len(private_payload)


# ---------------------------------------------------------------------------
# load_dataset_public defense-in-depth
# ---------------------------------------------------------------------------


@respx.mock
def test_load_dataset_public_sha256_mismatch_raises_trust_violation(
    tmp_path: Path,
) -> None:
    """If the service returns 200 with bytes whose sha256 differs from
    the commitment's public_root, the adapter recomputes and raises
    TrustViolation. Defense-in-depth — even server-verified bytes get
    checked against the producer's commitment."""

    public_payload = b"the-real-bytes"
    public_root = "0x" + hashlib.sha256(public_payload).hexdigest()
    # Service returns DIFFERENT bytes (e.g. a TS-side bug or transport
    # corruption that the server's own sha256 check missed somehow).
    served_bytes = b"different-bytes"

    respx.get(f"{SERVICE_URL}/load-dataset-public").mock(
        return_value=httpx.Response(
            200,
            content=served_bytes,
            headers={"Content-Type": "application/octet-stream"},
        )
    )

    adapter = _make_adapter(tmp_path)
    commitment = DatasetCommitment(
        domain="conformance",
        merkle_root="0x" + "ff" * 32,
        public_root=public_root,
        private_root="0x" + "00" * 32,
        storage_uri="mock://dataset/test",
        schema_version="v1",
    )

    with pytest.raises(TrustViolation, match="public payload root mismatch"):
        adapter.load_dataset_public(commitment)


@respx.mock
def test_load_dataset_public_returns_bytes_on_match(tmp_path: Path) -> None:
    public_payload = b"the-real-bytes"
    public_root = "0x" + hashlib.sha256(public_payload).hexdigest()

    respx.get(f"{SERVICE_URL}/load-dataset-public").mock(
        return_value=httpx.Response(200, content=public_payload)
    )

    adapter = _make_adapter(tmp_path)
    commitment = DatasetCommitment(
        domain="conformance",
        merkle_root="0x" + "ff" * 32,
        public_root=public_root,
        private_root="0x" + "00" * 32,
        storage_uri="mock://dataset/test",
        schema_version="v1",
    )
    result = adapter.load_dataset_public(commitment)
    assert result == public_payload


# ---------------------------------------------------------------------------
# download_receipt signature defense-in-depth
# ---------------------------------------------------------------------------


def _build_signed_receipt() -> Receipt:
    """Mirror the conformance-suite helper: a Receipt with a real ed25519
    signature so verify_signature returns True."""
    attestation = MockAttestationAdapter()
    pubkey, privkey = attestation.generate_attestation_keypair()
    evaluator = CoinFlipEvaluation().evaluator()
    grader_result = GraderResult(
        public_score_vector={"hits": 1.0},
        full_score_vector=None,
        metadata={},
    )
    return attestation.produce_receipt(
        grader_result=grader_result,
        evaluator=evaluator,
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment="0x" + "02" * 32,
        solution_bundle_hash="0x" + "03" * 32,
        dataset_commitment="0x" + "04" * 32,
        grader_version=evaluator.accepted_grader_versions[0],
        private_key=privkey,
        pubkey=pubkey,
        kind=ReceiptKind.INITIAL_GRADING,
        created_at=datetime(2026, 4, 25, 12, 0, tzinfo=UTC),
    )


@respx.mock
def test_download_receipt_returns_parsed_receipt_on_valid_signature(
    tmp_path: Path,
) -> None:
    receipt = _build_signed_receipt()
    body = canonical_json_bytes(receipt.model_dump(mode="json"))
    respx.get(f"{SERVICE_URL}/download-receipt").mock(
        return_value=httpx.Response(200, content=body)
    )

    adapter = _make_adapter(tmp_path)
    result = adapter.download_receipt("zg://0x" + "ee" * 32)
    assert result.receipt_id == receipt.receipt_id
    assert result.enclave.pubkey == receipt.enclave.pubkey


@respx.mock
def test_download_receipt_invalid_signature_raises_trust_violation(
    tmp_path: Path,
) -> None:
    """Tamper with the signature bytes after signing; pydantic validation
    still passes (the signature field is a valid hex string), but
    verify_signature returns False and the adapter raises
    TrustViolation. Receipt content fields (which feed into receipt_id)
    aren't touched, so the model_validate_json doesn't trip on the
    receipt_id-mismatch validator."""
    receipt = _build_signed_receipt()
    payload = receipt.model_dump(mode="json")
    # Replace signature with a syntactically valid but cryptographically
    # garbage value (flip the last byte).
    sig = payload["enclave"]["signature"]
    flipped = sig[:-2] + ("01" if sig[-2:] != "01" else "02")
    payload["enclave"]["signature"] = flipped
    body = canonical_json_bytes(payload)

    respx.get(f"{SERVICE_URL}/download-receipt").mock(
        return_value=httpx.Response(200, content=body)
    )

    adapter = _make_adapter(tmp_path)
    with pytest.raises(TrustViolation, match="receipt signature invalid"):
        adapter.download_receipt("zg://0x" + "ee" * 32)


# ---------------------------------------------------------------------------
# authorize_attestation
# ---------------------------------------------------------------------------


@respx.mock
def test_authorize_attestation_posts_to_service_then_seeds_local_set(
    tmp_path: Path,
) -> None:
    pubkey = "0x" + "DD" * 32  # uppercase to verify normalization
    route = respx.post(f"{SERVICE_URL}/authorize-attestation").mock(
        return_value=httpx.Response(204)
    )

    adapter = _make_adapter(tmp_path)
    adapter.authorize_attestation(pubkey)

    # POSTed to service.
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body == {"pubkey": pubkey}
    # And added to local set, normalized to lowercase.
    assert pubkey.lower() in adapter._authorized_attestations


@respx.mock
def test_authorize_attestation_service_failure_does_not_seed_local_set(
    tmp_path: Path,
) -> None:
    """If the service POST fails (5xx after retries), the local set
    must not be populated — the two halves of the gate must stay in
    sync."""
    pubkey = "0x" + "DD" * 32
    respx.post(f"{SERVICE_URL}/authorize-attestation").mock(
        return_value=httpx.Response(503, json={"error": "internal", "detail": "db down"})
    )

    adapter = _make_adapter(tmp_path)
    with pytest.raises(SubstrateError):
        adapter.authorize_attestation(pubkey)
    assert pubkey.lower() not in adapter._authorized_attestations


# ---------------------------------------------------------------------------
# load_dataset_full local short-circuit
# ---------------------------------------------------------------------------


def test_load_dataset_full_short_circuits_on_unauthorized_pubkey(
    tmp_path: Path,
) -> None:
    """The local set check fires before any network call. No respx route
    needs to be registered — if the adapter were calling the service,
    httpx would error out on the unmocked URL."""
    adapter = _make_adapter(tmp_path)
    commitment = DatasetCommitment(
        domain="conformance",
        merkle_root="0x" + "ff" * 32,
        public_root="0x" + "11" * 32,
        private_root="0x" + "22" * 32,
        storage_uri="mock://dataset/test",
        schema_version="v1",
    )
    with pytest.raises(TrustViolation, match="not authorized"):
        adapter.load_dataset_full(commitment, attestation_pubkey="0x" + "ee" * 32)


# ---------------------------------------------------------------------------
# log shape
# ---------------------------------------------------------------------------


@respx.mock
def test_log_event_shape_has_all_required_fields(tmp_path: Path) -> None:
    bundle = b"bundle"
    respx.post(f"{SERVICE_URL}/upload-encrypted-solution").mock(
        return_value=httpx.Response(200, json=_ok_upload_solution_response(bundle))
    )

    adapter = _make_adapter(tmp_path)
    adapter.upload_encrypted_solution(
        bundle,
        plaintext_commitment="0x" + "cc" * 32,
        recipient_pubkey="0x" + "bb" * 32,
    )

    events = _read_log(tmp_path / "storage.jsonl")
    assert len(events) == 1
    event = events[0]
    # Required schema fields per storage_real._emit_log:
    for field in ("ts", "op", "uri", "bytes", "cost", "latency_ms", "status"):
        assert field in event, f"missing log field: {field}"
    assert isinstance(event["ts"], (int, float))
    assert isinstance(event["latency_ms"], (int, float))
    assert event["latency_ms"] >= 0
