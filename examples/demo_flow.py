"""End-to-end demo flow exercising all six mock substrate adapters.

Steps (per ``spec/spec1.md`` Section 5.2):
  1. Initialize Mock {Storage, Chain, Attestation, Encryption, Transport, Payment}
  2. Register both trading evaluations (auto-registers their Evaluators
     via the Layer 2 in-memory registry)
  3. For each reference strategy:
       a. Encrypt the solution
       b. Upload to MockStorage
       c. Run grader (full path)
       d. Produce attested Receipt
       e. Mint iNFT on MockChain
  4. Print marketplace state: iNFTs sorted by rank_score, per domain
  5. Validator pass: sample one iNFT, re-grade with public-only path,
     gossip the revalidation receipt via MockTransport
  6. Rental: pick a top-ranked iNFT, settle payment via MockPayment,
     authorize_usage on MockChain, sealed-execute (logs the trade)
  7. Generate a LIVE_EXECUTION receipt for the rental, append to chain

Runs in well under 30 seconds on a laptop. No network IO. No randomness
inside the grading path. The script is the integration testbed for the
substrate; ``tests/integration/test_demo_flow.py`` runs it as a
subprocess in Section 6.
"""

from __future__ import annotations

import pathlib
import sys
from datetime import UTC, datetime
from typing import Any

from lockstep.domains.trading.directional import (
    DirectionalDataset,
    DirectionalSolution,
    TradingDirectionalEvaluation,
)
from lockstep.domains.trading.directional import (
    commitment_roots as dir_roots,
)
from lockstep.domains.trading.market_neutral import (
    MarketNeutralDataset,
    MarketNeutralSolution,
    TradingMarketNeutralEvaluation,
)
from lockstep.domains.trading.market_neutral import (
    commitment_roots as mn_roots,
)
from lockstep.evaluation.canonical import canonical_json_bytes
from lockstep.evaluation.evaluator import list_evaluators
from lockstep.evaluation.receipt import ReceiptKind
from lockstep.evaluation.solution import DatasetCommitment
from lockstep.substrate.attestation import MockAttestationAdapter
from lockstep.substrate.chain import MockChainAdapter
from lockstep.substrate.encryption import MockEncryptionAdapter, generate_keypair
from lockstep.substrate.payment import MockPaymentAdapter
from lockstep.substrate.storage import MockStorageAdapter
from lockstep.substrate.transport import MockTransportAdapter

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DIRECTIONAL_DIR = REPO_ROOT / "examples" / "strategies" / "directional"
MARKET_NEUTRAL_DIR = REPO_ROOT / "examples" / "strategies" / "market_neutral"


def _section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


# ---------------------------------------------------------------------------
# Synthetic dataset construction (parallel to test fixtures, kept in-process)
# ---------------------------------------------------------------------------

