"""APScheduler bootstrap.

Single AsyncIOScheduler per process. The nightly job runs `run_nightly()`,
which today does:
  1. Journal archival (Step 6).
  2. Send a heartbeat email summarising what happened.

Step 7 will add the Organize pass between (1) and the email.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings

from .journal_archive import ArchiveResult, run_journal_archive

log = logging.getLogger(__name__)

_SCHEDULER: AsyncIOScheduler | None = None


def _format_report(archive: ArchiveResult) -> str:
    lines = [
        f"Run: {datetime.now(timezone.utc).isoformat()}",
        "",
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


async def run_nightly() -> str:
    """Execute the nightly job. Returns the rendered report."""
    log.info("nightly job: starting")
    archive = await run_journal_archive()
    report = _format_report(archive)
    log.info("nightly job: archive moved=%d skipped=%d", archive.moved, archive.skipped)

    # Heartbeat — even an empty night should land in the user's inbox so they
    # can confirm cron is alive.
    try:
        from app.services.email import send_email

        send_email(
            subject=f"[second-brain] nightly heartbeat — moved {archive.moved}, skipped {archive.skipped}",
            body=report,
        )
    except Exception:
        log.exception("nightly heartbeat email failed")

    return report


def start_scheduler() -> None:
    global _SCHEDULER
    settings = get_settings()
    if not settings.organize.enabled:
        log.info("scheduler disabled (organize.enabled = false)")
        return
    if _SCHEDULER is not None:
        return

    sched = AsyncIOScheduler(timezone="UTC")
    trigger = CronTrigger.from_crontab(settings.organize.schedule, timezone="UTC")
    sched.add_job(
        run_nightly,
        trigger=trigger,
        id="nightly",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.start()
    _SCHEDULER = sched
    log.info("scheduler started (nightly cron: %s)", settings.organize.schedule)


def shutdown_scheduler() -> None:
    global _SCHEDULER
    if _SCHEDULER is None:
        return
    _SCHEDULER.shutdown(wait=False)
    _SCHEDULER = None
    log.info("scheduler stopped")
