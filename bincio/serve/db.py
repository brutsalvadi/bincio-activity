"""SQLite data layer for bincio multi-user instances.

Schema
------
users     — registered accounts (handle, hashed password, admin flag)
sessions  — active login sessions (token → handle, expiry)
invites   — invite codes (who created, who used, when)

All timestamps are Unix integers (UTC).
Passwords are hashed with bcrypt.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import bcrypt

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    handle        TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL DEFAULT '',
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    handle      TEXT NOT NULL REFERENCES users(handle) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS invites (
    code        TEXT PRIMARY KEY,
    created_by  TEXT NOT NULL REFERENCES users(handle) ON DELETE CASCADE,
    used_by     TEXT REFERENCES users(handle) ON DELETE SET NULL,
    created_at  INTEGER NOT NULL,
    used_at     INTEGER
);

CREATE TABLE IF NOT EXISTS reset_codes (
    code        TEXT PRIMARY KEY,
    handle      TEXT NOT NULL REFERENCES users(handle) ON DELETE CASCADE,
    created_by  TEXT NOT NULL REFERENCES users(handle) ON DELETE CASCADE,
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL,
    used_at     INTEGER
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_prefs (
    handle  TEXT NOT NULL REFERENCES users(handle) ON DELETE CASCADE,
    key     TEXT NOT NULL,
    value   TEXT NOT NULL,
    PRIMARY KEY (handle, key)
);

CREATE INDEX IF NOT EXISTS sessions_handle ON sessions(handle);
CREATE INDEX IF NOT EXISTS invites_created_by ON invites(created_by);
CREATE INDEX IF NOT EXISTS reset_codes_handle ON reset_codes(handle);
CREATE INDEX IF NOT EXISTS user_prefs_handle ON user_prefs(handle);
"""

_SESSION_DAYS = 30
_INVITE_LENGTH = 8
_RESET_CODE_TTL_S = 24 * 3600  # 24 hours


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class User:
    handle:       str
    display_name: str
    is_admin:     bool
    created_at:   int


@dataclass
class Invite:
    code:       str
    created_by: str
    used_by:    Optional[str]
    created_at: int
    used_at:    Optional[int]

    @property
    def used(self) -> bool:
        return self.used_by is not None


# ── Connection ────────────────────────────────────────────────────────────────

