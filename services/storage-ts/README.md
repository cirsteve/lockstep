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
- `POST /upload-receipt` — opaque receipt bytes in the body
  (`Content-Type: application/octet-stream`; the Python adapter sends
  pydantic-serialized canonical JSON). Returns `{ uri, root_hash,
  tx_hash, tx_seq, content_hash, size_bytes }`.
- `GET /download-receipt?uri=zg://<rootHash>` — receipt bytes with
  `X-Content-Hash` / `X-Root-Hash` headers.
- `POST /upload-dataset` — JSON body
  `{ public_root, private_root, public_b64, private_b64 }`. Each
  payload's sha256 must match its claimed root or the service rejects
  with 422 (defense-in-depth before paying for the upload). Returns
  `{ public_storage_uri, private_storage_uri, public_root_hash,
  private_root_hash, public_tx_hash, private_tx_hash, public_tx_seq,
  private_tx_seq, public_size_bytes, private_size_bytes }`.
- `GET /load-dataset-public?public_root=<sha256>` — public bytes with
  `X-Public-Root` header. 404 if the service has no upload record for
  that root (the in-memory `sha256 → 0G-rootHash` index is per-process;
  see "State" below). 422 if the downloaded bytes' sha256 doesn't
  match the claimed root.
- `GET /load-dataset-full?public_root=<sha256>&private_root=<sha256>&attestation_pubkey=<sha256>`
  — public + private bytes concatenated. 422 if `attestation_pubkey` is
  not in the in-memory authorized set; same 404/422 rules as the public
  endpoint apply per side.
- `POST /authorize-attestation` — JSON `{ pubkey: "0x<sha256>" }`. 204
  on success. Test scaffolding only; mirrors
  `MockStorageAdapter.authorize_attestation` and goes away once the
  ERC-7857 oracle re-encryption flow lands chain-side.

## State

Two in-memory structures are scoped to the service process:

- `authorizedAttestations: Set<string>` — populated by
  `POST /authorize-attestation`, consulted by `GET /load-dataset-full`.
- `datasetMap: Map<sha256, { 0G-rootHash, size }>` — populated by
  `POST /upload-dataset`, consulted by `GET /load-dataset-{public,full}`.

Both reset on restart. The bytes themselves persist on 0G storage —
only the `sha256 → 0G-rootHash` resolution is lost. Conformance and
demo flows run inside a single service lifetime, so this is fine for
the hackathon scope; persistence to disk is a Day 5+ concern.

## Security

The service binds to `127.0.0.1` only. It holds the wallet private key in
memory; do not expose it beyond localhost. The README will expand on the
threat model and operator checklist before PR #5 closes.
