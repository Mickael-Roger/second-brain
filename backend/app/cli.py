"""CLI utilities. Exposed as `second-brain` console script."""

from __future__ import annotations

import getpass
import sys

import click

from app.auth.passwords import hash_password
from app.db.connection import open_connection
from app.db.migrations import run_migrations


@click.group()
def cli() -> None:
    """Second Brain administrative CLI."""


@cli.command("hash-password")
def hash_password_cmd() -> None:
    """Prompt for a password and print its bcrypt hash."""
    pw1 = getpass.getpass("Password: ")
    pw2 = getpass.getpass("Confirm:  ")
    if pw1 != pw2:
        click.echo("Passwords do not match.", err=True)
        sys.exit(1)
    if len(pw1) < 8:
        click.echo("Password too short (min 8 chars).", err=True)
        sys.exit(1)
    click.echo(hash_password(pw1))


@cli.command("migrate")
def migrate_cmd() -> None:
    """Apply any pending SQL migrations to the configured database."""
    conn = open_connection()
    try:
        applied = run_migrations(conn)
    finally:
        conn.close()
    click.echo(f"Applied {applied} migration(s).")


if __name__ == "__main__":
    cli()
