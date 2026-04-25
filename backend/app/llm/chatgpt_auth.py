"""ChatGPT subscription OAuth — device-flow login + token management.

Adapted for second-brain. The OAuth scheme is the same one used by the official
Codex CLI / opencode (public client id `app_EMoamEEZ73f0CkXaXp7hrann`), so a
ChatGPT Plus / Pro / Team subscription is sufficient to authenticate.

Tokens are persisted to a JSON file under
`{data_dir}/chatgpt_oauth/{provider_name}.json` so they survive container
restarts (the data dir is the bind-mounted volume) and are scoped per
configured provider entry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

# Public OAuth client id — same value used by the Codex CLI and opencode.
ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"

# Refresh the access token this many seconds before its actual expiry.
_EXPIRY_MARGIN_SEC = 60


# ── Path resolution ──────────────────────────────────────────────────────────


def token_path_for(provider_name: str, data_dir: Path | None = None) -> Path:
    """Where the OAuth tokens for a given provider config are persisted.

    By default the root is `app.data_dir` from the loaded config (the
    container view). The CLI may pass an explicit `data_dir` so a host-side
    login writes to a directory that the container will later see at its
    mount point — e.g. host `/data/second-brain/data` ↔ container `/data`.
    """
    if data_dir is None:
        data_dir = get_settings().app.data_dir
    root = data_dir / "chatgpt_oauth"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{provider_name}.json"


# ── PKCE (kept for reference; the device flow returns its own verifier) ──────


def _pkce_verifier() -> str:
    return secrets.token_urlsafe(32)


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ── Token I/O ────────────────────────────────────────────────────────────────


def _load_tokens(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.warning("could not parse OAuth token file at %s", path)
        return None


def _save_tokens(path: Path, tokens: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tokens, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ── JWT claim helpers ────────────────────────────────────────────────────────


def _account_id_from_jwt(token: str) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace"))
    except Exception:
        return None
    return (
        claims.get("chatgpt_account_id")
        or (claims.get("https://api.openai.com/auth") or {}).get("chatgpt_account_id")
        or ((claims.get("organizations") or [{}])[0]).get("id")
    )


def _extract_account_id(token_data: dict[str, Any]) -> str | None:
    for key in ("id_token", "access_token"):
        jwt = token_data.get(key, "")
        if jwt:
            account_id = _account_id_from_jwt(jwt)
            if account_id:
                return account_id
    return None


# ── Token refresh ────────────────────────────────────────────────────────────


def _refresh(refresh_token: str) -> dict[str, Any]:
    resp = httpx.post(
        f"{ISSUER}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    resp.raise_for_status()
    return resp.json()


def get_valid_access_token(provider_name: str) -> tuple[str, str | None]:
    """Return ``(access_token, account_id)`` for the named provider.

    Refreshes automatically when the cached token is near expiry. Raises
    ``RuntimeError`` if no tokens are stored — the user must run
    ``second-brain chatgpt-login <provider>`` first.
    """
    path = token_path_for(provider_name)
    tokens = _load_tokens(path)
    if tokens is None:
        raise RuntimeError(
            f"No ChatGPT OAuth tokens for provider '{provider_name}'. "
            f"Run: second-brain chatgpt-login {provider_name}"
        )

    expires_at = float(tokens.get("expires_at", 0))
    if time.time() + _EXPIRY_MARGIN_SEC >= expires_at:
        log.info("ChatGPT access token for '%s' is expiring — refreshing", provider_name)
        try:
            data = _refresh(tokens["refresh_token"])
        except Exception as exc:
            raise RuntimeError(
                f"Failed to refresh ChatGPT token for '{provider_name}': {exc}. "
                f"Run: second-brain chatgpt-login {provider_name}"
            ) from exc
        tokens["access_token"] = data["access_token"]
        tokens["refresh_token"] = data.get("refresh_token", tokens["refresh_token"])
        tokens["expires_at"] = time.time() + float(data.get("expires_in", 3600))
        _save_tokens(path, tokens)

    return tokens["access_token"], tokens.get("account_id")


# ── Device-flow login ────────────────────────────────────────────────────────


def login_device_flow(
    provider_name: str,
    *,
    poll_timeout_sec: int = 300,
    data_dir: Path | None = None,
) -> Path:
    """Run the OAuth device-code flow interactively.

    Prints the verification URL and a user code, polls until the user
    authorizes (or the timeout fires), then stores the tokens. Returns the
    token file path.

    `data_dir`, when given, overrides where the token file is written. Use
    this when running the login on the host machine while the actual app
    runs in a container: pass the host-side path that maps to the
    container's `app.data_dir`.
    """
    print("\n── ChatGPT subscription login ───────────────────────────────────")

    # Step 1 — request a device code.
    resp = httpx.post(
        f"{ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": CLIENT_ID},
        headers={"Content-Type": "application/json"},
        timeout=30.0,
    )
    if not resp.is_success:
        raise RuntimeError(
            f"Failed to start device authorization (HTTP {resp.status_code}): "
            f"{resp.text[:500]}"
        )

    device_data = resp.json()
    device_auth_id: str = device_data["device_auth_id"]
    user_code: str = device_data["user_code"]
    poll_interval_sec: float = max(float(device_data.get("interval", 5)), 1.0)

    print(f"\n  1. Open: https://auth.openai.com/codex/device")
    print(f"  2. Enter code: {user_code}")
    print("\nWaiting for authorization", end="", flush=True)

    # Step 2 — poll until authorized, then exchange for tokens.
    deadline = time.time() + poll_timeout_sec
    auth_code: str | None = None
    code_verifier: str | None = None

    while time.time() < deadline:
        # The official Codex CLI sleeps `interval + 3s` between polls.
        time.sleep(poll_interval_sec + 3)
        print(".", end="", flush=True)

        poll_resp = httpx.post(
            f"{ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers={"Content-Type": "application/json"},
            timeout=30.0,
        )

        if poll_resp.status_code == 200:
            poll_data = poll_resp.json()
            auth_code = poll_data["authorization_code"]
            code_verifier = poll_data["code_verifier"]
            break

        # 403 / 404 = still pending. Anything else = hard failure.
        if poll_resp.status_code not in (403, 404):
            print()
            raise RuntimeError(
                f"Device authorization failed (HTTP {poll_resp.status_code}): "
                f"{poll_resp.text[:500]}"
            )

    if auth_code is None or code_verifier is None:
        print()
        raise RuntimeError(
            f"Device authorization timed out after {poll_timeout_sec}s. Please retry."
        )

    # Step 3 — exchange the auth code for tokens.
    token_resp = httpx.post(
        f"{ISSUER}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "redirect_uri": f"{ISSUER}/deviceauth/callback",
            "client_id": CLIENT_ID,
            "code_verifier": code_verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30.0,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json()

    account_id = _extract_account_id(token_data)
    tokens = {
        "access_token": token_data["access_token"],
        "refresh_token": token_data["refresh_token"],
        "expires_at": time.time() + float(token_data.get("expires_in", 3600)),
        "account_id": account_id,
    }
    path = token_path_for(provider_name, data_dir=data_dir)
    _save_tokens(path, tokens)

    print("\n\nAuthenticated successfully.")
    if account_id:
        print(f"  Account ID: {account_id}")
    print(f"  Tokens saved to: {path}\n")
    return path
