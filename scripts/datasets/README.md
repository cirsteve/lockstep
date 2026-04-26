# data/raw/

Raw market-data inputs to the canonical-dataset builders in
`scripts/datasets/`. Gitignored (`data/` is in `.gitignore`); not part
of the repo. Populate locally before running the build scripts.

## Source

Parquet files live in `gecko`'s TA store, which on the Springfield
homelab runs as a Docker volume mounted at `/app/data` inside the `ta`
container on **willie**. The TA `ingest` package writes them in the
layout documented at `~/codes/rol/ta/ingest/README.md`.

The canonical-dataset builders here use:

| File | Used by | Why |
|---|---|---|
| `binance/candles_spot/{BTC,ETH,SOL}_1h.parquet` | `build_directional.py` | 1y hourly OHLCV for the directional dataset |
| `binance/candles_spot/{BTC,ETH}_1h.parquet` | `build_market_neutral.py` | spot prices for basis computation |
| `hyperliquid/funding_rates/{BTC,ETH}.parquet` | `build_market_neutral.py` | funding-rate series |

## Pull from willie

From otto (or any machine with `ssh willie` working):

```bash
mkdir -p data/raw/binance/candles_spot data/raw/hyperliquid/funding_rates

for sym in BTC_1h ETH_1h SOL_1h; do
  ssh willie "docker exec ta cat /app/data/training/binance/candles_spot/${sym}.parquet" \
    > data/raw/binance/candles_spot/${sym}.parquet
done

for sym in BTC ETH; do
  ssh willie "docker exec ta cat /app/data/training/hyperliquid/funding_rates/${sym}.parquet" \
    > data/raw/hyperliquid/funding_rates/${sym}.parquet
done
```

Total transfer is under 2 MB and takes a few seconds.

## Schemas

**Candles** (`binance/candles_spot/*.parquet`):
`timestamp` (int64 ms), `coin` (str), `open`, `high`, `low`, `close`,
`volume` (float64).

**Funding rates** (`hyperliquid/funding_rates/*.parquet`):
`timestamp` (int64 ms), `coin` (str), `funding_rate`, `premium`
(float64). Note: Hyperliquid charges funding **hourly**, not every 8h
like most perps — the build script accounts for this when applying
the spec's funding-regime thresholds.

## Refresh

Re-run the `ssh willie ...` block above whenever you want fresher
data. The build scripts read whatever's on disk and produce a new
`canonical_dataset.json` with new Merkle roots.
