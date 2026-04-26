"""Transport adapter — in-process pub/sub Mock.

Production binding (Day 3+): Gensyn AXL. Validator nodes peer over AXL to
gossip sampled receipts and revalidation results. The Mock implements the
same Protocol with a process-local dispatch table so two adapter
instances can communicate without network IO.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from typing import Protocol

from lockstep.errors import SubstrateError

Handler = Callable[[str, bytes], None]


class TransportAdapter(Protocol):
    def send(self, peer_id: str, message: bytes) -> None: ...

    def subscribe(self, handler: Handler) -> None: ...

    def peer_id(self) -> str: ...


class TransportError(SubstrateError):
    """Raised when transport setup fails (e.g. duplicate peer_id)."""


class MockTransportAdapter:
    """Process-local pub/sub. Construct multiple instances to simulate peers."""

    _PEERS: dict[str, MockTransportAdapter] = {}

    def __init__(self, peer_id: str | None = None) -> None:
        chosen = peer_id or "peer_" + secrets.token_hex(4)
        if chosen in MockTransportAdapter._PEERS:
            raise TransportError(
                f"peer_id {chosen!r} already registered; "
                "use MockTransportAdapter.reset() between tests"
            )
        self._peer_id = chosen
        self._handlers: list[Handler] = []
        MockTransportAdapter._PEERS[self._peer_id] = self

    def peer_id(self) -> str:
        return self._peer_id

    def subscribe(self, handler: Handler) -> None:
        self._handlers.append(handler)

    def send(self, peer_id: str, message: bytes) -> None:
        target = MockTransportAdapter._PEERS.get(peer_id)
        if target is None:
            raise TransportError(f"unknown peer: {peer_id}")
        for handler in target._handlers:
            handler(self._peer_id, message)

    @classmethod
    def reset(cls) -> None:
        """Test helper: clear the global peer registry."""
        cls._PEERS.clear()