def _build_directional_dataset() -> DirectionalDataset:
    import math

    regimes = ["bull", "bear", "chop", "vol_spike"]
    bars: list[dict] = []
    base = 50_000.0
    n_public = 60
    n_private = 20
    block_size = 5
    for i in range(n_public + n_private):
        regime = regimes[(i // block_size) % len(regimes)]
        if regime == "bull":
            drift = 0.005
        elif regime == "bear":
            drift = -0.005
        elif regime == "chop":
            drift = 0.0005 * math.sin(i * 0.7)
        else:
            drift = 0.01 * (1 if i % 2 == 0 else -1)
        base = base * (1.0 + drift)
        bars.append(
            {
                "timestamp": 1_700_000_000 + i * 3600,
                "asset": "BTC",
                "open": round(base, 4),
                "high": round(base + 0.5, 4),
                "low": round(base - 0.5, 4),
                "close": round(base, 4),
                "volume": 1000.0,
                "regime": regime,
            }
        )
    public = tuple(bars[:n_public])
    private = tuple(bars[n_public:])
    pub_root, priv_root, merkle = dir_roots(public, private)
    commitment = DatasetCommitment(
        domain="trading_directional",
        merkle_root=merkle,
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="zg://dataset/trading_directional/v1",
        schema_version="v1",
    )
    return DirectionalDataset(
        commitment=commitment,
        public_bars=public,
        private_bars=private,
        walk_forward_windows=((n_public // 2, n_public),),
    )


def _build_market_neutral_dataset() -> MarketNeutralDataset:
    from lockstep.domains.trading.market_neutral.dataset import classify_funding_regime

    bars: list[dict] = []
    spot = 100.0
    n_public = 60
    n_private = 20
    for i in range(n_public + n_private):
        block = (i // 10) % 6
        if block in (0, 1):
            rate = 0.001
        elif block in (3, 4):
            rate = -0.001
        else:
            rate = 0.00005
        regime = classify_funding_regime(rate)
        spot = spot * 1.0001
        if rate > 0:
            basis = 0.5 + 0.1 * (i % 3)
        elif rate < 0:
            basis = -(0.5 + 0.1 * (i % 3))
        else:
            basis = 0.05 * (1 if i % 2 == 0 else -1)
        perp = spot + basis
        bars.append(
            {
                "timestamp": 1_700_000_000 + i * 3600,
                "funding_rate": rate,
                "spot_close": round(spot, 6),
                "perp_close": round(perp, 6),
                "basis": round(basis, 6),
                "regime": regime,
            }
        )
    public = tuple(bars[:n_public])
    private = tuple(bars[n_public:])
    pub_root, priv_root, merkle = mn_roots(public, private)
    commitment = DatasetCommitment(
        domain="trading_market_neutral",
        merkle_root=merkle,
        public_root=pub_root,
        private_root=priv_root,
        storage_uri="zg://dataset/trading_market_neutral/v1",
        schema_version="v1",
    )
    return MarketNeutralDataset(
        commitment=commitment,
        public_bars=public,
        private_bars=private,
        walk_forward_windows=((n_public // 2, n_public),),
    )


def _strategy_source(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def main() -> int:
    _section("Step 1 — Initialize Mock substrate adapters")
    storage = MockStorageAdapter()
    chain = MockChainAdapter()
    attestation = MockAttestationAdapter()
    encryption = MockEncryptionAdapter()
    transport = MockTransportAdapter("marketplace")
    payment = MockPaymentAdapter()
    print("storage, chain, attestation, encryption, transport, payment ready")

    _section("Step 2 — Register both trading evaluations")
    dir_eval = TradingDirectionalEvaluation()
    mn_eval = TradingMarketNeutralEvaluation()
    chain.register_evaluator_onchain(dir_eval.evaluator())
    chain.register_evaluator_onchain(mn_eval.evaluator())
    for ev in list_evaluators():
        print(f"  {ev.domain_name} v{ev.domain_version}  id={ev.evaluator_id[:14]}…")

    _section("Step 3 — Build datasets and upload to storage")
    dir_ds = _build_directional_dataset()
    mn_ds = _build_market_neutral_dataset()
    storage.upload_dataset(
        dir_ds.commitment,
        canonical_json_bytes(list(dir_ds.public_bars)),
        canonical_json_bytes(list(dir_ds.private_bars)),
    )
    storage.upload_dataset(
        mn_ds.commitment,
        canonical_json_bytes(list(mn_ds.public_bars)),
        canonical_json_bytes(list(mn_ds.private_bars)),
    )
    print(f"  directional commitment {dir_ds.commitment.merkle_root[:14]}…")
    print(f"  market-neutral commitment {mn_ds.commitment.merkle_root[:14]}…")

    _section("Step 4 — Grade reference strategies and mint iNFTs")
    grader_pubkey, grader_privkey = attestation.generate_attestation_keypair()
    storage.authorize_attestation(grader_pubkey)

    minted: list[dict[str, Any]] = []
    producers = {
        "ma_crossover": "0x" + "a1" * 20,
        "momentum": "0x" + "a2" * 20,
        "mean_reversion": "0x" + "a3" * 20,
        "naive_funding": "0x" + "b1" * 20,
        "basis_divergence": "0x" + "b2" * 20,
    }

    directional_strategies = [
        ("ma_crossover", DIRECTIONAL_DIR / "ma_crossover.py"),
        ("momentum", DIRECTIONAL_DIR / "momentum.py"),
        ("mean_reversion", DIRECTIONAL_DIR / "mean_reversion.py"),
    ]
    market_neutral_strategies = [
        ("naive_funding", MARKET_NEUTRAL_DIR / "naive_funding.py"),
        ("basis_divergence", MARKET_NEUTRAL_DIR / "basis_divergence.py"),
    ]

    # Directional pipeline
    for name, path in directional_strategies:
        source = _strategy_source(path)
        sol = DirectionalSolution(source=source)
        plaintext = sol.serialize()
        plaintext_commitment = encryption.compute_plaintext_commitment(plaintext)
        recipient_pub, _ = generate_keypair()
        ciphertext = encryption.encrypt_for(plaintext, recipient_pubkey=recipient_pub)
        encrypted_solution = storage.upload_encrypted_solution(
            ciphertext,
            plaintext_commitment=plaintext_commitment,
            recipient_pubkey=recipient_pub,
        )
        grader_result = dir_eval.grader().grade(sol, dir_ds)
        receipt = attestation.produce_receipt(
            grader_result=grader_result,
            evaluator=dir_eval.evaluator(),
            problem_id="0x" + "01" * 32,
            solution_plaintext_commitment=plaintext_commitment,
            solution_bundle_hash=encrypted_solution.bundle_hash,
            dataset_commitment=dir_ds.commitment.merkle_root,
            grader_version=dir_eval.grader().version(),
            private_key=grader_privkey,
            pubkey=grader_pubkey,
            kind=ReceiptKind.INITIAL_GRADING,
        )
        token_id = chain.mint_inft(receipt, owner=producers[name])
        payment.register_owner(token_id, producers[name])
        minted.append(
            {
                "name": name,
                "domain": "trading_directional",
                "token_id": token_id,
                "rank_score": dir_eval.rank_score(receipt),
                "receipt": receipt,
                "evaluation": dir_eval,
            }
        )
        print(
            f"  [DIR] {name:<14} token_id={token_id} "
            f"worst_regime_sharpe={receipt.full_score_vector['worst_regime_sharpe']:+.4f}"
        )

    # Market-neutral pipeline
    for name, path in market_neutral_strategies:
        source = _strategy_source(path)
        sol = MarketNeutralSolution(source=source)
        plaintext = sol.serialize()
        plaintext_commitment = encryption.compute_plaintext_commitment(plaintext)
        recipient_pub, _ = generate_keypair()
        ciphertext = encryption.encrypt_for(plaintext, recipient_pubkey=recipient_pub)
        encrypted_solution = storage.upload_encrypted_solution(
            ciphertext,
            plaintext_commitment=plaintext_commitment,
            recipient_pubkey=recipient_pub,
        )
        grader_result = mn_eval.grader().grade(sol, mn_ds)
        receipt = attestation.produce_receipt(
            grader_result=grader_result,
            evaluator=mn_eval.evaluator(),
            problem_id="0x" + "02" * 32,
            solution_plaintext_commitment=plaintext_commitment,
            solution_bundle_hash=encrypted_solution.bundle_hash,
            dataset_commitment=mn_ds.commitment.merkle_root,
            grader_version=mn_eval.grader().version(),
            private_key=grader_privkey,
            pubkey=grader_pubkey,
            kind=ReceiptKind.INITIAL_GRADING,
        )
        token_id = chain.mint_inft(receipt, owner=producers[name])
        payment.register_owner(token_id, producers[name])
        minted.append(
            {
                "name": name,
                "domain": "trading_market_neutral",
                "token_id": token_id,
                "rank_score": mn_eval.rank_score(receipt),
                "receipt": receipt,
                "evaluation": mn_eval,
            }
        )
        print(
            f"  [MN ] {name:<16} token_id={token_id} "
            f"net_pnl={receipt.full_score_vector['net_market_neutral_pnl']:+.6f}"
        )

    _section("Step 5 — Marketplace state (sorted by rank_score per domain)")
    for domain in ("trading_directional", "trading_market_neutral"):
        print(f"  {domain}")
        rows = sorted(
            (m for m in minted if m["domain"] == domain),
            key=lambda m: m["rank_score"],
            reverse=True,
        )
        for m in rows:
            print(f"    #{m['token_id']:<3} {m['name']:<16} rank_score={m['rank_score']:+.6f}")

    _section("Step 6 — Validator pass: sample one iNFT, re-grade public-only, gossip")
    sample = minted[0]
    sample_receipt = sample["receipt"]
    sample_evaluation = sample["evaluation"]
    if sample["domain"] == "trading_directional":
        sample_dataset = dir_ds
        sample_solution_cls = DirectionalSolution
        sample_strategy_dir = DIRECTIONAL_DIR
    else:
        sample_dataset = mn_ds
        sample_solution_cls = MarketNeutralSolution
        sample_strategy_dir = MARKET_NEUTRAL_DIR
    public_only_grade = sample_evaluation.grader().grade(
        sample_evaluation.deserialize_solution(
            sample_solution_cls(
                source=_strategy_source(sample_strategy_dir / f"{sample['name']}.py")
            ).serialize()
        ),
        sample_dataset.public_view(),
    )
    revalidation_receipt = attestation.produce_receipt(
        grader_result=public_only_grade,
        evaluator=sample_evaluation.evaluator(),
        problem_id=sample_receipt.problem_id,
        solution_plaintext_commitment=sample_receipt.solution_plaintext_commitment,
        solution_bundle_hash=sample_receipt.solution_bundle_hash,
        dataset_commitment=sample_dataset.commitment.merkle_root,
        grader_version=sample_evaluation.grader().version(),
        private_key=grader_privkey,
        pubkey=grader_pubkey,
        kind=ReceiptKind.REVALIDATION,
        previous_receipt_id=sample_receipt.receipt_id,
    )
    validator_node = MockTransportAdapter("validator-A")
    received: list[tuple[str, bytes]] = []
    transport.subscribe(lambda src, msg: received.append((src, msg)))
    validator_node.send("marketplace", canonical_json_bytes({
        "type": "revalidation",
        "receipt_id": revalidation_receipt.receipt_id,
    }))
    print(
        f"  validator regraded #{sample['token_id']} ({sample['name']}); "
        f"matches public score: "
        f"{public_only_grade.public_score_vector == sample_receipt.public_score_vector}"
    )
    print(f"  gossip received: {len(received)} message(s)")

    _section("Step 7 — Rental: pay, authorize, sealed-execute, append LIVE receipt")
    top = max(minted, key=lambda m: m["rank_score"])
    sealed_executor = "0x" + "ee" * 20
    quote = payment.quote(top["token_id"], executor=sealed_executor)
    pay_receipt = payment.pay(quote, payer_private_key=b"consumer-secret")
    print(f"  paid {pay_receipt.quote.amount} {pay_receipt.quote.currency} to {pay_receipt.quote.recipient[:10]}…")
    auth_tx = chain.authorize_usage(
        top["token_id"], sealed_executor, signature=pay_receipt.proof.encode()
    )
    print(f"  authorize_usage tx: {auth_tx[:20]}…")

    # Sealed executor "runs" the strategy and emits a live-execution receipt.
    if top["domain"] == "trading_directional":
        top_solution = DirectionalSolution(
            source=_strategy_source(DIRECTIONAL_DIR / f"{top['name']}.py")
        )
        top_dataset = dir_ds
    else:
        top_solution = MarketNeutralSolution(
            source=_strategy_source(MARKET_NEUTRAL_DIR / f"{top['name']}.py")
        )
        top_dataset = mn_ds
    live_grade = top["evaluation"].grader().grade(top_solution, top_dataset)
    live_receipt = attestation.produce_receipt(
        grader_result=live_grade,
        evaluator=top["evaluation"].evaluator(),
        problem_id=top["receipt"].problem_id,
        solution_plaintext_commitment=top["receipt"].solution_plaintext_commitment,
        solution_bundle_hash=top["receipt"].solution_bundle_hash,
        dataset_commitment=top["receipt"].dataset_commitment,
        grader_version=top["evaluation"].grader().version(),
        private_key=grader_privkey,
        pubkey=grader_pubkey,
        kind=ReceiptKind.LIVE_EXECUTION,
        previous_receipt_id=top["receipt"].receipt_id,
        created_at=datetime.now(UTC),
    )
    storage.upload_receipt(live_receipt)
    print(
        f"  LIVE_EXECUTION receipt {live_receipt.receipt_id[:14]}… "
        f"chained from #{top['token_id']} ({top['name']})"
    )

    _section("Demo complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
