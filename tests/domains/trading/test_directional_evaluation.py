"""Acceptance tests for TradingDirectionalEvaluation."""

from __future__ import annotations

from datetime import UTC, datetime

from lockstep.domains.trading.directional import (
    DirectionalGrader,
    TradingDirectionalEvaluation,
)
from lockstep.evaluation.receipt import EnclaveAttestation, Receipt, ReceiptKind


def _receipt_with_score(*, worst_regime_sharpe: float) -> Receipt:
    enclave = EnclaveAttestation(
        pubkey="0x" + "dd" * 32,
        signature="0x" + "ee" * 64,
        attestation_chain=b"mock",
        enclave_kind="tee_mock",
    )
    score_vector = {
        "worst_regime_sharpe": worst_regime_sharpe,
        "aggregate_sharpe": 0.5,
        "max_drawdown": 0.1,
        "bull_sharpe": 1.0,
        "bear_sharpe": 0.0,
        "chop_sharpe": 0.0,
        "vol_spike_sharpe": 0.0,
        "trade_count": 10.0,
        "win_rate": 0.55,
    }
    return Receipt.build(
        kind=ReceiptKind.INITIAL_GRADING,
        evaluator_id=TradingDirectionalEvaluation().evaluator().evaluator_id,
        domain="trading_directional",
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment="0x" + "02" * 32,
        solution_bundle_hash="0x" + "03" * 32,
        dataset_commitment="0x" + "04" * 32,
        grader_version=DirectionalGrader.version(),
        public_score_vector=score_vector,
        full_score_vector=score_vector,
        enclave=enclave,
        created_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
    )


def test_two_evaluation_instances_produce_identical_evaluator_id():
    a = TradingDirectionalEvaluation()
    b = TradingDirectionalEvaluation()
    assert a.evaluator().evaluator_id == b.evaluator().evaluator_id


def test_rank_dimension_exists_in_score_dimensions():
    ev = TradingDirectionalEvaluation()
    evaluator = ev.evaluator()
    keys = {d.key for d in evaluator.score_dimensions}
    assert evaluator.rank_dimension in keys


def test_grader_keys_appear_in_evaluator_score_dimensions(
    directional_dataset_factory, directional_oracle_solver_source
):
    from lockstep.domains.trading.directional.solution import DirectionalSolution

    ev = TradingDirectionalEvaluation()
    grader = ev.grader()
    sol = DirectionalSolution(source=directional_oracle_solver_source)
    ds = directional_dataset_factory()
    result = grader.grade(sol, ds)

    declared = {d.key for d in ev.evaluator().score_dimensions}
    assert set(result.public_score_vector.keys()).issubset(declared)
    if result.full_score_vector is not None:
        assert set(result.full_score_vector.keys()).issubset(declared)


def test_rank_score_orders_high_above_low():
    ev = TradingDirectionalEvaluation()
    high = _receipt_with_score(worst_regime_sharpe=2.5)
    low = _receipt_with_score(worst_regime_sharpe=-0.5)
    assert ev.rank_score(high) > ev.rank_score(low)
