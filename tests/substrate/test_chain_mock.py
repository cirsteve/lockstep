"""Acceptance tests for MockChainAdapter."""

from __future__ import annotations

from datetime import UTC, datetime

from lockstep.evaluation.evaluator import (
    DisputePolicy,
    Evaluator,
    HoldoutPolicy,
    ScoreDimension,
)
from lockstep.evaluation.receipt import EnclaveAttestation, Receipt, ReceiptKind
from lockstep.substrate.chain import MockChainAdapter


def _evaluator() -> Evaluator:
    return Evaluator.build(
        domain_name="chain_test",
        domain_version="v1",
        solver_input_schema={},
        solver_output_schema={},
        accepted_grader_versions=["0x" + "11" * 32],
        dataset_schema_version="v1",
        holdout_policy=HoldoutPolicy(public_fraction=0.5),
        score_dimensions=[ScoreDimension(key="score", description="x")],
        rank_dimension="score",
        dispute_policy=DisputePolicy(),
    )


def _receipt(*, score: float = 0.7) -> Receipt:
    enclave = EnclaveAttestation(
        pubkey="0x" + "dd" * 32,
        signature="0x" + "ee" * 64,
        attestation_chain=b"mock",
        enclave_kind="tee_mock",
    )
    return Receipt.build(
        kind=ReceiptKind.INITIAL_GRADING,
        evaluator_id="0x" + "22" * 32,
        domain="chain_test",
        problem_id="0x" + "33" * 32,
        solution_plaintext_commitment="0x" + "44" * 32,
        solution_bundle_hash="0x" + "55" * 32,
        dataset_commitment="0x" + "66" * 32,
        grader_version="0x" + "11" * 32,
        public_score_vector={"score": score},
        full_score_vector={"score": score},
        enclave=enclave,
        created_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
    )


def test_mint_then_read_returns_same_receipt():
    adapter = MockChainAdapter()
    receipt = _receipt()
    owner = "0x" + "ab" * 20
    token_id = adapter.mint_inft(receipt, owner)
    assert adapter.read_inft_metadata(token_id) == receipt


def test_register_evaluator_then_read_returns_same_evaluator():
    adapter = MockChainAdapter()
    evaluator = _evaluator()
    tx = adapter.register_evaluator_onchain(evaluator)
    assert tx.startswith("0x")
    assert adapter.read_evaluator(evaluator.evaluator_id) == evaluator


def test_authorize_usage_records_executor_against_token_id():
    adapter = MockChainAdapter()
    receipt = _receipt()
    owner = "0x" + "ab" * 20
    executor = "0x" + "cd" * 20
    token_id = adapter.mint_inft(receipt, owner)

    assert adapter.is_authorized(token_id, executor) is False
    adapter.authorize_usage(token_id, executor, signature=b"sig")
    assert adapter.is_authorized(token_id, executor) is True


def test_submit_challenge_increments_counter():
    adapter = MockChainAdapter()
    receipt = _receipt()
    divergent = _receipt(score=0.4)
    assert adapter.challenge_count(receipt.receipt_id) == 0
    adapter.submit_challenge(receipt.receipt_id, divergent)
    adapter.submit_challenge(receipt.receipt_id, divergent)
    assert adapter.challenge_count(receipt.receipt_id) == 2
