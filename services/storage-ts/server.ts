import * as path from 'path';
import * as crypto from 'crypto';
import * as dotenv from 'dotenv';
import express from 'express';
import { ethers } from 'ethers';
import { Indexer, MemData } from '@0gfoundation/0g-ts-sdk';

// Load .env from the repo root (two levels up from services/storage-ts/).
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const PRIVATE_KEY = process.env.LOCKSTEP_0G_PRIVATE_KEY;
if (!PRIVATE_KEY || PRIVATE_KEY === '0x_replace_with_real_key') {
  console.error(
    'storage-ts: LOCKSTEP_0G_PRIVATE_KEY is not set. ' +
      'Copy .env.example to .env at the repo root and fill in the wallet key.',
  );
  process.exit(1);
}

const RPC_URL =
  process.env.LOCKSTEP_0G_GALILEO_RPC ?? 'https://evmrpc-testnet.0g.ai';
const INDEXER_URL =
  process.env.LOCKSTEP_0G_GALILEO_INDEXER ??
  'https://indexer-storage-testnet-turbo.0g.ai';
const PORT = Number(process.env.LOCKSTEP_0G_STORAGE_PORT ?? '7878');
// Default bind: 127.0.0.1 (host-only). Containerized deployments
// override to 0.0.0.0 so Docker port mapping reaches the listener;
// the operator is then responsible for using `-p 127.0.0.1:7878:7878`
// (NOT bare `-p 7878:7878`, which exposes the wallet) — see README.md.
const BIND_HOST =
  process.env.LOCKSTEP_0G_STORAGE_BIND_HOST ?? '127.0.0.1';

const provider = new ethers.JsonRpcProvider(RPC_URL);
const wallet = new ethers.Wallet(PRIVATE_KEY, provider);
const indexer = new Indexer(INDEXER_URL);

// In-memory state. Resets on restart; 0G storage persists the bytes
// themselves but the sha256 -> 0G-rootHash mapping does not. Hackathon
// scope is single-process (conformance, demo); persistence across
// restarts is a Day 5+ concern.
const authorizedAttestations = new Set<string>();

// Idempotency index for every POST that pays for a 0G upload. Keyed by
// sha256 of the bytes uploaded; each entry caches the SDK result so
// retries (Python adapter retries on 5xx) reuse the cached upload
// instead of double-paying. See `getOrUpload` below.
type UploadIndexEntry = {
  zgRoot: string;
  txHash: string;
  txSeq: number;
  size: number;
};
const uploadIndex = new Map<string, UploadIndexEntry>();

function sha256Hex(bytes: Uint8Array | Buffer): string {
  return '0x' + crypto.createHash('sha256').update(bytes).digest('hex');
}

const ROOT_HASH_RE = /^0x[0-9a-fA-F]{64}$/;

function parseZgUri(uri: string): string {
  const prefix = 'zg://';
  if (!uri.startsWith(prefix)) {
    throw new Error(`expected zg:// uri, got ${uri}`);
  }
  const root = uri.slice(prefix.length);
  if (!ROOT_HASH_RE.test(root)) {
    throw new Error(`invalid root hash in uri: ${uri}`);
  }
  return root;
}

// Idempotent upload. If `contentHash` (sha256 of the bytes) is already
// in `uploadIndex`, returns the cached entry without paying. Otherwise
// uploads via the SDK and caches the result. This is what makes the
// service safe under the Python adapter's retry-on-5xx semantics:
// `POST /upload-dataset` can fail mid-flight (public paid, private
// 502), but a retry will short-circuit the public side via the cache
// rather than re-paying.
async function getOrUpload(
  bytes: Buffer,
  contentHash: string,
): Promise<[UploadIndexEntry, null] | [null, Error]> {
  const cached = uploadIndex.get(contentHash);
  if (cached) {
    return [cached, null];
  }
  const [tx, err] = await indexer.upload(new MemData(bytes), RPC_URL, wallet);
  if (err !== null || !('rootHash' in tx)) {
    return [null, err ?? new Error('unknown SDK upload failure')];
  }
  const entry: UploadIndexEntry = {
    zgRoot: tx.rootHash,
    txHash: tx.txHash,
    txSeq: tx.txSeq,
    size: bytes.length,
  };
  uploadIndex.set(contentHash, entry);
  return [entry, null];
}

async function downloadBytes(
  rootHash: string,
): Promise<[Buffer, null] | [null, Error]> {
  const [blob, err] = await indexer.downloadToBlob(rootHash);
  if (err !== null) {
    return [null, err];
  }
  return [Buffer.from(await blob.arrayBuffer()), null];
}

async function probeIndexer(url: string, timeoutMs = 5000): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { method: 'HEAD', signal: ctrl.signal });
    return Number.isInteger(res.status);
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function getBalanceEther(): Promise<string | null> {
  try {
    const wei = await provider.getBalance(wallet.address);
    return ethers.formatEther(wei);
  } catch {
    return null;
  }
}

