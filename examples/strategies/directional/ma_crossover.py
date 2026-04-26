"""MA-crossover directional strategy.

Long when the fast moving average crosses above the slow MA; short when
it crosses below. Trends-following baseline. Wins in bull/bear regimes,
struggles in chop where MAs cross frequently.

Self-contained: no imports — the sandbox would reject them. Allowed
builtins only.
"""

FAST_WINDOW = 6
SLOW_WINDOW = 20


def _avg(values, lookback):
    if len(values) < lookback:
        return None
    return sum(values[-lookback:]) / lookback


def signal(window, state):
    if len(window) < SLOW_WINDOW:
        return {"direction": "flat", "size": 0.0}
    closes = [bar["close"] for bar in window]
    fast = _avg(closes, FAST_WINDOW)
    slow = _avg(closes, SLOW_WINDOW)
    if fast is None or slow is None:
        return {"direction": "flat", "size": 0.0}
    if fast > slow:
        return {"direction": "long", "size": 1.0}
    if fast < slow:
        return {"direction": "short", "size": 1.0}
    return {"direction": "flat", "size": 0.0}
