"""APScheduler bootstrap.

Single AsyncIOScheduler per process. The nightly job runs `run_nightly()`,
which:
  1. Pre-flight: commits + pushes any uncommitted vault changes so the
     run starts from a clean baseline.
  2. Inside a `batch_session()` (suppresses per-primitive git IO):
     archives prior daily journals, runs the Organize pass.
  3. Captures `git diff --stat` of the run's working-tree changes.
  4. Apply mode → bulk-commits + pushes; dry-run → stashes.
  5. Sends a heartbeat email summarising what happened.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.vault.guard import (
    batch_session,
    capture_head,
    commit_and_push,
    diff_names,
    diff_stat,
    get_guard,
    stash,
)

from .journal_archive import ArchiveResult, run_journal_archive
from .organize import OrganizeResult, run_organize

log = logging.getLogger(__name__)

_SCHEDULER: AsyncIOScheduler | None = None


def _format_archive_section(archive: ArchiveResult) -> str:
    lines = [
        "## Journal archive",
        f"Moved:   {archive.moved}",
        f"Skipped: {archive.skipped}",
    ]
    if archive.paths:
        lines.append("")
        lines.append("Archived paths:")
        lines.extend(f"  - {p}" for p in archive.paths)
    if archive.errors:
        lines.append("")
        lines.append("Errors:")
        lines.extend(f"  - {e}" for e in archive.errors)
    return "\n".join(lines)


async def run_nightly(*, since: timedelta | None = None) -> str:
    """Execute the nightly job: archive prior days, run the Organize pass,
    finalise (commit or stash), email the combined report.

    `since`, when set, restricts the Organize pass to files modified
    within that window (overriding the per-note last_reviewed_at scope
    for this run). Used by the `second-brain organize --since …` CLI
    flag — cron runs leave it unset and use the default freshness logic.

    Both modes (`dry-run` and `apply`) actually mutate the working tree.
    The mode only decides what happens to those changes at the end:
      - `apply`   → bulk commit + push (one commit per nightly run).
      - `dry-run` → `git stash push` so the working tree returns to the
                    pre-run state, with the proposed changes recoverable
                    via `git stash pop`.
    """
    log.info("nightly job: starting")
    settings = get_settings()
    mode = settings.organize.mode

    # ── Pre-flight ───────────────────────────────────────────────────
    # Always commit + push any uncommitted vault changes BEFORE we
    # start. Anything in the working tree at this point is unrelated
    # to the run and the user wants it preserved on the remote.
    try:
        await get_guard().pre_flight()
    except Exception as exc:
        log.exception("nightly pre-flight (commit/push of pending changes) failed")
        # If the pre-flight fails we'd be running with dirty state and
        # the eventual stash/commit would mix this work with the user's.
        # Bail out now and let the next nightly try again.
        return f"# Nightly run — pre-flight failed\n\n{exc}\n"

    base_sha = capture_head()

    archive: ArchiveResult
    organize: OrganizeResult | None = None
    organize_error: str | None = None

    # ── Run with per-primitive git IO suppressed ─────────────────────
    async with batch_session():
        archive = await run_journal_archive()
        log.info("nightly job: archive moved=%d skipped=%d", archive.moved, archive.skipped)
        try:
            organize = await run_organize(since=since)
            log.info(
                "nightly job: organize considered=%d errors=%d",
                len(organize.candidate_paths), len(organize.errors),
            )
        except Exception as exc:
            organize_error = str(exc)
            log.exception("nightly organize failed")

    # ── Capture what the run produced ────────────────────────────────
    stat = diff_stat(base_sha)
    changed = diff_names(base_sha)

    # Modified / skipped vs the actual disk state, not a self-report.
    if organize is not None:
        considered = list(organize.candidate_paths)
        modified_paths = sorted(p for p in considered if p in changed)
        skipped_paths = sorted(p for p in considered if p not in changed)
    else:
        considered = []
        modified_paths = []
        skipped_paths = []

    # ── Finalise: bulk-commit (apply) or stash (dry-run) ────────────
    finalise_msg: str
    today = datetime.now(timezone.utc).date().isoformat()
    if mode == "apply":
        try:
            committed = commit_and_push(f"nightly organize {today}")
            finalise_msg = (
                f"committed and pushed nightly organize {today}"
                if committed else "no changes to commit"
            )
        except Exception as exc:
            log.exception("nightly bulk commit/push failed")
            finalise_msg = f"commit/push FAILED: {exc} (changes left in working tree)"
    else:
        try:
            stashed = stash(f"second-brain organize dry-run {today}")
            finalise_msg = (
                f"dry-run: changes stashed as 'second-brain organize dry-run {today}'"
                if stashed else "dry-run: no changes produced"
            )
        except Exception as exc:
            log.exception("nightly stash failed")
            finalise_msg = f"stash FAILED: {exc} (changes left in working tree)"

    # ── Build the markdown report ───────────────────────────────────
    parts = [
        f"# Nightly run — {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Mode: **{mode}**",
        f"Outcome: {finalise_msg}",
        "",
        "## Changes (git diff --stat)",
        "",
        f"```\n{stat.strip() or '(no changes)'}\n```",
        "",
        _format_archive_section(archive),
        "",
    ]
    parts.extend(_format_organize_section(
        organize, organize_error, considered, modified_paths, skipped_paths
    ))
    report = "\n".join(parts)

    try:
        from app.services.email import render_nightly_email_html, send_email

        if organize is not None:
            subject = (
                f"[second-brain] nightly — archived {archive.moved}, "
                f"considered {len(considered)}, modified {len(modified_paths)}"
            )
        else:
            subject = (
                f"[second-brain] nightly — archived {archive.moved} "
                f"(organize failed)"
            )
        try:
            html = render_nightly_email_html(
                subject=subject,
                archive=archive,
                organize=organize,
                organize_error=organize_error,
                diff_stat=stat,
                finalise_msg=finalise_msg,
                considered=considered,
                modified_paths=modified_paths,
                skipped_paths=skipped_paths,
            )
        except Exception:
            log.exception("nightly HTML render failed; sending plain-text only")
            html = None
        send_email(subject=subject, body=report, html=html)
    except Exception:
        log.exception("nightly report email failed")

    return report


def _format_organize_section(
    organize: OrganizeResult | None,
    organize_error: str | None,
    considered: list[str],
    modified_paths: list[str],
    skipped_paths: list[str],
) -> list[str]:
    if organize is None:
        return [
            "## Organize",
            "",
            f"Failed: {organize_error or 'unknown error'}",
            "",
        ]
    duration = (organize.finished_at - organize.started_at).total_seconds()
    lines = [
        f"## Organize — {organize.started_at.date().isoformat()}",
        "",
        f"Started:  {organize.started_at.isoformat()}",
        f"Finished: {organize.finished_at.isoformat()} ({duration:.1f}s)",
        f"Last run: {organize.last_run_at.isoformat() if organize.last_run_at else '(first run)'}",
        f"Notes considered: {len(considered)}",
        f"Modified: {len(modified_paths)}",
        f"Skipped: {len(skipped_paths)}",
        f"Agent errors: {len(organize.errors)}",
        "",
    ]
    if skipped_paths:
        lines.append("### Skipped")
        lines.append("")
        for p in skipped_paths:
            lines.append(f"- `{p}`")
        lines.append("")
    if organize.errors:
        lines.append("### Errors")
        lines.append("")
        for path, reason in organize.errors:
            lines.append(f"- `{path}` — {reason}")
        lines.append("")
    return lines


async def _news_fetch_job() -> None:
    """Cron-side fetch: ranged walk over the full retention window
    (default 30 days) plus the unread-completeness pass. Slower per
    tick than a since_id incremental, but guarantees we don't miss
    anything published in the window across restarts/gaps."""
    from app.news.service import fetch_all_sources, thirty_days_ago_ts

    try:
        await fetch_all_sources(from_ts=thirty_days_ago_ts())
    except Exception:
        log.exception("scheduled news fetch failed")


def start_scheduler() -> None:
    global _SCHEDULER
    settings = get_settings()
    if not settings.organize.enabled and not settings.news.enabled:
        log.info("scheduler disabled (organize.enabled and news.enabled both false)")
        return
    if _SCHEDULER is not None:
        return

    sched = AsyncIOScheduler(timezone="UTC")

    if settings.organize.enabled:
        sched.add_job(
            run_nightly,
            trigger=CronTrigger.from_crontab(settings.organize.schedule, timezone="UTC"),
            id="nightly",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("scheduler: nightly cron = %s", settings.organize.schedule)

    if settings.news.enabled:
        sched.add_job(
            _news_fetch_job,
            trigger=CronTrigger.from_crontab(settings.news.fetch_schedule, timezone="UTC"),
            id="news-fetch",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info("scheduler: news fetch = %s", settings.news.fetch_schedule)

    sched.start()
    _SCHEDULER = sched
    log.info("scheduler started")


def shutdown_scheduler() -> None:
    global _SCHEDULER
    if _SCHEDULER is None:
        return
    _SCHEDULER.shutdown(wait=False)
    _SCHEDULER = None
    log.info("scheduler stopped")