const app = express();

app.get('/healthz', async (_req, res) => {
  const [indexerReachable, balance] = await Promise.all([
    probeIndexer(INDEXER_URL),
    getBalanceEther(),
  ]);
  res.json({
    ok: true,
    wallet_address: wallet.address,
    rpc_url: RPC_URL,
    indexer_url: INDEXER_URL,
    indexer_reachable: indexerReachable,
    balance_0g: balance,
  });
});

// Upload an already-encrypted bundle to 0G storage. The Python adapter
// computes plaintext_commitment from the cleartext before encrypting,
// then passes both the ciphertext bytes (request body) and the two
// 0x-prefixed hex commitments via headers. We don't inspect the bundle
// — bundle_hash is sha256 of the received bytes, storage_uri is
// zg://<rootHash> from the SDK's upload result.
app.post(
  '/upload-encrypted-solution',
  express.raw({ type: '*/*', limit: '64mb' }),
  async (req, res) => {
    try {
      const bytes = req.body as Buffer;
      if (!Buffer.isBuffer(bytes) || bytes.length === 0) {
        res.status(400).json({
          error: 'empty_body',
          detail:
            'POST body required (Content-Type: application/octet-stream)',
        });
        return;
      }
      const plaintextCommitment = req.header('x-plaintext-commitment');
      const recipientPubkey = req.header('x-recipient-pubkey');
      if (!plaintextCommitment || !recipientPubkey) {
        res.status(400).json({
          error: 'missing_header',
          detail:
            'X-Plaintext-Commitment and X-Recipient-Pubkey headers required',
        });
        return;
      }

      const bundleHash = sha256Hex(bytes);
      const [entry, err] = await getOrUpload(bytes, bundleHash);
      if (err !== null) {
        res.status(502).json({
          error: 'upload_failed',
          detail: String(err.message ?? err),
        });
        return;
      }

      res.json({
        plaintext_commitment: plaintextCommitment,
        bundle_hash: bundleHash,
        storage_uri: `zg://${entry.zgRoot}`,
        recipient_pubkey: recipientPubkey,
        encryption_scheme: 'x25519-chacha20poly1305-mock',
        tx_hash: entry.txHash,
        root_hash: entry.zgRoot,
        tx_seq: entry.txSeq,
        size_bytes: entry.size,
      });
    } catch (e: unknown) {
      res.status(500).json({
        error: 'internal',
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  },
);

// Download bundle bytes given a zg://<rootHash> URI. We use the SDK's
// downloadToBlob (in-memory) so no tmp file is needed. Bundle integrity
// (sha256) is the Python adapter's responsibility — we relay an
// X-Bundle-Hash header for convenience but the adapter recomputes from
// the body bytes.
app.get('/download-encrypted-solution', async (req, res) => {
  try {
    const uri = String(req.query.uri ?? '');
    let rootHash: string;
    try {
      rootHash = parseZgUri(uri);
    } catch (e) {
      res.status(400).json({
        error: 'invalid_uri',
        detail: e instanceof Error ? e.message : String(e),
      });
      return;
    }

    const [blob, err] = await indexer.downloadToBlob(rootHash);
    if (err !== null) {
      res.status(502).json({
        error: 'download_failed',
        detail: String(err.message ?? err),
      });
      return;
    }
    const bytes = Buffer.from(await blob.arrayBuffer());
    res.setHeader('Content-Type', 'application/octet-stream');
    res.setHeader('X-Bundle-Hash', sha256Hex(bytes));
    res.setHeader('X-Root-Hash', rootHash);
    res.send(bytes);
  } catch (e: unknown) {
    res.status(500).json({
      error: 'internal',
      detail: e instanceof Error ? e.message : String(e),
    });
  }
});

// Upload an already-serialized receipt blob (canonical JSON bytes from
// the Python adapter; service is opaque to the body shape). Returns the
// zg URI plus a sha256 of the body for the adapter's defense-in-depth
// recheck.
app.post(
  '/upload-receipt',
  express.raw({ type: '*/*', limit: '8mb' }),
  async (req, res) => {
    try {
      const bytes = req.body as Buffer;
      if (!Buffer.isBuffer(bytes) || bytes.length === 0) {
        res
          .status(400)
          .json({ error: 'empty_body', detail: 'receipt body required' });
        return;
      }
      const contentHash = sha256Hex(bytes);
      const [entry, err] = await getOrUpload(bytes, contentHash);
      if (err !== null) {
        res
          .status(502)
          .json({ error: 'upload_failed', detail: String(err.message ?? err) });
        return;
      }
      res.json({
        uri: `zg://${entry.zgRoot}`,
        root_hash: entry.zgRoot,
        tx_hash: entry.txHash,
        tx_seq: entry.txSeq,
        content_hash: contentHash,
        size_bytes: entry.size,
      });
    } catch (e: unknown) {
      res.status(500).json({
        error: 'internal',
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  },
);

app.get('/download-receipt', async (req, res) => {
  try {
    const uri = String(req.query.uri ?? '');
    let rootHash: string;
    try {
      rootHash = parseZgUri(uri);
    } catch (e) {
      res.status(400).json({
        error: 'invalid_uri',
        detail: e instanceof Error ? e.message : String(e),
      });
      return;
    }
    const [bytes, err] = await downloadBytes(rootHash);
    if (err !== null) {
      res.status(502).json({
        error: 'download_failed',
        detail: String(err.message ?? err),
      });
      return;
    }
    res.setHeader('Content-Type', 'application/octet-stream');
    res.setHeader('X-Content-Hash', sha256Hex(bytes));
    res.setHeader('X-Root-Hash', rootHash);
    res.send(bytes);
  } catch (e: unknown) {
    res.status(500).json({
      error: 'internal',
      detail: e instanceof Error ? e.message : String(e),
    });
  }
});

// Upload a dataset's public + private payloads in a single atomic call.
// Body carries both halves base64-encoded plus their sha256 commitments;
// the service rejects 422 on either commitment mismatch (defense in
// depth — Python adapter must also have computed the same roots from
// commitment_roots()). Records both sha256 -> zgRoot mappings so
// /load-dataset-{public,full} can find them later.
app.post(
  '/upload-dataset',
  express.json({ limit: '128mb' }),
  async (req, res) => {
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const publicRoot = String(body.public_root ?? '').toLowerCase();
      const privateRoot = String(body.private_root ?? '').toLowerCase();
      const publicB64 = body.public_b64;
      const privateB64 = body.private_b64;
      if (!ROOT_HASH_RE.test(publicRoot) || !ROOT_HASH_RE.test(privateRoot)) {
        res.status(400).json({
          error: 'invalid_root',
          detail: 'public_root and private_root must be 0x-prefixed 64-hex',
        });
        return;
      }
      if (typeof publicB64 !== 'string' || typeof privateB64 !== 'string') {
        res.status(400).json({
          error: 'missing_payload',
          detail: 'public_b64 and private_b64 (base64 strings) required',
        });
        return;
      }
      const publicBytes = Buffer.from(publicB64, 'base64');
      const privateBytes = Buffer.from(privateB64, 'base64');
      if (sha256Hex(publicBytes) !== publicRoot) {
        res.status(422).json({
          error: 'public_root_mismatch',
          detail: 'sha256 of public_b64 does not match public_root',
        });
        return;
      }
      if (sha256Hex(privateBytes) !== privateRoot) {
        res.status(422).json({
          error: 'private_root_mismatch',
          detail: 'sha256 of private_b64 does not match private_root',
        });
        return;
      }
      // Upload each side via getOrUpload so a retry after a partial
      // failure (public paid, private 502) doesn't re-pay for public.
      const [pubEntry, pubErr] = await getOrUpload(publicBytes, publicRoot);
      if (pubErr !== null) {
        res.status(502).json({
          error: 'upload_failed',
          detail: `public side: ${String(pubErr.message ?? pubErr)}`,
        });
        return;
      }
      const [privEntry, privErr] = await getOrUpload(privateBytes, privateRoot);
      if (privErr !== null) {
        res.status(502).json({
          error: 'upload_failed',
          detail: `private side: ${String(privErr.message ?? privErr)}`,
        });
        return;
      }
      res.json({
        public_storage_uri: `zg://${pubEntry.zgRoot}`,
        private_storage_uri: `zg://${privEntry.zgRoot}`,
        public_root_hash: pubEntry.zgRoot,
        private_root_hash: privEntry.zgRoot,
        public_tx_hash: pubEntry.txHash,
        private_tx_hash: privEntry.txHash,
        public_tx_seq: pubEntry.txSeq,
        private_tx_seq: privEntry.txSeq,
        public_size_bytes: pubEntry.size,
        private_size_bytes: privEntry.size,
      });
    } catch (e: unknown) {
      res.status(500).json({
        error: 'internal',
        detail: e instanceof Error ? e.message : String(e),
      });
    }
  },
);

app.get('/load-dataset-public', async (req, res) => {
  try {
    const publicRoot = String(req.query.public_root ?? '').toLowerCase();
    if (!ROOT_HASH_RE.test(publicRoot)) {
      res.status(400).json({
        error: 'invalid_root',
        detail: 'public_root must be 0x-prefixed 64-hex',
      });
      return;
    }
    const entry = uploadIndex.get(publicRoot);
    if (!entry) {
      res.status(404).json({
        error: 'not_in_index',
        detail:
          'public_root has no upload record on this service (different uploader, or service restarted)',
      });
      return;
    }
    const [bytes, err] = await downloadBytes(entry.zgRoot);
    if (err !== null) {
      res.status(502).json({
        error: 'download_failed',
        detail: String(err.message ?? err),
      });
      return;
    }
    if (sha256Hex(bytes) !== publicRoot) {
      res.status(422).json({
        error: 'public_root_mismatch',
        detail: 'downloaded bytes sha256 does not match public_root',
      });
      return;
    }
    res.setHeader('Content-Type', 'application/octet-stream');
    res.setHeader('X-Public-Root', publicRoot);
    res.send(bytes);
  } catch (e: unknown) {
    res.status(500).json({
      error: 'internal',
      detail: e instanceof Error ? e.message : String(e),
    });
  }
});

// Returns public + private bytes concatenated (mirrors MockStorageAdapter
// .load_dataset_full). Gated on the in-memory authorized-attestations set.
app.get('/load-dataset-full', async (req, res) => {
  try {
    const publicRoot = String(req.query.public_root ?? '').toLowerCase();
    const privateRoot = String(req.query.private_root ?? '').toLowerCase();
    const attestationPubkey = String(
      req.query.attestation_pubkey ?? '',
    ).toLowerCase();
    if (!ROOT_HASH_RE.test(publicRoot)) {
      res.status(400).json({
        error: 'invalid_root',
        detail: 'public_root must be 0x-prefixed 64-hex',
      });
      return;
    }
    if (!ROOT_HASH_RE.test(privateRoot)) {
      res.status(400).json({
        error: 'invalid_root',
        detail: 'private_root must be 0x-prefixed 64-hex',
      });
      return;
    }
    if (!ROOT_HASH_RE.test(attestationPubkey)) {
      res.status(400).json({
        error: 'invalid_pubkey',
        detail: 'attestation_pubkey must be 0x-prefixed 64-hex',
      });
      return;
    }
    if (!authorizedAttestations.has(attestationPubkey)) {
      res.status(422).json({
        error: 'attestation_not_authorized',
        detail: `attestation pubkey ${attestationPubkey} not in authorized set`,
      });
      return;
    }
    const pubEntry = uploadIndex.get(publicRoot);
    const privEntry = uploadIndex.get(privateRoot);
    if (!pubEntry || !privEntry) {
      res.status(404).json({
        error: 'not_in_index',
        detail:
          'public_root or private_root has no upload record on this service',
      });
      return;
    }
    const [[pubBytes, pubErr], [privBytes, privErr]] = await Promise.all([
      downloadBytes(pubEntry.zgRoot),
      downloadBytes(privEntry.zgRoot),
    ]);
    if (pubErr !== null) {
      res.status(502).json({
        error: 'download_failed',
        detail: `public side: ${String(pubErr.message ?? pubErr)}`,
      });
      return;
    }
    if (privErr !== null) {
      res.status(502).json({
        error: 'download_failed',
        detail: `private side: ${String(privErr.message ?? privErr)}`,
      });
      return;
    }
    if (sha256Hex(pubBytes) !== publicRoot) {
      res.status(422).json({
        error: 'public_root_mismatch',
        detail: 'downloaded public bytes sha256 does not match public_root',
      });
      return;
    }
    if (sha256Hex(privBytes) !== privateRoot) {
      res.status(422).json({
        error: 'private_root_mismatch',
        detail: 'downloaded private bytes sha256 does not match private_root',
      });
      return;
    }
    res.setHeader('Content-Type', 'application/octet-stream');
    res.setHeader('X-Public-Root', publicRoot);
    res.setHeader('X-Private-Root', privateRoot);
    res.send(Buffer.concat([pubBytes, privBytes]));
  } catch (e: unknown) {
    res.status(500).json({
      error: 'internal',
      detail: e instanceof Error ? e.message : String(e),
    });
  }
});

// Test scaffolding: register an attestation pubkey as authorized for
// /load-dataset-full reads. Mirrors MockStorageAdapter.authorize_attestation.
// Removed once chain-side ERC-7857 oracle re-encryption lands (Day 5+).
app.post(
  '/authorize-attestation',
  express.json({ limit: '4kb' }),
  async (req, res) => {
    const pubkey = String((req.body ?? {}).pubkey ?? '').toLowerCase();
    if (!ROOT_HASH_RE.test(pubkey)) {
      res.status(400).json({
        error: 'invalid_pubkey',
        detail: 'pubkey must be 0x-prefixed 64-hex',
      });
      return;
    }
    authorizedAttestations.add(pubkey);
    res.status(204).send();
  },
);

app.listen(PORT, BIND_HOST, () => {
  console.log(
    `storage-ts: listening on ${BIND_HOST}:${PORT} (wallet ${wallet.address})`,
  );
});
