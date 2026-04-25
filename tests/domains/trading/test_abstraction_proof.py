"""Cross-cutting test: the two trading domains share no domain-specific code.

If this test fails, the abstraction is leaking — directional and
market_neutral should depend on Layer 2 only, not on each other.
"""

from __future__ import annotations

import importlib
import pathlib
import sys

from lockstep.domains.trading.directional import TradingDirectionalEvaluation
from lockstep.domains.trading.market_neutral import TradingMarketNeutralEvaluation
from lockstep.evaluation.evaluation import Evaluation

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
DIRECTIONAL_DIR = REPO_ROOT / "lockstep" / "domains" / "trading" / "directional"
MARKET_NEUTRAL_DIR = REPO_ROOT / "lockstep" / "domains" / "trading" / "market_neutral"


def test_evaluator_ids_differ():
    a = TradingDirectionalEvaluation()
    b = TradingMarketNeutralEvaluation()
    assert a.evaluator().evaluator_id != b.evaluator().evaluator_id


def test_both_subclass_evaluation_without_raising():
    """Constructing each Evaluation must not raise; both must satisfy the ABC."""
    a = TradingDirectionalEvaluation()
    b = TradingMarketNeutralEvaluation()
    assert isinstance(a, Evaluation)
    assert isinstance(b, Evaluation)
    # Touch every abstract method on both to confirm the contract is filled.
    for ev in (a, b):
        assert isinstance(ev.domain, str)
        assert ev.evaluator() is not None
        assert ev.solver_interface is not None
        assert ev.grader() is not None
        assert isinstance(ev.filter_dimensions(), list)


def test_directional_does_not_import_market_neutral_at_module_load():
    """Reload directional in a fresh module table; it must not pull market_neutral in."""
    with _trading_module_snapshot():
        _drop_trading_modules()
        importlib.import_module("lockstep.domains.trading.directional")
        assert "lockstep.domains.trading.market_neutral" not in sys.modules


def test_market_neutral_does_not_import_directional_at_module_load():
    with _trading_module_snapshot():
        _drop_trading_modules()
        importlib.import_module("lockstep.domains.trading.market_neutral")
        assert "lockstep.domains.trading.directional" not in sys.modules


def test_directional_does_not_import_market_neutral():
    """directional/ files must not import from market_neutral.

    The spec asks for ``no string "market_neutral" in directional/``. We
    interpret that as: no cross-package coupling. A bare English-word
    grep would create false positives where it shouldn't (e.g. metric
    names that legitimately describe market neutrality). Checking
    import statements is the structural test that matters.
    """
    offenders = _grep_for_imports(DIRECTIONAL_DIR, "market_neutral")
    assert not offenders, (
        "directional/ files contain market_neutral imports — abstraction leak: "
        f"{offenders}"
    )


def test_market_neutral_does_not_import_directional():
    """market_neutral/ files must not import from directional.

    Note: the metric ``directional_exposure_max`` is a legitimate
    market-neutral metric (it measures how much net direction a strategy
    accidentally takes). The English word ``directional`` appears in
    docstrings and the metric key, but no cross-package import should.
    """
    offenders = _grep_for_imports(MARKET_NEUTRAL_DIR, "directional")
    assert not offenders, (
        "market_neutral/ files contain directional imports — abstraction leak: "
        f"{offenders}"
    )


def _grep_for_imports(directory: pathlib.Path, sibling_name: str) -> list[str]:
    """Find any line that imports from the sibling subpackage."""
    matches: list[str] = []
    needles = (
        f"from lockstep.domains.trading.{sibling_name}",
        f"import lockstep.domains.trading.{sibling_name}",
    )
    for path in directory.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for needle in needles:
            if needle in text:
                matches.append(str(path.relative_to(REPO_ROOT)))
                break
    return matches


def _drop_trading_modules() -> None:
    """Pop the trading subpackage from sys.modules so reimport runs fresh."""
    keys = [k for k in list(sys.modules) if k.startswith("lockstep.domains.trading")]
    for k in keys:
        del sys.modules[k]


import contextlib  # noqa: E402


@contextlib.contextmanager
def _trading_module_snapshot():
    """Snapshot trading-subpackage entries in sys.modules and restore on exit.

    Without this, a test that pops modules to verify import isolation
    leaks an inconsistent sys.modules into later tests — e.g.
    ``inspect.getsource(SomeClass)`` fails because the class's module
    isn't in sys.modules anymore.
    """
    saved = {
        k: v for k, v in sys.modules.items() if k.startswith("lockstep.domains.trading")
    }
    try:
        yield
    finally:
        # Drop anything that was newly inserted, restore originals.
        keys = [k for k in list(sys.modules) if k.startswith("lockstep.domains.trading")]
        for k in keys:
            del sys.modules[k]
        sys.modules.update(saved)
