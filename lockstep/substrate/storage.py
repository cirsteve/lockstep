"""Storage adapter — vendor-agnostic Protocol + in-memory Mock.

Production binding (Day 3+): 0G Storage. Encrypted solution bundles ride
on its key-value primitive; receipts and dataset payloads ride on its
Log primitive. The Protocol below is what the substrate code calls; the
0G adapter and the Mock both satisfy it.

The Mock here is a process-local dict with optional disk persistence.
Sealed private payloads are gated on a set of attestation pubkeys
registered as authorized — the mock doesn't actually re-encrypt with the
attestation key; it just refuses to hand the bytes over to anyone outside
the allowed set. The cryptographic shape is correct; the substitution
point in production is a real ERC-7857 oracle re-encryption ceremony.
"""

from __future__ import annotations

from typing import Protocol

from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.receipt import Receipt
from lockstep.evaluation.solution import DatasetCommitment, EncryptedSolution

from ._merkle import merkle_root


class StorageError(RuntimeError):
    """Raised when a storage operation fails (integrity, authorization, missing)."""


class StorageAdapter(Protocol):
    """Vendor-agnostic storage interface.

    Implementations: ``MockStorageAdapter`` (in-process) for tests and the
    local demo; a real 0G Storage adapter for production.
    """

    def upload_encrypted_solution(
        self, bundle: bytes, recipient_pubkey: Bytes32Hex
    ) -> EncryptedSolution: ...

    def download_encrypted_solution(self, uri: str) -> bytes: ...

    def upload_receipt(self, receipt: Receipt) -> str: ...

    def download_receipt(self, uri: str) -> Receipt: ...

    def upload_dataset(
        self,
        commitment: DatasetCommitment,
        public_payload: bytes,
        private_payload: bytes,
    ) -> None: ...

    def load_dataset_public(self, commitment: DatasetCommitment) -> bytes: ...

    def load_dataset_full(
        self, commitment: DatasetCommitment, attestation_pubkey: Bytes32Hex
    ) -> bytes: ...


class MockStorageAdapter:
    """In-memory storage adapter suitable for tests and the local demo.

    Datasets are stored split: ``public_payload`` and ``private_payload``
    keyed by Merkle root. ``load_dataset_full`` requires the caller's
    ``attestation_pubkey`` to be in the allowed set, mocking the real
    re-encryption gate. ``load_dataset_public`` is open.

    Receipts and encrypted solutions ride a flat URI dict.
    """

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._receipts: dict[str, Receipt] = {}
        # Datasets keyed by storage_uri (the URI is part of the commitment).
        # Loads recompute the Merkle root from bytes and compare against the
        # commitment the caller passed in — so tampering with either bytes
        # or commitment is detected.
        self._dataset_public: dict[str, bytes] = {}
        self._dataset_private: dict[str, bytes] = {}
        self._allowed_attestations: set[Bytes32Hex] = set()
        self._counter = 0

    def authorize_attestation(self, pubkey: Bytes32Hex) -> None:
        """Register an attestation pubkey as authorized for full-dataset reads."""
        self._allowed_attestations.add(pubkey.lower())

    def upload_encrypted_solution(
        self, bundle: bytes, recipient_pubkey: Bytes32Hex
    ) -> EncryptedSolution:
        import hashlib

        bundle_hash = "0x" + hashlib.sha256(bundle).hexdigest()
        plaintext_commitment = "0x" + hashlib.sha256(b"plaintext-of:" + bundle).hexdigest()
        # NOTE: the mock does not actually perform the encryption ceremony
        # here. The plaintext_commitment field is meaningful only when the
        # caller already knows the cleartext (e.g. the producer building
        # the bundle). Tests that need a true commitment thread it in via
        # the EncryptionAdapter (see substrate.encryption).
        uri = f"mock://solution/{bundle_hash}"
        self._objects[uri] = bundle
        return EncryptedSolution(
            plaintext_commitment=plaintext_commitment,
            bundle_hash=bundle_hash,
            storage_uri=uri,
            recipient_pubkey=recipient_pubkey,
        )

    def upload_object(
        self,
        bundle: bytes,
        *,
        plaintext_commitment: Bytes32Hex,
        recipient_pubkey: Bytes32Hex,
    ) -> EncryptedSolution:
        """Upload an already-encrypted bundle whose plaintext commitment is known.

        Distinct from ``upload_encrypted_solution`` because the mock can't
        derive a real plaintext_commitment from the ciphertext alone. The
        encryption adapter computes the commitment from cleartext and
        passes it in here.
        """
        import hashlib

        bundle_hash = "0x" + hashlib.sha256(bundle).hexdigest()
        uri = f"mock://solution/{bundle_hash}"
        self._objects[uri] = bundle
        return EncryptedSolution(
            plaintext_commitment=plaintext_commitment,
            bundle_hash=bundle_hash,
            storage_uri=uri,
            recipient_pubkey=recipient_pubkey,
        )

    def download_encrypted_solution(self, uri: str) -> bytes:
        if uri not in self._objects:
            raise StorageError(f"unknown storage uri: {uri}")
        return self._objects[uri]

    def upload_receipt(self, receipt: Receipt) -> str:
        uri = f"mock://receipt/{receipt.receipt_id}"
        self._receipts[uri] = receipt
        return uri

    def download_receipt(self, uri: str) -> Receipt:
        if uri not in self._receipts:
            raise StorageError(f"unknown receipt uri: {uri}")
        return self._receipts[uri]

    def upload_dataset(
        self,
        commitment: DatasetCommitment,
        public_payload: bytes,
        private_payload: bytes,
    ) -> None:
        self._dataset_public[commitment.storage_uri] = public_payload
        self._dataset_private[commitment.storage_uri] = private_payload

    def load_dataset_public(self, commitment: DatasetCommitment) -> bytes:
        payload = self._dataset_public.get(commitment.storage_uri)
        if payload is None:
            raise StorageError(f"no public payload for commitment uri {commitment.storage_uri}")
        leaves = _chunked(payload, 64)
        if merkle_root(leaves) != commitment.public_root:
            raise StorageError(
                f"public payload Merkle root mismatch for commitment {commitment.storage_uri}"
            )
        return payload

    def load_dataset_full(
        self, commitment: DatasetCommitment, attestation_pubkey: Bytes32Hex
    ) -> bytes:
        if attestation_pubkey.lower() not in self._allowed_attestations:
            raise StorageError(
                f"attestation pubkey {attestation_pubkey} not authorized "
                "for full dataset access"
            )
        public_payload = self.load_dataset_public(commitment)
        private_payload = self._dataset_private.get(commitment.storage_uri)
        if private_payload is None:
            raise StorageError(f"no private payload for commitment uri {commitment.storage_uri}")
        leaves = _chunked(private_payload, 64)
        if merkle_root(leaves) != commitment.private_root:
            raise StorageError(
                f"private payload Merkle root mismatch for commitment {commitment.storage_uri}"
            )
        return public_payload + private_payload


def _chunked(payload: bytes, size: int) -> list[bytes]:
    if not payload:
        return [b""]
    return [payload[i : i + size] for i in range(0, len(payload), size)]
