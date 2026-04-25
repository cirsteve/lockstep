"""DirectionalGrader — walk-forward backtest with deterministic accumulation.

Methodology
-----------
1. Walk-forward: each window is ``(in_sample_end, out_of_sample_end)``.
   For parameterless streaming solvers this is purely organizational —
   the solver's ``state`` dict carries any fitted state across bars.
2. Per-step P&L: long earns close-over-close return; short earns the
   inverse; flat earns zero. Slippage of 5 basis points is charged on
   the change in (signed) position size between consecutive bars.
3. Per-regime aggregation: every bar carries a regime label; per-regime
   P&L series is the subset where that regime applies.
4. Per-regime metrics: Sharpe (annualized for hourly bars by
   ``sqrt(24*365)``) and max drawdown.
5. Aggregate metrics: same computations across the full P&L series.

Determinism
-----------
- All accumulation in ``decimal.Decimal`` with ``getcontext().prec = 50``.
- ``Decimal.sqrt`` for variance and the annualization factor.
- Exactly one rounding step at the boundary: convert each metric to a
  float by quantizing to 12 significant digits.
- No ``numpy.sum``, no ``pandas.groupby().agg()``, no parallelism, no
  RNG anywhere on the grading path.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterable
from decimal import Decimal, getcontext
from typing import Any

from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.evaluation import Grader

from .dataset import VALID_REGIMES, DirectionalDataset
from .solution import DirectionalSolution

getcontext().prec = 50


_SLIPPAGE_BPS = Decimal("0.0005")  # 5 bps per unit of position change
_HOURLY_ANNUALIZATION = (Decimal(24) * Decimal(365)).sqrt()
_SIG_FIGS = 12


_DIRECTION_SIGN = {"long": Decimal(1), "short": Decimal(-1), "flat": Decimal(0)}


def _to_decimal(x: float | int | Decimal) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(repr(float(x)))


def _signed_position(decision: dict[str, Any]) -> Decimal:
    direction = decision.get("direction", "flat")
    if direction not in _DIRECTION_SIGN:
        raise ValueError(f"unknown direction: {direction!r}")
    raw_size = decision.get("size", 0.0)
    size = _to_decimal(raw_size)
    if size < 0:
        size = Decimal(0)
    if size > 1:
        size = Decimal(1)
    return _DIRECTION_SIGN[direction] * size


def _round_sig(value: Decimal, sig_figs: int = _SIG_FIGS) -> float:
    """Round a Decimal to ``sig_figs`` significant digits and return as float.

    The single Decimal→float boundary. Done as one rounding step.
    """
    if value == 0:
        return 0.0
    quant = Decimal(10) ** (value.adjusted() - sig_figs + 1)
    rounded = value.quantize(quant)
    return float(rounded)


def _sharpe(returns: list[Decimal]) -> Decimal:
    if len(returns) < 2:
        return Decimal(0)
    n = Decimal(len(returns))
    mean = sum(returns, Decimal(0)) / n
    variance = sum(((r - mean) ** 2 for r in returns), Decimal(0)) / (n - 1)
    if variance <= 0:
        return Decimal(0)
    stddev = variance.sqrt()
    return (mean / stddev) * _HOURLY_ANNUALIZATION


def _max_drawdown(cumulative: list[Decimal]) -> Decimal:
    """Peak-to-trough drawdown on a cumulative-P&L series. Non-negative magnitude."""
    if not cumulative:
        return Decimal(0)
    peak = cumulative[0]
    worst = Decimal(0)
    for v in cumulative:
        if v > peak:
            peak = v
        drawdown = peak - v
        if drawdown > worst:
            worst = drawdown
    return worst


class DirectionalGrader(Grader[DirectionalSolution, DirectionalDataset]):
    """Walk-forward backtest grader for directional perp strategies."""

    @classmethod
    def version(cls) -> Bytes32Hex:
        source = inspect.getsource(cls).encode("utf-8")
        return "0x" + hashlib.sha256(source).hexdigest()

    def grade_public(
        self,
        solution: DirectionalSolution,
        public_dataset: DirectionalDataset,
    ) -> dict[str, float]:
        return self._grade_bars(solution, public_dataset.public_bars)

    def grade_full(
        self,
        solution: DirectionalSolution,
        full_dataset: DirectionalDataset,
    ) -> dict[str, float]:
        return self._grade_bars(solution, full_dataset.all_bars())

    def _grade_bars(
        self,
        solution: DirectionalSolution,
        bars: Iterable[dict],
    ) -> dict[str, float]:
        bars_t = tuple(bars)
        signal = solution.instantiate()

        per_step_returns: list[Decimal] = []
        per_step_regimes: list[str] = []
        cumulative_pnl: list[Decimal] = []
        running = Decimal(0)
        prev_position = Decimal(0)
        trade_count = 0
        wins = 0
        position_bars = 0
        state: dict[str, Any] = {}

        # Walk chronologically; per bar t, decide based on history through
        # bar t-1, realize the close-over-close return between t-1 and t.
        for idx in range(1, len(bars_t)):
            history = list(bars_t[:idx])
            window = history  # streaming: solver gets full history; uses only what it wants
            decision = signal(window, state)
            position = _signed_position(decision)

            prev_close = _to_decimal(bars_t[idx - 1]["close"])
            curr_close = _to_decimal(bars_t[idx]["close"])
            if prev_close == 0:
                ret = Decimal(0)
            else:
                ret = (curr_close - prev_close) / prev_close

            slippage = abs(position - prev_position) * _SLIPPAGE_BPS
            step_pnl = position * ret - slippage

            per_step_returns.append(step_pnl)
            per_step_regimes.append(bars_t[idx]["regime"])
            running += step_pnl
            cumulative_pnl.append(running)

            if position != prev_position:
                trade_count += 1
            if position != 0:
                position_bars += 1
                if step_pnl > 0:
                    wins += 1
            prev_position = position

        # Aggregate metrics
        agg_sharpe = _sharpe(per_step_returns)
        max_dd = _max_drawdown(cumulative_pnl)

        # Per-regime metrics
        per_regime_returns: dict[str, list[Decimal]] = {r: [] for r in VALID_REGIMES}
        for ret_, regime in zip(per_step_returns, per_step_regimes, strict=True):
            if regime in per_regime_returns:
                per_regime_returns[regime].append(ret_)

        per_regime_sharpe: dict[str, Decimal] = {
            regime: _sharpe(returns) for regime, returns in per_regime_returns.items()
        }

        worst = min(per_regime_sharpe.values()) if per_regime_sharpe else Decimal(0)

        win_rate = (
            Decimal(wins) / Decimal(position_bars) if position_bars > 0 else Decimal(0)
        )

        return {
            "worst_regime_sharpe": _round_sig(worst),
            "aggregate_sharpe": _round_sig(agg_sharpe),
            "max_drawdown": _round_sig(max_dd),
            "bull_sharpe": _round_sig(per_regime_sharpe.get("bull", Decimal(0))),
            "bear_sharpe": _round_sig(per_regime_sharpe.get("bear", Decimal(0))),
            "chop_sharpe": _round_sig(per_regime_sharpe.get("chop", Decimal(0))),
            "vol_spike_sharpe": _round_sig(per_regime_sharpe.get("vol_spike", Decimal(0))),
            "trade_count": float(trade_count),
            "win_rate": _round_sig(win_rate),
        }
