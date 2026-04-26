"""Integration test: run examples/demo_flow.py as a subprocess.

This is the safety net for end-to-end behavior. If anything in the
substrate adapters, the trading domains, or the demo flow itself
regresses, this test catches it before the rest of the suite has to.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
DEMO_PATH = REPO_ROOT / "examples" / "demo_flow.py"


@pytest.fixture(scope="module")
def demo_output() -> str:
    """Run the demo once per module and cache output for the assertions."""
    result = subprocess.run(  # noqa: S603 — pytest-controlled invocation
        [sys.executable, str(DEMO_PATH)],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(REPO_ROOT),
    )
    if result.returncode != 0:
        pytest.fail(
            "demo_flow exited non-zero\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result.stdout


def test_demo_exits_zero(demo_output: str) -> None:
    # demo_output fixture already raised if exit was non-zero.
    assert demo_output != ""


def test_demo_prints_each_step_section(demo_output: str) -> None:
    expected_sections = [
        "Step 1 — Initialize Mock substrate adapters",
        "Step 2 — Register both trading evaluations",
        "Step 3 — Build datasets and upload to storage",
        "Step 4 — Grade reference strategies and mint iNFTs",
        "Step 5 — Marketplace state",
        "Step 6 — Validator pass",
        "Step 7 — Rental",
        "Demo complete",
    ]
    missing = [s for s in expected_sections if s not in demo_output]
    assert not missing, f"demo output missing sections: {missing}"


def test_demo_mints_five_inft_tokens(demo_output: str) -> None:
    # Three directional + two market-neutral = five tokens.
    minted_lines = [line for line in demo_output.splitlines() if "token_id=" in line]
    assert len(minted_lines) >= 5, (
        f"expected at least 5 minted iNFTs, got {len(minted_lines)}: {minted_lines}"
    )


def test_demo_emits_live_execution_receipt(demo_output: str) -> None:
    assert "LIVE_EXECUTION receipt" in demo_output
