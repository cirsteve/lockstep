# Day 3 Closing Status

**Date:** 2026-04-25
**Spec:** `spec/smoketests_0G_storage_canonical_data.md` (gitignored — local working doc)
**PRs opened:** #2 (storage-real, merged), #3 (datasets, merged), #4 (status, this PR)

This doc is the input to the Day 4 spec drafter. Concrete and recommendation-oriented; no hand-waving.

---

## What shipped

### PR #2 — Track B (substrate storage), 5 commits
- **`lockstep/errors.py`** — `SubstrateError` base + `TrustViolation` subclass; reparented all six adapter errors (`StorageError`, `ChainError`, `AttestationError`, `EncryptionError`, `PaymentError`, `TransportError`) so callers can catch broadly or narrowly.
- **`RealStorageAdapter` skeleton** — constructable shell satisfying the Day 2 Protocol; method bodies raise `NotImplementedError("Day 4")`. Helpers landed and unit-tested in isolation: `_RetryBudget` (exponential backoff: 500 ms base, 4 s cap, max 4 attempts, 30 s aggregate wall-clock), `_with_retry` (retries `SubstrateError`, propagates `TrustViolation` immediately), `_log_event` (per-instance JSONL writer), `_CostTracker` (Decimal accumulator with hard budget cap).
- **Config-driven adapter selection** — `lockstep/substrate/factory.py` + `config/local.yaml` (mock) + `config/galileo.yaml` (real, env-var-overridable URLs). `examples/demo_flow.py` refactored to `--config <path>` while preserving the no-args integration-test contract.
- **Six-test conformance suite** — `tests/substrate/test_storage_conformance.py` parameterized over Mock and Real; Real branch skips when `LOCKSTEP_TEST_REAL_STORAGE` is unset. Made all six pass on Mock by adding `EnclaveAttestation.verify_signature(payload)` and tightening `MockStorageAdapter` to raise `TrustViolation` (not `StorageError`) on byzantine conditions + verify bundle-hash and receipt-signature on download.

