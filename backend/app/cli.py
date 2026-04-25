"""CLI utilities. Exposed as `second-brain` console script."""

from __future__ import annotations

import getpass
import os
import sys
from pathlib import Path

import click

from app.auth.passwords import hash_password
from app.config import get_settings
from app.db.connection import open_connection
from app.db.migrations import run_migrations


@click.group()
@click.option(
    "--config",
    "-c",
    "config_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to config.yml (overrides the CONFIG_PATH env var; default: ./config.yml).",
)
def cli(config_path: str | None) -> None:
    """Second Brain administrative CLI."""
    if config_path is not None:
        os.environ["CONFIG_PATH"] = config_path
        # Clear the lru_cache in case anything earlier in the process already
        # called get_settings() — keeps the override authoritative.
        get_settings.cache_clear()


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


@cli.command("chatgpt-login")
@click.argument("provider", required=False)
@click.option(
    "--data-dir",
    "data_dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help=(
        "Override where the OAuth token file is written. Defaults to the "
        "`app.data_dir` from config.yml. Use this when running the login on "
        "the host while the app runs in a container — pass the host-side "
        "directory that's bind-mounted into the container's data_dir."
    ),
)
def chatgpt_login_cmd(provider: str | None, data_dir: Path | None) -> None:
    """Authenticate a `kind: chatgpt` provider via the OAuth device flow.

    PROVIDER is the name of the entry under `llm.providers` in config.yml. If
    omitted and exactly one chatgpt provider is configured, that one is used.
    """
    from app.llm.chatgpt_auth import login_device_flow

    settings = get_settings()
    chatgpt_names = [
        name for name, cfg in settings.llm.providers.items() if cfg.kind == "chatgpt"
    ]
    if not chatgpt_names:
        click.echo(
            "No `kind: chatgpt` provider is configured in config.yml. "
            "Add one (see config.example.yml) and rerun.",
            err=True,
        )
        sys.exit(1)

    if provider is None:
        if len(chatgpt_names) > 1:
            click.echo(
                "Multiple chatgpt providers are configured: "
                f"{', '.join(chatgpt_names)}. Specify one explicitly.",
                err=True,
            )
            sys.exit(1)
        provider = chatgpt_names[0]
    elif provider not in chatgpt_names:
        click.echo(
            f"'{provider}' is not a configured chatgpt provider. "
            f"Known: {', '.join(chatgpt_names)}",
            err=True,
        )
        sys.exit(1)

    login_device_flow(provider, data_dir=data_dir)


if __name__ == "__main__":
    cli()
