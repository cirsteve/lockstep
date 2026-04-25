"""Encryption adapter — real cryptography, mocked key-management ceremony.

Production binding (Day 3+): the ERC-7857 oracle re-encryption protocol
binds a recipient's TEE attestation pubkey to a per-bundle symmetric key.
The mock skips the oracle ceremony and uses x25519 + XChaCha20-Poly1305
directly: the bundle is real-encrypted, only the key-derivation path is
shortened.

The shape that matters for the rest of the substrate:

    plaintext_commitment is computed from the cleartext bytes (caller's
    serialize() output). It's stable across re-encryptions.

    bundle_hash is computed from the ciphertext bytes. It changes every
    time you re-encrypt because the nonce rerolls.

    Same plaintext, encrypted twice for the same recipient, yields:
        - identical plaintext_commitment
        - different bundle_hash
"""

from __future__ import annotations

import hashlib
import os
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from lockstep.evaluation.canonical import Bytes32Hex


class EncryptionError(RuntimeError):
    """Raised when decryption fails or a key shape is wrong."""


class EncryptionAdapter(Protocol):
    """Protocol implemented by ``MockEncryptionAdapter`` and (Day 3+) the real adapter."""

    def encrypt_for(self, payload: bytes, recipient_pubkey: Bytes32Hex) -> bytes: ...

    def decrypt_with(self, ciphertext: bytes, my_private_key: bytes) -> bytes: ...

    def compute_bundle_hash(self, ciphertext: bytes) -> Bytes32Hex: ...

    def compute_plaintext_commitment(self, payload: bytes) -> Bytes32Hex: ...


def generate_keypair() -> tuple[Bytes32Hex, bytes]:
    """Generate an x25519 keypair. Returns (pubkey_hex, private_key_bytes)."""
    sk = X25519PrivateKey.generate()
    pk = sk.public_key()
    pub_bytes = pk.public_bytes(Encoding.Raw, PublicFormat.Raw)
    priv_bytes = sk.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    return "0x" + pub_bytes.hex(), priv_bytes


class MockEncryptionAdapter:
    """Real x25519 + ChaCha20-Poly1305 encryption with a mock key ceremony.

    The mock ceremony is: the producer ephemerally generates an x25519
    keypair, derives a shared secret with the recipient's pubkey, and
    encrypts under a key derived via HKDF-SHA256. The ephemeral pubkey is
    prepended to the ciphertext so the recipient can decrypt.

    Production replaces the producer-driven ECDH with a re-encryption
    oracle bound to ERC-7857 attestation. The wire format below is
    intentionally not interoperable with that protocol — it's substrate-
    internal mock data.
    """

    def __init__(self) -> None:
        self._scheme = "x25519-chacha20poly1305-mock"

    def encrypt_for(self, payload: bytes, recipient_pubkey: Bytes32Hex) -> bytes:
        recipient_pub = _load_pub(recipient_pubkey)
        ephemeral = X25519PrivateKey.generate()
        ephemeral_pub_bytes = ephemeral.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        shared = ephemeral.exchange(recipient_pub)
        key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"lockstep-mock-encryption",
        ).derive(shared)

        nonce = os.urandom(12)
        cipher = ChaCha20Poly1305(key)
        ct = cipher.encrypt(nonce, payload, associated_data=None)
        # wire format: ephemeral_pub (32) | nonce (12) | ciphertext+tag
        return ephemeral_pub_bytes + nonce + ct

    def decrypt_with(self, ciphertext: bytes, my_private_key: bytes) -> bytes:
        if len(ciphertext) < 32 + 12 + 16:
            raise EncryptionError("ciphertext too short")
        ephemeral_pub_bytes = ciphertext[:32]
        nonce = ciphertext[32:44]
        ct = ciphertext[44:]
        try:
            ephemeral_pub = X25519PublicKey.from_public_bytes(ephemeral_pub_bytes)
            sk = X25519PrivateKey.from_private_bytes(my_private_key)
            shared = sk.exchange(ephemeral_pub)
            key = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=b"lockstep-mock-encryption",
            ).derive(shared)
            cipher = ChaCha20Poly1305(key)
            return cipher.decrypt(nonce, ct, associated_data=None)
        except Exception as exc:  # broad catch: cryptography raises various
            raise EncryptionError("decryption failed") from exc

    def compute_bundle_hash(self, ciphertext: bytes) -> Bytes32Hex:
        return "0x" + hashlib.sha256(ciphertext).hexdigest()

    def compute_plaintext_commitment(self, payload: bytes) -> Bytes32Hex:
        return "0x" + hashlib.sha256(payload).hexdigest()


def _load_pub(recipient_pubkey: Bytes32Hex) -> X25519PublicKey:
    if not recipient_pubkey.startswith("0x") or len(recipient_pubkey) != 66:
        raise EncryptionError(f"invalid recipient pubkey shape: {recipient_pubkey}")
    raw = bytes.fromhex(recipient_pubkey[2:])
    try:
        return X25519PublicKey.from_public_bytes(raw)
    except Exception as exc:
        raise EncryptionError("invalid x25519 pubkey") from exc
