"""Git guard around every Obsidian mutation.

Workflow (PROJECT.md §6):
  1. If the working tree is dirty (Obsidian Sync wrote during a chat), commit
     those changes first under "external changes [auto]" so we never overwrite
     unsaved user edits.
  2. `git pull --rebase`. On conflict, abort and surface a structured error.
  3. Run the caller's mutation.
  4. `git add` the changed paths, `git commit`, `git push`. On non-fast-forward
     push rejection, retry once with another pull-rebase.

A single asyncio.Lock serializes all writes across the process — atomicity
per high-level operation (e.g. a 30-note Organize pass commits as 30 commits
because the caller releases / re-acquires per-note, but a single
`vault.write` is one round-trip).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.config import get_settings

from .paths import vault_root

log = logging.getLogger(__name__)


class GitConflictError(RuntimeError):
    """git pull --rebase produced a conflict; user must resolve manually."""


def _run(cmd: list[str], cwd: Path, *, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    import os

    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    log.debug("git: %s (cwd=%s)", " ".join(cmd), cwd)
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        env=env,
    )


def _ssh_env() -> dict[str, str]:
    """If a custom SSH key is configured, point GIT_SSH_COMMAND at it."""
    s = get_settings()
    key = s.obsidian.git.ssh_key_path
    if key is None:
        return {}
    return {
        "GIT_SSH_COMMAND": f"ssh -i {key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new",
    }


def _author_env() -> dict[str, str]:
    s = get_settings()
    return {
        "GIT_AUTHOR_NAME": s.obsidian.git.author_name,
        "GIT_AUTHOR_EMAIL": s.obsidian.git.author_email,
        "GIT_COMMITTER_NAME": s.obsidian.git.author_name,
        "GIT_COMMITTER_EMAIL": s.obsidian.git.author_email,
    }


class ObsidianGitGuard:
    """Single-process gatekeeper for vault mutations.

    Use as an async context manager via `async with guard.transaction(message):`.
    The body runs after a successful pre-pull, then a commit + push happens
    on exit (no-op if the body wrote nothing).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self, message: str) -> AsyncIterator[None]:
        async with self._lock:
            settings = get_settings()
            root = vault_root()

            if settings.obsidian.git.enabled:
                await asyncio.to_thread(self._pre_mutation, root)

            try:
                yield
            except Exception:
                # Mutation failed — leave the working tree as-is for the user
                # to inspect. Do NOT auto-revert.
                raise

            if settings.obsidian.git.enabled:
                await asyncio.to_thread(self._post_mutation, root, message)

    # ── git plumbing ─────────────────────────────────────────────────

    @staticmethod
    def _pre_mutation(root: Path) -> None:
        # 1. Commit any pre-existing local changes (Obsidian Sync, manual edits).
        status = _run(["git", "status", "--porcelain"], root)
        if status.returncode != 0:
            raise RuntimeError(f"git status failed: {status.stderr.strip()}")
        if status.stdout.strip():
            log.info("vault has external changes — committing under [auto]")
            add = _run(["git", "add", "-A"], root)
            if add.returncode != 0:
                raise RuntimeError(f"git add -A failed: {add.stderr.strip()}")
            cm = _run(
                ["git", "commit", "-m", "external changes [auto]"],
                root,
                env_extra=_author_env(),
            )
            if cm.returncode != 0 and "nothing to commit" not in (cm.stdout + cm.stderr):
                raise RuntimeError(f"auto-commit failed: {cm.stderr.strip()}")

        # 2. Pull --rebase from the configured remote/branch.
        s = get_settings()
        pull = _run(
            ["git", "pull", "--rebase", s.obsidian.git.remote, s.obsidian.git.branch],
            root,
            env_extra={**_ssh_env(), **_author_env()},
        )
        if pull.returncode != 0:
            # On conflict, abort the rebase and surface a structured error.
            _run(["git", "rebase", "--abort"], root)
            raise GitConflictError(
                "git pull --rebase failed:\n"
                + (pull.stderr or pull.stdout).strip()
                + "\n\nResolve manually inside the vault, then retry."
            )

    @staticmethod
    def _post_mutation(root: Path, message: str) -> None:
        # `git add -A` so deletions are tracked too.
        add = _run(["git", "add", "-A"], root)
        if add.returncode != 0:
            raise RuntimeError(f"git add -A failed: {add.stderr.strip()}")

        # Skip the commit if the working tree didn't actually change.
        diff = _run(["git", "diff", "--cached", "--quiet"], root)
        if diff.returncode == 0:
            log.debug("vault: no changes to commit (no-op transaction)")
            return

        cm = _run(["git", "commit", "-m", message], root, env_extra=_author_env())
        if cm.returncode != 0:
            raise RuntimeError(f"git commit failed: {cm.stderr.strip()}")

        s = get_settings()
        push = _run(
            ["git", "push", s.obsidian.git.remote, s.obsidian.git.branch],
            root,
            env_extra=_ssh_env(),
        )
        if push.returncode == 0:
            return

        # Non-fast-forward — pull-rebase + retry once.
        log.info("push rejected — pulling and retrying")
        pull = _run(
            ["git", "pull", "--rebase", s.obsidian.git.remote, s.obsidian.git.branch],
            root,
            env_extra={**_ssh_env(), **_author_env()},
        )
        if pull.returncode != 0:
            _run(["git", "rebase", "--abort"], root)
            raise GitConflictError(
                "post-mutation pull --rebase failed:\n"
                + (pull.stderr or pull.stdout).strip()
            )
        push2 = _run(
            ["git", "push", s.obsidian.git.remote, s.obsidian.git.branch],
            root,
            env_extra=_ssh_env(),
        )
        if push2.returncode != 0:
            raise RuntimeError(f"git push (retry) failed: {push2.stderr.strip()}")


_GUARD: ObsidianGitGuard | None = None


def get_guard() -> ObsidianGitGuard:
    global _GUARD
    if _GUARD is None:
        _GUARD = ObsidianGitGuard()
    return _GUARD
