"""Acceptance tests for MockPaymentAdapter."""

from __future__ import annotations

from lockstep.substrate.payment import MockPaymentAdapter


def _adapter() -> MockPaymentAdapter:
    return MockPaymentAdapter(
        owners={1: "0x" + "ab" * 20},
        price=1_500_000,
    )


def test_pay_then_verify_payment_returns_true():
    adapter = _adapter()
    quote = adapter.quote(token_id=1, executor="0x" + "ee" * 20)
    receipt = adapter.pay(quote, payer_private_key=b"payer-secret")
    assert (
        adapter.verify_payment(
            receipt,
            expected_recipient="0x" + "ab" * 20,
            expected_amount=1_500_000,
        )
        is True
    )


def test_verify_payment_with_mismatched_amount_is_false():
    adapter = _adapter()
    quote = adapter.quote(token_id=1, executor="0x" + "ee" * 20)
    receipt = adapter.pay(quote, payer_private_key=b"payer-secret")
    assert (
        adapter.verify_payment(
            receipt,
            expected_recipient="0x" + "ab" * 20,
            expected_amount=999,
        )
        is False
    )


def test_verify_payment_with_mismatched_recipient_is_false():
    adapter = _adapter()
    quote = adapter.quote(token_id=1, executor="0x" + "ee" * 20)
    receipt = adapter.pay(quote, payer_private_key=b"payer-secret")
    assert (
        adapter.verify_payment(
            receipt,
            expected_recipient="0x" + "ff" * 20,
            expected_amount=1_500_000,
        )
        is False
    )


def test_replaying_quote_produces_distinct_receipts():
    adapter = _adapter()
    q1 = adapter.quote(token_id=1, executor="0x" + "ee" * 20)
    q2 = adapter.quote(token_id=1, executor="0x" + "ee" * 20)
    assert q1.nonce != q2.nonce

    r1 = adapter.pay(q1, payer_private_key=b"payer-secret")
    r2 = adapter.pay(q2, payer_private_key=b"payer-secret")
    assert r1.proof != r2.proof
