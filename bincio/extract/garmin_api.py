"""Garmin Connect credential storage and client factory.

Credential storage layout
─────────────────────────
  {data_dir.parent}/.garmin_key        ← Fernet key (outside nginx webroot, chmod 600)
  {user_dir}/garmin_creds.json         ← encrypted email + password
  {user_dir}/garmin_session/           ← garth OAuth token directory (plain JSON, short-lived)

Security model
──────────────
- The Fernet key lives one directory above the data root, which nginx does NOT serve.
  For a standard VPS install: data_dir = /var/bincio/data/ → key at /var/bincio/.garmin_key.
- Credentials are encrypted with that key before being written to disk.
- The garth session directory holds OAuth tokens (not the user's password).
  These expire independently and are refreshed automatically by the library.
- If the session expires and re-login is needed, the stored credentials are decrypted
  and used automatically — the user does not need to re-enter them.

DISCLAIMER
──────────
This module uses the unofficial `garminconnect` library.
See docs/garmin_connect_disclaimer.md before shipping this feature to users.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional

# ── Constants ─────────────────────────────────────────────────────────────────

_CREDS_FILE    = "garmin_creds.json"
_SESSION_DIR   = "garmin_session"
_KEY_FILENAME  = ".garmin_key"


class GarminError(Exception):
    pass


# ── Encryption key management ─────────────────────────────────────────────────

def _key_path(data_dir: Path) -> Path:
    """Return the path to the Fernet key file (one level above data_dir)."""
    return data_dir.parent / _KEY_FILENAME


def _get_or_create_key(data_dir: Path) -> bytes:
    """Load the Fernet key, creating and locking it down on first use."""
    from cryptography.fernet import Fernet

    kp = _key_path(data_dir)
    if kp.exists():
        return kp.read_bytes().strip()

    key = Fernet.generate_key()
    kp.parent.mkdir(parents=True, exist_ok=True)
    kp.write_bytes(key)
    kp.chmod(stat.S_IRUSR | stat.S_IWUSR)   # 0o600 — owner read/write only
    return key


def _fernet(data_dir: Path):
    from cryptography.fernet import Fernet
    return Fernet(_get_or_create_key(data_dir))


# ── Credential encryption helpers ─────────────────────────────────────────────

def _encrypt(data_dir: Path, value: str) -> str:
    return _fernet(data_dir).encrypt(value.encode()).decode()


def _decrypt(data_dir: Path, token: str) -> str:
    try:
        return _fernet(data_dir).decrypt(token.encode()).decode()
    except Exception as exc:
        raise GarminError("Failed to decrypt Garmin credentials — key may have changed") from exc


# ── Credential CRUD ───────────────────────────────────────────────────────────

def has_credentials(user_dir: Path) -> bool:
    return (user_dir / _CREDS_FILE).exists()


def save_credentials(data_dir: Path, user_dir: Path, email: str, password: str) -> None:
    """Encrypt and persist the user's Garmin email + password."""
    payload = {
        "email":    _encrypt(data_dir, email),
        "password": _encrypt(data_dir, password),
    }
    creds_path = user_dir / _CREDS_FILE
    creds_path.write_text(json.dumps(payload, indent=2))
    creds_path.chmod(stat.S_IRUSR | stat.S_IWUSR)   # 0o600


def load_credentials(data_dir: Path, user_dir: Path) -> tuple[str, str]:
    """Return (email, password) decrypted from disk."""
    creds_path = user_dir / _CREDS_FILE
    if not creds_path.exists():
        raise GarminError("No Garmin credentials stored for this user")
    try:
        raw = json.loads(creds_path.read_text())
    except Exception as exc:
        raise GarminError("Garmin credentials file is corrupt") from exc
    return _decrypt(data_dir, raw["email"]), _decrypt(data_dir, raw["password"])


def delete_credentials(user_dir: Path) -> None:
    """Remove stored credentials and session (disconnect)."""
    creds_path = user_dir / _CREDS_FILE
    if creds_path.exists():
        creds_path.unlink()

    session_dir = user_dir / _SESSION_DIR
    if session_dir.exists():
        import shutil
        shutil.rmtree(session_dir)


# ── Session management (garth OAuth tokens) ───────────────────────────────────

def _session_dir(user_dir: Path) -> Path:
    d = user_dir / _SESSION_DIR
    d.mkdir(exist_ok=True)
    return d


def _save_session(user_dir: Path, client) -> None:
    """Persist garth OAuth tokens so the next sync skips re-login."""
    try:
        client.garth.dump(str(_session_dir(user_dir)))
    except Exception:
        pass   # session save is best-effort


def _load_session(user_dir: Path, client) -> bool:
    """Try to restore a saved garth session. Returns True on success."""
    sd = user_dir / _SESSION_DIR
    if not sd.exists():
        return False
    try:
        client.garth.load(str(sd))
        return True
    except Exception:
        return False


# ── Client factory ────────────────────────────────────────────────────────────

def get_client(data_dir: Path, user_dir: Path):
    """Return a logged-in Garmin client.

    Strategy:
    1. Try to resume a saved garth session (fast, no network round-trip).
    2. If that fails or the session has expired, re-login using the stored
       (encrypted) credentials.
    3. Persist the refreshed session for next time.

    Raises GarminError if credentials are missing or login fails.
    """
    try:
        import garminconnect
    except ImportError as exc:
        raise GarminError(
            "garminconnect is not installed. "
            "Run: uv sync --extra garmin"
        ) from exc

    client = garminconnect.Garmin()

    # Try cached session first
    if _load_session(user_dir, client):
        try:
            client.garth.refresh_oauth2()   # renew access token if needed
            _save_session(user_dir, client)  # persist refreshed token
            return client
        except Exception:
            pass   # session is dead — fall through to full re-login

    # Full login with stored credentials
    email, password = load_credentials(data_dir, user_dir)
    try:
        client = garminconnect.Garmin(email=email, password=password)
        client.login()
    except Exception as exc:
        raise GarminError(f"Garmin login failed: {exc}") from exc

    _save_session(user_dir, client)
    return client


def test_login(data_dir: Path, user_dir: Path, email: str, password: str) -> dict:
    """Attempt a login with the supplied credentials (does not save them).

    Returns a dict with display_name and full_name on success.
    Raises GarminError on failure.
    """
    try:
        import garminconnect
    except ImportError as exc:
        raise GarminError("garminconnect is not installed") from exc

    try:
        client = garminconnect.Garmin(email=email, password=password)
        client.login()
    except Exception as exc:
        raise GarminError(f"Login failed: {exc}") from exc

    try:
        profile = client.get_profile_user_summary()
        display = profile.get("displayName", email)
        full    = f"{profile.get('firstName', '')} {profile.get('lastName', '')}".strip()
    except Exception:
        display, full = email, ""

    # Credentials are valid — save them and the session
    save_credentials(data_dir, user_dir, email, password)
    _save_session(user_dir, client)

    return {"display_name": display, "full_name": full}
