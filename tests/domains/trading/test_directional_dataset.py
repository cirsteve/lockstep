"""Acceptance tests for DirectionalDataset."""

from __future__ import annotations


def test_construct_and_verify_integrity_returns_true(directional_dataset_factory):
    ds = directional_dataset_factory()
    assert ds.verify_integrity() is True


def test_mutating_one_row_makes_verify_integrity_false(directional_dataset_factory):
    ds = directional_dataset_factory()
    mutated_bars = list(ds.public_bars)
    mutated_bars[5] = {**mutated_bars[5], "close": mutated_bars[5]["close"] + 0.01}
    mutated = ds.model_copy(update={"public_bars": tuple(mutated_bars)})
    assert mutated.verify_integrity() is False


def test_public_view_strips_private_data(directional_dataset_factory):
    ds = directional_dataset_factory()
    pv = ds.public_view()
    assert pv.has_private_data() is False
    assert pv.public_bars == ds.public_bars
    assert pv.private_bars == ()


def test_regime_label_coverage(directional_dataset_factory):
    ds = directional_dataset_factory(n_public=40, n_private=10)
    all_bars = ds.all_bars()
    # every bar has a regime label
    assert all("regime" in b for b in all_bars)
    # all four regimes appear
    regimes = {b["regime"] for b in all_bars}
    assert regimes == {"bull", "bear", "chop", "vol_spike"}
