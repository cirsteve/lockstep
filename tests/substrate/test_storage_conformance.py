"""Storage adapter conformance suite — runs against Mock and Real.

Day 3 §2.3 + Day 4 §A.3. The same six tests run against
``MockStorageAdapter`` and ``RealStorageAdapter``; both must pass for an
implementation to be considered Protocol-compliant. Real-adapter cases
are gated by the ``LOCKSTEP_TEST_REAL_STORAGE=1`` environment variable
so they don't run by default and don't break CI when the testnet is
flaky.

When ``LOCKSTEP_TEST_REAL_STORAGE=1`` is set the fixture also probes
the TS storage service's ``/healthz`` and skips with a clear message if
the service isn't running on the configured URL. Boot it with
``cd services/storage-ts && npm run dev`` first.

Per the §A.3 review item, partial-pass classification splits failures
into integrity-vs-transport categories: ``TrustViolation`` is byzantine
evidence (substrate bug — fix before merging), other ``SubstrateError``
subclasses are transport (could retry up to 3× with backoff). The
``classify_failure`` helper at the bottom of the file supports test
runners that want to triage failures programmatically.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest

from lockstep.domains.coin_flip.evaluation import CoinFlipEvaluation
from lockstep.errors import SubstrateError, TrustViolation
from lockstep.evaluation.receipt import (
    EnclaveAttestation,
    Receipt,
    ReceiptKind,
)
from lockstep.evaluation.solution import DatasetCommitment
from lockstep.substrate.attestation import MockAttestationAdapter
from lockstep.substrate.storage import MockStorageAdapter
from lockstep.substrate.storage_real import RealStorageAdapter


def _root(payload: bytes) -> str:
    return "0x" + hashlib.sha256(payload).hexdigest()


def _build_dataset_commitment(public: bytes, private: bytes) -> DatasetCommitment:
    pub_root = _root(public)
    priv_root = _root(private)
    combined = hashlib.sha256(
        bytes.fromhex(pub_root[2:]) + bytes.fromhex(priv_root[2:])
    ).hexdigest()
    return DatasetCommitment(
        domain="conformance",
        merkle_root="0x" + combined,
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="mock://dataset/conformance",
        schema_version="v1",
    )


def _build_signed_receipt() -> tuple[Receipt, EnclaveAttestation]:
    """Construct a Receipt with a genuinely-valid ed25519 signature.

    Borrows the coin_flip toy evaluator (the abstraction-proof domain)
    so we have a fully-registered Evaluator without coupling the
    substrate conformance suite to a specific production domain. Uses
    MockAttestationAdapter to produce the signature so the receipt
    validates under EnclaveAttestation.verify_signature.
    """
    from lockstep.evaluation.evaluation import GraderResult

    attestation_adapter = MockAttestationAdapter()
    pubkey, privkey = attestation_adapter.generate_attestation_keypair()
    evaluator = CoinFlipEvaluation().evaluator()

    grader_result = GraderResult(
        public_score_vector={"hits": 1.0},
        full_score_vector=None,
        metadata={},
    )
    receipt = attestation_adapter.produce_receipt(
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
    return receipt, receipt.enclave


# ---------------------------------------------------------------------------
# Parameterized adapter fixture
# ---------------------------------------------------------------------------


def _real_adapter_or_skip(tmp_path: Path) -> RealStorageAdapter:
    if os.environ.get("LOCKSTEP_TEST_REAL_STORAGE") != "1":
        pytest.skip("set LOCKSTEP_TEST_REAL_STORAGE=1 to run real-adapter conformance")
    rpc_url = os.environ.get("LOCKSTEP_0G_GALILEO_RPC", "https://evmrpc-testnet.0g.ai")
    indexer_url = os.environ.get(
        "LOCKSTEP_0G_GALILEO_INDEXER", "https://indexer-storage-testnet-turbo.0g.ai"
    )
    service_url = os.environ.get(
        "LOCKSTEP_0G_STORAGE_SERVICE_URL", "http://localhost:7878"
    )
    if not os.environ.get("LOCKSTEP_0G_PRIVATE_KEY"):
        pytest.skip("LOCKSTEP_0G_PRIVATE_KEY not set; cannot run real-adapter conformance")

    # Probe the TS storage service before constructing the adapter. A
    # missing service is the most common reason these tests fail in
    # ways that look like infrastructure outages but are actually local
    # — boot the service with `cd services/storage-ts && npm run dev`.
    try:
        resp = httpx.get(f"{service_url}/healthz", timeout=3.0)
    except httpx.RequestError as exc:
        pytest.skip(
            f"TS storage service unreachable at {service_url} ({exc}). "
            "Boot it with `cd services/storage-ts && npm run dev` before "
            "running the Real conformance branch."
        )
    if resp.status_code != 200:
        pytest.skip(
            f"TS storage service /healthz returned {resp.status_code} "
            f"(expected 200): {resp.text[:200]}"
        )
    health = resp.json()
    if not health.get("indexer_reachable", False):
        pytest.skip(
            f"TS storage service can't reach the 0G indexer at "
            f"{health.get('indexer_url')}; conformance can't run end-to-end."
        )

    return RealStorageAdapter(
        rpc_url=rpc_url,
        indexer_url=indexer_url,
        signer_key=os.environ["LOCKSTEP_0G_PRIVATE_KEY"],
        service_url=service_url,
        log_path=tmp_path / "storage.jsonl",
    )


@pytest.fixture(
    params=[
        pytest.param("mock", id="mock"),
        pytest.param("real", id="real"),
    ]
)
def adapter(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    if request.param == "mock":
        return MockStorageAdapter()
    return _real_adapter_or_skip(tmp_path)


# ---------------------------------------------------------------------------
# The six spec-required conformance tests
# ---------------------------------------------------------------------------


def test_upload_download_roundtrip_preserves_bytes(adapter: Any) -> None:
    bundle = b"some-encrypted-bundle" * 16
    pubkey = "0x" + "ab" * 32
    plaintext_commitment = "0x" + "cd" * 32

    enc = adapter.upload_encrypted_solution(
        bundle,
        plaintext_commitment=plaintext_commitment,
        recipient_pubkey=pubkey,
    )
    downloaded = adapter.download_encrypted_solution(enc.storage_uri)

    assert downloaded == bundle
    assert enc.plaintext_commitment == plaintext_commitment
    assert enc.bundle_hash == _root(bundle)


def test_download_with_wrong_merkle_commitment_raises_trust_violation(
    adapter: Any,
) -> None:
    public = b"public-portion" * 16
    private = b"private-portion" * 16
    commitment = _build_dataset_commitment(public, private)
    adapter.upload_dataset(commitment, public, private)

    bad_commitment = commitment.model_copy(update={"public_root": "0x" + "00" * 32})
    with pytest.raises(TrustViolation):
        adapter.load_dataset_public(bad_commitment)


def test_load_dataset_full_with_unauthorized_pubkey_raises(adapter: Any) -> None:
    public = b"public-portion" * 16
    private = b"private-portion" * 16
    commitment = _build_dataset_commitment(public, private)
    adapter.upload_dataset(commitment, public, private)

    unauthorized = "0x" + "ff" * 32
    with pytest.raises(TrustViolation):
        adapter.load_dataset_full(commitment, attestation_pubkey=unauthorized)


def test_load_dataset_full_with_authorized_pubkey_returns_full_payload(
    adapter: Any,
) -> None:
    public = b"public-portion" * 16
    private = b"private-portion" * 16
    commitment = _build_dataset_commitment(public, private)
    adapter.upload_dataset(commitment, public, private)

    authorized = "0x" + "cc" * 32
    adapter.authorize_attestation(authorized)
    full = adapter.load_dataset_full(commitment, attestation_pubkey=authorized)
    assert full == public + private


def test_upload_receipt_then_download_returns_equal_receipt(adapter: Any) -> None:
    receipt, _ = _build_signed_receipt()
    uri = adapter.upload_receipt(receipt)
    downloaded = adapter.download_receipt(uri)
    assert downloaded.receipt_id == receipt.receipt_id
    assert downloaded.canonical_signing_payload() == receipt.canonical_signing_payload()
    assert downloaded.enclave.signature == receipt.enclave.signature


def test_signature_verification_fails_on_tampered_bytes(adapter: Any) -> None:
    """A receipt whose signature doesn't validate against its canonical
    payload (e.g. signature replaced with garbage) must fail download
    with TrustViolation rather than returning a bad receipt to callers."""
    receipt, enclave = _build_signed_receipt()
    tampered_enclave = enclave.model_copy(update={"signature": "0x" + "00" * 64})
    tampered = receipt.model_copy(update={"enclave": tampered_enclave})

    uri = adapter.upload_receipt(tampered)
    with pytest.raises(TrustViolation):
        adapter.download_receipt(uri)


# ---------------------------------------------------------------------------
# Partial-pass classification helper (Day 4 §A.3)
# ---------------------------------------------------------------------------


FailureKind = Literal["integrity", "transport", "unknown"]


def classify_failure(exc: BaseException) -> FailureKind:
    """Classify a conformance-test failure for partial-pass triage.

    The §A.3 review item splits the response on a partial pass:

    - ``integrity`` failures (``TrustViolation``) are byzantine
      evidence — the substrate produced bytes that disagree with their
      commitment, or a signature didn't verify, or an unauthorized
      pubkey reached sealed data. Treat as a substrate bug; fix in
      the TS service or Python adapter before considering §A landed.
      **Do not retry past an integrity failure.**

    - ``transport`` failures (``SubstrateError`` that isn't
      ``TrustViolation``) are transient — timeouts, indexer flakes,
      RPC drops. Retry up to 3× with backoff. If still failing,
      document as known-flaky in the test docstring and surface in §F
      STATUS so Day 5 can revisit.

    - ``unknown`` covers everything else (assertion errors, unrelated
      exceptions). Treat as a hard failure; investigate before
      assuming retry is appropriate.

    Use from a wrapper test runner that's responsible for triage; the
    individual tests don't change behavior based on this function.
    """
    if isinstance(exc, TrustViolation):
        return "integrity"
    if isinstance(exc, SubstrateError):
        return "transport"
    return "unknown"


def test_classify_failure_matrix() -> None:
    """Sanity-check the classifier against representative exceptions."""
    assert classify_failure(TrustViolation("byzantine")) == "integrity"
    assert classify_failure(SubstrateError("indexer timeout")) == "transport"
    assert classify_failure(AssertionError("unexpected")) == "unknown"
    assert classify_failure(RuntimeError("other")) == "unknown"
