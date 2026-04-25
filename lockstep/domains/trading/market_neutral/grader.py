"""MarketNeutralGrader — funding-capture and basis behavior under deterministic accumulation.

Methodology
-----------
Per bar t:
  - Solver decides paired (spot, perp) positions from history through t-1.
  - Net P&L for the bar is:
        spot_pos * spot_return + perp_pos * perp_return
        + funding_paid_or_received (only when perp_pos != 0)
        - slippage on legs that changed
  - Funding accrual: short perp earns positive funding when rate > 0;
    long perp pays it. Each bar's funding payment is
    ``-perp_pos * funding_rate`` (per the 8-hour convention divided
    by 8 to apply hourly).
  - Slippage: 5 bps on |spot_pos - prev_spot_pos| + |perp_pos - prev_perp_pos|.

Metrics
-------
- ``funding_capture_rate``: fraction of theoretical funding actually
  captured. Theoretical = sum over bars of |funding_rate| * 1.0 (full
  short-perp position). Captured = sum of realized funding income.
- ``basis_dislocation_max``: max |basis| across bars while in any position.
- ``net_market_neutral_pnl``: cumulative net P&L expressed as % of notional.
- per-regime Sharpe: Sharpe restricted to bars within each funding regime.
- ``trade_count``: number of bars where any leg's signed position changed.
- ``directional_exposure_max``: max absolute |spot_pos + perp_pos| —
  the metric that punishes strategies that drift away from neutrality.

Determinism rules match the directional grader: Decimal accumulation,
``Decimal.sqrt`` for the annualization factor, single rounding at the
boundary.
"""

from __future__ import annotations

import hashlib
import inspect
from collections.abc import Iterable
from decimal import Decimal, getcontext
from typing import Any

from lockstep.evaluation.canonical import Bytes32Hex
from lockstep.evaluation.evaluation import Grader

from .dataset import VALID_REGIMES, MarketNeutralDataset
from .solution import MarketNeutralSolution

getcontext().prec = 50


_SLIPPAGE_BPS = Decimal("0.0005")
_HOURLY_ANNUALIZATION = (Decimal(24) * Decimal(365)).sqrt()
_FUNDING_PER_HOUR = Decimal(1) / Decimal(8)  # 8-hr funding spread hourly
_SIG_FIGS = 12

_DIRECTION_SIGN = {"long": Decimal(1), "short": Decimal(-1), "flat": Decimal(0)}


def _to_decimal(x: float | int | Decimal) -> Decimal:
    if isinstance(x, Decimal):
        return x
    return Decimal(repr(float(x)))


def _signed_position(leg: dict[str, Any]) -> Decimal:
    direction = leg.get("direction", "flat")
    if direction not in _DIRECTION_SIGN:
        raise ValueError(f"unknown direction: {direction!r}")
    raw_size = leg.get("size", 0.0)
    size = _to_decimal(raw_size)
    if size < 0:
        size = Decimal(0)
    if size > 1:
        size = Decimal(1)
    return _DIRECTION_SIGN[direction] * size


def _round_sig(value: Decimal, sig_figs: int = _SIG_FIGS) -> float:
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


