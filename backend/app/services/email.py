"""Outbound SMTP — used for the nightly Organize report and heartbeat.

Synchronous (stdlib `smtplib`); we send rarely and a sync call inside a
scheduler job is fine. Falls back to logging the message when SMTP is
disabled — handy during initial setup.

Three security modes (config: `smtp.security`):
  - none     — plain SMTP, no TLS.
  - starttls — plain SMTP then STARTTLS upgrade (port 587 typical).
  - ssl      — SSL/TLS from the start (port 465 typical).

The HTML alternative for the nightly Organize report is rendered from the
job's structured result via a Jinja template (see
`render_nightly_email_html`). The plain-text alternative stays as the
markdown report so any client without HTML support remains readable.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from app.config import get_settings

if TYPE_CHECKING:
    from app.jobs.journal_archive import ArchiveResult
    from app.jobs.organize import OrganizeResult

log = logging.getLogger(__name__)


_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _jinja_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(_TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def render_nightly_email_html(
    *,
    subject: str,
    archive: "ArchiveResult",
    organize: "OrganizeResult | None",
    organize_error: str | None,
) -> str:
    """Build the HTML alternative for the nightly Organize email from the
    job's structured result. Deterministic, no LLM call.

    The proposals list is partitioned in Python so the template stays free
    of derivation logic: actionable (will change something), parse_error
    failures, and silent no-ops (each shown as a single bullet at the end).
    """
    if organize is not None:
        proposals = organize.proposals
        actionable = [p for p in proposals if not p.is_no_op and not p.parse_error]
        failures = [p for p in proposals if p.parse_error]
        no_ops = [p for p in proposals if p.is_no_op and not p.parse_error]
        applied_ok = [
            a for a in organize.applied if not a.error and a.operations
        ]
        last_run_iso = (
            organize.last_run_at.isoformat() if organize.last_run_at else None
        )
        run_date = organize.started_at.date().isoformat()
        started_iso = organize.started_at.isoformat()
        finished_iso = organize.finished_at.isoformat()
        duration_s = f"{(organize.finished_at - organize.started_at).total_seconds():.1f}"
        mode = organize.mode
    else:
        actionable = failures = no_ops = applied_ok = []
        last_run_iso = None
        # Without an organize result we still want a date stamp on the email.
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        run_date = now.date().isoformat()
        started_iso = finished_iso = now.isoformat()
        duration_s = "0.0"
        mode = "n/a"

    env = _jinja_env()
    template = env.get_template("nightly_report.html.j2")
    return template.render(
        subject=subject,
        run_date=run_date,
        started=started_iso,
        finished=finished_iso,
        duration_s=duration_s,
        mode=mode,
        last_run=last_run_iso,
        archive=archive,
        organize=organize,
        organize_error=organize_error,
        actionable=actionable,
        failures=failures,
        no_ops=no_ops,
        applied_ok=applied_ok,
    )


def send_email(subject: str, body: str, *, html: str | None = None) -> None:
    """Send an email per the configured SMTP settings.

    If SMTP is disabled, the body is logged at INFO level so dry-run setups
    can verify the job ran without configuring a mail server first.

    When `html` is provided, the message is sent as multipart/alternative
    with the markdown source as the plain-text part — works in any client.
    """
    s = get_settings().smtp
    if not s.enabled:
        log.info("[email disabled] %s\n%s", subject, body)
        return

    msg = EmailMessage()
    msg["From"] = s.from_address
    msg["To"] = s.to_address
    msg["Subject"] = subject
    if html:
        msg.set_content(body)
        msg.add_alternative(html, subtype="html")
    elif s.format == "html":
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)

    log.info(
        "sending email %r → %s (host=%s port=%s security=%s)",
        subject, s.to_address, s.host, s.port, s.security,
    )
    try:
        if s.security == "ssl":
            ctx = ssl.create_default_context()
            smtp = smtplib.SMTP_SSL(s.host, s.port, timeout=30, context=ctx)
        else:
            smtp = smtplib.SMTP(s.host, s.port, timeout=30)
        with smtp:
            if s.security == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if s.username and s.password:
                smtp.login(s.username, s.password)
            smtp.send_message(msg)
    except (smtplib.SMTPServerDisconnected, TimeoutError) as exc:
        if s.security != "ssl":
            raise RuntimeError(
                f"SMTP timed out connecting to {s.host}:{s.port} with "
                f"security={s.security}. If your provider requires TLS from "
                f"the start (port 465 / 'SSL/TLS' in their docs), set "
                f"`smtp.security: ssl` in config.yml. Original error: {exc}"
            ) from exc
        raise
