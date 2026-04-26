"""Factory: construct substrate adapters from a parsed config dict.

Day 3 §2.2. The storage adapter is the only one factory-driven so far
because it's the only one with a real implementation in flight.
Other adapters (chain, attestation, encryption, payment, transport)
remain hard-coded Mock at call sites until their real implementations
land — at which point each gets a parallel ``get_*_adapter`` here.

Config shape (YAML or any dict-like):

    storage:
      kind: mock | real
      real:                 # only when kind == "real"
        rpc_url: ...
        indexer_url: ...
        token_budget: 100
        log_path: logs/substrate-storage.jsonl   # optional

Secrets are NOT in YAML. The signer key is read from the
``LOCKSTEP_0G_PRIVATE_KEY`` environment variable; URLs can be
overridden via ``LOCKSTEP_0G_GALILEO_RPC`` / ``LOCKSTEP_0G_GALILEO_INDEXER``
when present (env wins over YAML defaults).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lockstep.substrate.storage import MockStorageAdapter, StorageAdapter
from lockstep.substrate.storage_real import RealStorageAdapter


def get_storage_adapter(config: dict[str, Any]) -> StorageAdapter:
    """Return a Mock or Real storage adapter based on ``config["storage"]["kind"]``.

    Defaults to ``MockStorageAdapter`` when no storage section is present —
    so a partial config doesn't accidentally hit the network.
    """
    storage_cfg = (config or {}).get("storage") or {}
    kind = storage_cfg.get("kind", "mock")

    if kind == "mock":
        return MockStorageAdapter()

    if kind == "real":
        real_cfg = storage_cfg.get("real") or {}
        rpc_url = os.environ.get("LOCKSTEP_0G_GALILEO_RPC") or real_cfg.get("rpc_url")
        indexer_url = os.environ.get("LOCKSTEP_0G_GALILEO_INDEXER") or real_cfg.get(
            "indexer_url"
        )
        if not rpc_url or not indexer_url:
            raise ValueError(
                "storage.kind=real requires rpc_url and indexer_url "
                "(in YAML or via LOCKSTEP_0G_GALILEO_RPC / LOCKSTEP_0G_GALILEO_INDEXER)"
            )
        signer_key = os.environ.get("LOCKSTEP_0G_PRIVATE_KEY")
        log_path_str = real_cfg.get("log_path")
        return RealStorageAdapter(
            rpc_url=rpc_url,
            indexer_url=indexer_url,
            signer_key=signer_key,
            token_budget=real_cfg.get("token_budget", "100"),
            log_path=Path(log_path_str) if log_path_str else None,
        )

    raise ValueError(
        f"unknown storage.kind: {kind!r} (expected 'mock' or 'real')"
    )


__all__ = ["get_storage_adapter"]
