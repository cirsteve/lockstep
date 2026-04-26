"""Attestation adapter — ed25519 signing wired to receipt construction.

Production binding (Day 3+): a real TEE (0G Sealed Inference, Intel TDX,
or NVIDIA confidential GPU) exposes a hardware-bound attestation key. The
adapter wraps that, taking a canonical signing payload in and emitting an
``EnclaveAttestation`` proving the bytes were signed by an attested key.

The mock here uses real ed25519 from ``cryptography``. The signature is
genuine; only the attestation chain is fake. Validators that re-derive
the receipt_id and verify the signature against the pubkey will accept
the receipt as long as the pubkey shape is right — which is what we want
for tests, and is the right behavioral shape for the validator network.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from lockstep.errors import SubstrateError
from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.evaluation import GraderResult
from lockstep.evaluation.evaluator import Evaluator
from lockstep.evaluation.receipt import (
    EnclaveAttestation,
    Receipt,
    ReceiptKind,
)


class AttestationError(SubstrateError):
    """Raised when an attestation fails to verify or a key is malformed."""


class AttestationAdapter(Protocol):
    """Vendor-agnostic attestation interface."""

    def generate_attestation_keypair(self) -> tuple[Bytes32Hex, bytes]: ...

    def sign_canonical_payload(self, payload: bytes, private_key: bytes) -> EnclaveAttestation: ...

    def verify_attestation(self, attestation: EnclaveAttestation, payload: bytes) -> bool: ...

    def produce_receipt(
        self,
        *,
        grader_result: GraderResult,
        evaluator: Evaluator,
        problem_id: Bytes32Hex,
        solution_plaintext_commitment: Bytes32Hex,
        solution_bundle_hash: Bytes32Hex,
        dataset_commitment: Bytes32Hex,
        grader_version: Bytes32Hex,
        private_key: bytes,
        pubkey: Bytes32Hex,
        kind: ReceiptKind = ReceiptKind.INITIAL_GRADING,
        previous_receipt_id: Bytes32Hex | None = None,
        created_at: datetime | None = None,
    ) -> Receipt: ...


class MockAttestationAdapter:
    """Ed25519-backed attestation with a fake attestation chain.

    The signature is real ed25519 over the canonical signing payload. The
    ``attestation_chain`` field is a placeholder ``b"tee_mock"`` —
    production will populate it with a vendor-specific quote.
    """

    def __init__(self, *, enclave_kind: str = "tee_mock") -> None:
        self._enclave_kind = enclave_kind

    def generate_attestation_keypair(self) -> tuple[Bytes32Hex, bytes]:
        sk = Ed25519PrivateKey.generate()
        pk = sk.public_key()
        pub_bytes = pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
        priv_bytes = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
        return "0x" + pub_bytes.hex(), priv_bytes

    def sign_canonical_payload(self, payload: bytes, private_key: bytes) -> EnclaveAttestation:
        try:
            sk = Ed25519PrivateKey.from_private_bytes(private_key)
        except Exception as exc:
            raise AttestationError("invalid ed25519 private key") from exc
        sig = sk.sign(payload)
        pk = sk.public_key()
        pub_hex = "0x" + pk.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
        return EnclaveAttestation(
            pubkey=pub_hex,
            signature="0x" + sig.hex(),
            attestation_chain=b"tee_mock",
            enclave_kind=self._enclave_kind,
        )

    def verify_attestation(self, attestation: EnclaveAttestation, payload: bytes) -> bool:
        try:
            pub_bytes = bytes.fromhex(attestation.pubkey[2:])
            sig_hex = attestation.signature
            if sig_hex.startswith("0x"):
                sig_hex = sig_hex[2:]
            sig = bytes.fromhex(sig_hex)
            pk = Ed25519PublicKey.from_public_bytes(pub_bytes)
            pk.verify(sig, payload)
            return True
        except (InvalidSignature, ValueError):
            return False

    def produce_receipt(
        self,
        *,
        grader_result: GraderResult,
        evaluator: Evaluator,
        problem_id: Bytes32Hex,
        solution_plaintext_commitment: Bytes32Hex,
        solution_bundle_hash: Bytes32Hex,
        dataset_commitment: Bytes32Hex,
        grader_version: Bytes32Hex,
        private_key: bytes,
        pubkey: Bytes32Hex,
        kind: ReceiptKind = ReceiptKind.INITIAL_GRADING,
        previous_receipt_id: Bytes32Hex | None = None,
        created_at: datetime | None = None,
    ) -> Receipt:
        """Orchestrate the signing flow described in ``Receipt.build``.

        1. Compute the canonical signing payload from all receipt fields
           except receipt_id and the enclave attestation.
        2. Sign the payload with the enclave's private key.
        3. Construct the Receipt with the derived receipt_id and the
           freshly-built attestation.
        """
        from datetime import UTC

        if created_at is None:
            created_at = datetime.now(UTC)

        # One source of truth for the canonical signing bytes — the same
        # helper Receipt.build uses internally. Sign these exact bytes,
        # then hand off to Receipt.build which derives receipt_id from
        # the identical helper. No body duplication, no drift.
        payload = Receipt.signing_payload_for_fields(
            kind=kind,
            previous_receipt_id=previous_receipt_id,
            evaluator_id=evaluator.evaluator_id,
            domain=evaluator.domain_name,
            problem_id=problem_id,
            solution_plaintext_commitment=solution_plaintext_commitment,
            solution_bundle_hash=solution_bundle_hash,
            dataset_commitment=dataset_commitment,
            grader_version=grader_version,
            public_score_vector=grader_result.public_score_vector,
            full_score_vector=grader_result.full_score_vector,
            metadata=grader_result.metadata,
            created_at=created_at,
        )

        attestation = self.sign_canonical_payload(payload, private_key)
        if attestation.pubkey != pubkey.lower():
            raise AttestationError(
                f"declared pubkey {pubkey} does not match private key's pubkey "
                f"{attestation.pubkey}"
            )

        return Receipt.build(
            kind=kind,
            evaluator_id=evaluator.evaluator_id,
            domain=evaluator.domain_name,
            problem_id=problem_id,
            solution_plaintext_commitment=solution_plaintext_commitment,
            solution_bundle_hash=solution_bundle_hash,
            dataset_commitment=dataset_commitment,
            grader_version=grader_version,
            public_score_vector=grader_result.public_score_vector,
            full_score_vector=grader_result.full_score_vector,
            metadata=grader_result.metadata,
            previous_receipt_id=previous_receipt_id,
            created_at=created_at,
            enclave=attestation,
        )
