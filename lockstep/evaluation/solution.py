"""
Solution and Dataset abstractions.

These are the inputs to evaluation. Solutions are what solvers submit;
Datasets are what graders evaluate solutions against. Both are typed
abstractly at the substrate layer because their concrete shape varies
across domains, and committed cryptographically so that receipts can
reference them unambiguously.

Design correction from v1: solutions track two distinct hashes.

    plaintext_commitment is the hash of the canonical bytes of the
    cleartext solution. It's the solution's identity. Two encrypted
    bundles for two different consumers carry the same solution, with
    the same plaintext_commitment, but different bundle_hashes (because
    they're encrypted to different recipient keys).

    bundle_hash is the hash of the encrypted-on-storage blob. Public-tier
    validators verify receipt structure against the bundle_hash without
    needing decryption access. Full-tier validators decrypt the bundle,
    confirm the plaintext hashes to plaintext_commitment, and re-grade.

    Receipts carry both. Disputes that need re-grading reference the
    plaintext commitment. Receipt structure verification references the
    bundle hash. Conflating them was the bug in v1; separating them is
    the fix.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import TypeVar

from pydantic import BaseModel, ConfigDict

from .canonical import Bytes32Hex


class SolutionPayload(BaseModel, ABC):
    """
    Abstract base for domain-specific solution payloads.

    A SolutionPayload carries whatever the domain needs to execute the
    solution: code, model weights, parameter vectors, route plans,
    predictions. The substrate doesn't inspect the payload; it only sees
    the encrypted bundle hash and (when a receipt is being constructed)
    the plaintext commitment.

    Concrete subclasses live in domains/<n>/solution.py.
    """

    model_config = ConfigDict(frozen=True)

    @abstractmethod
    def serialize(self) -> bytes:
        """
        Canonical bytes representation of the cleartext solution.

        Used for hashing (to compute plaintext_commitment) and for
        encryption. Must be deterministic: serializing the same
        solution twice must produce identical bytes, or two graders
        will compute different commitments for the same solution and
        the validation network breaks.
        """

    @classmethod
    @abstractmethod
    def deserialize(cls, data: bytes) -> SolutionPayload:
        """Reconstruct from canonical bytes. Must be the inverse of serialize()."""

    @abstractmethod
    def instantiate(self) -> Callable:
        """
        Return a callable that conforms to this domain's solver_output_schema.

        For batch domains, this is a function taking the full input batch
        and returning predictions. For streaming domains, it's a function
        taking (window, state) per step. Concrete signature is domain-
        defined; the Evaluator's solver_input_schema / solver_output_schema
        documents it.

        The returned callable must be deterministic: same inputs produce
        same outputs across calls and processes.
        """

    def plaintext_commitment(self) -> Bytes32Hex:
        """Hash of the canonical cleartext bytes. Identity of the solution."""
        return "0x" + hashlib.sha256(self.serialize()).hexdigest()


SolutionT = TypeVar("SolutionT", bound=SolutionPayload)


class EncryptedSolution(BaseModel):
    """
    A SolutionPayload encrypted for storage.

    Two distinct hashes are tracked:

        plaintext_commitment binds to the underlying cleartext solution.
        Two encrypted bundles for two different recipients carry the
        same plaintext and therefore share this commitment.

        bundle_hash binds to the specific encrypted blob on storage.
        Different recipients get different bundles with different hashes.
    """

    model_config = ConfigDict(frozen=True)

    plaintext_commitment: Bytes32Hex
    bundle_hash: Bytes32Hex
    storage_uri: str
    recipient_pubkey: Bytes32Hex
    encryption_scheme: str = "x25519-chacha20poly1305-mock"


class DatasetCommitment(BaseModel):
    """
    Cryptographic commitment to a canonical evaluation dataset.

    Every dataset has two portions:
    - public: producer can download and develop against
    - private: sealed in the grader's TEE; producer never sees

    Both portions contribute to merkle_root. Validators verifying the
    public portion can re-run grading partially. Validators with TEE
    access (or the original grader running on attested hardware) can
    re-run grading on the full dataset.
    """

    model_config = ConfigDict(frozen=True)

    domain: str
    merkle_root: Bytes32Hex
    public_root: Bytes32Hex
    private_root: Bytes32Hex
    storage_uri: str
    schema_version: str
    rotation_epoch: int = 0


class DatasetPayload(BaseModel, ABC):
    """
    Abstract base for domain-specific dataset payloads.

    The substrate doesn't know what's in a dataset. Trading domains
    carry OHLCV time series; ML benchmark domains carry input/label
    pairs; forecasting domains carry feature vectors and (eventually)
    resolutions. Each concrete subclass defines the schema.
    """

    model_config = ConfigDict(frozen=True)

    commitment: DatasetCommitment

    @abstractmethod
    def verify_integrity(self) -> bool:
        """Verify loaded contents match the commitment's Merkle root."""

    @abstractmethod
    def public_view(self) -> DatasetPayload:
        """Return a view exposing only the public portion."""

    @abstractmethod
    def has_private_data(self) -> bool:
        """Whether this view of the dataset includes the private holdout."""


DatasetT = TypeVar("DatasetT", bound=DatasetPayload)
