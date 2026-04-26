# Lockstep

**A way to agree on results without revealing inputs.**

Lockstep is a protocol for verifiable evaluation of private computation. Solvers submit answers to bounded problems. Graders score those answers inside attested execution environments against committed datasets that the solver never sees. Anyone can independently re-run the evaluation and challenge divergent results on-chain. Solutions, once graded, become rentable assets — the consumer pays for outcomes without ever seeing the underlying logic.

Three primitives, one substrate:

- **Attested grading.** Open-source graders run inside TEEs, signing receipts that bind a solution hash to a dataset commitment, a grader version, and a score vector.
- **Sampling validation.** A permissionless validator network re-runs grading against the same committed inputs. Divergence triggers on-chain dispute resolution. Trust the enclave for execution integrity; trust the validator set for reproducibility.
- **Sealed execution.** Solutions live encrypted on decentralized storage, accessible only to designated executors that emit outputs without revealing internals. Consumers rent solutions; producers monetize without disclosure.

The substrate is the product. The first vertical we ship — trading strategies — is the proof that the abstraction holds.

---

## Why this exists

Most markets for valuable computation are gated by a trust problem: the supplier reports a quality claim, and the buyer has no way to independently verify it. Trading strategy backtests, ML model benchmarks, optimization solutions, forecasting accuracy — every domain with a machine-checkable answer hits the same wall.

The standard response is centralized intermediation. Kaggle holds the test set. Numerai runs the scoring. QuantConnect controls the backtest engine. The marketplace becomes the trusted party because there's no other way to make claims credible.

This works until the marketplace's incentives diverge from the participants', which they always do eventually. It also fails the much larger long tail — anyone whose problem doesn't fit a centralized platform's product, anyone whose solution can't be sold through that platform's channel.

Lockstep proposes a different topology. The trust primitive is the protocol, not the platform. Anyone can publish a problem with sealed evaluation data. Anyone can submit a solution. Anyone can verify a grade. The substrate is permissionless and the marketplaces that emerge on top of it are domain-specific instantiations, not gatekeepers.

---

## Three-layer architecture

