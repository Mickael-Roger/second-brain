"""Path resolution helpers — every vault write must funnel through here.

The single guarantee: a relative path coming from a tool call, the chat
orchestrator, or an HTTP request resolves to an absolute path *strictly*
under the configured vault root. Symlinks pointing outside, traversal with
`..`, absolute paths, paths starting with the vault root prefix that escape
via symlink — all rejected.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_settings


class VaultPathError(ValueError):
    """A path is not safely contained in the vault."""


def vault_root() -> Path:
    """Configured vault root, validated."""
    s = get_settings()
    if s.obsidian.vault_path is None:
        raise RuntimeError(
            "obsidian.vault_path is not configured — add it to config.yml"
        )
    root = s.obsidian.vault_path
    if not root.is_dir():
        raise RuntimeError(f"vault path {root} is not a directory")
    return root


def resolve_vault_path(rel: str | Path) -> Path:
    """Resolve a vault-relative path to an absolute path under the vault root.

    Raises VaultPathError if the path escapes the vault.
    """
    root = vault_root().resolve()
    p = Path(rel)
    if p.is_absolute():
        # Allow absolute paths only if they're already inside the vault.
        candidate = p.resolve()
    else:
        candidate = (root / p).resolve()

    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise VaultPathError(f"path {rel!r} escapes the vault root") from exc
    return candidate


def to_relative(absolute: Path) -> str:
    """Convert an absolute path under the vault to a forward-slash relative path."""
    rel = absolute.resolve().relative_to(vault_root().resolve())
    return rel.as_posix()
