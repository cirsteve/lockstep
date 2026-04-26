# Day 4 §B.1 — Market-neutral canonical dataset

## Source window and asset list

| Asset | Source parquet | Raw rows | After join | After 8h drop |
|---|---|---:|---:|---:|
| AVAX | `data/raw/binance/candles_spot/AVAX_1h.parquet` | 8760 | 5630 | 5624 |
| AVAX | `data/raw/hyperliquid/candles_perp/AVAX_1h.parquet` | 5750 | 5630 | 5624 |
| AVAX | `data/raw/hyperliquid/funding_rates/AVAX.parquet` | 19487 | 5630 | 5624 |

**Total bars:** 5624 (single-coin AVAX × ~5624 hours, ~234 days
2025-07-31 → 2026-03-22)

**Asset selection — single-coin AVAX, NOT the BTC/ETH pair the spec
originally proposed.** The spec called for BTC/ETH because they're the
canonical perp pair, but the 240-day perp data window in `ta`'s
training store is too narrow for either to clear the spec's funding
regime balance check (≥10% per regime). Probed every coin with
funding + Binance spot + Hyperliquid perp coverage:

| Coin | positive | negative | neutral | meets ≥10% balance? |
|---|---:|---:|---:|---|
| BTC  |  5.7% |  2.4% | 91.9% | ✗ both tails |
| ETH  |  7.0% |  2.7% | 90.3% | ✗ both tails |
| ADA  |  1.3% | 14.1% | 84.6% | ✗ positive |
| BCH  |  5.7% | 14.2% | 80.1% | ✗ positive |
| **AVAX** | **14.1%** | **14.7%** | **71.3%** | **✓ all three** |

AVAX alone is the only candidate inside the window. Per spec §B.1
step 4 — "do not fudge the threshold to manufacture coverage" —
asset selection is the right lever to pull, not the threshold.
Single-coin keeps the demo simpler than the directional dataset
(BTC/ETH/SOL); the strategies process per-coin so the asymmetry is
structural rather than functional. Wider-window / multi-coin work is
saved for gecko's TA store; this is a hackathon demo to demonstrate
protocol capability.

## Cleaning decisions and gap handling

- **Funding-rate timestamp drift.** Hyperliquid funding events fire
  approximately on hourly boundaries with millisecond-level offsets
  (e.g. `1704067200151` = top-of-hour + 151 ms). The loader snaps
  funding timestamps to the hour via integer-divide before joining
  against the exact-hourly spot/perp candles. Without this, the
  inner-join matches only ~77/5750 rows; with it, the join recovers
  the full overlap.
- **Inner-join, not outer-fill.** The three sources are inner-joined
  on the snapped timestamp. Forward-filling would silently mask
  source-side gaps; we'd rather drop and surface the row count.
  120 hourly rows are dropped on the perp side (the perp parquet is
  the smallest source — 5750 rows vs. 8760 spot vs. 19487 funding).
- **8h-window completeness.** After joining, hourly funding rates are
  resampled to 8h windows (sum, see "Threshold methodology" below).
  Hours in *incomplete* 8h windows — typically the leading partial
  window of the source range — are dropped so every surviving bar's
  `funding_rate` field is a proper 8-hour sum. 6 hours dropped per
  the leading window edge.
- **One-row gap in funding.** The funding parquet shows a single
  non-hourly diff after the snap; a missed hour somewhere in the
  source stream. Bars on either side were retained without
  interpolation. Re-run with `--strict` to fail on gaps instead of
  warning.
- **Timestamp normalization.** Parquet timestamps are epoch ms; the
  canonical dataset uses epoch seconds, matching the directional
  builder and the `DirectionalDataset` schema convention.

## Threshold methodology