Lockstep separates protocol substrate from domain instantiation. This matters because the same primitives that secure trading strategy grading also secure ML benchmark evaluation, optimization solutions, forecasting predictions, code and security artifacts — anything where solutions are machine-checkable.

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 3: Domain Instantiation                              │
│  Trading-directional · Trading market-neutral               │
│  (Future: ML benchmarks, optimization, forecasting, code)   │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  Layer 2: Evaluation Contract                               │
│  Typed Evaluation interface · Receipt schema · Solver spec  │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Substrate                                         │
│  TEE attestation · Sealed storage · iNFT mint · Validators  │
│  Sealed execution · Payment rails                           │
└─────────────────────────────────────────────────────────────┘
```

**Layer 1 (substrate)** is reusable across all domains. **Layer 2 (evaluation contract)** is the typed interface every domain implements. **Layer 3 (instantiation)** is the domain-specific eval methodology, dataset, and metrics.

Lockstep ships with two trading instantiations in its first release — directional perp strategies and market-neutral funding-rate arbitrage — varying on dataset structure, solver interface, and scoring metrics. Two distinct domains in a single release is deliberate: it's the proof that Layer 2 is genuinely abstract rather than secretly trading-shaped.

---

## The substrate

### Attested grading

Every grader is open-source, content-addressed, and immutable. Producers can read the grader code before submitting. Validators can re-run it. The grader version is part of every receipt, so disputes are anchored to specific code.

Graders run inside TEEs. The enclave generates an attestation key on startup, binds it cryptographically to the hardware via remote attestation, and signs receipts before they leave the secure environment. A receipt proves: this solution hash, evaluated by this grader version, against this dataset commitment, produced this score vector — and the proof was generated inside genuine secure hardware.

The TEE prevents the grader from being subverted. It does not prevent the grader from being wrong. That's what validators are for.

### Sampling validation

The OSS grader runs anywhere. Validator nodes connect to a peer-to-peer transport (AXL), sample graded receipts at random, fetch the canonical dataset by Merkle root, fetch the encrypted solution from decentralized storage, and re-run the grading.

Validators that compute identical receipts gossip agreement. Validators that diverge submit on-chain challenges. Disputes are resolved by replay against the canonical inputs — the grader version is content-addressed, the dataset is Merkle-committed, the solution hash is immutable. There is no ambiguity about what should happen when the protocol is followed honestly.

The validator network's job is not to certify quality. It's to confirm that the enclave didn't lie and the grader code does what its hash says it does.

### Sealed execution

Solutions live encrypted on decentralized storage. Only the producer holds the decryption key, until the moment they grant access to a designated sealed executor.

The sealed executor is any environment that can decrypt and execute a solution while emitting outputs without revealing internals. For trading strategies, that's KeeperHub's Turnkey-secured workflow infrastructure: the strategy is decrypted inside their execution environment, signals are generated, trades land on-chain, and the consumer receives execution outcomes — never the strategy code.

The sealed executor pattern is what makes solution rental viable. Without it, anyone who pays for a solution can copy it; with it, consumers pay for ongoing access to outputs and producers retain control of the underlying logic.

### Receipts and reputation

A receipt is the canonical artifact of the substrate. It binds together everything that matters:

```
Receipt {
  problem_id: bytes32                       // domain + problem instance
  evaluator_id: bytes32                     // content-addressed domain contract
  solution_plaintext_commitment: bytes32    // identity of the underlying solution
  solution_bundle_hash: bytes32             // identity of this specific encrypted copy
  dataset_commitment: bytes32               // Merkle root of canonical dataset
  grader_version: bytes32                   // content-addressed grader code
  public_score_vector: dict                 // computable from public data alone
  full_score_vector: dict                   // computable only with sealed-holdout access
  metadata: dict                            // domain-defined additional info
  enclave: EnclaveAttestation               // TEE pubkey + signature + attestation chain
  receipt_id: bytes32                       // derived hash of the canonical signing payload
}
```

Two hashes for the solution because the same plaintext can be re-encrypted for different consumers without changing the underlying solution's identity; two score vectors because validators without TEE access can verify the public portion while full-tier validators reproduce both. The split is structural, not cosmetic — see `lockstep/evaluation/receipt.py` for the canonical schema.

Receipts are public, indefinitely retrievable, and independently verifiable. They become the unit of reputation in the marketplace — every solver's track record is a sequence of receipts, each cryptographically anchored to specific evaluation conditions.

When a solution is rented and executed, new receipts flow back: not initial-grading receipts, but live-execution receipts. The same substrate that attests backtest grading attests live performance. Rankings shift from backtest-weighted to live-weighted as track records accumulate.

---

## Trading: the first vertical

The substrate is general. The first vertical is trading because:

- **High value, clear metrics.** Quantitative finance has decades of established methodology for evaluating strategies. Walk-forward validation, regime decomposition, slippage modeling — the eval contract isn't speculative.
- **Existing supply, existing demand.** Underutilized quant talent exists everywhere. Capital allocators looking for alpha exist everywhere. The marketplace's job is to remove the trust friction between them.
- **Composability with existing DeFi infrastructure.** KeeperHub already runs sealed-executor patterns for protocol clients. Aave, Spark, Morpho, and other DeFi protocols already provide execution venues. The substrate doesn't have to invent the execution layer; it can compose with what exists.
- **Honest evaluation methodology can be adversarial-robust.** Cherry-picked training data, self-selected backtest periods, look-ahead leak — the failure modes are known and addressable. The marketplace can structurally constrain the most common forms of fraud.

### Two trading instantiations

Lockstep ships two trading evaluation contracts in its first release. They share the substrate; they differ in everything else.

**Trading-directional.** Solvers submit functions that consume rolling OHLCV windows and emit position signals (long/short/flat with size). The canonical dataset is BTC/ETH/SOL hourly across labeled regimes (bull, bear, chop, vol spike), with a sealed private holdout the producer never sees. The grader runs walk-forward optimization, computes per-regime Sharpe and drawdown, and ranks by worst-regime score.

**Trading market-neutral.** Solvers submit functions that consume joined funding-rate and spot-perp basis data, and emit paired position signals. The canonical dataset joins Hyperliquid funding history with Binance spot prices, with a sealed private holdout. The grader runs against funding-regime labels (funding-positive vs funding-negative), computes funding capture rate and basis-dislocation behavior, and ranks by net market-neutral PnL after slippage.

Both instantiations expose the same substrate primitives: TEE-attested grading, AXL-validated receipts, sealed execution via KeeperHub, x402 payments. The marketplace UI is parameterized by domain — different score vectors, different rank functions, different filterable dimensions — but the underlying machinery is shared.

This is the abstraction proof. If two trading domains with meaningfully different shapes work in the same substrate, the substrate is doing real work. Adding ML benchmark evaluation later will be a Layer 3 implementation, not a Layer 1 redesign.

### Why trading is structurally hardened

The standard failure mode of trading strategy marketplaces is *the producer chooses the test*: they run their own backtest, on data they selected, with parameters they tuned for that data, and submit a number. Cherry-picking is undetectable. Lockstep changes the topology:

- Producers optimize their strategy on whatever training data they want.
- The marketplace publishes the canonical evaluation dataset, regime labels, walk-forward methodology, and scoring metrics.
- The grader runs inside a TEE against the canonical eval, including a private holdout the producer never sees.
- The receipt is signed, public, and reproducible by validators against the public portion of the dataset.

The producer can't game what they don't control. Self-selected tests, cherry-picked backtests, look-ahead leak through scoring data — structurally constrained, not just discouraged.

We don't certify alpha. We make cheating the test harder than finding real signal.

---

## Beyond trading

The two trading instantiations exist to demonstrate the abstraction. The substrate is intended to support any domain with a machine-checkable answer. Concrete next-vertical candidates:

**ML model benchmarks.** Solvers submit models. The canonical eval is a sealed test set (MMLU, HumanEval, custom domain benchmarks). The grader runs the model against the test set inside a TEE and produces an attested score. Verifiable benchmark claims replace self-reported ones.

**Optimization solutions.** Problem-posters publish a TSP instance, scheduling problem, route optimization, ML hyperparameter tuning task. Solvers submit candidate solutions. The grader checks feasibility and computes the objective value. Top-k payouts from on-chain bounty escrow.

**Forecasting tournaments.** Solvers submit predictions about resolvable future events. The grader waits for resolution data, scores via Brier or log-loss, and updates receipts. Time provides natural holdout — the future hasn't happened yet, so it can't be optimized against.

**Code and security artifacts.** Solvers submit fuzzers, exploit patches, vulnerability reports. The grader runs canonical fuzzing harnesses or known-vulnerability replays inside the TEE. Verifiable security claims for code that currently relies on auditor reputation.

Each new vertical is a Layer 3 instantiation. The substrate is reusable. Validator nodes can serve multiple domains, increasing utilization and network value.

---

## Two market modes

Lockstep supports both supply-side and demand-side flows on the same substrate. The hackathon implementation ships supply-side; demand-side is documented as architectural sketch.

**Supply-side (shipped):** Producers solve problems and offer their solutions for rent. Consumers browse graded solutions and pay for ongoing access. This is the trading marketplace flow described above.

**Demand-side (sketched):** Problem-posters lock bounties in escrow contracts on 0G Chain, publish problems with sealed evaluation data, and the protocol auto-pays top-k receipts when solvers submit. The same trust primitives apply: sealed eval data, TEE-attested grading, validator network, content-addressed grader code. The flow inverts but the substrate doesn't change.

See [`docs/demand_side.md`](docs/demand_side.md) for the full sequence diagram and Solidity contract spec.

---

## Trust model

Lockstep is precise about what it protects and what it doesn't. See [`docs/trust_model.md`](docs/trust_model.md) for the full writeup. The summary:

**What's structurally constrained:**
- Producer-controlled evaluation data — they don't have access to the canonical eval set or its private holdout
- Forged receipts — TEE attestation + validator network cross-check
- Cherry-picked backtest periods — walk-forward enforced by grader, not producer
- Look-ahead leak in scoring — the grader is the only thing that touches scoring data
- Solution IP leakage during rental — sealed execution via designated executors
- Grader-version drift — content-addressed grader hashes, immutable receipts under their original grader

**What's *not* claimed:**
- Lockstep does not certify alpha (or model quality, or solution optimality, or forecast accuracy). A solution that passes every available historical test can still fail in conditions that haven't happened yet.
- The marketplace is not magic. Bad eval methodology produces verifiable garbage. The grader's quality is the marketplace's ceiling.
- TEE attestation is hardware-rooted. Compromise of the underlying enclave technology degrades the first trust path. The validator network is the secondary defense, not a replacement.
- Sybil-resistant validation is a separate problem. The protocol assumes at least one honest validator in the sampling set; production deployments need explicit Sybil resistance for the validator network.

We don't certify alpha. We make cheating the test harder than finding real signal.

---

## Sponsor integrations

Lockstep is built for the ETHGlobal Open Agents hackathon and integrates with three sponsor stacks. Each sponsor's technology does work the others can't, and the substrate cannot exist without all three.

### 0G — substrate and standard
- **0G Chain** hosts the ERC-7857 iNFT contract. Solutions are minted, rented, and tracked on-chain.
- **0G Storage** holds encrypted solution bundles (private metadata) and plaintext receipts (public). Canonical dataset Merkle commitments are anchored here.
- **0G Compute** runs the OSS grader inside a TEE-attested Confidential VM. Sealed Inference produces the cryptographic signature that anchors every receipt.
- **ERC-7857** is the rentable-asset primitive. Without `authorizeUsage()` and the sealed-executor pattern, the marketplace can't separate ownership from access.

### KeeperHub — sealed executor and reliability layer
- KeeperHub workflows are the sealed executor for rented solutions. Encrypted solutions are decrypted inside KeeperHub's Turnkey-secured environment; consumers receive execution outcomes, never the underlying logic.
- DeFi workflow integrations (Aave, Spark, Morpho) provide the actual execution venues for the trading vertical. Battle-tested infrastructure with retry logic, gas optimization, and audit trails.
- x402 enables autonomous per-call payments from consumers to producers without intermediated billing.

### Gensyn — validator transport
- AXL provides the peer-to-peer transport layer for the validator network. Validator nodes run anywhere, peer without central infrastructure, and gossip sampled receipts.
- The substrate is permissionlessly extensible: anyone can run a validator, sample any graded receipt, and challenge divergent grades on-chain.

See `docs/submissions/` for per-sponsor framing.

---

## What ships in the hackathon demo

The demo is a happy-path end-to-end flow demonstrating the substrate via the trading vertical:

1. Three reference strategies (MA crossover, momentum, mean-reversion) graded under the directional trading evaluation
2. Two reference strategies (naive funding capture, basis-divergence trigger) graded under the market-neutral trading evaluation
3. All five strategies minted as ERC-7857 iNFTs on 0G Chain Newton testnet
4. AXL validator on a second machine sampling graded iNFTs and confirming receipts
5. Marketplace UI browsing iNFTs by domain-specific ranking metric
6. End-to-end rental: x402 payment → `authorizeUsage()` → KeeperHub workflow → live trade landing on Aave

Reference strategies are deliberately simple. The point of the demo is the protocol, not the alpha. Real strategy generation is downstream of the marketplace working.

See [`docs/demo.md`](docs/demo.md) for the full demo script and reproduction instructions.

---

## What does NOT ship in the hackathon demo

Documented as architectural sketches, not implementations. See [`docs/roadmap.md`](docs/roadmap.md).

- **Demand-side mode.** Problem-posters lock bounties in escrow, sealed eval data is published, solvers compete, top-k receipts trigger automatic payout.
- **Production-grade sealed storage.** The hackathon mocks private holdout sealing. Production requires Phala CVM with attestation-derived keys or equivalent BYO-TEE infrastructure.
- **Real iNFT royalty mechanics.** Producers earning percentage fees on secondary trades. Standard supports it; not implemented.
- **Domain expansion.** ML benchmarks, optimization, forecasting, code/security. Architecture supports them; only trading is implemented.
- **Decentralized eval methodology governance.** Currently the marketplace defines the canonical evaluation. Production requires a process for proposing, debating, and ratifying new versions.
- **Sybil resistance for the validator network.** The hackathon assumes honest validators. Production needs stake-and-slash or equivalent.

---

## Repository layout

```
lockstep/
├── pyproject.toml
├── README.md
├── CLAUDE.md                       permanent context, reloaded each session
├── lockstep/
│   ├── substrate/                  TEE attestation, 0G Storage, 0G Chain, AXL transport, payment
│   ├── evaluation/                 Evaluator + Evaluation interfaces, Receipt schema
│   ├── domains/
│   │   ├── coin_flip/              toy domain — abstraction proof for non-trading workloads
│   │   └── trading/                directional and market-neutral instantiations
│   ├── verification/   (planned)   validator node, AXL gossip, challenge submission
│   └── execution/      (planned)   KeeperHub MCP integration, x402 payment hooks
├── contracts/          (planned)   ERC-7857 reference impl + bounty escrow spec
├── examples/                       reference strategies, demo flow scripts
├── tests/                          mirrors the lockstep/ tree
└── docs/                (planned)  trust model, roadmap, demand-side spec, FAQ, submissions
```

See [`docs/architecture.md`](docs/architecture.md) for the detailed component breakdown.

---

## Quickstart

See [`docs/development.md`](docs/development.md) for setup, dependencies, and reproducing the demo locally.

---

## License

Apache-2.0. The substrate, the evaluation contract abstraction, and the trading instantiation are open source from day one. The marketplace's value is in the protocol, not in proprietary code.

---

## Built with jig

Lockstep was designed and implemented using jig, a custom multi-agent development framework. The architect/implementer split described in `CLAUDE.md` runs on top of jig's orchestration primitives. The substrate itself has no jig runtime dependency — graders, validators, and executors run as plain Python services against the interfaces in `lockstep/`.
