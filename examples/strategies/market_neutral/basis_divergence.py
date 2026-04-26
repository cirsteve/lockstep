"""Basis-divergence market-neutral strategy.

Uses the basis (perp - spot) rather than funding rate as the trigger.
When perp trades above spot by more than ``BASIS_BAND``, short perp +
long spot expecting convergence. Symmetrically the other way. Pairs
the legs to keep net direction near zero.
"""

BASIS_BAND = 0.4
SIZE_PER_BAR = 1.0


def signal(funding_window, basis_window, state):
    window = basis_window if basis_window else funding_window
    if not window:
        return {
            "spot": {"direction": "flat", "size": 0.0},
            "perp": {"direction": "flat", "size": 0.0},
        }
    last = window[-1]
    basis = last.get("basis", 0.0)
    if basis > BASIS_BAND:
        return {
            "spot": {"direction": "long", "size": SIZE_PER_BAR},
            "perp": {"direction": "short", "size": SIZE_PER_BAR},
        }
    if basis < -BASIS_BAND:
        return {
            "spot": {"direction": "short", "size": SIZE_PER_BAR},
            "perp": {"direction": "long", "size": SIZE_PER_BAR},
        }
    return {
        "spot": {"direction": "flat", "size": 0.0},
        "perp": {"direction": "flat", "size": 0.0},
    }
