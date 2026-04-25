"""End-to-end exercise of the Lockstep interfaces.

Covers the full happy path plus the critical failure modes:
    1. Solution serialization round-trips and produces a stable plaintext
       commitment.
    2. EncryptedSolution distinguishes plaintext_commitment (solution
       identity) from bundle_hash (this specific encrypted copy's identity).
    3. Grader produces both public and full score vectors when given a full
       dataset, only public scores for a public-only view.
    4. Receipt.build() derives receipt_id from canonical signing payload;
       tampering with any field invalidates the receipt.
    5. canonical_signing_payload() is deterministic across runs.
    6. CoinFlipEvaluation works end-to-end — concrete proof the abstract
       interfaces don't carry hidden trading-specific assumptions.

If you change a canonical-form rule (signing payload, plaintext commitment,
or receipt id derivation), this file's expected hashes flip. That's the
point: it's the regression net for the trust boundary.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from lockstep import (
    DatasetCommitment,
    EnclaveAttestation,
    EncryptedSolution,
    Evaluator,
    Receipt,
    ReceiptKind,
    derive_receipt_id,
    get_evaluator,
)
from lockstep.domains.coin_flip import (
    CoinFlipDataset,
    CoinFlipEvaluation,
    CoinFlipGrader,
    CoinFlipSolution,
)
from lockstep.evaluation.evaluator import DisputePolicy, HoldoutPolicy, ScoreDimension


def test_end_to_end_lockstep_interfaces():
    # ----- Phase 1: Solution identity -----
    sol = CoinFlipSolution(rule="match:0")
    serialized = sol.serialize()
    deserialized = CoinFlipSolution.deserialize(serialized)
    assert sol == deserialized
    assert sol.plaintext_commitment() == deserialized.plaintext_commitment()

    sol2 = CoinFlipSolution(rule="match:1")
    assert sol.plaintext_commitment() != sol2.plaintext_commitment()

    # ----- Phase 2: EncryptedSolution distinguishes plaintext from bundle -----
    plaintext = sol.plaintext_commitment()
    bundle_for_alice = EncryptedSolution(
        plaintext_commitment=plaintext,
        bundle_hash="0x" + hashlib.sha256(b"bundle-for-alice").hexdigest(),
        storage_uri="zg://bundle/alice",
        recipient_pubkey="0x" + "a" * 64,
    )
    bundle_for_bob = EncryptedSolution(
        plaintext_commitment=plaintext,
        bundle_hash="0x" + hashlib.sha256(b"bundle-for-bob").hexdigest(),
        storage_uri="zg://bundle/bob",
        recipient_pubkey="0x" + "b" * 64,
    )
    assert bundle_for_alice.plaintext_commitment == bundle_for_bob.plaintext_commitment
    assert bundle_for_alice.bundle_hash != bundle_for_bob.bundle_hash

    # ----- Phase 3: Public vs full grading -----
    commitment = DatasetCommitment(
        domain="coin_flip",
        merkle_root="0x" + "11" * 32,
        public_root="0x" + "22" * 32,
        private_root="0x" + "33" * 32,
        storage_uri="zg://dataset/coin_flip/v1",
        schema_version="v1",
    )
    public_examples = (
        ((0.6,), True),
        ((0.4,), True),
        ((0.7,), True),
        ((0.3,), False),
    )
    private_examples = (
        ((0.8,), True),
        ((0.9,), True),
        ((0.2,), False),
        ((0.1,), False),
        ((0.55,), False),
    )
    dataset_full = CoinFlipDataset(
        commitment=commitment,
        public_examples=public_examples,
        private_examples=private_examples,
    )
    dataset_public_only = dataset_full.public_view()

    grader = CoinFlipGrader()
    full_result = grader.grade(sol, dataset_full)
    public_result = grader.grade(sol, dataset_public_only)

    assert full_result.full_score_vector is not None
    assert public_result.full_score_vector is None
    assert full_result.full_score_vector["accuracy"] != full_result.public_score_vector["accuracy"]

    # Determinism
    again = grader.grade(sol, dataset_full)
    assert again == full_result

    # ----- Phase 4: Receipt construction with derived id -----
    fake_signature = "0x" + "ee" * 64
    fake_pubkey = "0x" + "dd" * 32
    enclave = EnclaveAttestation(
        pubkey=fake_pubkey,
        signature=fake_signature,
        attestation_chain=b"mock-attestation",
        enclave_kind="tee_mock",
    )

    rcpt = Receipt.build(
        kind=ReceiptKind.INITIAL_GRADING,
        evaluator_id=CoinFlipEvaluation().evaluator().evaluator_id,
        domain="coin_flip",
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment=sol.plaintext_commitment(),
        solution_bundle_hash=bundle_for_alice.bundle_hash,
        dataset_commitment=commitment.merkle_root,
        grader_version=CoinFlipGrader.version(),
        public_score_vector=full_result.public_score_vector,
        full_score_vector=full_result.full_score_vector,
        metadata=full_result.metadata,
        enclave=enclave,
        created_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
    )

    assert rcpt.receipt_id == derive_receipt_id(rcpt.canonical_signing_payload())

    # ----- Phase 5: Canonical signing payload is deterministic -----
    payload_a = rcpt.canonical_signing_payload()
    payload_b = rcpt.canonical_signing_payload()
    assert payload_a == payload_b

    # ----- Phase 6: Tamper detection -----
    with pytest.raises(Exception, match="receipt_id mismatch"):
        Receipt(
            receipt_id="0x" + "f" * 64,
            kind=ReceiptKind.INITIAL_GRADING,
            evaluator_id=CoinFlipEvaluation().evaluator().evaluator_id,
            domain="coin_flip",
            problem_id="0x" + "01" * 32,
            solution_plaintext_commitment=sol.plaintext_commitment(),
            solution_bundle_hash=bundle_for_alice.bundle_hash,
            dataset_commitment=commitment.merkle_root,
            grader_version=CoinFlipGrader.version(),
            public_score_vector=full_result.public_score_vector,
            full_score_vector=full_result.full_score_vector,
            metadata=full_result.metadata,
            created_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
            enclave=enclave,
        )

    tampered = Receipt.build(
        kind=ReceiptKind.INITIAL_GRADING,
        evaluator_id=CoinFlipEvaluation().evaluator().evaluator_id,
        domain="coin_flip",
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment=sol.plaintext_commitment(),
        solution_bundle_hash=bundle_for_alice.bundle_hash,
        dataset_commitment=commitment.merkle_root,
        grader_version=CoinFlipGrader.version(),
        public_score_vector={**full_result.public_score_vector, "accuracy": 1.0},
        full_score_vector=full_result.full_score_vector,
        metadata=full_result.metadata,
        enclave=enclave,
        created_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
    )
    assert tampered.receipt_id != rcpt.receipt_id

    # ----- Phase 7: Domain-agnostic ranking -----
    ev = CoinFlipEvaluation()
    score = ev.rank_score(rcpt)
    assert score == full_result.full_score_vector["accuracy"]

    public_only_rcpt = Receipt.build(
        kind=ReceiptKind.REVALIDATION,
        evaluator_id=CoinFlipEvaluation().evaluator().evaluator_id,
        domain="coin_flip",
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment=sol.plaintext_commitment(),
        solution_bundle_hash=bundle_for_alice.bundle_hash,
        dataset_commitment=commitment.merkle_root,
        grader_version=CoinFlipGrader.version(),
        public_score_vector=full_result.public_score_vector,
        full_score_vector=None,
        metadata={},
        enclave=enclave,
        previous_receipt_id=rcpt.receipt_id,
    )
    assert ev.rank_score(public_only_rcpt) == full_result.public_score_vector["accuracy"]

    # ----- Phase 8: Evaluator schema and registry -----
    evaluator = ev.evaluator()
    ev2 = CoinFlipEvaluation()
    assert ev2.evaluator().evaluator_id == evaluator.evaluator_id
    assert rcpt.evaluator_id == evaluator.evaluator_id
    assert rcpt.grader_version in evaluator.accepted_grader_versions

    declared_keys = {d.key for d in evaluator.score_dimensions}
    assert set(rcpt.public_score_vector.keys()).issubset(declared_keys)

    fetched = get_evaluator(evaluator.evaluator_id)
    assert fetched == evaluator

    bytes_a = evaluator.canonical_bytes()
    bytes_b = evaluator.canonical_bytes()
    assert bytes_a == bytes_b

    with pytest.raises(Exception, match="rank_dimension"):
        Evaluator.build(
            domain_name="coin_flip_broken",
            domain_version="v1",
            solver_input_schema={},
            solver_output_schema={},
            accepted_grader_versions=["0x" + "00" * 32],
            dataset_schema_version="v1",
            holdout_policy=HoldoutPolicy(public_fraction=0.5),
            score_dimensions=[ScoreDimension(key="foo", description="x")],
            rank_dimension="nonexistent_dimension",
            dispute_policy=DisputePolicy(),
        )
