"""
Grader and Evaluation interfaces — Layer 2 of the Lockstep architecture.

A Grader is the thing that runs inside a TEE and produces GraderResults.
It's domain-specific: trading graders run backtests, ML benchmark
graders run test suites, forecasting graders wait for resolution data.

An Evaluation is the typed contract a domain implements. It bundles the
Grader with everything else the domain needs: solution and dataset
types, ranking logic, solver interface specification.

The substrate orchestrates Evaluations without knowing their concrete
types. Domain implementations live in domains/<name>/evaluation.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Generic, cast

from pydantic import BaseModel, ConfigDict

from .receipt import Receipt
from .solution import DatasetT, SolutionT

if TYPE_CHECKING:
    from .evaluator import Evaluator


class SolverInterface(BaseModel):
    """
    The contract solvers conform to when implementing a solution for a domain.

    The schema fields are JSON Schema documents. Solvers can validate
    their implementations against the schema before submitting; graders
    validate inputs against the schema before invoking the solver.
    """

    model_config = ConfigDict(frozen=True)

    domain: str
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]

    invocation_model: str = "batch"


class GraderResult(BaseModel):
    """
    Internal result type returned by Grader.grade(), pre-attestation.

    The substrate wraps this with enclave attestation to produce a final
    Receipt. Domain Graders never produce Receipts directly — they produce
    GraderResults, and the substrate's enclave-side wrapper signs the
    canonical signing payload to produce the EnclaveAttestation.
    """

    model_config = ConfigDict(frozen=True)

    public_score_vector: dict[str, float]
    full_score_vector: dict[str, float] | None
    metadata: dict[str, Any]


class Grader(ABC, Generic[SolutionT, DatasetT]):
    """
    Abstract base for domain-specific graders.

    Concrete subclasses implement grade_public() and grade_full(). The
    public grading is computed from the public portion of the canonical
    dataset only; the full grading uses public + private. The substrate's
    grade() entry point dispatches between them based on whether the
    dataset view contains private data.

    Determinism requirement: grade_public() and grade_full() must be
    deterministic for given inputs. Two validators running the same
    grader against the same inputs must produce identical results.
    """

    @classmethod
    @abstractmethod
    def version(cls) -> str:
        """Content-addressed hash of this grader's canonical code."""

    @abstractmethod
    def grade_public(self, solution: SolutionT, public_dataset: DatasetT) -> dict[str, float]:
        """Compute the score vector using only the public portion of the dataset."""

    @abstractmethod
    def grade_full(self, solution: SolutionT, full_dataset: DatasetT) -> dict[str, float]:
        """Compute the score vector using the full dataset (public + private holdout)."""

    def grade(self, solution: SolutionT, dataset: DatasetT) -> GraderResult:
        """Standard entry point: dispatch to public or full grading.

        ``DatasetPayload.public_view()`` widens its return type to
        ``DatasetPayload`` because the base class can't reference the
        concrete subclass. Concrete datasets always return their own
        type, which is what ``grade_public`` expects — hence the cast.
        """
        public_view = cast(DatasetT, dataset.public_view())
        public = self.grade_public(solution, public_view)
        full = None
        metadata: dict[str, Any] = {}
        if dataset.has_private_data():
            full = self.grade_full(solution, dataset)
        return GraderResult(
            public_score_vector=public,
            full_score_vector=full,
            metadata=metadata,
        )


class Evaluation(ABC, Generic[SolutionT, DatasetT]):
    """
    The typed contract a domain implements to participate in Lockstep.

    An Evaluation bundles together everything domain-specific:
    - the Solution type that solvers submit
    - the Dataset type that the canonical eval data conforms to
    - the Grader that produces score vectors
    - the SolverInterface that specifies the solver contract
    - the ranking logic that turns score vectors into a single rankable scalar
    """

    @property
    @abstractmethod
    def domain(self) -> str:
        """Domain identifier. e.g. 'trading_directional', 'coin_flip'."""

    @abstractmethod
    def evaluator(self) -> Evaluator:
        """Return the canonical Evaluator schema this Evaluation implements."""

    @property
    @abstractmethod
    def solver_interface(self) -> SolverInterface:
        """The solver contract for this domain."""

    @abstractmethod
    def grader(self) -> Grader[SolutionT, DatasetT]:
        """Construct a fresh Grader instance."""

    @abstractmethod
    def deserialize_solution(self, data: bytes) -> SolutionT:
        """Reconstruct a solution from its serialized bytes."""

    @abstractmethod
    def load_dataset(self, commitment_uri: str, *, with_private: bool) -> DatasetT:
        """Load a dataset given its commitment storage URI."""

    @abstractmethod
    def rank_score(self, receipt: Receipt) -> float:
        """Domain-specific scalar ranking from a receipt's full score vector."""

    @abstractmethod
    def filter_dimensions(self) -> list[str]:
        """Score vector keys allocators commonly filter on, in display order."""
