"""Naive funding-capture strategy.

Whenever the latest funding rate is above threshold, short perp + long
spot to collect funding. When below negative threshold, long perp +
short spot. Otherwise flat.

Self-contained: no imports.
"""

THRESHOLD = 0.0001


def signal(funding_window, basis_window, state):
    if not funding_window:
        return {
            "spot": {"direction": "flat", "size": 0.0},
            "perp": {"direction": "flat", "size": 0.0},
        }
    last = funding_window[-1]
    rate = last.get("funding_rate", 0.0)
    if rate > THRESHOLD:
        return {
            "spot": {"direction": "long", "size": 1.0},
            "perp": {"direction": "short", "size": 1.0},
        }
    if rate < -THRESHOLD:
        return {
            "spot": {"direction": "short", "size": 1.0},
            "perp": {"direction": "long", "size": 1.0},
        }
    return {
        "spot": {"direction": "flat", "size": 0.0},
        "perp": {"direction": "flat", "size": 0.0},
    }
