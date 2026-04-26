"""Storage adapter — vendor-agnostic Protocol + in-memory Mock.

Production binding (Day 3+): 0G Storage. Encrypted solution bundles ride
on its key-value primitive; receipts and dataset payloads ride on its
Log primitive. The Protocol below is what the substrate code calls; the
0G adapter and the Mock both satisfy it.

The Mock is a process-local dict. Sealed private payloads are gated on a
set of attestation pubkeys registered as authorized — the mock doesn't
actually re-encrypt with the attestation key; it just refuses to hand
the bytes to anyone outside the allowed set. The cryptographic shape is
correct; the substitution point in production is a real ERC-7857 oracle
re-encryption ceremony.

Integrity verification uses ``sha256(payload_bytes)``. Producers commit
to the bytes they upload, the storage adapter recomputes the same hash
on download, and the trading domains' ``commitment_roots()`` helper
emits the same hash by serializing through ``canonical_json_bytes`` —
so storage and domain code agree on what a "root" means.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.receipt import Receipt
from lockstep.evaluation.solution import DatasetCommitment, EncryptedSolution


class StorageError(RuntimeError):
    """Raised when a storage operation fails (integrity, authorization, missing)."""


class StorageAdapter(Protocol):
    """Vendor-agnostic storage interface.

    Implementations: ``MockStorageAdapter`` (in-process) for tests and the
    local demo; a real 0G Storage adapter for production.

    ``plaintext_commitment`` is required on upload because storage cannot
    derive it from ciphertext alone — the encryption adapter computes it
    from the cleartext payload before encrypting. See ``substrate.encryption``
    for the helper that produces both halves.
    """

    def upload_encrypted_solution(
        self,
        bundle: bytes,
        *,
        plaintext_commitment: Bytes32Hex,
        recipient_pubkey: Bytes32Hex,
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

    Datasets are stored split into public and private payloads keyed by
    storage URI. Loads recompute ``sha256(payload_bytes)`` and compare
    against the commitment so tampering with bytes or commitment is
    detected. ``load_dataset_full`` requires ``attestation_pubkey`` to
    be in the allowed set, mocking the real re-encryption gate.
    """

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self._receipts: dict[str, Receipt] = {}
        self._dataset_public: dict[str, bytes] = {}
        self._dataset_private: dict[str, bytes] = {}
        self._allowed_attestations: set[Bytes32Hex] = set()

    def authorize_attestation(self, pubkey: Bytes32Hex) -> None:
        """Register an attestation pubkey as authorized for full-dataset reads."""
        self._allowed_attestations.add(pubkey.lower())

    def upload_encrypted_solution(
        self,
        bundle: bytes,
        *,
        plaintext_commitment: Bytes32Hex,
        recipient_pubkey: Bytes32Hex,
    ) -> EncryptedSolution:
        """Upload an already-encrypted bundle whose plaintext commitment is known.

        The encryption adapter computes ``plaintext_commitment`` from the
        cleartext payload before handing it here. Storage cannot derive
        it from ciphertext alone — that's why the commitment is part of
        the upload contract and not something storage fabricates.
        """
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
        if _payload_root(payload) != commitment.public_root:
            raise StorageError(
                f"public payload root mismatch for commitment {commitment.storage_uri}"
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
        if _payload_root(private_payload) != commitment.private_root:
            raise StorageError(
                f"private payload root mismatch for commitment {commitment.storage_uri}"
            )
        return public_payload + private_payload


def _payload_root(payload: bytes) -> str:
    """sha256 of payload bytes as 0x-prefixed hex.

    Same shape the trading-domain ``commitment_roots`` helpers emit when
    the producer feeds them ``canonical_json_bytes(list(bars))``.
    """
    return "0x" + hashlib.sha256(payload).hexdigest()
