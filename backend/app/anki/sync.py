"""AnkiWeb sync client (v11, full upload/download only).

What this implements:
  - POST /sync/hostKey   — username/password → hostkey
  - POST /sync/upload    — upload our `collection.anki2` byte-for-byte
  - POST /sync/download  — fetch the server's `collection.anki2`

What this does NOT implement:
  - Incremental sync (meta/start/applyChanges/applyChunk/sanityCheck/finish).
    The user picks the direction explicitly.
  - Media sync (`/msync/*`).

Wire format (v11):
  - Body: zstd-compressed bytes. Inner payload is JSON for control
    endpoints; raw SQLite bytes for upload/download.
  - Headers:
      Content-Type: application/octet-stream
      anki-sync: <JSON {v,k,c,s}>
        v: 11
        k: hostkey ("" for /sync/hostKey)
        c: client identifier, free-form
        s: 8-char alphanumeric session id
  - Redirects: server may return 308 with an `endpoint` in the body.
    We follow it once (and only once) and persist the new endpoint.

Source-of-truth references:
  - rslib/src/sync/version.rs        SYNC_VERSION_11
  - rslib/src/sync/login.rs           hostKey login JSON shape
  - rslib/src/sync/http_client/mod.rs zstd framing, anki-sync header
  - rslib/src/sync/collection/upload.rs  /sync/upload returns "OK"
"""

from __future__ import annotations

import json
import logging
import random
import string
from dataclasses import dataclass

import httpx

try:
    import zstandard as zstd
except ImportError as exc:  # pragma: no cover - dep is required at runtime
    raise RuntimeError(
        "anki sync requires the 'zstandard' package — add it to pyproject.toml"
    ) from exc

log = logging.getLogger(__name__)


SYNC_VERSION = 11
CLIENT_NAME = "second-brain,1.0,linux"
DEFAULT_TIMEOUT_S = 120.0


class AnkiSyncError(RuntimeError):
    """Raised on any AnkiWeb sync failure (auth, network, server)."""

    def __init__(self, status: int, message: str, *, body: str | None = None):
        super().__init__(f"[{status}] {message}")
        self.status = status
        self.message = message
        self.body = body


# ── Header machinery ─────────────────────────────────────────────────


def _new_session_key() -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=8))


def _anki_sync_header(hkey: str, session: str) -> str:
    return json.dumps({
        "v": SYNC_VERSION,
        "k": hkey,
        "c": CLIENT_NAME,
        "s": session,
    })


def _build_headers(hkey: str, session: str) -> dict[str, str]:
    return {
        "Content-Type": "application/octet-stream",
        "anki-sync": _anki_sync_header(hkey, session),
    }


# ── zstd framing ─────────────────────────────────────────────────────


_zstd_compressor = zstd.ZstdCompressor()
_zstd_decompressor = zstd.ZstdDecompressor()


def _compress(payload: bytes) -> bytes:
    return _zstd_compressor.compress(payload)


def _decompress(payload: bytes) -> bytes:
    if not payload:
        return b""
    # Server replies are always compressed; if it's not (some error
    # paths return plain text), fall back to raw bytes.
    try:
        return _zstd_decompressor.decompress(payload)
    except zstd.ZstdError:
        return payload


# ── Endpoint resolution & POST helper ────────────────────────────────


def _join_path(base_url: str, path: str) -> str:
    if not base_url.endswith("/"):
        base_url = base_url + "/"
    return base_url + path.lstrip("/")


def _post(
    base_url: str,
    path: str,
    *,
    payload: bytes,
    hkey: str,
    session: str,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> tuple[bytes, str]:
    """Send one POST. On 308 redirect, retry exactly once with the new
    endpoint. Returns (decompressed_response_bytes, final_base_url).
    """
    url = _join_path(base_url, path)
    headers = _build_headers(hkey, session)
    body = _compress(payload)

    # We follow 308 manually (httpx's default follow_redirects=False).
    with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
        resp = client.post(url, content=body, headers=headers)

        # 308 → migrate to the new endpoint and retry once.
        if resp.status_code == 308:
            try:
                redirect = json.loads(_decompress(resp.content).decode("utf-8"))
                new_endpoint = redirect.get("endpoint")
            except Exception:
                new_endpoint = resp.headers.get("location")
            if not new_endpoint:
                raise AnkiSyncError(
                    308, "got 308 redirect but no new endpoint", body=resp.text
                )
            log.info("anki sync: 308 redirect %s → %s", base_url, new_endpoint)
            new_url = _join_path(new_endpoint, path)
            resp = client.post(new_url, content=body, headers=headers)
            base_url = new_endpoint

        if resp.status_code >= 400:
            try:
                detail = _decompress(resp.content).decode("utf-8", errors="replace")
            except Exception:
                detail = resp.text
            raise AnkiSyncError(resp.status_code, detail or "request failed", body=detail)

        return _decompress(resp.content), base_url


# ── Public API ───────────────────────────────────────────────────────


@dataclass(slots=True)
class SyncSession:
    hkey: str
    endpoint: str   # final endpoint after any 308 redirects


def host_key(username: str, password: str, base_url: str) -> SyncSession:
    """Exchange username + password for a hostkey.

    Per `rslib/src/sync/login.rs`: POST `/sync/hostKey` with
    `{"u": username, "p": password}` (zstd-compressed JSON). The
    response is zstd-compressed `{"key": "<hkey>"}`.
    """
    session = _new_session_key()
    payload = json.dumps({"u": username, "p": password}).encode("utf-8")
    body, final_base = _post(
        base_url, "sync/hostKey",
        payload=payload, hkey="", session=session,
    )
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise AnkiSyncError(200, f"hostKey: invalid JSON response: {exc}") from exc
    key = data.get("key")
    if not key:
        # AnkiWeb sometimes returns {"err": "..."} on bad creds.
        err = data.get("err") or "no key in response"
        raise AnkiSyncError(401, f"hostKey: {err}")
    return SyncSession(hkey=key, endpoint=final_base)


def upload(session: SyncSession, anki2_bytes: bytes) -> None:
    """Full upload: send the entire collection.anki2 file.

    Server validates the SQLite file, returns `"OK"` on success.
    """
    sk = _new_session_key()
    body, _ = _post(
        session.endpoint, "sync/upload",
        payload=anki2_bytes, hkey=session.hkey, session=sk,
    )
    text = body.decode("utf-8", errors="replace").strip().strip('"')
    if text != "OK":
        # Non-OK is treated as a server-side rejection.
        raise AnkiSyncError(400, f"upload rejected: {text}", body=text)


def download(session: SyncSession) -> bytes:
    """Full download: receive the entire collection.anki2 file.

    Returns the raw SQLite file bytes; caller is responsible for
    writing it to disk atomically.
    """
    sk = _new_session_key()
    body, _ = _post(
        session.endpoint, "sync/download",
        payload=b"", hkey=session.hkey, session=sk,
    )
    if not body:
        raise AnkiSyncError(500, "download: empty response from server")
    # SQLite files start with "SQLite format 3\x00".
    if not body.startswith(b"SQLite format 3"):
        # Likely an error JSON or HTML page.
        try:
            preview = body[:200].decode("utf-8", errors="replace")
        except Exception:
            preview = "<binary>"
        raise AnkiSyncError(500, f"download: unexpected response: {preview}")
    return body


__all__ = [
    "AnkiSyncError",
    "CLIENT_NAME",
    "SYNC_VERSION",
    "SyncSession",
    "download",
    "host_key",
    "upload",
]
