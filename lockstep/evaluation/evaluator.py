"""
Evaluator schema — the serializable, content-addressable specification of an
evaluation contract.

An Evaluator is *what* a domain is; an Evaluation (in evaluation.py) is *how*
Python code interacts with that domain. The Evaluator is the artifact that
gets registered, content-addressed, and pointed to by receipts. The
Evaluation is the runtime interface that loads datasets, instantiates
graders, and ranks scores.

The Evaluator is a Pydantic model — it serializes to canonical JSON, hashes
to a content-addressed identifier, and can be stored on-chain or
distributed via 0G Storage. The Evaluation is a Python class that depends
on imports and only exists at runtime.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import Bytes32Hex, canonical_json_bytes


class ScoreDimension(BaseModel):
    """Specification of one key in the score vector."""

    model_config = ConfigDict(frozen=True)

    key: str
    description: str
    unit: str = "scalar"
    higher_is_better: bool = True
    expected_range: tuple[float, float] | None = None


class HoldoutPolicy(BaseModel):
    """How the dataset's private holdout is structured and managed."""

    model_config = ConfigDict(frozen=True)

    public_fraction: float = Field(ge=0.0, le=1.0)
    rotation_cadence_days: int | None = None
    rotation_governance: str = "marketplace_admin"


class DisputePolicy(BaseModel):
    """Rules for resolving validator disagreements about a receipt's correctness."""

    model_config = ConfigDict(frozen=True)

    challenge_window_seconds: int = 86_400
    minimum_validators_for_consensus: int = 1
    resolution_method: str = "replay"


