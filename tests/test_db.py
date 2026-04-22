"""Tests for bincio.serve.db — SQLite auth data layer."""

import sqlite3
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from bincio.serve.db import (
    Invite,
    User,
    authenticate,
    create_invite,
    create_session,
    create_user,
    delete_session,
    delete_user,
    get_invite,
    get_session,
    get_user,
    list_invites,
    list_users,
    open_db,
    purge_expired_sessions,
    use_invite,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    return open_db(tmp_path)


@pytest.fixture
def admin(db) -> User:
    return create_user(db, "admin", "Admin User", "adminpass", is_admin=True)


@pytest.fixture
def user(db) -> User:
    return create_user(db, "alice", "Alice", "alicepass", is_admin=False)


# ── open_db ───────────────────────────────────────────────────────────────────

def test_open_db_creates_file(tmp_path: Path):
    open_db(tmp_path)
    assert (tmp_path / "instance.db").exists()


def test_open_db_wal_mode(tmp_path: Path):
    db = open_db(tmp_path)
    row = db.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"


def test_open_db_idempotent(tmp_path: Path):
    db1 = open_db(tmp_path)
    db2 = open_db(tmp_path)   # must not raise on existing schema
    db1.close()
    db2.close()


# ── create_user / get_user ────────────────────────────────────────────────────

def test_create_user_returns_user(db):
    u = create_user(db, "dave", "Dave", "secret", is_admin=False)
    assert u.handle == "dave"
    assert u.display_name == "Dave"
    assert u.is_admin is False
    assert u.created_at > 0


def test_create_user_admin_flag(db):
    u = create_user(db, "boss", "Boss", "secret", is_admin=True)
    assert u.is_admin is True


def test_get_user_found(db, user):
    u = get_user(db, "alice")
    assert u is not None
    assert u.handle == "alice"
    assert u.display_name == "Alice"


def test_get_user_not_found(db):
    assert get_user(db, "nobody") is None


def test_list_users(db, admin, user):
    users = list_users(db)
    handles = [u.handle for u in users]
    assert "admin" in handles
    assert "alice" in handles


def test_delete_user(db, user):
    delete_user(db, "alice")
    assert get_user(db, "alice") is None


# ── authenticate ──────────────────────────────────────────────────────────────

def test_authenticate_valid(db, user):
    result = authenticate(db, "alice", "alicepass")
    assert result is not None
    assert result.handle == "alice"


def test_authenticate_wrong_password(db, user):
    result = authenticate(db, "alice", "wrongpass")
    assert result is None


def test_authenticate_unknown_handle(db):
    result = authenticate(db, "ghost", "anypass")
    assert result is None


def test_authenticate_empty_password_rejected(db, user):
    result = authenticate(db, "alice", "")
    assert result is None


# ── sessions ──────────────────────────────────────────────────────────────────

def test_create_session_returns_token(db, user):
    token = create_session(db, "alice")
    assert isinstance(token, str)
    assert len(token) == 64   # secrets.token_hex(32)


def test_get_session_returns_user(db, user):
    token = create_session(db, "alice")
    u = get_session(db, token)
    assert u is not None
    assert u.handle == "alice"


def test_get_session_invalid_token(db):
    assert get_session(db, "not-a-real-token") is None


def test_delete_session(db, user):
    token = create_session(db, "alice")
    delete_session(db, token)
    assert get_session(db, token) is None


def test_get_session_expired(db, user):
    token = create_session(db, "alice")
    # Backdate the expiry to the past
    db.execute("UPDATE sessions SET expires_at = ? WHERE token = ?",
               (int(time.time()) - 1, token))
    db.commit()
    assert get_session(db, token) is None


def test_get_session_expired_token_deleted(db, user):
    token = create_session(db, "alice")
    db.execute("UPDATE sessions SET expires_at = ? WHERE token = ?",
               (int(time.time()) - 1, token))
    db.commit()
    get_session(db, token)   # triggers deletion
    row = db.execute("SELECT * FROM sessions WHERE token = ?", (token,)).fetchone()
    assert row is None


def test_purge_expired_sessions(db, user):
    t1 = create_session(db, "alice")
    t2 = create_session(db, "alice")
    # Expire t1
    db.execute("UPDATE sessions SET expires_at = ? WHERE token = ?",
               (int(time.time()) - 1, t1))
    db.commit()
    count = purge_expired_sessions(db)
    assert count == 1
    assert get_session(db, t2) is not None


def test_multiple_sessions_same_user(db, user):
    t1 = create_session(db, "alice")
    t2 = create_session(db, "alice")
    assert t1 != t2
    assert get_session(db, t1) is not None
    assert get_session(db, t2) is not None


# ── invites ───────────────────────────────────────────────────────────────────

def test_create_invite_returns_code(db, admin):
    code = create_invite(db, "admin")
    assert isinstance(code, str)
    assert len(code) == 8


def test_create_invite_admin_unlimited(db, admin):
    # Admin can create more than 3 invites
    for _ in range(5):
        create_invite(db, "admin")


def test_create_invite_regular_user_limited(db, user):
    for _ in range(3):
        create_invite(db, "alice")
    with pytest.raises(ValueError, match="Invite limit"):
        create_invite(db, "alice")


def test_get_invite_found(db, admin):
    code = create_invite(db, "admin")
    invite = get_invite(db, code)
    assert invite is not None
    assert invite.code == code
    assert invite.created_by == "admin"
    assert invite.used is False


def test_get_invite_not_found(db):
    assert get_invite(db, "NOTEXIST") is None


def test_use_invite_marks_used(db, admin, user):
    code = create_invite(db, "admin")
    result = use_invite(db, code, "alice")
    assert result is True
    invite = get_invite(db, code)
    assert invite.used is True
    assert invite.used_by == "alice"
    assert invite.used_at is not None


def test_use_invite_already_used_returns_false(db, admin, user):
    code = create_invite(db, "admin")
    use_invite(db, code, "alice")
    result = use_invite(db, code, "alice")   # second use
    assert result is False


def test_use_invite_invalid_code_returns_false(db):
    result = use_invite(db, "INVALID1", "alice")
    assert result is False


def test_list_invites(db, admin):
    c1 = create_invite(db, "admin")
    c2 = create_invite(db, "admin")
    invites = list_invites(db, "admin")
    codes = [i.code for i in invites]
    assert c1 in codes
    assert c2 in codes


def test_list_invites_own_only(db, admin, user):
    create_invite(db, "admin")
    create_invite(db, "alice")
    admin_invites = list_invites(db, "admin")
    for i in admin_invites:
        assert i.created_by == "admin"


def test_invite_used_property(db, admin):
    code = create_invite(db, "admin")
    invite = get_invite(db, code)
    assert invite.used is False

    create_user(db, "bob", "Bob", "bobpass")
    use_invite(db, code, "bob")
    invite = get_invite(db, code)
    assert invite.used is True


# ── cascade on delete ─────────────────────────────────────────────────────────

def test_delete_user_cascades_sessions(db, user):
    token = create_session(db, "alice")
    delete_user(db, "alice")
    # Session should be gone (ON DELETE CASCADE)
    assert get_session(db, token) is None
