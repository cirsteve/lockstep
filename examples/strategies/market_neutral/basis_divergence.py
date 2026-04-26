"""Basis-divergence market-neutral strategy.

Uses the basis (perp - spot) rather than funding rate as the trigger.
When perp trades above spot by more than ``BASIS_BAND_BPS``, short
perp + long spot expecting convergence. Symmetrically the other way.
Pairs the legs to keep net direction near zero.

The threshold is in basis points (``basis / spot * 10000``) rather
than raw USD so the strategy is asset-price independent — same
threshold works for AVAX (~$30) and BTC (~$100k).
"""

BASIS_BAND_BPS = 10.0
SIZE_PER_BAR = 1.0


def signal(funding_window, basis_window, state):
    window = basis_window if basis_window else funding_window
    if not window:
        return {
            "spot": {"direction": "flat", "size": 0.0},
            "perp": {"direction": "flat", "size": 0.0},
        }
    last = window[-1]
    # Prefer the precomputed basis_bps field; fall back to deriving it
    # from basis + spot_close so older / synthetic datasets without
    # basis_bps still trigger the strategy. Default 0 only if neither
    # the precomputed field nor the source fields are present.
    basis_bps = last.get("basis_bps")
    if basis_bps is None:
        basis = last.get("basis", 0.0)
        spot_close = last.get("spot_close", 0.0)
        basis_bps = (basis / spot_close * 10_000) if spot_close else 0.0
    if basis_bps > BASIS_BAND_BPS:
        return {
            "spot": {"direction": "long", "size": SIZE_PER_BAR},
            "perp": {"direction": "short", "size": SIZE_PER_BAR},
        }
    if basis_bps < -BASIS_BAND_BPS:
        return {
            "spot": {"direction": "short", "size": SIZE_PER_BAR},
            "perp": {"direction": "long", "size": SIZE_PER_BAR},
        }
    return {
        "spot": {"direction": "flat", "size": 0.0},
        "perp": {"direction": "flat", "size": 0.0},
    }
