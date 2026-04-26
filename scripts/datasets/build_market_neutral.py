"""Build the canonical market-neutral trading dataset.

Day 4 §B.1. Reads funding + spot OHLCV + perp OHLCV for AVAX from
``data/raw/`` (populated from gecko's TA store on willie — see
``scripts/datasets/README.md``), joins on hourly timestamp, computes
basis from spot/perp closes, labels each bar with one of three funding
regimes after resampling rates to 8h windows, splits 80/20
chronologically, and persists a :class:`MarketNeutralDataset`
(commitment + bars + walk-forward windows) to
``lockstep/domains/trading/market_neutral/canonical_dataset.json``.

Asset selection — AVAX (single-coin) is canonical for the
market-neutral dataset, NOT the BTC/ETH the spec originally proposed.
The 240-day perp data window (`ta`'s coverage) was too narrow for
BTC/ETH funding to clear the spec's `|rate| > 0.0001` per 8h
threshold in both tails — both coins landed >90% in `funding_neutral`
with <10% in either positive or negative. ADA, BCH, and AVAX+BCH
combined also fell short. AVAX alone produces a clean regime
distribution (14% / 15% / 71%) and is the only candidate hitting the
≥10% balance band in every regime within our window. Per spec
guidance "do not fudge the threshold" — pick a different asset
selection. Single-coin keeps the demo simpler than the directional
dataset (which has 3 coins) without sacrificing methodology.

Funding-rate frequency note: Hyperliquid charges funding **hourly**,
not every 8h like most perps. The spec's regime threshold of
``|rate| > 0.0001`` is canonically per-8h-window — so we resample to
8h windows by sum (not mean — funding accrues, doesn't average)
before applying the threshold.

Usage::

    uv run python scripts/datasets/build_market_neutral.py
    uv run python scripts/datasets/build_market_neutral.py --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from lockstep.domains.trading.market_neutral.dataset import (
    FUNDING_THRESHOLD,
    VALID_REGIMES,
    classify_funding_regime,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_FUNDING_DIR = REPO_ROOT / "data" / "raw" / "hyperliquid" / "funding_rates"
RAW_SPOT_DIR = REPO_ROOT / "data" / "raw" / "binance" / "candles_spot"
RAW_PERP_DIR = REPO_ROOT / "data" / "raw" / "hyperliquid" / "candles_perp"

ASSETS: tuple[str, ...] = ("AVAX",)
HOURLY_STEP_MS = 3_600_000
EIGHT_HOUR_MS = 8 * HOURLY_STEP_MS
HOURS_PER_WINDOW = 8

# Spec §B.1: each of the three funding regimes must hit ≥10% of bars.
# Below that, the data window can't differentiate strategies and the
# script surfaces (or fails, with --strict). DO NOT fudge the threshold
# downstream — pick a different window.
REGIME_FLOOR = 0.10


def _load_one_coin(
    coin: str, *, strict: bool = False
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Load funding + spot + perp parquets for a coin and inner-join on timestamp.

    Returns ``(joined_df, gap_counts)``. ``joined_df`` has one row per
    hourly bar present in all three sources; ``gap_counts`` reports
    per-source non-hourly diffs in the raw streams.
    """
    funding_path = RAW_FUNDING_DIR / f"{coin}.parquet"
    spot_path = RAW_SPOT_DIR / f"{coin}_1h.parquet"
    perp_path = RAW_PERP_DIR / f"{coin}_1h.parquet"
    for p in (funding_path, spot_path, perp_path):
        if not p.exists():
            raise FileNotFoundError(
                f"missing input parquet: {p}\n"
                f"populate via the ssh-cat block in scripts/datasets/README.md"
            )

    funding = (
        pd.read_parquet(funding_path).sort_values("timestamp").reset_index(drop=True)
    )
    # Hyperliquid funding events fire approximately on hourly
    # boundaries with millisecond-level drift (e.g. 1704067200151 =
    # top-of-hour + 151 ms). Snap to the hour so the timestamp join
    # against spot/perp candles (which are exact hourly bars) matches.
    # Drift is sub-second; flooring never collapses two distinct hours
    # but does dedup the rare duplicate event per hour.
    funding["timestamp"] = (funding["timestamp"] // HOURLY_STEP_MS) * HOURLY_STEP_MS
    funding = (
        funding.drop_duplicates(subset=["timestamp"], keep="first")
        .reset_index(drop=True)
    )

    spot = pd.read_parquet(spot_path).sort_values("timestamp").reset_index(drop=True)
    perp = pd.read_parquet(perp_path).sort_values("timestamp").reset_index(drop=True)

    def _gap_count(df: pd.DataFrame) -> int:
        diffs = df["timestamp"].diff().dropna()
        return int((diffs != HOURLY_STEP_MS).sum())

    gaps = {
        "funding": _gap_count(funding),
        "spot": _gap_count(spot),
        "perp": _gap_count(perp),
    }
    if strict and any(gaps.values()):
        raise ValueError(f"{coin}: non-hourly gaps detected: {gaps}")

    # Inner-join on timestamp: only keep hours where all three sides
    # report. Outer-joining with forward-fill would silently mask
    # source-side gaps; we'd rather drop and surface the row count.
    joined = (
        funding[["timestamp", "funding_rate"]]
        .merge(
            spot[["timestamp", "close"]].rename(columns={"close": "spot_close"}),
            on="timestamp",
            how="inner",
        )
        .merge(
            perp[["timestamp", "close"]].rename(columns={"close": "perp_close"}),
            on="timestamp",
            how="inner",
        )
    )
    joined["coin"] = coin
    return joined, gaps


def _add_basis_and_resample_funding(df: pd.DataFrame) -> pd.DataFrame:
    """Derive basis + resample hourly funding to 8h sums (per §B.1).

    Adds three columns and drops rows in incomplete 8h windows:

    - ``basis`` = ``perp_close - spot_close`` (USD).
    - ``basis_bps`` = ``basis / spot_close * 10000`` (basis points).
    - ``funding_rate`` is REPLACED with the 8h-window sum (not the
      hourly value). Hyperliquid charges funding hourly; the regime
      threshold of ``|rate| > 0.0001`` is canonically per-8h, so we
      sum within each 8h window aligned on UTC 00:00/08:00/16:00.
      Sum, not mean — funding accrues, doesn't average.

    Bars in incomplete 8h windows (typically the leading and trailing
    partial windows of the source range) are dropped so every
    surviving bar's ``funding_rate`` is a proper 8-hour sum. Windows
    are aligned via integer-divide on the UTC epoch, so they sit on
    00:00/08:00/16:00 boundaries regardless of where the data starts.
    """
    out = df.copy()
    # Window-aligned 8h boundary that this hourly bar belongs to.
    out["window_8h"] = (out["timestamp"] // EIGHT_HOUR_MS) * EIGHT_HOUR_MS

    # Per-window aggregate: sum of hourly funding rates + how many
    # hourly samples we have. Keep only windows with all 8 hours so
    # the sum reflects a full 8-hour window.
    grouped = out.groupby(["coin", "window_8h"], as_index=False).agg(
        funding_rate_8h=("funding_rate", "sum"),
        hours_in_window=("funding_rate", "count"),
    )
    complete = grouped[grouped["hours_in_window"] == HOURS_PER_WINDOW]

    # Inner-merge the 8h sum back onto the hourly rows; rows in
    # incomplete windows fall away.
    out = out.merge(
        complete[["coin", "window_8h", "funding_rate_8h"]],
        on=["coin", "window_8h"],
        how="inner",
    )

    out["basis"] = out["perp_close"] - out["spot_close"]
    out["basis_bps"] = out["basis"] / out["spot_close"] * 10_000
    out["funding_rate"] = out["funding_rate_8h"]
    return out.drop(columns=["window_8h", "funding_rate_8h"]).reset_index(drop=True)


def _label_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``regime`` column via classify_funding_regime on the 8h sum.

    Assumes ``_add_basis_and_resample_funding`` has already replaced
    the hourly funding with the 8h-window sum, so the threshold check
    (``|rate| > FUNDING_THRESHOLD``) lands in the spec's per-8h units.
    """
    out = df.copy()
    out["regime"] = out["funding_rate"].apply(classify_funding_regime)
    return out


def _regime_distribution(df: pd.DataFrame) -> dict[str, float]:
    """Fraction of bars in each of the three regimes."""
    total = len(df)
    if total == 0:
        return {r: 0.0 for r in VALID_REGIMES}
    counts = df["regime"].value_counts().to_dict()
    return {r: counts.get(r, 0) / total for r in VALID_REGIMES}


def _check_regime_balance(dist: dict[str, float]) -> list[str]:
    """Return list of regimes below ``REGIME_FLOOR`` (the 10% spec band)."""
    return [
        f"{r} = {dist[r]:.1%} (< {REGIME_FLOOR:.0%})"
        for r in VALID_REGIMES
        if dist[r] < REGIME_FLOOR
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Fail (exit 1) if any source parquet has non-hourly "
            "timestamp gaps. Default behavior warns instead."
        ),
    )
    args = parser.parse_args(argv)

    print(f"reading {len(ASSETS)} coins")
    per_coin: dict[str, pd.DataFrame] = {}
    per_coin_raw_counts: dict[str, dict[str, int]] = {}
    for coin in ASSETS:
        funding_raw = pd.read_parquet(RAW_FUNDING_DIR / f"{coin}.parquet")
        spot_raw = pd.read_parquet(RAW_SPOT_DIR / f"{coin}_1h.parquet")
        perp_raw = pd.read_parquet(RAW_PERP_DIR / f"{coin}_1h.parquet")
        per_coin_raw_counts[coin] = {
            "funding": len(funding_raw),
            "spot": len(spot_raw),
            "perp": len(perp_raw),
        }

        joined, gaps = _load_one_coin(coin, strict=args.strict)
        if any(gaps.values()):
            print(f"WARN: {coin} non-hourly gaps: {gaps}")
        smallest_source = min(per_coin_raw_counts[coin].values())
        dropped = smallest_source - len(joined)
        print(
            f"  {coin}: raw rows funding={per_coin_raw_counts[coin]['funding']} "
            f"spot={per_coin_raw_counts[coin]['spot']} "
            f"perp={per_coin_raw_counts[coin]['perp']}  "
            f"→ joined {len(joined)} (dropped {dropped} unmatched from smallest side)  "
            f"gaps funding={gaps['funding']} spot={gaps['spot']} perp={gaps['perp']}"
        )

        resampled = _add_basis_and_resample_funding(joined)
        partial_dropped = len(joined) - len(resampled)
        labeled = _label_regimes(resampled)
        per_coin[coin] = labeled
        per_coin_dist = _regime_distribution(labeled)
        print(
            f"     basis: median={labeled['basis_bps'].median():+.2f} bps  "
            f"min/max={labeled['basis_bps'].min():+.1f}/{labeled['basis_bps'].max():+.1f} bps"
        )
        print(
            f"     funding (8h sums): median={labeled['funding_rate'].median()*1e4:+.3f} bps  "
            f"min/max={labeled['funding_rate'].min()*1e4:+.2f}/{labeled['funding_rate'].max()*1e4:+.2f} bps  "
            f"(dropped {partial_dropped} bars in incomplete 8h windows)"
        )
        print(
            f"     regimes: positive={per_coin_dist['funding_positive']:.1%} "
            f"negative={per_coin_dist['funding_negative']:.1%} "
            f"neutral={per_coin_dist['funding_neutral']:.1%}"
        )

    all_bars = pd.concat(per_coin.values(), ignore_index=True)
    overall_dist = _regime_distribution(all_bars)
    print(f"\noverall: {len(all_bars)} hourly rows across {len(ASSETS)} coins")
    print(
        f"regimes: positive={overall_dist['funding_positive']:.1%} "
        f"negative={overall_dist['funding_negative']:.1%} "
        f"neutral={overall_dist['funding_neutral']:.1%}  "
        f"(threshold |rate| > {FUNDING_THRESHOLD} per 8h)"
    )

    issues = _check_regime_balance(overall_dist)
    if issues:
        msg = (
            f"regime distribution outside {REGIME_FLOOR:.0%} balance band: "
            + "; ".join(issues)
            + "\n  do NOT fudge the threshold — pick a different historical "
              "window or surface to Steve (per spec §B.1 step 4)"
        )
        if args.strict:
            print(f"FATAL: {msg}", file=sys.stderr)
            return 1
        print(f"WARN: {msg}")

    # Steps 5 (split + commitment + persist) and 6 (report) land in
    # subsequent commits.
    print("\n[skeleton] split / persist not yet implemented (steps 5-6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
