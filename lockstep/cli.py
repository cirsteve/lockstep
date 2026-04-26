"""Lockstep CLI - minimal stub.

Real implementations of mint/validate/rent ship Day 4-5. For now the
subcommands print a not-yet-implemented notice and exit cleanly so that
Section 1 acceptance (``lockstep --help`` lists the three subcommands)
can pass without dragging in substrate wiring.
"""

from __future__ import annotations

import click


@click.group()
@click.version_option(package_name="lockstep")
def main() -> None:
    """Lockstep — verifiable evaluation of private computation."""


@main.command()
def mint() -> None:
    """Mint a graded solution as an ERC-7857 iNFT."""
    click.echo("not yet implemented")


@main.command()
def validate() -> None:
    """Run a validator pass over a sampled receipt."""
    click.echo("not yet implemented")


@main.command()
def rent() -> None:
    """Rent a solution and route it through the sealed executor."""
    click.echo("not yet implemented")


if __name__ == "__main__":
    main()
