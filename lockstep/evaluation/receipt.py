"""
Receipt schema — the canonical artifact of the Lockstep substrate.

A Receipt binds a graded solution to its evaluation conditions: which
solution was graded, against which dataset, by which grader version,
producing what scores, signed by which enclave. Receipts are public,
indefinitely retrievable, and independently verifiable.

Design corrections from v1:
    1. receipt_id is derived from the canonical hash of the receipt body,
       not manually supplied. Manually-supplied IDs would silently allow
       two different receipts to share an ID and break dispute resolution.
    2. canonical_signing_payload() defines the deterministic byte form
       that the enclave signs. Defining this now prevents two different
       serializations from both becoming "canonical" by accident.
    3. Public vs full validation paths are modeled explicitly: receipts
       carry both a public_score_vector (computable from public data
       only) and a full_score_vector (computable only with TEE access
       to the private holdout). Public-tier validators verify the public
       portion; full-tier validators verify both.

Production path (post-hackathon):
    - Receipt chains will be replaced by append-only logs on 0G Storage's
      Log primitive, with the iNFT's metadata pointer referencing the log
      head rather than the latest receipt.
    - Receipts will be cryptographically batched for gas efficiency when
      live-execution receipts arrive at high frequency.
    - Canonical serialization will use a binary format (CBOR or RLP) rather
      than canonical JSON, both for gas efficiency on-chain and for
      narrower deserialization attack surface.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import Bytes32Hex, canonical_json_bytes


class ReceiptKind(StrEnum):
    """
    The kind of evaluation event a receipt records.

    INITIAL_GRADING is produced when a solution is first graded against
    the canonical evaluation. Each solution has exactly one INITIAL_GRADING
    receipt.

    LIVE_EXECUTION is produced when a rented solution executes via a
    sealed executor. A solution can have many LIVE_EXECUTION receipts
    over time; they form the live track record that reputation aggregation
    consumes.

    REVALIDATION is produced when a validator re-runs grading and confirms
    (or disputes) an INITIAL_GRADING receipt. These are gossiped via AXL
    and may be promoted to on-chain challenges when they diverge.
    """

    INITIAL_GRADING = "initial_grading"
    LIVE_EXECUTION = "live_execution"
    REVALIDATION = "revalidation"


class ValidationTier(StrEnum):
    """
    Which dataset portion a score vector was computed against.

    PUBLIC scores are computed using only the public portion of the
    canonical dataset. Any validator can independently reproduce them
    by fetching public data and re-running the OSS grader.

    FULL scores are computed using the public portion plus the sealed
    private holdout. Only the original grader (running inside a TEE
    with sealed-data access) and full-tier validators (also running
    inside TEEs) can reproduce them.
    """

    PUBLIC = "public"
    FULL = "full"


class EnclaveAttestation(BaseModel):
    """
    Cryptographic proof that a receipt was produced inside a genuine TEE.

    The pubkey is bound to the hardware enclave at startup via remote
    attestation. The signature covers the canonical signing payload of
    the receipt body. Verifiers check the signature against the pubkey
    and verify the pubkey's attestation chain against the underlying TEE
    vendor's attestation infrastructure.
    """

    model_config = ConfigDict(frozen=True)

    pubkey: Bytes32Hex
    signature: str
    attestation_chain: bytes = b""
    enclave_kind: str = "tee_mock"

    @field_validator("pubkey")
    @classmethod
    def _validate_pubkey(cls, v: str) -> str:
        if not (v.startswith("0x") and len(v) == 66):
            raise ValueError("pubkey must be 0x-prefixed 32-byte hex")
        try:
            bytes.fromhex(v[2:])
        except ValueError as exc:
            raise ValueError("pubkey must be valid hex after 0x prefix") from exc
        return v.lower()

    def verify_signature(self, payload: bytes) -> bool:
        """Verify ``signature`` was produced by ``pubkey`` over ``payload``.

        Pure ed25519 check — does not validate the attestation chain
        (that's ``AttestationAdapter.verify_attestation`` for production
        TEE-vendor verification). Returned bool: True iff valid.
        Substrate adapters that detect a False here should raise
        ``lockstep.errors.TrustViolation`` rather than returning a
        bad receipt to a caller.
        """
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        try:
            pub_bytes = bytes.fromhex(self.pubkey[2:])
            sig_hex = self.signature[2:] if self.signature.startswith("0x") else self.signature
            sig_bytes = bytes.fromhex(sig_hex)
            Ed25519PublicKey.from_public_bytes(pub_bytes).verify(sig_bytes, payload)
            return True
        except (InvalidSignature, ValueError):
            return False


def derive_receipt_id(payload: bytes) -> Bytes32Hex:
    """
    Derive a receipt_id from its canonical signing payload.

    The receipt_id is the hash of the exact bytes the enclave signs. Two
    receipts with identical bodies have identical IDs; any mutation
    changes the ID; a receipt's ID is independently checkable by anyone
    with the receipt body.
    """
    return "0x" + hashlib.sha256(payload).hexdigest()


class Receipt(BaseModel):
    """
    The canonical evaluation artifact.

    Receipts bind together everything the substrate needs to prove what
    happened during grading: the solution that was graded (by both
    plaintext commitment and encrypted bundle hash), the dataset it was
    graded against (by Merkle commitment), the grader code that did the
    grading (by content-addressed hash), the scores produced (one per
    validation tier), and the enclave attestation that anchors all of it
    to genuine secure hardware.
    """

    model_config = ConfigDict(frozen=True)

    receipt_id: Bytes32Hex

    kind: ReceiptKind

    previous_receipt_id: Bytes32Hex | None = None

    evaluator_id: Bytes32Hex
    domain: str
    problem_id: Bytes32Hex

    solution_plaintext_commitment: Bytes32Hex
    solution_bundle_hash: Bytes32Hex

    dataset_commitment: Bytes32Hex
    grader_version: Bytes32Hex

    public_score_vector: dict[str, float]
    full_score_vector: dict[str, float] | None = None

    metadata: dict[str, Any] = Field(default_factory=dict)

    created_at: datetime
    enclave: EnclaveAttestation

    @field_validator("created_at")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware UTC")
        return v.astimezone(UTC)

    @field_validator("public_score_vector")
    @classmethod
    def _ensure_public_finite(cls, v: dict[str, float]) -> dict[str, float]:
        return _ensure_finite_scores("public_score_vector", v)

    @field_validator("full_score_vector")
    @classmethod
    def _ensure_full_finite(cls, v: dict[str, float] | None) -> dict[str, float] | None:
        if v is None:
            return None
        return _ensure_finite_scores("full_score_vector", v)

    @model_validator(mode="after")
    def _verify_receipt_id(self) -> Receipt:
        expected = derive_receipt_id(self.canonical_signing_payload())
        if self.receipt_id != expected:
            raise ValueError(
                f"receipt_id mismatch: stated={self.receipt_id} derived={expected}. "
                "Use Receipt.build(...) to construct receipts with derived IDs."
            )
        return self

    def canonical_signing_payload(self) -> bytes:
        """
        Deterministic byte form that the enclave signs.

        Excludes receipt_id (derived from this payload) and the enclave
        attestation (the signature *over* this payload). Includes
        everything else.
        """
        return self.signing_payload_for_fields(
            kind=self.kind,
            previous_receipt_id=self.previous_receipt_id,
            evaluator_id=self.evaluator_id,
            domain=self.domain,
            problem_id=self.problem_id,
            solution_plaintext_commitment=self.solution_plaintext_commitment,
            solution_bundle_hash=self.solution_bundle_hash,
            dataset_commitment=self.dataset_commitment,
            grader_version=self.grader_version,
            public_score_vector=self.public_score_vector,
            full_score_vector=self.full_score_vector,
            metadata=self.metadata,
            created_at=self.created_at,
        )

    @classmethod
    def signing_payload_for_fields(
        cls,
        *,
        kind: ReceiptKind,
        previous_receipt_id: Bytes32Hex | None,
        evaluator_id: Bytes32Hex,
        domain: str,
        problem_id: Bytes32Hex,
        solution_plaintext_commitment: Bytes32Hex,
        solution_bundle_hash: Bytes32Hex,
        dataset_commitment: Bytes32Hex,
        grader_version: Bytes32Hex,
        public_score_vector: dict[str, float],
        full_score_vector: dict[str, float] | None,
        metadata: dict[str, Any],
        created_at: datetime,
    ) -> bytes:
        """
        Single source of truth for the canonical signing payload bytes.

        Both ``Receipt.build`` and adapters that need to sign before
        construction (see ``substrate.attestation.produce_receipt``) call
        this so the signed bytes and the bytes ``canonical_signing_payload``
        returns at verify time can never drift apart.
        """
        body = {
            "kind": kind.value,
            "previous_receipt_id": previous_receipt_id,
            "evaluator_id": evaluator_id,
            "domain": domain,
            "problem_id": problem_id,
            "solution_plaintext_commitment": solution_plaintext_commitment,
            "solution_bundle_hash": solution_bundle_hash,
            "dataset_commitment": dataset_commitment,
            "grader_version": grader_version,
            "public_score_vector": public_score_vector,
            "full_score_vector": full_score_vector,
            "metadata": metadata,
            "created_at": created_at.astimezone(UTC).isoformat(),
        }
        return canonical_json_bytes(body)

    @classmethod
    def build(
        cls,
        *,
        kind: ReceiptKind,
        evaluator_id: Bytes32Hex,
        domain: str,
        problem_id: Bytes32Hex,
        solution_plaintext_commitment: Bytes32Hex,
        solution_bundle_hash: Bytes32Hex,
        dataset_commitment: Bytes32Hex,
        grader_version: Bytes32Hex,
        public_score_vector: dict[str, float],
        enclave: EnclaveAttestation,
        full_score_vector: dict[str, float] | None = None,
        metadata: dict[str, Any] | None = None,
        previous_receipt_id: Bytes32Hex | None = None,
        created_at: datetime | None = None,
    ) -> Receipt:
        """
        Construct a Receipt with a derived receipt_id.

        Computes the canonical signing payload, derives the receipt_id,
        and returns an immutable Receipt. Caller must supply an
        EnclaveAttestation already containing a signature over the
        canonical payload.
        """
        if created_at is None:
            created_at = datetime.now(UTC)

        payload = cls.signing_payload_for_fields(
            kind=kind,
            previous_receipt_id=previous_receipt_id,
            evaluator_id=evaluator_id,
            domain=domain,
            problem_id=problem_id,
            solution_plaintext_commitment=solution_plaintext_commitment,
            solution_bundle_hash=solution_bundle_hash,
            dataset_commitment=dataset_commitment,
            grader_version=grader_version,
            public_score_vector=public_score_vector,
            full_score_vector=full_score_vector,
            metadata=metadata or {},
            created_at=created_at,
        )
        receipt_id = derive_receipt_id(payload)

        return cls(
            receipt_id=receipt_id,
            kind=kind,
            previous_receipt_id=previous_receipt_id,
            evaluator_id=evaluator_id,
            domain=domain,
            problem_id=problem_id,
            solution_plaintext_commitment=solution_plaintext_commitment,
            solution_bundle_hash=solution_bundle_hash,
            dataset_commitment=dataset_commitment,
            grader_version=grader_version,
            public_score_vector=public_score_vector,
            full_score_vector=full_score_vector,
            metadata=metadata or {},
            created_at=created_at,
            enclave=enclave,
        )


def _ensure_finite_scores(field_name: str, v: dict[str, float]) -> dict[str, float]:
    for key, val in v.items():
        if val != val or val in (float("inf"), float("-inf")):
            raise ValueError(f"{field_name}[{key!r}] must be finite, got {val!r}")
    return v
