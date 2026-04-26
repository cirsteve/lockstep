"""Acceptance tests for MarketNeutralDataset."""

from __future__ import annotations


def test_construct_and_verify_integrity_returns_true(market_neutral_dataset_factory):
    ds = market_neutral_dataset_factory()
    assert ds.verify_integrity() is True


def test_mutating_one_row_makes_verify_integrity_false(market_neutral_dataset_factory):
    ds = market_neutral_dataset_factory()
    bars = list(ds.public_bars)
    bars[3] = {**bars[3], "funding_rate": bars[3]["funding_rate"] + 1.0}
    mutated = ds.model_copy(update={"public_bars": tuple(bars)})
    assert mutated.verify_integrity() is False


def test_public_view_strips_private_data(market_neutral_dataset_factory):
    ds = market_neutral_dataset_factory()
    pv = ds.public_view()
    assert pv.has_private_data() is False
    assert pv.private_bars == ()


def test_public_view_verify_integrity_passes(market_neutral_dataset_factory):
    pv = market_neutral_dataset_factory().public_view()
    assert pv.verify_integrity() is True


def test_funding_regime_coverage(market_neutral_dataset_factory):
    ds = market_neutral_dataset_factory(n_public=60, n_private=20)
    regimes = {b["regime"] for b in ds.all_bars()}
    assert regimes == {"funding_positive", "funding_negative", "funding_neutral"}
