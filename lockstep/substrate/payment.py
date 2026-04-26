"""Payment adapter — Mock implementing the x402 contract surface.

Production binding (Day 3+): KeeperHub's x402 integration settles per-call
payments from consumers to producers. The Mock here is an in-memory
ledger; quotes get nonces, payments get content-addressed receipts that
can be presented to ``ChainAdapter.authorize_usage`` as the signature
payload.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from lockstep.evaluation.canonical import Address, Bytes32Hex


class PaymentError(RuntimeError):
    """Raised when a quote can't be priced or a payment fails to verify."""


class Quote(BaseModel):
    """A priced offer to rent a specific iNFT for a specific executor.

    The recipient is the iNFT owner (the producer). The amount is in the
    smallest unit of whatever currency the deployment uses (wei for ETH,
    micro-USDC for stablecoin rails). ``nonce`` is the substrate-side
    handle that prevents replay.
    """

    model_config = ConfigDict(frozen=True)

    token_id: int
    executor: Address
    recipient: Address
    amount: int
    currency: str = "mock-USDC"
    nonce: str
    expires_at_ns: int


class PaymentReceipt(BaseModel):
    """Proof of settlement for a Quote.

    ``proof`` is the content-addressed handle the substrate threads into
    ``ChainAdapter.authorize_usage`` as the signature blob. In production
    it would be the on-chain settlement tx hash; here it's sha256 over
    (payer, recipient, amount, nonce).
    """

    model_config = ConfigDict(frozen=True)

    quote: Quote
    payer: Address
    proof: Bytes32Hex
    settled_at_ns: int


class PaymentAdapter(Protocol):
    def quote(self, token_id: int, executor: Address) -> Quote: ...

    def pay(self, quote: Quote, payer_private_key: bytes) -> PaymentReceipt: ...

    def verify_payment(
        self,
        receipt: PaymentReceipt,
        expected_recipient: Address,
        expected_amount: int,
    ) -> bool: ...


class MockPaymentAdapter:
    """In-memory payment ledger.

    Construct with an ``owners`` mapping (token_id → owner address) and a
    ``price`` (flat per-rent amount). Quote nonces and payment proofs are
    randomized; replays reroll the nonce so distinct payments produce
    distinct receipts.
    """

    def __init__(
        self,
        owners: dict[int, Address] | None = None,
        *,
        price: int = 1_000_000,  # 1 USDC if currency is mock-USDC
        currency: str = "mock-USDC",
        validity_seconds: int = 300,
    ) -> None:
        self._owners: dict[int, Address] = dict(owners or {})
        self._price = price
        self._currency = currency
        self._validity_ns = validity_seconds * 1_000_000_000
        self._ledger: dict[Bytes32Hex, PaymentReceipt] = {}
        self._used_nonces: set[str] = set()

    def register_owner(self, token_id: int, owner: Address) -> None:
        """Bind a token to its owner so quotes can target the right recipient."""
        self._owners[token_id] = owner

    def quote(self, token_id: int, executor: Address) -> Quote:
        if token_id not in self._owners:
            raise PaymentError(f"unknown token_id: {token_id}")
        now_ns = time.time_ns()
        return Quote(
            token_id=token_id,
            executor=executor,
            recipient=self._owners[token_id],
            amount=self._price,
            currency=self._currency,
            nonce=secrets.token_hex(16),
            expires_at_ns=now_ns + self._validity_ns,
        )

    def pay(self, quote: Quote, payer_private_key: bytes) -> PaymentReceipt:
        if time.time_ns() > quote.expires_at_ns:
            raise PaymentError("quote expired")
        if quote.nonce in self._used_nonces:
            raise PaymentError(f"quote nonce {quote.nonce} already settled")
        # Derive a payer address from the private key bytes. The mock
        # treats the first 20 bytes of sha256(private_key) as the address.
        payer_hash = hashlib.sha256(payer_private_key).digest()
        payer_address = "0x" + payer_hash[:20].hex()
        # Bind the proof to every field of the rental context, not just
        # (payer, recipient, amount, nonce). This prevents a proof from
        # being repurposed for the wrong token, executor, or currency.
        proof_seed = (
            f"{payer_address}:{quote.recipient}:{quote.amount}:{quote.currency}:"
            f"{quote.token_id}:{quote.executor}:{quote.nonce}"
        ).encode()
        proof = "0x" + hashlib.sha256(proof_seed).hexdigest()
        receipt = PaymentReceipt(
            quote=quote,
            payer=payer_address,
            proof=proof,
            settled_at_ns=time.time_ns(),
        )
        self._ledger[proof] = receipt
        self._used_nonces.add(quote.nonce)
        return receipt

    def verify_payment(
        self,
        receipt: PaymentReceipt,
        expected_recipient: Address,
        expected_amount: int,
        *,
        expected_token_id: int | None = None,
        expected_executor: Address | None = None,
        expected_currency: str | None = None,
    ) -> bool:
        """Verify a settlement receipt is in the ledger and matches expectations.

        ``expected_token_id``, ``expected_executor``, and
        ``expected_currency`` are optional defense-in-depth checks for
        callers that have those fields and want to confirm the proof is
        bound to the right rental context.
        """
        ledger_entry = self._ledger.get(receipt.proof)
        if ledger_entry is None or ledger_entry != receipt:
            return False
        if receipt.quote.recipient != expected_recipient:
            return False
        if receipt.quote.amount != expected_amount:
            return False
        if expected_token_id is not None and receipt.quote.token_id != expected_token_id:
            return False
        if expected_executor is not None and receipt.quote.executor != expected_executor:
            return False
        if expected_currency is not None and receipt.quote.currency != expected_currency:
            return False
        return True
