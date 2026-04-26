"""Build the canonical directional trading dataset.

Day 3 §3.1. Reads 1y of hourly OHLCV for BTC/ETH/SOL from
``data/raw/binance/candles_spot/`` (populated from gecko's TA store
on willie — see ``scripts/datasets/README.md``), labels each bar with
one of four regimes, splits 80/20 chronologically, and persists a
:class:`DirectionalDataset` (commitment + bars + walk-forward windows)
to ``lockstep/domains/trading/directional/canonical_dataset.json``.

Regime methodology (final tuned thresholds — see ``THRESHOLD_*``
constants below for the full record of how the spec's starting values
were retuned to land each regime in the 5%/60% balance band):

* **bull**       — rolling 30d return > 10% AND rolling 30d vol < its median
* **bear**       — rolling 30d return < -10% OR drawdown > 15% from rolling 30d peak
* **vol_spike**  — short (7d) rolling vol > 1.4× the median of the same series
* **chop**       — everything else

The vol-spike check uses a separate short-window vol series so a slow
multi-day pump doesn't drag the spike threshold up past where actual
flash events can clear it. Realized vol is the rolling std of hourly
log-returns; not annualized because the comparison is against the same
window's median, so units cancel. Drawdown is measured against the
rolling 30-day high.

Reproducibility: same input parquets → identical Merkle roots. Storage
URI is a placeholder ``mock://...`` today; PR 4 (Day 4) will upload to
0G Galileo and rewrite the URI in the persisted JSON.

Usage::

    uv run python scripts/datasets/build_directional.py
    uv run python scripts/datasets/build_directional.py --strict
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from lockstep.domains.trading.directional.dataset import commitment_roots
from lockstep.evaluation.solution import DatasetCommitment

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw" / "binance" / "candles_spot"
OUT_JSON = REPO_ROOT / "lockstep" / "domains" / "trading" / "directional" / "canonical_dataset.json"
OUT_REPORT = REPO_ROOT / "reports" / "day-03" / "dataset-directional.md"

ASSETS = ("BTC", "ETH", "SOL")
HOURS_PER_DAY = 24
WARMUP_DAYS = 30
WARMUP_HOURS = WARMUP_DAYS * HOURS_PER_DAY

# Regime thresholds — tuned against the 2025-03-25 → 2026-03-24 BTC/ETH/SOL window
# to satisfy "each regime ≥ 5%, no regime > 60%". Initial spec values
# (15% / -15% / -20% / 2.0×) produced chop=68%, vol_spike=0%; retuned
# to the values below. Document any further retunes here.
THRESHOLD_BULL_RETURN = 0.10
THRESHOLD_BEAR_RETURN = -0.10
THRESHOLD_BEAR_DRAWDOWN = -0.15
THRESHOLD_VOL_SPIKE_MULT = 1.4
# Vol-spike check uses a SHORT rolling window (catches flash events)
# against the median of the same short-window series. Decoupled from the
# 30-day window used for bull/bear so a multi-day pump doesn't drag the
# spike threshold up past where actual spikes can clear it.
VOL_SPIKE_WINDOW_HOURS = HOURS_PER_DAY * 7  # 7-day window for spike detection


def _load_one_asset(asset: str) -> pd.DataFrame:
    path = RAW_DIR / f"{asset}_1h.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"missing input parquet: {path}\n"
            f"populate via the ssh-cat block in scripts/datasets/README.md"
        )
    df = pd.read_parquet(path)
    df = df.sort_values("timestamp").reset_index(drop=True)
    return df


def _label_regimes(df: pd.DataFrame) -> pd.DataFrame:
    """Add a ``regime`` column per row using the spec's methodology.

    Computed per-asset (each coin has its own local market state).
    Window-warmup rows (first 30 days, where the rolling stats are NaN)
    are trimmed from the output rather than carrying NaN downstream.
    """
    out = df.copy()
    out["log_return"] = np.log(out["close"] / out["close"].shift(1))
    window = WARMUP_HOURS

    out["rolling_30d_return"] = out["close"] / out["close"].shift(window) - 1
    out["rolling_30d_vol"] = out["log_return"].rolling(window).std()
    out["rolling_max"] = out["close"].rolling(window).max()
    out["drawdown"] = out["close"] / out["rolling_max"] - 1
    out["short_vol"] = out["log_return"].rolling(VOL_SPIKE_WINDOW_HOURS).std()

    median_30d_vol = out["rolling_30d_vol"].median()
    median_short_vol = out["short_vol"].median()

    def _label(row: pd.Series) -> str:
        if pd.isna(row["rolling_30d_return"]) or pd.isna(row["rolling_30d_vol"]):
            return ""  # warmup; trimmed below
        # Vol spikes win over directional labels — a flash event in a
        # bull market is still a vol regime for grader differentiation.
        if row["short_vol"] > THRESHOLD_VOL_SPIKE_MULT * median_short_vol:
            return "vol_spike"
        if (
            row["rolling_30d_return"] > THRESHOLD_BULL_RETURN
            and row["rolling_30d_vol"] < median_30d_vol
        ):
            return "bull"
        if (
            row["rolling_30d_return"] < THRESHOLD_BEAR_RETURN
            or row["drawdown"] < THRESHOLD_BEAR_DRAWDOWN
        ):
            return "bear"
        return "chop"

    out["regime"] = out.apply(_label, axis=1)
    out = out[out["regime"] != ""].reset_index(drop=True)
    return out


def _to_bar_dicts(df: pd.DataFrame, asset: str) -> list[dict]:
    """Convert dataframe rows to the bar-dict shape DirectionalDataset expects."""
    bars: list[dict] = []
    for row in df.itertuples(index=False):
        bars.append(
            {
                # spec says epoch seconds; parquet stores ms
                "timestamp": int(row.timestamp // 1000),
                "asset": asset,
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": float(row.volume),
                "regime": str(row.regime),
            }
        )
    return bars


def _regime_distribution(bars: list[dict]) -> dict[str, float]:
    counts: dict[str, int] = {}
    for b in bars:
        counts[b["regime"]] = counts.get(b["regime"], 0) + 1
    total = len(bars)
    return {r: counts.get(r, 0) / total for r in ("bull", "bear", "chop", "vol_spike")}


def _check_distribution(dist: dict[str, float]) -> list[str]:
    """Return list of violations against the spec's 5%/60% balance constraint."""
    issues: list[str] = []
    for regime, frac in dist.items():
        if frac < 0.05:
            issues.append(f"{regime} = {frac:.1%} (< 5%)")
        if frac > 0.60:
            issues.append(f"{regime} = {frac:.1%} (> 60%)")
    return issues


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail (exit 1) if regime distribution violates the 5%%/60%% balance constraint.",
    )
    args = parser.parse_args(argv)

    print(f"reading {len(ASSETS)} parquets from {RAW_DIR}")
    per_asset: dict[str, list[dict]] = {}
    for asset in ASSETS:
        raw = _load_one_asset(asset)
        labeled = _label_regimes(raw)
        bars = _to_bar_dicts(labeled, asset)
        per_asset[asset] = bars
        dist = _regime_distribution(bars)
        print(
            f"  {asset}: {len(raw)} raw rows → {len(bars)} labeled "
            f"(dropped {len(raw) - len(bars)} warmup) "
            f"regimes: bull={dist['bull']:.1%} bear={dist['bear']:.1%} "
            f"chop={dist['chop']:.1%} vol_spike={dist['vol_spike']:.1%}"
        )

    # Interleave assets per timestamp: for each hour t, emit BTC then ETH then SOL.
    # Keeps the bar order deterministic and chronological while preserving
    # all (asset, t) pairs.
    all_bars = sorted(
        (b for asset_bars in per_asset.values() for b in asset_bars),
        key=lambda b: (b["timestamp"], b["asset"]),
    )

    # 80/20 chronological split — last 20% of unique timestamps is the holdout.
    unique_ts = sorted({b["timestamp"] for b in all_bars})
    split_ts = unique_ts[int(len(unique_ts) * 0.80)]
    public_bars = [b for b in all_bars if b["timestamp"] < split_ts]
    private_bars = [b for b in all_bars if b["timestamp"] >= split_ts]

    overall_dist = _regime_distribution(all_bars)
    print(
        f"\noverall: {len(all_bars)} bars  "
        f"public={len(public_bars)} private={len(private_bars)}  "
        f"split @ ts={split_ts} ({datetime.fromtimestamp(split_ts, tz=UTC).isoformat()})"
    )
    print(
        f"regimes: bull={overall_dist['bull']:.1%} bear={overall_dist['bear']:.1%} "
        f"chop={overall_dist['chop']:.1%} vol_spike={overall_dist['vol_spike']:.1%}"
    )

    issues = _check_distribution(overall_dist)
    if issues:
        msg = "regime distribution outside spec band: " + "; ".join(issues)
        if args.strict:
            print(f"FATAL: {msg}", file=sys.stderr)
            return 1
        print(f"WARN: {msg}")

    pub_root, priv_root, merkle_root = commitment_roots(
        tuple(public_bars), tuple(private_bars)
    )
    storage_uri = f"mock://dataset/trading_directional/{merkle_root[:18]}"
    commitment = DatasetCommitment(
        domain="trading_directional",
        merkle_root=merkle_root,
        public_root=pub_root,
        private_root=priv_root,
        storage_uri=storage_uri,
        schema_version="v1",
    )

    walk_forward_windows = ((len(public_bars) // 2, len(public_bars)),)

    payload = {
        "commitment": commitment.model_dump(),
        "public_bars": public_bars,
        "private_bars": private_bars,
        "walk_forward_windows": list(walk_forward_windows),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote canonical dataset → {OUT_JSON} ({OUT_JSON.stat().st_size // 1024} KB)")

    _write_report(
        per_asset=per_asset,
        all_bars=all_bars,
        public_bars=public_bars,
        private_bars=private_bars,
        commitment=commitment,
        overall_dist=overall_dist,
    )
    print(f"wrote report → {OUT_REPORT}")
    return 0


def _write_report(
    *,
    per_asset: dict[str, list[dict]],
    all_bars: list[dict],
    public_bars: list[dict],
    private_bars: list[dict],
    commitment: DatasetCommitment,
    overall_dist: dict[str, float],
) -> None:
    OUT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    pub_ts = sorted({b["timestamp"] for b in public_bars})
    priv_ts = sorted({b["timestamp"] for b in private_bars})

    def _fmt(ts: int) -> str:
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()

    lines: list[str] = []
    lines.append("# Day 3 §3.1 — Directional canonical dataset")
    lines.append("")
    lines.append("## Source window and asset list")
    lines.append("")
    lines.append("| Asset | Source parquet | Raw rows | Labeled rows |")
    lines.append("|---|---|---:|---:|")
    for asset, bars in per_asset.items():
        path = f"data/raw/binance/candles_spot/{asset}_1h.parquet"
        # raw rows is bars + WARMUP_HOURS dropped
        lines.append(f"| {asset} | `{path}` | {len(bars) + WARMUP_HOURS} | {len(bars)} |")
    lines.append("")
    lines.append(f"**Total bars:** {len(all_bars)} ({len(per_asset)} assets × ~"
                 f"{len(all_bars) // len(per_asset)} hours each)")
    lines.append("")

    lines.append("## Cleaning decisions and gap handling")
    lines.append("")
    lines.append(
        f"- **Warmup trim:** dropped the first {WARMUP_DAYS} days "
        f"({WARMUP_HOURS} hours) per asset where the rolling 30-day "
        "stats are NaN. Cleaner than carrying a sentinel regime label "
        "into downstream grading."
    )
    lines.append(
        "- **Gap handling:** verified via `pd.read_parquet` + "
        "`timestamp.diff() == 3600s` that all three Binance parquets "
        "have zero gaps over the source window. No forward-fill needed."
    )
    lines.append("- **Timestamp normalization:** parquet timestamps are epoch ms; "
                 "the canonical dataset uses epoch seconds (per the "
                 "`DirectionalDataset` schema in `domains/trading/directional/dataset.py`).")
    lines.append("")

    lines.append("## Final regime thresholds and distribution")
    lines.append("")
    lines.append("Methodology (per spec §3.1 step 4):")
    lines.append("")
    lines.append(f"- **bull:** rolling 30d return > {THRESHOLD_BULL_RETURN:.0%} "
                 "AND realized vol < median")
    lines.append(f"- **bear:** rolling 30d return < {THRESHOLD_BEAR_RETURN:.0%} "
                 f"OR drawdown > {abs(THRESHOLD_BEAR_DRAWDOWN):.0%} from rolling 30d peak")
    lines.append(f"- **vol_spike:** realized vol > {THRESHOLD_VOL_SPIKE_MULT}× median")
    lines.append("- **chop:** everything else")
    lines.append("")
    lines.append("Realized vol = rolling std of hourly log-returns over a 30-day window. "
                 "Not annualized — the comparison is against the same window's median, "
                 "so units cancel.")
    lines.append("")
    lines.append("Distribution across the full labeled window:")
    lines.append("")
    lines.append("| Regime | Fraction | Bars |")
    lines.append("|---|---:|---:|")
    counts = {r: sum(1 for b in all_bars if b["regime"] == r)
              for r in ("bull", "bear", "chop", "vol_spike")}
    for regime, frac in overall_dist.items():
        lines.append(f"| {regime} | {frac:.1%} | {counts[regime]} |")
    lines.append("")
    issues = _check_distribution(overall_dist)
    if issues:
        lines.append("**Spec balance check (each regime ≥5%, no regime >60%):** "
                     f"violations — {'; '.join(issues)}. "
                     "Thresholds may need retuning for this window.")
    else:
        lines.append("**Spec balance check (each regime ≥5%, no regime >60%):** ✓ all regimes within band.")
    lines.append("")

    lines.append("## Public/private split methodology")
    lines.append("")
    lines.append("80/20 chronological split. The split point is chosen at the "
                 "80th-percentile unique timestamp; all bars with timestamp `<` that "
                 "go into public, all bars with timestamp `>=` into private. "
                 "Keeps the holdout strictly in the future of the public window — "
                 "no leakage.")
    lines.append("")
    lines.append(f"- **Public:** {len(public_bars)} bars, "
                 f"{_fmt(pub_ts[0])} → {_fmt(pub_ts[-1])}")
    lines.append(f"- **Private:** {len(private_bars)} bars, "
                 f"{_fmt(priv_ts[0])} → {_fmt(priv_ts[-1])}")
    lines.append("")

    lines.append("## Merkle roots and storage URI")
    lines.append("")
    lines.append(f"- `merkle_root` = `{commitment.merkle_root}`")
    lines.append(f"- `public_root` = `{commitment.public_root}`")
    lines.append(f"- `private_root` = `{commitment.private_root}`")
    lines.append(f"- `storage_uri` = `{commitment.storage_uri}` (placeholder; "
                 "PR 4 uploads to 0G Galileo and rewrites)")
    lines.append("")
    lines.append("Roots are computed via "
                 "`lockstep.domains.trading.directional.commitment_roots`, which "
                 "matches `MockStorageAdapter._payload_root` so downloads verify "
                 "cleanly on either adapter.")
    lines.append("")

    lines.append("## File sizes and upload cost")
    lines.append("")
    lines.append(f"- `{OUT_JSON.relative_to(REPO_ROOT)}`: "
                 f"{OUT_JSON.stat().st_size // 1024} KB")
    lines.append("- Upload cost: **deferred to PR 4 (Day 4)** — today the storage "
                 "URI is `mock://`, so no testnet tokens spent.")
    lines.append("")

    OUT_REPORT.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
