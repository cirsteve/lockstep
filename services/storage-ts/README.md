# storage-ts

Long-lived TypeScript HTTP service that wraps `@0gfoundation/0g-ts-sdk`
so the Python `RealStorageAdapter` can talk to 0G Storage over HTTP.

## Why a separate service?

The published Python SDK (`0g-storage-sdk` on PyPI) is structurally broken —
its wheel ships without `config/` and `utils/` modules and imports fail out
of the box. The TS SDK is the actively maintained one. Running it as a
long-lived service keeps SDK clients warm and avoids the 100–500 ms
per-call cost of subprocess-per-call.

### SDK package

We use `@0gfoundation/0g-ts-sdk@^1.2.6`, *not* the older `@0glabs/0g-ts-sdk`
on npm. The `@0glabs` package is stuck at `0.3.3` and targets the
pre-upgrade Galileo flow contract — its `submit()` selector
(`0xef3e12dc`) reverts on the current beacon-proxied contract, which now
uses the wrapped `Submission { SubmissionData data; address submitter; }`
struct (selector `0xbc8c11f8`). The `@0gfoundation` package is the
re-released SDK that matches the current contract. Pin `ethers@6.13.1`
exactly — both SDK packages declare it as a strict peer.

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
- `POST /upload-encrypted-solution` — bundle bytes in the body
  (`Content-Type: application/octet-stream`), `X-Plaintext-Commitment`
  and `X-Recipient-Pubkey` in headers (both `0x`-prefixed sha256). Returns
  `{ plaintext_commitment, bundle_hash, storage_uri, recipient_pubkey,
  encryption_scheme, tx_hash, root_hash, tx_seq, size_bytes }`. The
  `storage_uri` is `zg://<rootHash>`; `bundle_hash` is sha256 of the
  request body.
- `GET /download-encrypted-solution?uri=zg://<rootHash>` — bundle bytes
  with `Content-Type: application/octet-stream` and `X-Bundle-Hash` /
  `X-Root-Hash` headers. The Python adapter recomputes sha256 against
  the body for defense-in-depth.

Receipt and dataset endpoints land in subsequent commits.

## Security

The service binds to `127.0.0.1` only. It holds the wallet private key in
memory; do not expose it beyond localhost. The README will expand on the
threat model and operator checklist before PR #5 closes.