class MarketNeutralGrader(Grader[MarketNeutralSolution, MarketNeutralDataset]):
    """Funding-capture grader for market-neutral strategies."""

    @classmethod
    def version(cls) -> Bytes32Hex:
        source = inspect.getsource(cls).encode("utf-8")
        return "0x" + hashlib.sha256(source).hexdigest()

    def grade_public(
        self,
        solution: MarketNeutralSolution,
        public_dataset: MarketNeutralDataset,
    ) -> dict[str, float]:
        return self._grade_bars(solution, public_dataset.public_bars)

    def grade_full(
        self,
        solution: MarketNeutralSolution,
        full_dataset: MarketNeutralDataset,
    ) -> dict[str, float]:
        return self._grade_bars(solution, full_dataset.all_bars())

    def _grade_bars(
        self,
        solution: MarketNeutralSolution,
        bars: Iterable[dict],
    ) -> dict[str, float]:
        bars_t = tuple(bars)
        signal = solution.instantiate()

        per_step_returns: list[Decimal] = []
        per_step_regimes: list[str] = []
        captured_funding = Decimal(0)
        theoretical_funding = Decimal(0)
        basis_dislocation_max = Decimal(0)
        directional_exposure_max = Decimal(0)
        running = Decimal(0)
        prev_spot = Decimal(0)
        prev_perp = Decimal(0)
        trade_count = 0
        state: dict[str, Any] = {}

        for idx in range(1, len(bars_t)):
            history_funding = list(bars_t[:idx])  # acts as both windows for the mock dataset
            history_basis = list(bars_t[:idx])
            decision = signal(history_funding, history_basis, state)
            spot_leg = decision.get("spot", {"direction": "flat", "size": 0.0})
            perp_leg = decision.get("perp", {"direction": "flat", "size": 0.0})
            spot_pos = _signed_position(spot_leg)
            perp_pos = _signed_position(perp_leg)

            prev_bar = bars_t[idx - 1]
            curr_bar = bars_t[idx]
            spot_prev = _to_decimal(prev_bar["spot_close"])
            spot_curr = _to_decimal(curr_bar["spot_close"])
            perp_prev = _to_decimal(prev_bar["perp_close"])
            perp_curr = _to_decimal(curr_bar["perp_close"])

            spot_ret = (spot_curr - spot_prev) / spot_prev if spot_prev != 0 else Decimal(0)
            perp_ret = (perp_curr - perp_prev) / perp_prev if perp_prev != 0 else Decimal(0)

            funding_rate = _to_decimal(curr_bar["funding_rate"])
            # short perp earns funding when funding > 0; long perp pays it.
            funding_pnl = -perp_pos * funding_rate * _FUNDING_PER_HOUR
            captured_funding += funding_pnl
            theoretical_funding += abs(funding_rate) * _FUNDING_PER_HOUR

            slippage = (
                abs(spot_pos - prev_spot) + abs(perp_pos - prev_perp)
            ) * _SLIPPAGE_BPS
            step_pnl = spot_pos * spot_ret + perp_pos * perp_ret + funding_pnl - slippage

            per_step_returns.append(step_pnl)
            per_step_regimes.append(curr_bar["regime"])
            running += step_pnl

            # Track basis dislocation while in any position.
            if spot_pos != 0 or perp_pos != 0:
                basis_now = abs(_to_decimal(curr_bar["basis"]))
                if basis_now > basis_dislocation_max:
                    basis_dislocation_max = basis_now

            net_directional = abs(spot_pos + perp_pos)
            if net_directional > directional_exposure_max:
                directional_exposure_max = net_directional

            if spot_pos != prev_spot or perp_pos != prev_perp:
                trade_count += 1
            prev_spot = spot_pos
            prev_perp = perp_pos

        per_regime_returns: dict[str, list[Decimal]] = {r: [] for r in VALID_REGIMES}
        for r, regime in zip(per_step_returns, per_step_regimes, strict=True):
            if regime in per_regime_returns:
                per_regime_returns[regime].append(r)
        per_regime_sharpe = {
            regime: _sharpe(returns) for regime, returns in per_regime_returns.items()
        }

        if theoretical_funding > 0:
            funding_capture_rate = captured_funding / theoretical_funding
        else:
            funding_capture_rate = Decimal(0)

        return {
            "funding_capture_rate": _round_sig(funding_capture_rate),
            "basis_dislocation_max": _round_sig(basis_dislocation_max),
            "net_market_neutral_pnl": _round_sig(running),
            "funding_positive_sharpe": _round_sig(
                per_regime_sharpe.get("funding_positive", Decimal(0))
            ),
            "funding_negative_sharpe": _round_sig(
                per_regime_sharpe.get("funding_negative", Decimal(0))
            ),
            "funding_neutral_sharpe": _round_sig(
                per_regime_sharpe.get("funding_neutral", Decimal(0))
            ),
            "trade_count": float(trade_count),
            "directional_exposure_max": _round_sig(directional_exposure_max),
        }
