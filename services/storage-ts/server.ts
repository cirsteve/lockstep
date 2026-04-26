import * as path from 'path';
import * as dotenv from 'dotenv';
import express from 'express';
import { ethers } from 'ethers';

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
app.use(express.json({ limit: '50mb' }));

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

app.listen(PORT, '127.0.0.1', () => {
  console.log(
    `storage-ts: listening on 127.0.0.1:${PORT} (wallet ${wallet.address})`,
  );
});
