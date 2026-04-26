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


async def run_nightly() -> str:
    """Execute the nightly job: archive prior days, run the Organize pass,
    email the combined report. Returns the report text."""
    log.info("nightly job: starting")
    archive = await run_journal_archive()
    log.info("nightly job: archive moved=%d skipped=%d", archive.moved, archive.skipped)

    organize: OrganizeResult | None = None
    organize_error: str | None = None
    try:
        organize = await run_organize()
        log.info(
            "nightly job: organize processed=%d proposals=%d skipped=%d",
            organize.processed, len(organize.proposals), len(organize.skipped),
        )
    except Exception as exc:
        organize_error = str(exc)
        log.exception("nightly organize failed")

    parts = [
        f"# Nightly run — {datetime.now(timezone.utc).isoformat()}",
        "",
        _format_archive_section(archive),
        "",
    ]
    if organize is not None:
        parts.append(organize.report)
    elif organize_error:
        parts.append(f"## Organize\n\nFailed: {organize_error}")
    report = "\n".join(parts)

    try:
        from app.services.email import render_markdown_to_html_via_llm, send_email

        if organize is not None:
            subject = (
                f"[second-brain] nightly — archived {archive.moved}, "
                f"reviewed {organize.processed}, proposals {len(organize.proposals)}"
            )
        else:
            subject = (
                f"[second-brain] nightly — archived {archive.moved} "
                f"(organize failed)"
            )
        # Render the markdown to a styled HTML document via the LLM so the
        # email looks readable in GUI clients. Falls back to plain text if
        # the LLM is unreachable or returns garbage.
        html = await render_markdown_to_html_via_llm(report)
        send_email(subject=subject, body=report, html=html)
    except Exception:
        log.exception("nightly report email failed")

    return report


async def _news_fetch_job() -> None:
    from app.news.service import fetch_all_sources

    try:
        await fetch_all_sources()
    except Exception:
        log.exception("scheduled news fetch failed")


async def _news_cluster_job() -> None:
    from app.news.cluster import run_cluster_pass

    try:
        await run_cluster_pass()
    except Exception:
        log.exception("scheduled news cluster failed")


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
        sched.add_job(
            _news_cluster_job,
            trigger=CronTrigger.from_crontab(settings.news.cluster_schedule, timezone="UTC"),
            id="news-cluster",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        log.info(
            "scheduler: news fetch = %s, news cluster = %s",
            settings.news.fetch_schedule, settings.news.cluster_schedule,
        )

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
