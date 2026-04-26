# storage-ts

Long-lived TypeScript HTTP service that wraps `@0glabs/0g-ts-sdk` so the
Python `RealStorageAdapter` can talk to 0G Storage over HTTP.

## Why a separate service?

The published Python SDK (`0g-storage-sdk` on PyPI) is structurally broken —
its wheel ships without `config/` and `utils/` modules and imports fail out
of the box. The TS SDK is the actively maintained one. Running it as a
long-lived service keeps SDK clients warm and avoids the 100–500 ms
per-call cost of subprocess-per-call.

## Run

```bash
cd services/storage-ts
npm install
npm run dev    # starts on 127.0.0.1:7878
```

Reads `.env` from the repo root (`../../.env`).

## Required env vars

- `LOCKSTEP_0G_PRIVATE_KEY` — hex private key for the Galileo dev wallet.
  Required at boot; the service refuses to start without it.
- `LOCKSTEP_0G_GALILEO_RPC` (default `https://evmrpc-testnet.0g.ai`)
- `LOCKSTEP_0G_GALILEO_INDEXER`
  (default `https://indexer-storage-testnet-turbo.0g.ai`)
- `LOCKSTEP_0G_STORAGE_PORT` (default `7878`)

## Endpoints (current)

- `GET /healthz` — `{ ok, wallet_address, rpc_url, indexer_url,
  indexer_reachable, balance_0g }`. The balance comes from a live RPC
  call against `LOCKSTEP_0G_GALILEO_RPC`; the indexer reachability is a
  HEAD probe against `LOCKSTEP_0G_GALILEO_INDEXER`.

Upload / download / receipt / dataset endpoints land in subsequent commits.

## Security

The service binds to `127.0.0.1` only. It holds the wallet private key in
memory; do not expose it beyond localhost. The README will expand on the
threat model and operator checklist before PR #5 closes.
