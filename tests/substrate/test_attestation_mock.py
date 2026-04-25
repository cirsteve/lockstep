"""Acceptance tests for MockAttestationAdapter."""

from __future__ import annotations

from lockstep.evaluation.canonical import canonical_json_bytes
from lockstep.evaluation.evaluation import GraderResult
from lockstep.evaluation.evaluator import (
    DisputePolicy,
    Evaluator,
    HoldoutPolicy,
    ScoreDimension,
)
from lockstep.evaluation.receipt import ReceiptKind, derive_receipt_id
from lockstep.substrate.attestation import MockAttestationAdapter


def _build_evaluator() -> Evaluator:
    return Evaluator.build(
        domain_name="attestation_test",
        domain_version="v1",
        solver_input_schema={},
        solver_output_schema={},
        accepted_grader_versions=["0x" + "aa" * 32],
        dataset_schema_version="v1",
        holdout_policy=HoldoutPolicy(public_fraction=0.5),
        score_dimensions=[ScoreDimension(key="score", description="x")],
        rank_dimension="score",
        dispute_policy=DisputePolicy(),
    )


def _produce_receipt(adapter: MockAttestationAdapter):
    pubkey, privkey = adapter.generate_attestation_keypair()
    grader_result = GraderResult(
        public_score_vector={"score": 0.5},
        full_score_vector={"score": 0.6},
        metadata={},
    )
    evaluator = _build_evaluator()
    receipt = adapter.produce_receipt(
        grader_result=grader_result,
        evaluator=evaluator,
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment="0x" + "02" * 32,
        solution_bundle_hash="0x" + "03" * 32,
        dataset_commitment="0x" + "04" * 32,
        grader_version="0x" + "aa" * 32,
        private_key=privkey,
        pubkey=pubkey,
        kind=ReceiptKind.INITIAL_GRADING,
    )
    return receipt, pubkey, privkey


def test_produce_receipt_round_trips_through_verify_attestation():
    adapter = MockAttestationAdapter()
    receipt, _, _ = _produce_receipt(adapter)
    assert adapter.verify_attestation(receipt.enclave, receipt.canonical_signing_payload()) is True


def test_tampered_receipt_field_fails_verify_attestation():
    adapter = MockAttestationAdapter()
    receipt, _, _ = _produce_receipt(adapter)

    tampered_payload = canonical_json_bytes(
        {
            **{
                "kind": receipt.kind.value,
                "previous_receipt_id": receipt.previous_receipt_id,
                "evaluator_id": receipt.evaluator_id,
                "domain": receipt.domain,
                "problem_id": receipt.problem_id,
                "solution_plaintext_commitment": receipt.solution_plaintext_commitment,
                "solution_bundle_hash": receipt.solution_bundle_hash,
                "dataset_commitment": receipt.dataset_commitment,
                "grader_version": receipt.grader_version,
                "public_score_vector": receipt.public_score_vector,
                "full_score_vector": receipt.full_score_vector,
                "metadata": receipt.metadata,
                "created_at": receipt.created_at.isoformat(),
            },
            "domain": "this is not the original domain",
        }
    )
    assert adapter.verify_attestation(receipt.enclave, tampered_payload) is False


def test_receipt_id_matches_derive_of_canonical_payload():
    adapter = MockAttestationAdapter()
    receipt, _, _ = _produce_receipt(adapter)
    assert receipt.receipt_id == derive_receipt_id(receipt.canonical_signing_payload())
