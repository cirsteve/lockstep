"""Shared test fixtures.

Three pieces:
- ``evaluator_registry_baseline`` (autouse): snapshots the registry once
  per session after Layer 3 imports and restores that baseline between
  tests. This preserves the module-import-time registrations every
  domain performs, while still cleaning up any test-local registrations.
- ``fake_attestation_factory``: builds a fake EnclaveAttestation with the
  right pubkey shape so receipt.build() accepts it.
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
    restore_registry,
    snapshot_registry,
)


def _force_layer3_imports() -> None:
    """Trigger domain module imports so register_evaluator fires.

    Module-import-time registration only happens once per process. Tests
    that need the domain evaluators in the registry rely on these
    imports having already run.
    """
    import lockstep.domains.coin_flip.evaluation  # noqa: F401
    import lockstep.domains.trading.directional.evaluation  # noqa: F401
    import lockstep.domains.trading.market_neutral.evaluation  # noqa: F401


@pytest.fixture(autouse=True, scope="session")
def _registry_session_baseline() -> dict:
    """Force domain imports once, then capture the post-import registry."""
    _force_layer3_imports()
    return snapshot_registry()


@pytest.fixture(autouse=True)
def evaluator_registry_baseline(_registry_session_baseline: dict) -> None:
    """Restore the post-import registry after each test runs."""
    yield
    restore_registry(_registry_session_baseline)


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
