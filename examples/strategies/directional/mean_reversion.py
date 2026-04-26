"""Mean-reversion directional strategy.

Computes a short-window MA. If the latest close is below MA by more
than ``THRESHOLD``, go long expecting reversion; if above, go short.
Wins in chop, struggles in strong trends.
"""

WINDOW = 12
THRESHOLD = 0.001  # 10 bps from MA before taking a position


def signal(window, state):
    if len(window) < WINDOW:
        return {"direction": "flat", "size": 0.0}
    closes = [bar["close"] for bar in window[-WINDOW:]]
    ma = sum(closes) / WINDOW
    last = window[-1]["close"]
    if ma == 0:
        return {"direction": "flat", "size": 0.0}
    deviation = (last - ma) / ma
    if deviation > THRESHOLD:
        return {"direction": "short", "size": 1.0}
    if deviation < -THRESHOLD:
        return {"direction": "long", "size": 1.0}
    return {"direction": "flat", "size": 0.0}
