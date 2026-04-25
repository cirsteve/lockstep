"""Momentum directional strategy.

Compares the most recent close to the close N bars ago. If up, go long
proportional to the magnitude (capped); if down, go short. Strong in
trend regimes (bull, bear), struggles in vol_spike.
"""

LOOKBACK = 8
SCALE = 50.0  # converts log-ish return to size in [0, 1]


def signal(window, state):
    if len(window) < LOOKBACK + 1:
        return {"direction": "flat", "size": 0.0}
    last = window[-1]["close"]
    past = window[-1 - LOOKBACK]["close"]
    if past == 0:
        return {"direction": "flat", "size": 0.0}
    change = (last - past) / past
    size = min(1.0, max(0.0, abs(change) * SCALE))
    if size == 0.0:
        return {"direction": "flat", "size": 0.0}
    if change > 0:
        return {"direction": "long", "size": size}
    return {"direction": "short", "size": size}