class Evaluator(BaseModel):
    """
    Content-addressable specification of an evaluation contract.

    An Evaluator is the canonical, serializable definition of a domain.
    Its hash is the domain's identity. Receipts reference Evaluators by id.
    Validators fetch Evaluators to know what they're verifying.

    Multiple Grader implementations may satisfy a single Evaluator. For
    example, a Python reference grader and a Rust production grader could
    both be valid for trading_directional, with the Evaluator listing both
    grader version hashes in accepted_grader_versions.
    """

    model_config = ConfigDict(frozen=True)

    schema_version: str = "v1"
    evaluator_id: Bytes32Hex

    domain_name: str
    domain_version: str

    solver_input_schema: dict[str, Any]
    solver_output_schema: dict[str, Any]
    invocation_model: str = "batch"

    accepted_grader_versions: list[Bytes32Hex]

    dataset_schema_version: str
    holdout_policy: HoldoutPolicy

    score_dimensions: list[ScoreDimension]
    rank_dimension: str

    dispute_policy: DisputePolicy

    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("rank_dimension")
    @classmethod
    def _rank_dimension_must_exist(cls, v: str, info) -> str:
        score_dims = info.data.get("score_dimensions", [])
        if score_dims:
            keys = [d.key for d in score_dims]
            if v not in keys:
                raise ValueError(
                    f"rank_dimension {v!r} must appear in score_dimensions; "
                    f"available keys: {keys}"
                )
        return v

    @field_validator("score_dimensions")
    @classmethod
    def _unique_score_keys(cls, v: list[ScoreDimension]) -> list[ScoreDimension]:
        keys = [d.key for d in v]
        if len(set(keys)) != len(keys):
            raise ValueError(f"score_dimensions keys must be unique; got {keys}")
        return v

    @field_validator("accepted_grader_versions")
    @classmethod
    def _at_least_one_grader(cls, v: list[Bytes32Hex]) -> list[Bytes32Hex]:
        if not v:
            raise ValueError("accepted_grader_versions must contain at least one grader hash")
        return v

    @model_validator(mode="after")
    def _verify_evaluator_id(self) -> Evaluator:
        """Reject manually-supplied evaluator_ids that don't match canonical bytes.

        Mirrors Receipt._verify_receipt_id. Bypassing Evaluator.build()
        and constructing Evaluator(evaluator_id="0x...", ...) directly
        with a wrong id would otherwise succeed silently and let
        downstream receipts reference a bogus content-addressed
        identifier.
        """
        expected = "0x" + hashlib.sha256(self.canonical_bytes()).hexdigest()
        if self.evaluator_id != expected:
            raise ValueError(
                f"evaluator_id mismatch: stated={self.evaluator_id} derived={expected}. "
                "Use Evaluator.build(...) to construct evaluators with derived IDs."
            )
        return self

    def canonical_body(self) -> dict[str, Any]:
        """Serializable body of the Evaluator, excluding evaluator_id."""
        return {
            "schema_version": self.schema_version,
            "domain_name": self.domain_name,
            "domain_version": self.domain_version,
            "solver_input_schema": self.solver_input_schema,
            "solver_output_schema": self.solver_output_schema,
            "invocation_model": self.invocation_model,
            "accepted_grader_versions": sorted(self.accepted_grader_versions),
            "dataset_schema_version": self.dataset_schema_version,
            "holdout_policy": self.holdout_policy.model_dump(),
            "score_dimensions": [d.model_dump() for d in self.score_dimensions],
            "rank_dimension": self.rank_dimension,
            "dispute_policy": self.dispute_policy.model_dump(),
            "metadata": self.metadata,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic serialization for hashing and on-chain commitment."""
        return canonical_json_bytes(self.canonical_body())

    def to_json(self) -> str:
        """Pretty JSON for human inspection. Distinct from canonical_bytes."""
        return json.dumps(self.canonical_body(), indent=2, sort_keys=True)

    @classmethod
    def build(
        cls,
        *,
        domain_name: str,
        domain_version: str,
        solver_input_schema: dict[str, Any],
        solver_output_schema: dict[str, Any],
        accepted_grader_versions: list[Bytes32Hex],
        dataset_schema_version: str,
        holdout_policy: HoldoutPolicy,
        score_dimensions: list[ScoreDimension],
        rank_dimension: str,
        dispute_policy: DisputePolicy,
        invocation_model: str = "batch",
        metadata: dict[str, Any] | None = None,
        schema_version: str = "v1",
    ) -> Evaluator:
        """Construct an Evaluator with a derived evaluator_id."""
        body = {
            "schema_version": schema_version,
            "domain_name": domain_name,
            "domain_version": domain_version,
            "solver_input_schema": solver_input_schema,
            "solver_output_schema": solver_output_schema,
            "invocation_model": invocation_model,
            "accepted_grader_versions": sorted(accepted_grader_versions),
            "dataset_schema_version": dataset_schema_version,
            "holdout_policy": holdout_policy.model_dump(),
            "score_dimensions": [d.model_dump() for d in score_dimensions],
            "rank_dimension": rank_dimension,
            "dispute_policy": dispute_policy.model_dump(),
            "metadata": metadata or {},
        }
        canonical = canonical_json_bytes(body)
        evaluator_id = "0x" + hashlib.sha256(canonical).hexdigest()

        return cls(
            schema_version=schema_version,
            evaluator_id=evaluator_id,
            domain_name=domain_name,
            domain_version=domain_version,
            solver_input_schema=solver_input_schema,
            solver_output_schema=solver_output_schema,
            invocation_model=invocation_model,
            accepted_grader_versions=sorted(accepted_grader_versions),
            dataset_schema_version=dataset_schema_version,
            holdout_policy=holdout_policy,
            score_dimensions=score_dimensions,
            rank_dimension=rank_dimension,
            dispute_policy=dispute_policy,
            metadata=metadata or {},
        )


# In-memory registry. Production replaces with on-chain registry contract reads.
_REGISTRY: dict[Bytes32Hex, Evaluator] = {}


def register_evaluator(evaluator: Evaluator) -> Evaluator:
    """
    Register an Evaluator so receipts can reference it by id.

    Idempotent: registering the same evaluator twice is a no-op. Conflicting
    registrations (same id, different bodies) is impossible because id is
    content-addressed.
    """
    existing = _REGISTRY.get(evaluator.evaluator_id)
    if existing is not None and existing != evaluator:
        raise RuntimeError(
            f"evaluator_id collision for {evaluator.evaluator_id} "
            "with non-equal Evaluator bodies; check canonical form determinism"
        )
    _REGISTRY[evaluator.evaluator_id] = evaluator
    return evaluator


def get_evaluator(evaluator_id: Bytes32Hex) -> Evaluator | None:
    """Look up a registered Evaluator by id. None if not registered."""
    return _REGISTRY.get(evaluator_id)


def list_evaluators() -> list[Evaluator]:
    """List all registered Evaluators. Useful for marketplace UI."""
    return list(_REGISTRY.values())


def clear_registry() -> None:
    """Test helper. Production code should not call this."""
    _REGISTRY.clear()


def snapshot_registry() -> dict[Bytes32Hex, Evaluator]:
    """Return a shallow copy of the current registry — for snapshot/restore."""
    return dict(_REGISTRY)


def restore_registry(snapshot: dict[Bytes32Hex, Evaluator]) -> None:
    """Replace the registry contents with a previously-captured snapshot."""
    _REGISTRY.clear()
    _REGISTRY.update(snapshot)
