"""Outbound SMTP — used for the nightly Organize report and heartbeat.

Synchronous (stdlib `smtplib`); we send rarely and a sync call inside a
scheduler job is fine. Falls back to logging the message when SMTP is
disabled — handy during initial setup.

Three security modes (config: `smtp.security`):
  - none     — plain SMTP, no TLS.
  - starttls — plain SMTP then STARTTLS upgrade (port 587 typical).
  - ssl      — SSL/TLS from the start (port 465 typical).

The HTML alternative (used by the nightly Organize report) is rendered
by the configured LLM rather than a markdown library — see
`render_markdown_to_html_via_llm`.
"""

from __future__ import annotations

import logging
import re
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings
from app.llm import Message, TextBlock, complete

log = logging.getLogger(__name__)


_HTML_RENDER_SYSTEM_PROMPT = """\
You convert a markdown document into a self-contained HTML email document.

OUTPUT CONTRACT (strict):
- Output ONLY the HTML. No preamble, no commentary, no markdown code fences
  wrapping the result.
- Begin with `<!doctype html>` and end with `</html>`.
- Embed all CSS in a single <style> block inside <head>. No external
  resources (no <link>, no <script>, no images, no @import). Email clients
  often strip those.
- Use only widely-supported HTML and CSS (no JS, no SVG, no CSS variables).
- Keep the body width ~720px max so it looks good on both desktop and
  mobile email clients.

CONTENT FIDELITY (strict):
- Preserve EVERY heading, paragraph, list item, code fence, blockquote,
  and table from the input. Do not summarize, drop, reorder, or invent.
- Render code fences (```bash, ```json, etc.) as <pre><code> with a tinted
  background and a monospace font; no syntax highlighting needed.
- Status markers like ✅ / ❌ / ⚠ get subtle inline color (green / red /
  amber). Keep it tasteful.
- Wikilinks `[[Note]]` should render as inline <code>[[Note]]</code> (no
  link — this is an email and the user reads it outside the app).

STYLE:
- Clean, scannable, professional. Sans-serif body, h1/h2/h3 hierarchy
  visually distinct. Generous spacing. Limited palette (3–4 colors max
  including the body text and link colors). Do NOT add decorative
  emoji, illustrations, or stock content.
"""


def _strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap their answer in ```html ... ``` despite the
    instructions. Strip a single leading/trailing fence if present."""
    s = text.strip()
    m = re.match(r"^```(?:html)?\s*\n", s)
    if m:
        s = s[m.end():]
        if s.endswith("```"):
            s = s[: -3].rstrip()
    return s


async def render_markdown_to_html_via_llm(markdown_text: str) -> str | None:
    """Ask the configured LLM to render the markdown as a styled HTML
    document. Returns None if the LLM is unreachable or the output looks
    unusable — caller should fall back to the plain-text body.
    """
    try:
        raw = await complete(
            _HTML_RENDER_SYSTEM_PROMPT,
            [Message(role="user", content=[TextBlock(text=markdown_text)])],
        )
    except Exception as exc:
        log.warning("LLM markdown→HTML render failed: %s", exc)
        return None

    html = _strip_code_fence(raw)
    if not html:
        log.warning("LLM markdown→HTML returned an empty response")
        return None
    if "<html" not in html.lower():
        log.warning(
            "LLM markdown→HTML output doesn't look like an HTML document "
            "(no <html> tag): %r…",
            html[:200],
        )
        return None
    return html


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
