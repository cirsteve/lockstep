"""Shared test fixtures.

Three pieces:
- ``clear_registry`` (autouse): resets the in-memory Evaluator dict
  between tests so registrations from one test don't leak into another.
- ``fake_attestation_factory``: builds a fake EnclaveAttestation with the
  right pubkey shape so receipt.build() accepts it. Real cryptography
  arrives via the substrate.attestation Mock in Section 2.
- ``fake_dataset_commitment_factory``: builds a fake DatasetCommitment
  with placeholder Merkle roots, parameterized by domain.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from lockstep import (
    Bytes32Hex,
    DatasetCommitment,
    EnclaveAttestation,
)
from lockstep import (
    clear_registry as _clear_registry,
)


@pytest.fixture(autouse=True)
def clear_registry():
    """Reset the in-memory evaluator registry before and after each test."""
    _clear_registry()
    yield
    _clear_registry()


@pytest.fixture
def fake_attestation_factory() -> Callable[..., EnclaveAttestation]:
    """Construct an EnclaveAttestation suitable for use in receipt tests."""

    def _build(
        *,
        pubkey: Bytes32Hex = "0x" + "dd" * 32,
        signature: str = "0x" + "ee" * 64,
        attestation_chain: bytes = b"mock-attestation",
        enclave_kind: str = "tee_mock",
    ) -> EnclaveAttestation:
        return EnclaveAttestation(
            pubkey=pubkey,
            signature=signature,
            attestation_chain=attestation_chain,
            enclave_kind=enclave_kind,
        )

    return _build


@pytest.fixture
def fake_dataset_commitment_factory() -> Callable[..., DatasetCommitment]:
    """Construct a DatasetCommitment with placeholder Merkle roots."""

    def _build(
        *,
        domain: str = "test_domain",
        merkle_root: Bytes32Hex = "0x" + "11" * 32,
        public_root: Bytes32Hex = "0x" + "22" * 32,
        private_root: Bytes32Hex = "0x" + "33" * 32,
        storage_uri: str = "zg://dataset/test/v1",
        schema_version: str = "v1",
        rotation_epoch: int = 0,
    ) -> DatasetCommitment:
        return DatasetCommitment(
            domain=domain,
            merkle_root=merkle_root,
            public_root=public_root,
            private_root=private_root,
            storage_uri=storage_uri,
            schema_version=schema_version,
            rotation_epoch=rotation_epoch,
        )

    return _build
