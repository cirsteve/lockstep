"""Acceptance tests for MockEncryptionAdapter."""

from __future__ import annotations

import pytest

from lockstep.substrate.encryption import (
    EncryptionError,
    MockEncryptionAdapter,
    generate_keypair,
)


def test_encrypt_decrypt_roundtrip():
    adapter = MockEncryptionAdapter()
    pub, priv = generate_keypair()
    payload = b"the secret plaintext payload, repeating: " * 8
    ct = adapter.encrypt_for(payload, recipient_pubkey=pub)
    assert adapter.decrypt_with(ct, my_private_key=priv) == payload


def test_decrypt_with_wrong_private_key_raises():
    adapter = MockEncryptionAdapter()
    pub_a, _ = generate_keypair()
    _, priv_b = generate_keypair()
    payload = b"some plaintext"
    ct = adapter.encrypt_for(payload, recipient_pubkey=pub_a)
    with pytest.raises(EncryptionError):
        adapter.decrypt_with(ct, my_private_key=priv_b)


def test_double_encrypt_same_plaintext_yields_distinct_ciphertexts_same_commitment():
    adapter = MockEncryptionAdapter()
    pub, _ = generate_keypair()
    payload = b"identical plaintext bytes, repeated " * 4

    ct1 = adapter.encrypt_for(payload, recipient_pubkey=pub)
    ct2 = adapter.encrypt_for(payload, recipient_pubkey=pub)

    # Different ciphertexts (nonce randomization)
    assert ct1 != ct2
    assert adapter.compute_bundle_hash(ct1) != adapter.compute_bundle_hash(ct2)

    # But identical plaintext_commitments (computed from cleartext)
    assert adapter.compute_plaintext_commitment(payload) == adapter.compute_plaintext_commitment(
        payload
    )
