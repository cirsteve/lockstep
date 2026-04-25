# CLAUDE.md

Permanent context for Lockstep. Shanc reloads this every session. Update in the same commit as any architectural change.

## Project

Lockstep is a protocol for verifiable evaluation of private computation. Trading strategies are the first vertical. Built for ETHGlobal Open Agents (April 24 – May 3, 2026), submitting to 0G Track 2, KeeperHub primary, and KeeperHub feedback bounty.

## Three-layer architecture

**Layer 1 — Substrate.** Vendor-agnostic primitives: TEE attestation, sealed storage, iNFT mint, validator transport, sealed execution, payment rails. Substrate code lives in `substrate/`.

**Layer 2 — Evaluation Contract.** Typed interfaces every domain implements. Defined in `evaluation/`. The contract has two distinct artifacts that should never be conflated:
- `Evaluator` — content-addressable, serializable Pydantic model. Defines *what* a domain is. Receipts reference it by `evaluator_id`.
- `Evaluation` — runtime Python class. Defines *how* code interacts with the domain. Returns its `Evaluator` via `.evaluator()`.

**Layer 3 — Domain Instantiation.** Concrete domain implementations under `domains/`. Each domain provides concrete `Solution`, `Dataset`, `Grader`, and `Evaluation` types plus a registered `Evaluator`. Adding a new domain must require zero changes to Layers 1 or 2.

## Load-bearing invariants

These are not negotiable. Code that violates them is wrong, even if tests pass.

1. **Receipt IDs are derived, never supplied.** `Receipt.build()` computes the canonical signing payload, hashes it, and uses that as `receipt_id`. Direct construction with a manually-supplied ID triggers a `model_validator` that rejects mismatches. This is verified in `tests/test_receipt.py`.

2. **Canonical signing payload is part of the protocol surface.** `Receipt.canonical_signing_payload()` returns deterministic JSON (sorted keys, no whitespace, NaN/Inf rejected). Changing it invalidates every receipt produced before the change. Treat as on-chain protocol — bumping it requires a coordinated grader version bump.

3. **Plaintext commitment vs encrypted bundle hash are distinct concepts.** `solution_plaintext_commitment` is the solution's identity (stable across re-encryptions). `solution_bundle_hash` is the specific encrypted blob's identity (different per recipient). Receipts carry both. Conflating them breaks the validation network.

4. **Public and full grading are separate paths.** `Grader.grade_public()` runs against the public dataset portion only. `Grader.grade_full()` runs against public + sealed private holdout. Receipts carry `public_score_vector` (always) and `full_score_vector` (only when full grading was performed). Public-tier validators reproduce only the public score; full-tier validators reproduce both.

5. **Graders must be deterministic.** Two validators running the same `Grader.version()` against the same `(solution, dataset)` must produce identical `GraderResult`. Hand-enforced via fixed-precision arithmetic, explicit reduction order, no parallelism in scoring code. Production replaces hand-enforcement with Rust + RepOps; for now, accept that cross-architecture drift is mocked and document the production path.

6. **Evaluator IDs are content-addressed.** Two `Evaluator.build()` calls with identical bodies produce identical `evaluator_id`. The registry is keyed by `evaluator_id`. Adding a domain means registering an Evaluator at module import time.

7. **Receipts must reference a registered Evaluator.** Validators look up `evaluator_id` in the registry to know what they're verifying. Unregistered evaluator_ids fail validation.

## Vendor neutrality

The substrate is intentionally vendor-neutral. 0G is the deployment target for the hackathon (chain, storage, compute, ERC-7857). KeeperHub is the sealed executor. Gensyn AXL is the validator transport. All three are deployment choices configured in `substrate/`, not architectural constraints in `evaluation/` or `domains/`.

When writing protocol code: never import vendor-specific symbols outside `substrate/`. The Evaluation interface, Receipt schema, and domain implementations are vendor-blind. If a domain needs vendor-specific behavior, it's wrong — push the vendor coupling into a substrate adapter.

## Hackathon eligibility constraints

ETHGlobal rules: all code must be created during the hackathon window (April 24 – May 3). No copy-paste from gecko or any prior private codebase. Public libraries (Pydantic, OpenZeppelin contracts, 0G SDK, etc.) are fine. Methodology can be inspired by gecko; implementation must be fresh.

Use incremental commits. Single-large-commit submissions get disqualified. Commit messages should reflect actual progress.

## Solver workflow

