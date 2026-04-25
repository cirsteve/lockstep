"""Acceptance tests for MockStorageAdapter."""

from __future__ import annotations

import pytest

from lockstep.evaluation.solution import DatasetCommitment
from lockstep.substrate._merkle import merkle_root
from lockstep.substrate.storage import MockStorageAdapter, StorageError


def _commitment_for(public: bytes, private: bytes) -> DatasetCommitment:
    pub_root = merkle_root(_chunked(public, 64))
    priv_root = merkle_root(_chunked(private, 64))
    return DatasetCommitment(
        domain="test",
        merkle_root=pub_root,  # placeholder; mock doesn't combine roots
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="mock://dataset/test",
        schema_version="v1",
    )


def _chunked(payload: bytes, size: int) -> list[bytes]:
    if not payload:
        return [b""]
    return [payload[i : i + size] for i in range(0, len(payload), size)]


def test_upload_and_download_roundtrip():
    adapter = MockStorageAdapter()
    bundle = b"encrypted-bundle-bytes" * 8
    pubkey = "0x" + "ab" * 32
    enc_sol = adapter.upload_encrypted_solution(bundle, recipient_pubkey=pubkey)
    assert adapter.download_encrypted_solution(enc_sol.storage_uri) == bundle


def test_download_with_wrong_merkle_commitment_raises():
    adapter = MockStorageAdapter()
    public = b"the public dataset bytes" * 8
    private = b"the private holdout bytes" * 4
    commitment = _commitment_for(public, private)

    # Upload with the right roots, but tamper with the public root in
    # the commitment we pass to load — mock recomputes and compares.
    adapter.upload_dataset(commitment, public, private)

    bad_commitment = commitment.model_copy(update={"public_root": "0x" + "00" * 32})
    with pytest.raises(StorageError, match="Merkle root"):
        adapter.load_dataset_public(bad_commitment)


def test_load_dataset_full_unauthorized_pubkey_raises():
    adapter = MockStorageAdapter()
    public = b"public" * 16
    private = b"private" * 16
    commitment = _commitment_for(public, private)
    adapter.upload_dataset(commitment, public, private)

    with pytest.raises(StorageError, match="not authorized"):
        adapter.load_dataset_full(commitment, attestation_pubkey="0x" + "ff" * 32)


def test_load_dataset_full_authorized_pubkey_returns_full_payload():
    adapter = MockStorageAdapter()
    public = b"public" * 16
    private = b"private" * 16
    commitment = _commitment_for(public, private)
    adapter.upload_dataset(commitment, public, private)

    pubkey = "0x" + "cc" * 32
    adapter.authorize_attestation(pubkey)
    full = adapter.load_dataset_full(commitment, attestation_pubkey=pubkey)
    assert full == public + private
