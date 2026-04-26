"""Trading vertical: two distinct evaluation contracts share the substrate.

``directional`` and ``market_neutral`` are intentionally siblings with no
cross-imports. If you find yourself importing one from the other, the
abstraction is leaking — push shared code up to ``lockstep.evaluation``
or down to a small helper module that lives outside both subpackages.
"""
