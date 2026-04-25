"""Outbound SMTP — used for the nightly Organize report and heartbeat.

Synchronous (stdlib `smtplib`); we send rarely and a sync call inside a
scheduler job is fine. Falls back to logging the message when SMTP is
disabled — handy during initial setup.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger(__name__)


class SMTPDisabled(RuntimeError):
    pass


def send_email(subject: str, body: str, *, html: str | None = None) -> None:
    """Send an email per the configured SMTP settings.

    If SMTP is disabled, the body is logged at INFO level so dry-run setups
    can verify the job ran without configuring a mail server first.
    """
    s = get_settings().smtp
    if not s.enabled:
        log.info("[email disabled] %s\n%s", subject, body)
        return

    msg = EmailMessage()
    msg["From"] = s.from_address
    msg["To"] = s.to_address
    msg["Subject"] = subject
    if s.format == "html" and html:
        msg.set_content(body)
        msg.add_alternative(html, subtype="html")
    elif s.format == "html":
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)

    log.info("sending email %r → %s", subject, s.to_address)
    with smtplib.SMTP(s.host, s.port, timeout=30) as smtp:
        if s.starttls:
            smtp.starttls()
        if s.username and s.password:
            smtp.login(s.username, s.password)
        smtp.send_message(msg)