Per spec §B.1 step 3, the regime threshold of `|rate| > 0.0001` is
canonically *per 8h window*. Hyperliquid charges funding hourly, so
the loader resamples to 8h windows by **sum** (not mean — funding
accrues, doesn't average) before applying the threshold.

Windows are aligned on UTC 00:00 / 08:00 / 16:00 boundaries via
integer-divide on the epoch-ms timestamp. Each hourly bar carries the
8h-window sum that contains it, broadcast across the 8 hours of that
window. The threshold is then applied via
`lockstep.domains.trading.market_neutral.classify_funding_regime`:

- `funding_positive` — 8h sum > +0.0001 (i.e. > +1 bps per 8h)
- `funding_negative` — 8h sum < -0.0001 (i.e. < -1 bps per 8h)
- `funding_neutral` — `|sum| <= 0.0001`

Considered alternatives (and why they were rejected):

- *Hourly-scaled threshold* (`|rate| > 0.0000125 per hour`): produces
  the same regime distribution mathematically but is unintuitive when
  explaining to a hackathon audience used to the 8h convention.
- *Mean over 8h*: rejected per spec — funding is paid out, not
  averaged. Sum is the dimensionally correct aggregation.

## Final regime distribution

Distribution across the full labeled window:

| Regime | Fraction | Bars |
|---|---:|---:|
| funding_positive | 14.1% | 793 |
| funding_negative | 14.7% | 824 |
| funding_neutral | 71.3% | 4007 |

**Spec balance check (each regime ≥10%):** ✓ all three regimes within
band.

Basis (`perp_close - spot_close`) summary:

- median: -2.87 bps (slight backwardation on Hyperliquid AVAX, perp
  trading marginally below spot)
- range: -90.1 / +34.4 bps

8h-summed funding rate summary:

- median: +0.92 bps (just below the +1 bps positive threshold)
- range: -12.71 / +7.28 bps

## Public/private split methodology

80/20 chronological split. The split point is the 80th-percentile
unique timestamp; all bars with timestamp `<` that go into public,
all bars with timestamp `>=` into private. Keeps the holdout strictly
in the future of the public window — no leakage.

- **Public:** 4499 bars, 2025-08-01T00:00:00 UTC → 2026-02-04T02:00:00 UTC
- **Private:** 1125 bars, 2026-02-04T03:00:00 UTC → 2026-03-22T19:00:00 UTC

The split timestamp is `1770174000` (epoch seconds) =
`2026-02-04T03:00:00 UTC`.

## Merkle roots and storage URI

- `merkle_root` = `0x8476640a3886d5b5e9cba2a2747ac590827323bf9b2d5218be9d207bd0c91e87`
- `public_root` = `0xae798ba3ab6235fd18450e8b3f83a1e0b28ddcb923d30924d8aa63f3ecd53307`
- `private_root` = `0x61cc29d426a9863bf7a5b9c376f73b7a6a7e2f8e74c23d07d500932bd6587c6f`
- `storage_uri` = `mock://dataset/trading_market_neutral/0x8476640a3886d5b5`

Per spec §A.0, the on-disk `storage_uri` is **producer-chosen** and
stays as `mock://...` permanently. The `RealStorageAdapter` resolves
loads by sha256 (`public_root` / `private_root`) directly via the TS
service's in-memory upload index — `commitment.storage_uri` is an
informational label, not a load-bearing pointer to 0G.

PR #8 §D.2 uploads the bytes to live 0G inside a TS-service lifetime
(so `load_dataset_public(commitment)` and `load_dataset_full(...)`
succeed end-to-end), but does not rewrite the JSON.

Roots are computed via
`lockstep.domains.trading.market_neutral.commitment_roots`, matching
`MockStorageAdapter._payload_root` so downloads verify cleanly on
either adapter.

## File sizes and upload cost

- `lockstep/domains/trading/market_neutral/canonical_dataset.json`:
  1465 KB (5624 bars × ~260 bytes/bar)
- Upload cost: ~1.16 m`0G` per upload at the prep-run baseline, ×2
  (public + private halves) = ~2.32 m`0G` for §D.2's retroactive
  upload. Wallet runway at 0.0742 `0G` (after PR #1's prep run)
  covers ~30 such double-uploads — comfortable margin.

## Reproducibility

Running the builder twice on the same input parquets produces
byte-identical `canonical_dataset.json`:

```bash
$ uv run python scripts/datasets/build_market_neutral.py
merkle_root  = 0x8476640a3886d5b5e9cba2a2747ac590827323bf9b2d5218be9d207bd0c91e87
$ uv run python scripts/datasets/build_market_neutral.py
merkle_root  = 0x8476640a3886d5b5e9cba2a2747ac590827323bf9b2d5218be9d207bd0c91e87
$ # diff -q across runs: identical
```

The §B.1 step 7 explicit reproducibility test will gate this in CI.
