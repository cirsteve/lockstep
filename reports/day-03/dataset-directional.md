# Day 3 §3.1 — Directional canonical dataset

## Source window and asset list

| Asset | Source parquet | Raw rows | Labeled rows |
|---|---|---:|---:|
| BTC | `data/raw/binance/candles_spot/BTC_1h.parquet` | 8760 | 8040 |
| ETH | `data/raw/binance/candles_spot/ETH_1h.parquet` | 8760 | 8040 |
| SOL | `data/raw/binance/candles_spot/SOL_1h.parquet` | 8760 | 8040 |

**Total bars:** 24120 (3 assets × ~8040 hours each)

## Cleaning decisions and gap handling

- **Warmup trim:** dropped the first 30 days (720 hours) per asset where the rolling 30-day stats are NaN. Cleaner than carrying a sentinel regime label into downstream grading.
- **Gap handling:** the script's `_load_one_asset` recomputes `timestamp.diff()` and counts any value that isn't exactly 3,600,000 ms (1 hour). All three Binance parquets reported zero gaps over the source window — no forward-fill needed.
- **Timestamp normalization:** parquet timestamps are epoch ms; the canonical dataset uses epoch seconds (per the `DirectionalDataset` schema in `domains/trading/directional/dataset.py`).

## Final regime thresholds and distribution

Methodology (per spec §3.1 step 4):

- **bull:** rolling 30d return > 10% AND rolling 30d vol < its median
- **bear:** rolling 30d return < -10% OR drawdown > 15% from rolling 30d peak
- **vol_spike:** SHORT (7-day) rolling vol > 1.4× the median of the same short series
- **chop:** everything else

Two separate volatility series are computed per asset:

1. **30-day rolling vol** (`rolling_30d_vol` in the script) — used only for the bull-filter check (must be below its median).
2. **7-day rolling vol** (`short_vol`) — used only for vol_spike detection (must exceed 1.4× the median of the SHORT series, not the 30d one).

Decoupling them keeps a slow multi-day pump from dragging the spike threshold up past where actual flash events can clear it. Both are rolling std of hourly log-returns; not annualized because each comparison is against its own series's median, so units cancel.

Distribution across the full labeled window:

| Regime | Fraction | Bars |
|---|---:|---:|
| bull | 14.9% | 3601 |
| bear | 26.7% | 6450 |
| chop | 51.6% | 12443 |
| vol_spike | 6.7% | 1626 |

**Spec balance check (each regime ≥5%, no regime >60%):** ✓ all regimes within band.

## Public/private split methodology

80/20 chronological split. The split point is chosen at the 80th-percentile unique timestamp; all bars with timestamp `<` that go into public, all bars with timestamp `>=` into private. Keeps the holdout strictly in the future of the public window — no leakage.

- **Public:** 19296 bars, 2025-04-24T00:00:00+00:00 → 2026-01-16T23:00:00+00:00
- **Private:** 4824 bars, 2026-01-17T00:00:00+00:00 → 2026-03-24T23:00:00+00:00

## Merkle roots and storage URI

- `merkle_root` = `0xc36aa06fc6c01a1439caa63ff60f86e1c57d3a44c66e4a149f0ec9c33ddbe46d`
- `public_root` = `0xa8471ce45b1579cb7d703587ed4f5406d8a7559fce518efce8463c6b6bd3e1e5`
- `private_root` = `0x9a797c6a06cffcde4a3a8c707fb658617d5d1c374501a145a8a1ba6547efe11d`
- `storage_uri` = `mock://dataset/trading_directional/0xc36aa06fc6c01a14` (placeholder; PR 4 uploads to 0G Galileo and rewrites)

Roots are computed via `lockstep.domains.trading.directional.commitment_roots`, which matches `MockStorageAdapter._payload_root` so downloads verify cleanly on either adapter.

## File sizes and upload cost

- `lockstep/domains/trading/directional/canonical_dataset.json`: 4914 KB
- Upload cost: **deferred to PR 4 (Day 4)** — today the storage URI is `mock://`, so no testnet tokens spent.

