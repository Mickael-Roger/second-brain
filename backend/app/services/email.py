"""Outbound SMTP — used for the nightly Organize report and heartbeat.

Synchronous (stdlib `smtplib`); we send rarely and a sync call inside a
scheduler job is fine. Falls back to logging the message when SMTP is
disabled — handy during initial setup.

Three security modes (config: `smtp.security`):
  - none     — plain SMTP, no TLS.
  - starttls — plain SMTP then STARTTLS upgrade (port 587 typical).
  - ssl      — SSL/TLS from the start (port 465 typical).
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

import markdown as md_lib

from app.config import get_settings

log = logging.getLogger(__name__)


_HTML_CSS = """
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
       line-height: 1.55; color: #1f2937; max-width: 760px; margin: 1.2rem auto;
       padding: 0 1rem; }
h1 { font-size: 1.6em; border-bottom: 1px solid #e5e7eb; padding-bottom: 0.3em;
     margin-top: 1.6em; }
h1:first-child { margin-top: 0; }
h2 { font-size: 1.25em; margin-top: 1.5em; }
h3 { font-size: 1.05em; margin-top: 1.2em; }
code { background: #f3f4f6; padding: 0.1em 0.35em; border-radius: 3px;
       font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
pre { background: #f3f4f6; padding: 0.75em 1em; border-radius: 6px; overflow-x: auto; }
pre code { background: none; padding: 0; }
ul, ol { padding-left: 1.5em; }
li { margin: 0.2em 0; }
blockquote { border-left: 3px solid #9ca3af; padding-left: 1em; color: #6b7280;
             margin: 0.6em 0; }
hr { border: 0; border-top: 1px solid #e5e7eb; margin: 1.4em 0; }
table { border-collapse: collapse; }
th, td { border: 1px solid #d1d5db; padding: 0.4em 0.7em; }
.note-error { color: #b91c1c; }
.note-ok { color: #047857; }
""".strip()


def render_markdown_to_html_doc(markdown_text: str) -> str:
    """Render an organize-report-style markdown string into a standalone
    HTML document with light styling — for sending as the multipart/html
    alternative of an outgoing email."""
    body = md_lib.markdown(
        markdown_text,
        extensions=["fenced_code", "tables", "sane_lists", "nl2br"],
        output_format="html",
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_HTML_CSS}</style></head><body>{body}</body></html>"
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
        # Caller didn't pre-render; treat the body as raw HTML.
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)

    log.info(
        "sending email %r → %s (host=%s port=%s security=%s)",
        subject, s.to_address, s.host, s.port, s.security,
    )
    try:
        if s.security == "ssl":
            # SSL/TLS handshake happens immediately on connect.
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
