"""Acceptance tests for TradingMarketNeutralEvaluation."""

from __future__ import annotations

from datetime import UTC, datetime

from lockstep.domains.trading.market_neutral import (
    MarketNeutralGrader,
    TradingMarketNeutralEvaluation,
)
from lockstep.evaluation.receipt import EnclaveAttestation, Receipt, ReceiptKind


def _receipt(*, pnl: float) -> Receipt:
    enclave = EnclaveAttestation(
        pubkey="0x" + "dd" * 32,
        signature="0x" + "ee" * 64,
        attestation_chain=b"mock",
        enclave_kind="tee_mock",
    )
    score_vector = {
        "funding_capture_rate": 0.5,
        "basis_dislocation_max": 0.01,
        "net_market_neutral_pnl": pnl,
        "funding_positive_sharpe": 0.7,
        "funding_negative_sharpe": 0.3,
        "funding_neutral_sharpe": 0.0,
        "trade_count": 12.0,
        "directional_exposure_max": 0.05,
    }
    return Receipt.build(
        kind=ReceiptKind.INITIAL_GRADING,
        evaluator_id=TradingMarketNeutralEvaluation().evaluator().evaluator_id,
        domain="trading_market_neutral",
        problem_id="0x" + "01" * 32,
        solution_plaintext_commitment="0x" + "02" * 32,
        solution_bundle_hash="0x" + "03" * 32,
        dataset_commitment="0x" + "04" * 32,
        grader_version=MarketNeutralGrader.version(),
        public_score_vector=score_vector,
        full_score_vector=score_vector,
        enclave=enclave,
        created_at=datetime(2026, 4, 25, 12, 0, 0, tzinfo=UTC),
    )


def test_two_evaluation_instances_produce_identical_evaluator_id():
    a = TradingMarketNeutralEvaluation()
    b = TradingMarketNeutralEvaluation()
    assert a.evaluator().evaluator_id == b.evaluator().evaluator_id


def test_rank_dimension_exists_in_score_dimensions():
    ev = TradingMarketNeutralEvaluation()
    keys = {d.key for d in ev.evaluator().score_dimensions}
    assert ev.evaluator().rank_dimension in keys


def test_grader_keys_appear_in_evaluator_score_dimensions(
    market_neutral_dataset_factory, market_neutral_capture_solver_source
):
    from lockstep.domains.trading.market_neutral.solution import MarketNeutralSolution

    ev = TradingMarketNeutralEvaluation()
    grader = ev.grader()
    sol = MarketNeutralSolution(source=market_neutral_capture_solver_source)
    ds = market_neutral_dataset_factory()
    result = grader.grade(sol, ds)

    declared = {d.key for d in ev.evaluator().score_dimensions}
    assert set(result.public_score_vector.keys()).issubset(declared)
    if result.full_score_vector is not None:
        assert set(result.full_score_vector.keys()).issubset(declared)


def test_rank_score_orders_high_above_low():
    ev = TradingMarketNeutralEvaluation()
    high = _receipt(pnl=0.10)
    low = _receipt(pnl=-0.05)
    assert ev.rank_score(high) > ev.rank_score(low)
