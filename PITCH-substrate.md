# Lockstep — Substrate Pitch

*For portfolio conversations, investor framing, and audiences that care about platform thesis over implementation specifics.*

---

**A way to agree on results without revealing inputs.**

Lockstep is a protocol for verifiable evaluation of private computation. Three primitives, one substrate:

1. **Attested grading.** Open-source graders run inside TEEs against committed datasets, producing receipts that anyone can verify and no one can forge. The grader version is content-addressed; the dataset is Merkle-committed; the receipt is signed by the enclave's attested key.

2. **Sampling validation.** A permissionless validator network re-runs grading against the same committed inputs. Trust the enclave for execution integrity; trust the validators for reproducibility. Disputes resolve on-chain against immutable grader code.

3. **Sealed execution.** Solutions live encrypted on decentralized storage and execute only inside designated sealed environments that emit outputs without revealing internals. Consumers rent solutions; producers monetize without disclosure.

Together these primitives solve a problem that gates every market for valuable computation: the supplier reports a quality claim and the buyer has no way to independently verify it. Trading strategy backtests, ML model benchmarks, optimization solutions, forecasting accuracy — every domain with a machine-checkable answer hits the same trust wall.

The standard response is centralized intermediation. Kaggle holds the test set. Numerai runs the scoring. QuantConnect controls the backtest engine. The marketplace becomes the trusted party because there's no other way to make claims credible. This works until the marketplace's incentives diverge from the participants', which they always do.

Lockstep proposes a different topology. The trust primitive is the protocol, not the platform. Anyone can publish a problem with sealed evaluation data. Anyone can submit a solution. Anyone can verify a grade. The substrate is permissionless; the marketplaces that emerge on top of it are domain-specific instantiations, not gatekeepers.

The architecture separates substrate from instantiation. Layer 1 is the substrate: TEE attestation, sealed storage, iNFT receipts, validator transport, payment rails. Layer 2 is a typed evaluation contract that any domain implements. Layer 3 is the domain-specific eval methodology, dataset, and metrics.

We're shipping the trading vertical as the first instantiation. Two distinct trading evaluation contracts in the same release — directional perp strategies and market-neutral funding-rate arbitrage — varying on dataset structure, solver interface, and scoring metrics. Two domains in one release is deliberate: it's the proof that Layer 2 is genuinely abstract rather than secretly trading-shaped.

The next verticals are concrete. ML model benchmarks: solvers submit models, the canonical eval is a sealed test set, the grader runs the model inside a TEE. Optimization solutions: problem-posters publish TSP instances or scheduling problems with bounty escrow, solvers compete, top-k receipts trigger automatic payout. Forecasting tournaments: time provides natural holdout, the future hasn't happened yet, it can't be optimized against. Code and security artifacts: solvers submit fuzzers and exploit patches, the grader runs canonical fuzzing harnesses against canonical vulnerability databases. Each is a Layer 3 implementation; the substrate is reusable.

Trading is the wedge because it has high value, clear metrics, and existing supply of underutilized quant talent. The protocol generalizes from there.

We don't certify alpha, model quality, solution optimality, or forecast accuracy. We make cheating the test harder than finding real signal. That's the smaller, defensible claim. It's also the only claim that compounds.

*Built with jig — multi-agent development framework. See README.*