def open_db(data_dir: Path) -> sqlite3.Connection:
    """Open (and if needed create) the instance database."""
    db = sqlite3.connect(data_dir / "instance.db", check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(_SCHEMA)
    db.commit()
    return db


# ── Users ─────────────────────────────────────────────────────────────────────

def create_user(
    db: sqlite3.Connection,
    handle: str,
    display_name: str,
    password: str,
    is_admin: bool = False,
) -> User:
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    now = int(time.time())
    db.execute(
        "INSERT INTO users (handle, display_name, password_hash, is_admin, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (handle, display_name, password_hash, int(is_admin), now),
    )
    db.commit()
    return User(handle=handle, display_name=display_name, is_admin=is_admin, created_at=now)


def get_user(db: sqlite3.Connection, handle: str) -> Optional[User]:
    row = db.execute("SELECT * FROM users WHERE handle = ?", (handle,)).fetchone()
    if not row:
        return None
    return User(
        handle=row["handle"],
        display_name=row["display_name"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
    )


def authenticate(db: sqlite3.Connection, handle: str, password: str) -> Optional[User]:
    """Return the User if credentials are valid, else None."""
    row = db.execute(
        "SELECT * FROM users WHERE handle = ?", (handle,)
    ).fetchone()
    if not row:
        return None
    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return None
    return User(
        handle=row["handle"],
        display_name=row["display_name"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
    )


def change_password(db: sqlite3.Connection, handle: str, new_password: str) -> None:
    """Replace the password hash for a user."""
    new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    db.execute("UPDATE users SET password_hash = ? WHERE handle = ?", (new_hash, handle))
    db.commit()


def list_users(db: sqlite3.Connection) -> list[User]:
    rows = db.execute("SELECT * FROM users ORDER BY created_at").fetchall()
    return [User(handle=r["handle"], display_name=r["display_name"],
                 is_admin=bool(r["is_admin"]), created_at=r["created_at"]) for r in rows]


def delete_user(db: sqlite3.Connection, handle: str) -> None:
    db.execute("DELETE FROM users WHERE handle = ?", (handle,))
    db.commit()


def get_member_tree(db: sqlite3.Connection) -> list[dict]:
    """Return users with their inviter handle and join timestamp.

    Each entry: {handle, display_name, created_at, invited_by (handle or None)}.
    Ordered oldest-first so callers can build the tree top-down.
    """
    users = {r["handle"]: r for r in db.execute(
        "SELECT handle, display_name, created_at FROM users ORDER BY created_at"
    ).fetchall()}
    # Map invitee → inviter from the used invites
    invited_by: dict[str, str] = {}
    for row in db.execute(
        "SELECT created_by, used_by FROM invites WHERE used_by IS NOT NULL"
    ).fetchall():
        invited_by[row["used_by"]] = row["created_by"]

    return [
        {
            "handle": r["handle"],
            "display_name": r["display_name"],
            "created_at": r["created_at"],
            "invited_by": invited_by.get(r["handle"]),
        }
        for r in users.values()
    ]


def count_users(db: sqlite3.Connection) -> int:
    """Return the total number of registered users."""
    row = db.execute("SELECT COUNT(*) FROM users").fetchone()
    return row[0] if row else 0


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(db: sqlite3.Connection, key: str) -> Optional[str]:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_setting(db: sqlite3.Connection, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    db.commit()


# ── Sessions ──────────────────────────────────────────────────────────────────

def create_session(db: sqlite3.Connection, handle: str) -> str:
    """Create a session token for the given user. Returns the token."""
    token = secrets.token_hex(32)
    now = int(time.time())
    expires_at = now + _SESSION_DAYS * 86400
    db.execute(
        "INSERT INTO sessions (token, handle, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, handle, now, expires_at),
    )
    db.commit()
    return token


def get_session(db: sqlite3.Connection, token: str) -> Optional[User]:
    """Return the User owning this session, or None if expired/invalid."""
    row = db.execute(
        "SELECT s.handle, s.expires_at, u.display_name, u.is_admin, u.created_at "
        "FROM sessions s JOIN users u ON s.handle = u.handle "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
    if not row:
        return None
    if row["expires_at"] < int(time.time()):
        delete_session(db, token)
        return None
    return User(
        handle=row["handle"],
        display_name=row["display_name"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
    )


def delete_session(db: sqlite3.Connection, token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token = ?", (token,))
    db.commit()


def purge_expired_sessions(db: sqlite3.Connection) -> int:
    cur = db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))
    db.commit()
    return cur.rowcount


# ── Invites ───────────────────────────────────────────────────────────────────

_MAX_USER_INVITES = 3  # regular users; admins are unlimited


def create_invite(db: sqlite3.Connection, created_by: str) -> str:
    """Generate an invite code. Raises ValueError if the user has hit their limit."""
    user = get_user(db, created_by)
    if user and not user.is_admin:
        count = db.execute(
            "SELECT COUNT(*) FROM invites WHERE created_by = ?", (created_by,)
        ).fetchone()[0]
        if count >= _MAX_USER_INVITES:
            raise ValueError(f"Invite limit reached ({_MAX_USER_INVITES})")

    code = secrets.token_urlsafe(_INVITE_LENGTH)[:_INVITE_LENGTH].upper()
    db.execute(
        "INSERT INTO invites (code, created_by, created_at) VALUES (?, ?, ?)",
        (code, created_by, int(time.time())),
    )
    db.commit()
    return code


def use_invite(db: sqlite3.Connection, code: str, handle: str) -> bool:
    """Mark an invite as used. Returns False if the code is invalid or already used."""
    row = db.execute(
        "SELECT used_by FROM invites WHERE code = ?", (code,)
    ).fetchone()
    if not row or row["used_by"] is not None:
        return False
    db.execute(
        "UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?",
        (handle, int(time.time()), code),
    )
    db.commit()
    return True


def list_invites(db: sqlite3.Connection, handle: str) -> list[Invite]:
    rows = db.execute(
        "SELECT * FROM invites WHERE created_by = ? ORDER BY created_at DESC",
        (handle,),
    ).fetchall()
    return [
        Invite(
            code=r["code"],
            created_by=r["created_by"],
            used_by=r["used_by"],
            created_at=r["created_at"],
            used_at=r["used_at"],
        )
        for r in rows
    ]


def get_invite(db: sqlite3.Connection, code: str) -> Optional[Invite]:
    row = db.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
    if not row:
        return None
    return Invite(
        code=row["code"],
        created_by=row["created_by"],
        used_by=row["used_by"],
        created_at=row["created_at"],
        used_at=row["used_at"],
    )


# ── Password reset codes ──────────────────────────────────────────────────────

def create_reset_code(db: sqlite3.Connection, handle: str, created_by: str) -> str:
    """Generate a password reset code for a user (admin only, out-of-band delivery).

    Any previous unused codes for this handle are invalidated first.
    Returns the new code.
    """
    now = int(time.time())
    # Invalidate existing unused codes for this handle
    db.execute(
        "DELETE FROM reset_codes WHERE handle = ? AND used_at IS NULL",
        (handle,),
    )
    code = secrets.token_urlsafe(_INVITE_LENGTH)[:_INVITE_LENGTH].upper()
    db.execute(
        "INSERT INTO reset_codes (code, handle, created_by, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (code, handle, created_by, now, now + _RESET_CODE_TTL_S),
    )
    db.commit()
    return code


# ── User preferences ─────────────────────────────────────────────────────────

def get_user_prefs(db: sqlite3.Connection, handle: str) -> dict[str, str]:
    """Return all preferences for a user as a plain dict."""
    rows = db.execute(
        "SELECT key, value FROM user_prefs WHERE handle = ?", (handle,)
    ).fetchall()
    return {r["key"]: r["value"] for r in rows}


def set_user_pref(db: sqlite3.Connection, handle: str, key: str, value: str) -> None:
    db.execute(
        "INSERT INTO user_prefs (handle, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(handle, key) DO UPDATE SET value = excluded.value",
        (handle, key, value),
    )
    db.commit()


def set_user_prefs(db: sqlite3.Connection, handle: str, prefs: dict[str, str]) -> None:
    """Bulk-upsert multiple preferences for a user."""
    for key, value in prefs.items():
        db.execute(
            "INSERT INTO user_prefs (handle, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(handle, key) DO UPDATE SET value = excluded.value",
            (handle, key, value),
        )
    db.commit()


def use_reset_code(db: sqlite3.Connection, code: str, handle: str) -> bool:
    """Validate a reset code for the given handle and mark it used.

    Returns False if the code is invalid, already used, expired, or
    belongs to a different handle.
    """
    now = int(time.time())
    row = db.execute(
        "SELECT handle, expires_at, used_at FROM reset_codes WHERE code = ?",
        (code,),
    ).fetchone()
    if not row:
        return False
    if row["handle"] != handle:
        return False
    if row["used_at"] is not None:
        return False
    if row["expires_at"] < now:
        return False
    db.execute(
        "UPDATE reset_codes SET used_at = ? WHERE code = ?",
        (now, code),
    )
    db.commit()
    return True
