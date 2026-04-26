"""Acceptance tests for MockStorageAdapter."""

from __future__ import annotations

import hashlib

import pytest

from lockstep.evaluation.solution import DatasetCommitment
from lockstep.substrate.storage import MockStorageAdapter, StorageError


def _root(payload: bytes) -> str:
    return "0x" + hashlib.sha256(payload).hexdigest()


def _commitment_for(public: bytes, private: bytes) -> DatasetCommitment:
    pub_root = _root(public)
    priv_root = _root(private)
    return DatasetCommitment(
        domain="test",
        merkle_root=pub_root,  # placeholder; mock doesn't combine roots
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="mock://dataset/test",
        schema_version="v1",
    )


def test_upload_and_download_roundtrip_carries_plaintext_commitment():
    adapter = MockStorageAdapter()
    bundle = b"encrypted-bundle-bytes" * 8
    pubkey = "0x" + "ab" * 32
    plaintext_commitment = "0x" + "cd" * 32
    enc_sol = adapter.upload_encrypted_solution(
        bundle,
        plaintext_commitment=plaintext_commitment,
        recipient_pubkey=pubkey,
    )
    assert adapter.download_encrypted_solution(enc_sol.storage_uri) == bundle
    assert enc_sol.plaintext_commitment == plaintext_commitment


def test_download_with_wrong_root_raises():
    adapter = MockStorageAdapter()
    public = b"the public dataset bytes" * 8
    private = b"the private holdout bytes" * 4
    commitment = _commitment_for(public, private)

    adapter.upload_dataset(commitment, public, private)

    bad_commitment = commitment.model_copy(update={"public_root": "0x" + "00" * 32})
    with pytest.raises(StorageError, match="root"):
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