Spec-first, then Shanc:
1. Claude (architecture) produces specs and design decisions
2. Shanc (implementation) reads specs and writes code, can ask clarifying questions before starting
3. Code review happens at PR boundaries; plan-level scans happen at spec acceptance

Specs are concise and precise. They tell Shanc what to build and how to verify it's correct, not why it's structured the way it is. The "why" lives here in CLAUDE.md and in `docs/`.

## Determinism and floating point

Scoring code never uses:
- `numpy.sum`, `pandas.groupby().agg()`, or any other operation with hardware-dependent reduction order
- `random` without an explicit seed sourced from receipt-deterministic data
- parallel constructs (`multiprocessing`, `concurrent.futures`, `asyncio.gather` over scoring tasks)
- `float` arithmetic where precision matters; use `decimal.Decimal` for accumulation

The grader's output must round-trip through canonical JSON without precision loss. If a metric is fundamentally a float (e.g., Sharpe ratio), compute it in `Decimal`, round to a fixed number of significant digits at the boundary, then convert to float for the receipt.

## Mocking conventions

Things mocked for the hackathon, with production paths in code comments:
- Encryption ceremony (mock x25519 + XChaCha20-Poly1305)
- Sealed private holdout (separate file the producer's environment lacks access to)
- Cross-architecture determinism (hand-enforced fixed-precision arithmetic)
- Domain registry (in-memory dict)
- TEE signing (`enclave_kind="tee_mock"`, fake signature, valid pubkey format)
- Holdout rotation (config constant)

Each mock is documented at the point of mocking. When in doubt about whether something can be mocked: if the architecture survives the production swap without changing interfaces, mock is fine.

## Repo layout

```
lockstep/
├── pyproject.toml
├── LICENSE                Apache-2.0
├── README.md
├── CLAUDE.md              this file
├── ARCHITECTURE.md
├── lockstep/
│   ├── __init__.py
│   ├── substrate/         vendor adapters: 0G, KeeperHub, AXL, encryption
│   ├── evaluation/        Evaluator + Evaluation interfaces, Receipt schema
│   ├── domains/
│   │   └── trading/       directional + market_neutral instantiations
│   ├── verification/      validator node, AXL gossip, challenge submission
│   ├── execution/         KeeperHub MCP integration, x402 payment hooks
│   └── examples/          reference strategies, demo_flow.py
├── contracts/             ERC-7857 deployment, bounty escrow stub
├── tests/                 mirrors lockstep/ structure
└── docs/
    ├── trust_model.md
    ├── roadmap.md
    ├── demand_side.md
    ├── demo.md
    ├── development.md
    ├── faq.md
    └── submissions/
```

## Day-by-day status

Updated each day as work progresses. See `docs/status.md` for full history.

**Day 1 (Sat April 25):** Repo bootstrapped. Layer 2 schemas defined. Coin-flip toy domain working as abstraction proof. README, two pitch variants, design notes committed. Scaffolding only — no real substrate integration yet.

**Day 2 (Sun April 26):** Substrate adapter stubs, real trading-directional grader, real trading-market-neutral grader, reference strategies, project-level files. See `specs/day-02.md`.

**Day 3+ (Mon onwards):** Spec issued at start of each day. Check-in 1 with ETHGlobal Mon afternoon.

## Decisions log

Significant decisions and their rationale, in chronological order. Newest at the top.

- **2026-04-25:** Evaluator schema separated from Evaluation runtime. Content-addressable Pydantic model defines "what is this domain." Closes the gap where "domain registry" previously had no canonical artifact to register.
- **2026-04-25:** Receipt IDs are derived from canonical signing payload, never manually supplied. Pydantic model_validator enforces.
- **2026-04-25:** Public vs full validation tiers modeled explicitly. Receipts carry both score vectors when applicable.
- **2026-04-25:** Plaintext commitment and encrypted bundle hash kept distinct on Receipt. Solution identity vs specific encrypted copy identity.
- **2026-04-25:** Coin-flip toy domain added as the abstraction proof alongside trading. If interfaces work for both, abstractions aren't secretly trading-shaped.
- **2026-04-25:** Trading vertical ships two distinct domains in first release (directional + market-neutral) to prove Layer 2 generalizes within trading before claiming it generalizes beyond.
- **2026-04-25:** Substrate is vendor-neutral by design. 0G/KeeperHub/AXL are deployment choices, not architectural constraints.
- **2026-04-25:** Apache-2.0 license. Substrate, evaluation contract, and trading instantiation open from day one.
- **2026-04-25:** Project name: Lockstep. Captures coordination across producer/grader/validator/consumer roles.
