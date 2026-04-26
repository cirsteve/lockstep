"""Cross-cutting error types for substrate adapters.

``SubstrateError`` is the base class for any failure surfaced by a
substrate adapter (storage, chain, attestation, encryption, payment,
transport). Adapter-specific subclasses live in their respective
substrate modules so callers can either catch broadly (``SubstrateError``)
or narrowly (``StorageError``).

``TrustViolation`` is reserved for failures that indicate byzantine
behavior — bytes that don't match a commitment, a signature that
doesn't verify, an unauthorized pubkey requesting sealed data. These
are evidence of misbehavior and must never be retried as if they were
transient network errors.
"""


class SubstrateError(RuntimeError):
    """Base class for all substrate adapter failures.

    Catch this when you want to react to any adapter problem
    (network, auth, missing object, bad input). Adapter-specific
    subclasses are raised in practice; this is the common ancestor.
    """


class TrustViolation(SubstrateError):
    """Raised when adapter output disagrees with its commitment.

    Examples: downloaded bytes don't hash to the recorded commitment,
    a receipt signature fails to verify against its declared pubkey,
    a caller without the right attestation pubkey tries to read a
    sealed dataset. These are byzantine-evidence conditions, not
    transient failures — never retry.
    """
