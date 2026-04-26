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

## Error contract

Every error returns `{ "error": "<code>", "detail": "<human description>" }`
with one of the codes below. The Python adapter switches on the status
code and the `error` code; `detail` is for humans only.

| Status | Meaning                                                                | `error` codes                                                                                          | Python mapping       |
| ------ | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ | -------------------- |
| 400    | Client bug — malformed input. Don't retry; fix the call site.          | `empty_body`, `missing_header`, `missing_payload`, `invalid_root`, `invalid_pubkey`, `invalid_uri`     | hard fail (no retry) |
| 404    | Lookup miss in the per-process index (the bytes may exist on 0G but this service can't find them; service didn't upload it, or it restarted) | `not_in_index`                                                                                         | `SubstrateError`     |
| 422    | Trust violation. Producer claim contradicts the bytes, or attestation pubkey isn't authorized. **Never retry** — this is byzantine evidence. | `public_root_mismatch`, `private_root_mismatch`, `attestation_not_authorized`                          | `TrustViolation`     |
| 500    | Unexpected internal exception (catch-all). Treat as transient.         | `internal`                                                                                             | `SubstrateError`     |
| 502    | Upstream 0G call (SDK / indexer / RPC) failed. Transient; retry per `_with_retry`. | `upload_failed`, `download_failed`                                                                     | `SubstrateError`     |

`upload_failed` and `download_failed` carry side information in `detail`
for the dataset endpoints (e.g. `"public side: ..."` / `"private side:
..."`). The adapter doesn't branch on side — the side is for human
debugging.

The endpoints' integrity-check semantics are also enforced **server-side
before paying for an upload**: `POST /upload-dataset` recomputes
sha256 of the decoded base64 payload and compares to the claimed root.
On mismatch the service returns 422 and never calls the SDK upload
path. This means a wrong-root payload costs nothing in `0G`.

The Python adapter performs the **same sha256 check on download**, even
on HTTP 200 with a matching `X-Bundle-Hash` / `X-Content-Hash` /
`X-Public-Root` / `X-Private-Root` header. The header is convenience;
the integrity check uses the body bytes. The service is partially
trusted (it holds the wallet key but not the trust assumptions of the
substrate); double-checking on the consumer side is cheap and forecloses
a class of bugs.

## Security

The service binds to `127.0.0.1` by default (override via the
`LOCKSTEP_0G_STORAGE_BIND_HOST` env var; see "Containerized" below for
the only intended override). **Never** expose it beyond localhost —
the security model below relies on this.

### Trust boundary

The service holds `LOCKSTEP_0G_PRIVATE_KEY` in process memory and signs
every chain transaction (every upload to 0G storage involves a flow
contract `submit()` that pays the storage market fee). Anyone who can
make HTTP requests to the service can:

- Spend the wallet's `0G` balance, by uploading any payload they choose.
- Authorize any attestation pubkey for `/load-dataset-full` reads, by
  POSTing it to `/authorize-attestation`.
- Read any dataset the service has uploaded in this lifetime, by
  querying its `public_root` / `private_root` against
  `/load-dataset-public` (no auth).

The service does **not** authenticate callers. There's no API key, no
mTLS, no IP allowlist beyond the `127.0.0.1` bind. The trust boundary
is therefore "anyone with a TCP socket to the loopback interface" —
which on a single-user dev box is the local user.

### Operator checklist

- [ ] Service binds to `127.0.0.1:7878`. Verify with `ss -tln | grep 7878`
      after boot — port should show as `127.0.0.1:7878`, never
      `0.0.0.0:7878` or `*:7878`.
- [ ] No reverse proxy / nginx / Traefik / Cloudflare tunnel forwards
      port 7878 to the public internet, a Tailscale tailnet, an
      organization VPN, or any other non-loopback interface. The
      service is a localhost-only dev tool, not a network service.
- [ ] No `docker run -p 0.0.0.0:7878:7878` (or the implicit `-p
      7878:7878` form, which binds to `0.0.0.0` by default). If
      containerizing, use `-p 127.0.0.1:7878:7878` explicitly — see
      "Containerized" below for why this is critical.
- [ ] Faucet wallet has minimum-viable `0G` balance — a couple `0G` is
      plenty (~1.16 m`0G` per upload at the 2026-04-26 baseline). No
      reason to keep larger balances on the service's wallet, even
      though it's testnet — limits blast radius if the bind misconfig
      ever does happen.
- [ ] `.env` at the repo root is mode `600` and the wallet key is not
      duplicated into shell history or any committed file.

### What's NOT in the trust boundary

- The service does not validate Python adapter input beyond shape
  (`0x`-prefixed 64-hex, base64-decodable, etc.). It doesn't cross-check
  payloads against any chain commitment — `commitment.storage_uri` and
  `merkle_root` from `DatasetCommitment` are entirely the producer's
  responsibility.
- The service does not retry SDK calls — it returns 502 fast. The
  Python adapter's `_with_retry` owns the retry budget so the wall-clock
  contract is single-sided.
- The service does not persist state to disk. Restart loses the
  `datasetMap` and `authorizedAttestations` (per §A.0; see "State"
  above).

### Containerized

The Dockerfile sets `LOCKSTEP_0G_STORAGE_BIND_HOST=0.0.0.0` because
Docker's default port mapping forwards from the host to the
container's `eth0` interface, not its loopback. Binding `127.0.0.1`
inside the container would make the service unreachable from the host.

This means the *host-side* port mapping is what enforces localhost-only
access. **Always** run the container with
`-p 127.0.0.1:7878:7878` — the Docker default `-p 7878:7878` is
shorthand for `-p 0.0.0.0:7878:7878` and exposes the wallet to anyone
who can reach the host's network interfaces.

Recommended invocation:

```bash
docker build -t lockstep-storage-ts services/storage-ts
docker run --rm \
  -p 127.0.0.1:7878:7878 \
  --env-file .env \
  lockstep-storage-ts
```

To preserve the in-process `127.0.0.1` bind instead, use
`--network host` (Linux only) and unset
`LOCKSTEP_0G_STORAGE_BIND_HOST` so the default applies. The trade-off:
host-network containers see all the host's interfaces, which can be
surprising — port mapping is the more portable safety net.
