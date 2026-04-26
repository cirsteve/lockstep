"""Public surface for Layer 2 — schemas and contracts."""

from .canonical import (
    ZERO_BYTES32,
    Address,
    Bytes32Hex,
    canonical_json_bytes,
)
from .evaluation import (
    Evaluation,
    Grader,
    GraderResult,
    SolverInterface,
)
from .evaluator import (
    DisputePolicy,
    Evaluator,
    HoldoutPolicy,
    ScoreDimension,
    clear_registry,
    get_evaluator,
    list_evaluators,
    register_evaluator,
    restore_registry,
    snapshot_registry,
)
from .receipt import (
    EnclaveAttestation,
    Receipt,
    ReceiptKind,
    ValidationTier,
    derive_receipt_id,
)
from .solution import (
    DatasetCommitment,
    DatasetPayload,
    DatasetT,
    EncryptedSolution,
    SolutionPayload,
    SolutionT,
)

__all__ = [
    "Address",
    "Bytes32Hex",
    "DatasetCommitment",
    "DatasetPayload",
    "DatasetT",
    "DisputePolicy",
    "EnclaveAttestation",
    "EncryptedSolution",
    "Evaluation",
    "Evaluator",
    "Grader",
    "GraderResult",
    "HoldoutPolicy",
    "Receipt",
    "ReceiptKind",
    "ScoreDimension",
    "SolutionPayload",
    "SolutionT",
    "SolverInterface",
    "ValidationTier",
    "ZERO_BYTES32",
    "canonical_json_bytes",
    "clear_registry",
    "derive_receipt_id",
    "get_evaluator",
    "list_evaluators",
    "register_evaluator",
    "restore_registry",
    "snapshot_registry",
]
