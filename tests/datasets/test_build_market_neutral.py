"""Reproducibility check for the market-neutral canonical-dataset builder.

PR #6 step 7. Same input parquets must produce byte-identical
``canonical_dataset.json`` across runs — same Merkle roots, same
public/private split, same on-disk bytes. Without this guarantee a
silent change in the builder (e.g. an unsorted dict somewhere)
would produce different commitments on different machines, breaking
verification end-to-end.

Skips when the source parquets aren't on disk (CI doesn't pull
them — ``data/`` is gitignored). Local devs run with the parquets
populated per ``scripts/datasets/README.md``.
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "scripts" / "datasets" / "build_market_neutral.py"
OUT_JSON = (
    REPO_ROOT
    / "lockstep"
    / "domains"
    / "trading"
    / "market_neutral"
    / "canonical_dataset.json"
)
RAW_INPUTS = (
    REPO_ROOT / "data" / "raw" / "binance" / "candles_spot" / "AVAX_1h.parquet",
    REPO_ROOT / "data" / "raw" / "hyperliquid" / "candles_perp" / "AVAX_1h.parquet",
    REPO_ROOT / "data" / "raw" / "hyperliquid" / "funding_rates" / "AVAX.parquet",
)


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_builder() -> None:
    subprocess.run(  # noqa: S603 — pytest-controlled invocation
        [sys.executable, str(BUILDER)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO_ROOT),
    )


def test_market_neutral_builder_is_reproducible() -> None:
    missing = [p for p in RAW_INPUTS if not p.exists()]
    if missing:
        pytest.skip(
            "skipping reproducibility test — missing input parquets: "
            + ", ".join(str(p.relative_to(REPO_ROOT)) for p in missing)
            + ". Pull via `scripts/datasets/README.md` to run locally."
        )

    # Save the current canonical_dataset.json so a failed test doesn't
    # leave the working copy in a half-built state. Track the
    # pre-test sha so we can also assert the rebuilt bytes match the
    # committed copy — catches the "I changed the builder but forgot
    # to regenerate the JSON" drift case before validators do.
    backup = OUT_JSON.read_bytes() if OUT_JSON.exists() else None
    backup_sha = (
        hashlib.sha256(backup).hexdigest() if backup is not None else None
    )
    try:
        _run_builder()
        first = _sha256(OUT_JSON)
        _run_builder()
        second = _sha256(OUT_JSON)
        assert first == second, (
            "two invocations of build_market_neutral.py produced different "
            f"canonical_dataset.json bytes:\n  first  sha256 = {first}\n"
            f"  second sha256 = {second}\n"
            "investigate the builder for nondeterminism (unsorted dicts, "
            "iteration order, time.time() leaking into output, etc.)"
        )
        if backup_sha is not None:
            assert first == backup_sha, (
                "rebuilt canonical_dataset.json differs from the committed copy:\n"
                f"  committed sha256 = {backup_sha}\n"
                f"  rebuilt   sha256 = {first}\n"
                "regenerate (uv run python scripts/datasets/build_market_neutral.py) "
                "and commit, or investigate the diff."
            )
    finally:
        if backup is not None:
            OUT_JSON.write_bytes(backup)
        elif OUT_JSON.exists():
            # Test ran on a clean clone (no prior canonical_dataset.json);
            # remove the freshly-built file so the working tree matches
            # the pre-test state.
            OUT_JSON.unlink()
