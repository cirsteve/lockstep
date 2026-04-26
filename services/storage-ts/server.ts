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

const provider = new ethers.JsonRpcProvider(RPC_URL);
const wallet = new ethers.Wallet(PRIVATE_KEY, provider);
const indexer = new Indexer(INDEXER_URL);

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
      const file = new MemData(bytes);
      const [tx, err] = await indexer.upload(file, RPC_URL, wallet);
      if (err !== null || !('rootHash' in tx)) {
        res.status(502).json({
          error: 'upload_failed',
          detail: err ? String(err.message ?? err) : 'unknown SDK failure',
        });
        return;
      }

      res.json({
        plaintext_commitment: plaintextCommitment,
        bundle_hash: bundleHash,
        storage_uri: `zg://${tx.rootHash}`,
        recipient_pubkey: recipientPubkey,
        encryption_scheme: 'x25519-chacha20poly1305-mock',
        tx_hash: tx.txHash,
        root_hash: tx.rootHash,
        tx_seq: tx.txSeq,
        size_bytes: bytes.length,
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

app.listen(PORT, '127.0.0.1', () => {
  console.log(
    `storage-ts: listening on 127.0.0.1:${PORT} (wallet ${wallet.address})`,
  );
});