**Test counts:** 86 (Day 2 baseline) → **122 passed, 6 skipped** (118 from the §2.1–§2.4 work; +4 added by the late `d9f15d0` commit that addressed Copilot's retry-deadline review and added `_RetryBudget` validation tests). The 6 skips are the Real-adapter conformance branch awaiting Day 4 wiring + creds.

### PR #3 — Track C (datasets), 2 commits
- **Setup** — `pandas` + `pyarrow` runtime deps; `/data/` + `/logs/` + `uv.lock` gitignored; `scripts/datasets/README.md` documenting the ssh-cat pipeline that pulls 5 parquet files (under 2 MB total) from gecko's TA Docker volume on willie into `data/raw/`.
- **§3.1 directional dataset** — `scripts/datasets/build_directional.py` produces a reproducible 24,120-bar canonical dataset for BTC/ETH/SOL hourly OHLCV (2025-03-25 → 2026-03-24, 80/20 chronological split, regime distribution `bull 14.9% / bear 26.7% / chop 51.6% / vol_spike 6.7%`). Persisted to `lockstep/domains/trading/directional/canonical_dataset.json`, methodology in `reports/day-03/dataset-directional.md`. Same input → identical Merkle root verified.

### Architectural decisions logged
- **0G Storage adapter uses a long-lived TS HTTP service** (option B), not subprocess-per-call (A) and not auto-spawned-over-unix-socket (D). Rationale: the published Python SDK (`0g-storage-sdk` v0.3.0) is structurally incomplete on PyPI — the wheel is missing `config/` and `utils/` modules entirely, so its imports fail out of the box. The TS SDK (`@0glabs/0g-ts-sdk`) is the actively maintained one. Service architecture beats subprocess for per-call latency and SDK init reuse. Decided with @cirsteve; saved to project memory.
- **Spec network target flipped** from Newton (chain 16600) to **Galileo (chain 16602, native token `0G`)**. Newton is implicitly deprecated per `docs.0g.ai/developer-hub/testnet/testnet-overview` ("remove any old 0G testnet configurations before adding Galileo"). Spec edits, env-var names, and `config/galileo.yaml` all updated.
- **Spec §3.1 Merkle algorithm corrected.** Initial spec edit specified a row-level Merkle tree; that conflicted with Day 2's existing `commitment_roots()` helper (`sha256(canonical_json_bytes(rows))`) and the adapter contract. Corrected to use the existing helper; row-level trees can come back as a separate `DatasetCommitment` field if Day 4+ needs sampling proofs.
- **All clock-time references stripped** from the spec (was: 4pm, midday, morning/afternoon). Replaced with phase markers (`Phase 1`, `Checkpoint`, `Phase 2`, `Closing`) and genuine durations (e.g., 30-min smoke-test debugging timeboxes).

### Adapters: real vs Mock

| Adapter | Status |
|---|---|
| storage | **Real skeleton landed**; method bodies Day 4 |
| chain | Mock |
| attestation | Mock |
| encryption | Mock |
| payment | Mock |
| transport | Mock |

---

## What didn't ship

| Section | Status | Why |
|---|---|---|
| §1.1 0G Galileo smoke test (iNFT contract deploy + mint + transfer) | **Deferred to Day 4** | No wallet creds today; agreed at session start to write smoke scripts with placeholder env-var reads only when credentials land |
| §1.2 0G Compute grader latency | **Deferred to Day 4** | Same — no creds, no live exercise possible |
| §1.3 KeeperHub MCP smoke + feedback doc | **Deferred to Day 4** | Same — no creds |
| §1.4 AXL multi-machine peering smoke | **Deferred to Day 4** | Needs both machines accessible + AXL binaries; haven't scoped |
| §1.5 Smoke summary | **Deferred to Day 4** | Synthesizes 1.1–1.4 |
| §2.1 `RealStorageAdapter` method bodies | **Deferred to Day 4** | Pending TS storage service + Galileo creds; the skeleton is in place to receive the wiring |
| §3.2 Market-neutral dataset | **Deferred to Day 4** | Scope cut to keep PR #3 focused; same builder pattern as §3.1 |
| §3.3 Reference-strategy retune against real datasets | **Deferred to Day 4** | Coupled to §3.2 — needs both datasets to verify cross-domain spread |

Track A (smoke tests) was always a Day 4 carry given no creds today. The Day 3 spec was honest about this from the start; nothing surprising landed in this column.

---

## Smoke-test findings (per sponsor)

Smoke tests didn't run today because credentials weren't available. Each sponsor needs the same Day 4 trigger: get creds → run the smoke script (already-written skeleton or fresh) → write the JSON report. Day 4 recommendations below are based on what we *learned about the integration shape* during this session, not from running anything against the live testnet.

### 0G (Galileo testnet, storage + compute + chain)
- **Recommendation: real adapter on Day 4.** The TS storage service (option B) is the leading path; the Python SDK is not viable in its published form. Architecture is committed; today's PR #2 ships everything that doesn't need creds.
- **What needs creds:** EVM wallet private key (env: `LOCKSTEP_0G_PRIVATE_KEY`), faucet-claimed testnet `0G` tokens (faucet at `faucet.0g.ai`, 0.1 0G/day), publicly-hosted Indexer at `https://indexer-storage-testnet-turbo.0g.ai` (no auth).
- **Open questions for Day 4:** verify the indexer URL is still Galileo-current vs. Newton-era; determine whether the ERC-7857 reference contract from `0g-agent-nft` has been redeployed to Galileo.

### KeeperHub (MCP server + x402)
- **Recommendation: real adapter on Day 5 (per spec scope).** Day 4 just runs the smoke test (§1.3) and starts the feedback doc.
- **What we don't know yet:** which environment to target (sandbox vs mainnet), whether their workflows include any read-only/no-op shape for safe smoke testing, whether x402 testing is integrated into the MCP server or a separate flow.
- **Day 4 ask:** when starting §1.3, document one concrete piece of friction in `docs/feedback/keeperhub.md` (the bounty deliverable). If their docs don't surface a no-op workflow path, that itself is feedback.

### Gensyn (AXL transport)
- **Recommendation: real adapter on Day 4 IF multi-machine peering works first try.** The downgrade path is single-machine multi-process — coded the same against the Protocol, just less of a "validator network" demo story.
- **What needs setup:** AXL binary on two machines (Bosgame + RTX 5000 if both available), open ports / NAT-aware peering, structured-message round-trip.
- **Critical decision gate:** if peering doesn't establish before midday-checkpoint Day 4, downgrade in the demo and flag in the Day 4 STATUS so Day 5 can plan around it.

---

## Cost / throughput observations

**Cost:** zero testnet tokens spent today. The `RealStorageAdapter._CostTracker` is in place with a default 100-token-per-process cap that fail-stops via `SubstrateError("token budget exhausted")` rather than silently draining the faucet. First real costs land in Day 4 once the TS service ships.

**Throughput / latency baselines:**
- **Mock storage adapter:** sub-ms per op (in-process dict). Not useful as a baseline for Real.
- **Real storage adapter:** unmeasured (skeleton). Day 4 conformance run will be the first signal. Order-of-magnitude expectation: tens of ms per op (HTTP to local TS service + TS-to-0G-Indexer round-trip).
- **Conformance suite runtime:** 0.6 s for 118 tests against Mock. The 6 Real tests will add wall-clock proportional to network latency × test count when Day 4 wires them up.

**Anything that affects Day 5+ scope:** nothing yet. The Day 4 SDK exercise is the first place we'll learn whether 0G Storage's per-call latency is compatible with the demo's throughput story (graders re-running per validator pass, etc.). If latency is bad enough to matter, the demo can shrink to fewer validators — but we won't know until we measure.

---

## Day 4 spec requests

For the Day 4 spec drafter (architecture-Claude or human), here are the concrete asks based on Day 3's findings. In rough priority order:

### Must-have (gates everything else)

1. **§4-A — TS storage service + `RealStorageAdapter` method bodies.**
   - New `services/storage-ts/` directory with `package.json` (deps: `@0glabs/0g-ts-sdk`, `express` or `fastify`), `server.ts` exposing `/upload-encrypted-solution`, `/download-encrypted-solution`, `/upload-receipt`, `/download-receipt`, `/upload-dataset`, `/load-dataset-public`, `/load-dataset-full`, plus a Dockerfile and a README with start/stop commands.
   - Python `RealStorageAdapter` method bodies become `httpx` calls to `localhost:PORT` configured via `LOCKSTEP_0G_STORAGE_SERVICE_URL`. Wrap each call in `_with_retry` and instrument `_CostTracker`.
   - Run the conformance suite with `LOCKSTEP_TEST_REAL_STORAGE=1` against the live Galileo testnet; all 6 tests pass before merging.

2. **§4-B — Galileo wallet credentials provisioning.**
   - Generate a fresh dev wallet (don't reuse one that holds anything real):
     ```bash
     uv run python -c "from eth_account import Account; a = Account.create(); print('addr:', a.address); print('key:', a.key.hex())"
     ```
     Save both `addr` and `key`.
   - Claim 0.1+ testnet `0G` from [faucet.0g.ai](https://faucet.0g.ai) by pasting the wallet address. Daily limit is 0.1 `0G`; their Discord has higher-allocation requests if needed.
   - Set `LOCKSTEP_0G_PRIVATE_KEY=<key from step 1>` in your shell. Also add it to GitHub Actions secrets if CI should exercise the real path on `main` merges (not strictly required — the env-var gate keeps the conformance suite skipped by default).
   - Verify chain ID 16602 RPC (`https://evmrpc-testnet.0g.ai`) and indexer URL are still current per [docs.0g.ai/developer-hub/testnet/testnet-overview](https://docs.0g.ai/developer-hub/testnet/testnet-overview); update `config/galileo.yaml` defaults if not.

3. **§4-C — §3.2 market-neutral dataset.**
   - Same shape as §3.1 builder. Inputs: `data/raw/hyperliquid/funding_rates/{BTC,ETH}.parquet` and `data/raw/binance/candles_spot/{BTC,ETH}_1h.parquet` (already pulled into `data/raw/`).
   - Hyperliquid funding is **hourly, not 8h** (gotcha already noted in `scripts/datasets/README.md`). The spec's `funding_positive` threshold of 0.01% per 8h needs to become 0.00125% per hour — or resample to 8h windows before applying the threshold.
   - Persist to `lockstep/domains/trading/market_neutral/canonical_dataset.json` + `reports/day-03/dataset-market-neutral.md`.

4. **§4-D — §3.3 reference-strategy retune.**
   - Coupled to §4-C. Re-grade the five existing reference strategies (`ma_crossover`, `momentum`, `mean_reversion` directional; `naive_funding`, `basis_divergence` market-neutral) against both real canonical datasets.
   - Acceptance: `max - min > 0.3` on `worst_regime_sharpe` for directional, `> 0.05` on `net_market_neutral_pnl` for market-neutral.
   - If spread is flat after parameter retune, **don't paper over** — surface as a Day 5 grader-investigation item.

### Should-have (sponsor smoke tests)

5. **§4-E — Sponsor smoke tests.** Run §1.1 (0G Galileo iNFT mint+transfer), §1.2 (0G Compute grader latency), §1.3 (KeeperHub MCP), §1.4 (AXL peering). Each is a self-contained script with a 30-min debugging timebox and writes a JSON report to `reports/day-04/smoke-{sponsor}.json`.

6. **§4-F — Critical decision gates from §1.2 and §1.4.**
   - If 0G Compute grader latency > 30 s for a 30-day window → BYO-TEE fallback (already documented as plan B in the spec); update Day 5 architecture notes.
   - If AXL multi-machine peering fails by midday checkpoint → downgrade validator network demo to single-machine multi-process; document the downgrade path.

### Nice-to-have (polish)

7. **§4-G — Retroactive uploads.** Once §4-A is real, run the §3.1 + §3.2 builders with the real adapter so the canonical dataset JSONs get a `zg://...` storage URI instead of `mock://...`. Current files ship `mock://dataset/trading_directional/0xc36aa06f...` as a placeholder — Day 4 should replace.

8. **§4-H — Cost tracking summary.** First Day-4 conformance run should produce a `logs/substrate-storage.jsonl` with cost-per-op data. Summarize cumulative testnet spend in the Day 4 STATUS.

### Open architectural questions

- **Should `canonical_dataset.json` include the bars inline (current: 4.8 MB for directional) or split bars to a gitignored sibling?** Current: heavy but self-contained; if this becomes friction, split.
- **Should the TS storage service get its own Docker image and run via docker-compose, or just `npm start` for the hackathon?** Lighter-weight is fine for now; production-shaped is a Day 6+ concern if at all.
- **Where do the env vars (`LOCKSTEP_0G_PRIVATE_KEY`, etc.) live for CI?** Currently the real-storage conformance branch is opt-in via `LOCKSTEP_TEST_REAL_STORAGE=1` and CI doesn't set it; that's the right default. If CI should exercise the real path on `main` merges, that's a separate decision.

---

## Sanity checks for the Day 4 drafter

Before drafting Day 4 spec sections, confirm these are still true:

- [x] PRs #2 and #3 merged to main.
- [ ] `uv run pytest -q` on main shows 122 passed, 6 skipped.
- [ ] `uv run python examples/demo_flow.py` runs end-to-end against Mock.
- [ ] `data/raw/binance/candles_spot/{BTC,ETH,SOL}_1h.parquet` and `data/raw/hyperliquid/funding_rates/{BTC,ETH}.parquet` are present locally (re-pull via `scripts/datasets/README.md` if not).
- [ ] Galileo (chain 16602) is still the recommended testnet per `docs.0g.ai`.

If any of those are false, fix or note before generating Day 4 spec.
