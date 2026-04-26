"""Build the canonical market-neutral trading dataset.

Day 4 §B.1. Reads funding + spot OHLCV + perp OHLCV for BTC/ETH from
``data/raw/`` (populated from gecko's TA store on willie — see
``scripts/datasets/README.md``), joins on hourly timestamp, computes
basis from spot/perp closes, labels each bar with one of three funding
regimes after resampling rates to 8h windows, splits 80/20
chronologically, and persists a :class:`MarketNeutralDataset`
(commitment + bars + walk-forward windows) to
``lockstep/domains/trading/market_neutral/canonical_dataset.json``.

This commit (PR #6 step 2) ships only the loader + timestamp join.
Basis derivation, regime labeling, persistence, and the methodology
report land in subsequent commits.

Funding-rate frequency note: Hyperliquid charges funding **hourly**,
not every 8h like most perps. The spec's regime threshold of
``|rate| > 0.0001`` is canonically per-8h-window — so step 3 of the
plan resamples to 8h windows by sum (not mean — funding accrues,
doesn't average) before applying the threshold. The hourly join here
preserves the raw funding stream for that downstream resample.

Usage::

    uv run python scripts/datasets/build_market_neutral.py
    uv run python scripts/datasets/build_market_neutral.py --strict
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_FUNDING_DIR = REPO_ROOT / "data" / "raw" / "hyperliquid" / "funding_rates"
RAW_SPOT_DIR = REPO_ROOT / "data" / "raw" / "binance" / "candles_spot"
RAW_PERP_DIR = REPO_ROOT / "data" / "raw" / "hyperliquid" / "candles_perp"

ASSETS = ("BTC", "ETH")
HOURLY_STEP_MS = 3_600_000
EIGHT_HOUR_MS = 8 * HOURLY_STEP_MS
HOURS_PER_WINDOW = 8


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
        per_coin[coin] = resampled
        print(
            f"     basis: median={resampled['basis_bps'].median():+.2f} bps  "
            f"min/max={resampled['basis_bps'].min():+.1f}/{resampled['basis_bps'].max():+.1f} bps"
        )
        print(
            f"     funding (8h sums): median={resampled['funding_rate'].median()*1e4:+.3f} bps  "
            f"min/max={resampled['funding_rate'].min()*1e4:+.2f}/{resampled['funding_rate'].max()*1e4:+.2f} bps  "
            f"(dropped {partial_dropped} bars in incomplete 8h windows)"
        )

    total_rows = sum(len(df) for df in per_coin.values())
    print(f"\noverall: {total_rows} hourly rows across {len(ASSETS)} coins")

    # Steps 4 (regime labels + threshold tuning), 5 (split + commitment +
    # persist), and 6 (report) land in subsequent commits.
    print("\n[skeleton] regime / split / persist not yet implemented (steps 4-6).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
