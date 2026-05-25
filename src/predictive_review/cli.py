"""Predictive Review CLI.

The CLI is the v1 production wiring for the application: it stands in
for the FastAPI web layer until that exists, and it remains useful for
non-interactive automation afterwards (scripted dogfooding, regression
runs, etc.).

Commands are intentionally narrow. `prep init-db` brings a fresh DB to
the current migration head. `prep run` walks a single full session
end-to-end.
"""

from __future__ import annotations

from pathlib import Path

import click


@click.group()
def cli() -> None:
    """Predictive Review — prior elicitation with reconciliation."""


@cli.command("init-db")
@click.option(
    "--alembic-config",
    "alembic_config",
    type=click.Path(exists=True, dir_okay=False),
    default="alembic.ini",
    show_default=True,
    help="Path to alembic.ini. Defaults to alembic.ini in the current directory.",
)
def init_db(alembic_config: str) -> None:
    """Apply Alembic migrations to bring the database to head."""
    from alembic import command
    from alembic.config import Config

    cfg_path = Path(alembic_config).resolve()
    if not cfg_path.exists():
        raise click.UsageError(
            f"alembic config not found at {cfg_path}. "
            "Run from the project root, or pass --alembic-config."
        )
    cfg = Config(str(cfg_path))
    command.upgrade(cfg, "head")
    click.echo(f"applied migrations using {cfg_path}")
