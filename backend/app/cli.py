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


@cli.command("serve")
@click.option("--host", "host_override", default=None, help="Override app.host from config.yml.")
@click.option(
    "--port", "port_override", type=int, default=None, help="Override app.port from config.yml."
)
@click.option(
    "--reload", is_flag=True, default=False, help="Hot-reload on source changes (development only)."
)
@click.option("--log-level", default=None, help="Override logging.level from config.yml.")
def serve_cmd(
    host_override: str | None,
    port_override: int | None,
    reload: bool,
    log_level: str | None,
) -> None:
    """Start the HTTP server using settings from config.yml."""
    import uvicorn

    settings = get_settings()
    host = host_override or settings.app.host
    port = port_override or settings.app.port
    level = (log_level or settings.logging.level).lower()

    click.echo(f"Starting Second Brain on http://{host}:{port} (reload={reload}, level={level})")
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level=level,
    )


@cli.command("organize")
@click.option(
    "--mode",
    type=click.Choice(["dry-run", "apply"]),
    default=None,
    help="Override organize.mode for this run (default: whatever config.yml says).",
)
@click.option(
    "--no-email",
    is_flag=True,
    default=False,
    help="Skip the SMTP send. Report still prints to stdout.",
)
def organize_cmd(mode: str | None, no_email: bool) -> None:
    """Run the nightly Organize job (journal archive + LLM organize pass) right now.

    Same code path as the scheduled cron run. Useful to iterate on
    ORGANIZE.md, INDEX.md, or PREFERENCES.md without waiting until 03:00.
    """
    import asyncio

    settings = get_settings()
    if mode is not None:
        settings.organize.mode = mode  # type: ignore[assignment]
    if no_email:
        settings.smtp.enabled = False

    # Ensure the SQLite schema exists (the job records last_run_at in
    # module_state). The HTTP server normally does this in its lifespan;
    # the CLI does it here so a fresh data dir works without a separate
    # `second-brain migrate` step.
    conn = open_connection()
    try:
        run_migrations(conn)
    finally:
        conn.close()

    click.echo(
        f"Running organize (mode={settings.organize.mode}, email={settings.smtp.enabled})…",
        err=True,
    )
    from app.jobs import run_nightly

    report = asyncio.run(run_nightly())
    click.echo(report)


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
