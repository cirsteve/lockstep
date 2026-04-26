"""Unit tests for ``lockstep.substrate.factory.get_storage_adapter``."""

from __future__ import annotations

from pathlib import Path

import pytest

from lockstep.substrate.factory import get_storage_adapter
from lockstep.substrate.storage import MockStorageAdapter
from lockstep.substrate.storage_real import RealStorageAdapter


def test_empty_config_defaults_to_mock_storage():
    adapter = get_storage_adapter({})
    assert isinstance(adapter, MockStorageAdapter)


def test_explicit_mock_kind_returns_mock_adapter():
    adapter = get_storage_adapter({"storage": {"kind": "mock"}})
    assert isinstance(adapter, MockStorageAdapter)


def test_real_kind_constructs_real_adapter_with_yaml_endpoints(monkeypatch):
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_PRIVATE_KEY", raising=False)

    adapter = get_storage_adapter(
        {
            "storage": {
                "kind": "real",
                "real": {
                    "rpc_url": "https://yaml-rpc.example",
                    "indexer_url": "https://yaml-indexer.example",
                    "token_budget": "50",
                },
            }
        }
    )
    assert isinstance(adapter, RealStorageAdapter)
    assert adapter._rpc_url == "https://yaml-rpc.example"
    assert adapter._indexer_url == "https://yaml-indexer.example"
    assert adapter._signer_key is None  # no env var set


def test_env_vars_override_yaml_endpoints(monkeypatch):
    monkeypatch.setenv("LOCKSTEP_0G_GALILEO_RPC", "https://env-rpc.example")
    monkeypatch.setenv("LOCKSTEP_0G_GALILEO_INDEXER", "https://env-indexer.example")
    monkeypatch.setenv("LOCKSTEP_0G_PRIVATE_KEY", "0xdeadbeef")

    adapter = get_storage_adapter(
        {
            "storage": {
                "kind": "real",
                "real": {
                    "rpc_url": "https://yaml-rpc.example",
                    "indexer_url": "https://yaml-indexer.example",
                },
            }
        }
    )
    assert adapter._rpc_url == "https://env-rpc.example"
    assert adapter._indexer_url == "https://env-indexer.example"
    assert adapter._signer_key == "0xdeadbeef"


def test_real_kind_without_endpoints_raises_value_error(monkeypatch):
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)

    with pytest.raises(ValueError, match="rpc_url and indexer_url"):
        get_storage_adapter({"storage": {"kind": "real"}})


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError, match="unknown storage.kind"):
        get_storage_adapter({"storage": {"kind": "ipfs"}})


def test_real_kind_propagates_service_url_from_yaml(monkeypatch):
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_STORAGE_SERVICE_URL", raising=False)

    adapter = get_storage_adapter(
        {
            "storage": {
                "kind": "real",
                "real": {
                    "rpc_url": "https://r.example",
                    "indexer_url": "https://i.example",
                    "service_url": "http://localhost:9999",
                },
            }
        }
    )
    assert adapter._service_url == "http://localhost:9999"


def test_real_kind_defaults_service_url_to_localhost_7878(monkeypatch):
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_STORAGE_SERVICE_URL", raising=False)

    adapter = get_storage_adapter(
        {
            "storage": {
                "kind": "real",
                "real": {
                    "rpc_url": "https://r.example",
                    "indexer_url": "https://i.example",
                },
            }
        }
    )
    assert adapter._service_url == "http://localhost:7878"


def test_service_url_env_overrides_yaml(monkeypatch):
    monkeypatch.setenv("LOCKSTEP_0G_STORAGE_SERVICE_URL", "http://env-host:1234")
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)

    adapter = get_storage_adapter(
        {
            "storage": {
                "kind": "real",
                "real": {
                    "rpc_url": "https://r.example",
                    "indexer_url": "https://i.example",
                    "service_url": "http://yaml-host:9999",
                },
            }
        }
    )
    assert adapter._service_url == "http://env-host:1234"


def test_real_kind_propagates_log_path(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)

    log_path = tmp_path / "logs" / "storage.jsonl"
    adapter = get_storage_adapter(
        {
            "storage": {
                "kind": "real",
                "real": {
                    "rpc_url": "https://r.example",
                    "indexer_url": "https://i.example",
                    "log_path": str(log_path),
                },
            }
        }
    )
    assert adapter._log_path == log_path


def test_local_yaml_loads_and_constructs_mock():
    """End-to-end check that the shipped config/local.yaml actually parses
    and round-trips through the factory."""
    import yaml

    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "config" / "local.yaml"
    with config_path.open() as fh:
        config = yaml.safe_load(fh)
    adapter = get_storage_adapter(config)
    assert isinstance(adapter, MockStorageAdapter)


def test_galileo_yaml_loads_and_constructs_real(monkeypatch):
    """End-to-end check that the shipped config/galileo.yaml parses and
    constructs a RealStorageAdapter. Construction is cheap — both the
    httpx client and the Web3 provider lazy-connect, so no TS service
    or RPC needs to be reachable."""
    import yaml

    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_RPC", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_GALILEO_INDEXER", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_STORAGE_SERVICE_URL", raising=False)
    monkeypatch.delenv("LOCKSTEP_0G_PRIVATE_KEY", raising=False)

    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "config" / "galileo.yaml"
    with config_path.open() as fh:
        config = yaml.safe_load(fh)
    adapter = get_storage_adapter(config)
    assert isinstance(adapter, RealStorageAdapter)
    assert adapter._rpc_url == "https://evmrpc-testnet.0g.ai"
    assert adapter._indexer_url == "https://indexer-storage-testnet-turbo.0g.ai"
    assert adapter._service_url == "http://localhost:7878"
