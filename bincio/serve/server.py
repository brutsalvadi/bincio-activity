"""bincio serve — multi-user FastAPI application server.

Handles auth, user management, and auth-gated write operations.
nginx serves static files; this server only handles /api/* routes.

Run via `bincio serve` CLI command.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("bincio.serve")

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from bincio.serve.db import (
    User,
    authenticate,
    create_invite,
    create_session,
    count_users,
    create_user,
    delete_session,
    get_invite,
    get_member_tree,
    get_session,
    get_setting,
    get_user,
    get_user_prefs,
    set_user_prefs,
    list_invites,
    list_users,
    open_db,
    use_invite,
)

from pydantic import BaseModel, Field

# ── Pydantic request/response models ─────────────────────────────────────────


class LoginRequest(BaseModel):
    handle: str = Field(..., description="User handle (username)")
    password: str = Field(..., description="User password")


class LoginResponse(BaseModel):
    ok: bool = Field(True, description="Success flag")
    handle: str = Field(..., description="User handle")
    display_name: str = Field(..., description="User's display name")


class ResetPasswordRequest(BaseModel):
    handle: str = Field(..., description="User handle")
    code: str = Field(..., description="Reset code (24 hours valid)")
    password: str = Field(..., description="New password (min 8 chars)")


class RegisterRequest(BaseModel):
    code: str = Field(..., description="Invite code")
    handle: str = Field(..., description="Desired username (lowercase, 1-30 chars)")
    password: str = Field(..., description="Password (min 8 characters)")
    display_name: str = Field(default="", description="Full name (optional, defaults to handle)")


class RegisterResponse(BaseModel):
    ok: bool = Field(True, description="Success flag")
    handle: str = Field(..., description="New user's handle")


class CurrentUserResponse(BaseModel):
    handle: str = Field(..., description="User handle")
    display_name: str = Field(..., description="User's display name")
    is_admin: bool = Field(..., description="Whether user is an admin")
    store_originals_default: bool = Field(
        default=True,
        description="Instance-wide default for storing original files"
    )


class ActivityEditRequest(BaseModel):
    title: str | None = Field(default=None, description="Activity title")
    description: str | None = Field(default=None, description="Activity description (markdown)")
    sport: str | None = Field(default=None, description="Sport type")
    private: bool | None = Field(default=None, description="Hide from public feed")
    highlight: bool | None = Field(default=None, description="Mark as favorite")
    gear: str | None = Field(default=None, description="Gear used (e.g., 'Trek Domane')")


class ActivityEditResponse(BaseModel):
    ok: bool = Field(True, description="Success flag")


class ResetPasswordCodeResponse(BaseModel):
    ok: bool = Field(True, description="Success flag")
    code: str = Field(..., description="One-time reset code")
    expires_in_hours: int = Field(24, description="Code validity period in hours")


class GenericResponse(BaseModel):
    ok: bool = Field(True, description="Success flag")


# ── Active job tracker ───────────────────────────────────────────────────────
# Tracks in-progress upload/processing jobs so admins can see what's running.
# Jobs are added when a streaming upload starts and removed when it finishes.

_jobs_lock = threading.Lock()
_active_jobs: dict[str, dict] = {}


def _job_start(user_handle: str, total_files: int) -> str:
    job_id = uuid.uuid4().hex[:8]
    with _jobs_lock:
        _active_jobs[job_id] = {
            "id": job_id,
            "user": user_handle,
            "started_at": int(time.time()),
            "total": total_files,
            "done": 0,
            "current": "",
        }
    return job_id


def _job_update(job_id: str, done: int, current: str) -> None:
    with _jobs_lock:
        if job_id in _active_jobs:
            _active_jobs[job_id]["done"] = done
            _active_jobs[job_id]["current"] = current


def _job_finish(job_id: str) -> None:
    with _jobs_lock:
        _active_jobs.pop(job_id, None)


# ── Globals (set by CLI before uvicorn starts) ────────────────────────────────

data_dir: Path | None = None
site_dir: Path | None = None   # for post-write rebuild trigger
webroot: Path | None = None    # nginx webroot — when set, trigger full rebuild + rsync
strava_client_id: str = ""
strava_client_secret: str = ""
public_url: str = ""   # e.g. "https://yourdomain.com" — used for OAuth redirect URIs
dem_url: str = "https://api.open-elevation.com"  # Open-Elevation-compatible API base URL
_db = None  # sqlite3.Connection, opened lazily


def _get_db():
    global _db
    if _db is None:
        _db = open_db(_get_data_dir())
    return _db


_STRAVA_CREDS_FILE = "strava_credentials.json"


def _strava_creds(handle: str) -> tuple[str, str]:
    """Return (client_id, client_secret) for a user.

    Per-user credentials stored in {user_dir}/strava_credentials.json take
    precedence over the global instance-level strava_client_id/secret.
    Returns ("", "") when neither is configured.
    """
    creds_path = _get_data_dir() / handle / _STRAVA_CREDS_FILE
    if creds_path.exists():
        try:
            d = json.loads(creds_path.read_text(encoding="utf-8"))
            cid = str(d.get("client_id", "")).strip()
            csec = str(d.get("client_secret", "")).strip()
            if cid and csec:
                return cid, csec
        except Exception:
            pass
    return strava_client_id, strava_client_secret


def _get_data_dir() -> Path:
    if data_dir is None:
        raise HTTPException(500, "Server not configured")
    return data_dir


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="BincioActivity Serve")


@app.on_event("startup")
async def _cleanup_orphaned_tmp_zips() -> None:
    """Remove tmp*.zip files left in user data dirs by the pre-fix upload handler."""
    import glob as _glob
    data_dir = _get_data_dir()
    for p in _glob.glob(str(data_dir / "*" / "tmp*.zip")):
        try:
            Path(p).unlink()
        except Exception:
            pass


app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://localhost(:\d+)?",
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

_VALID_HANDLE = re.compile(r'^[a-z0-9][a-z0-9_-]{0,29}$')
from bincio.edit.ops import VALID_ACTIVITY_ID as _VALID_ACTIVITY_ID
_SESSION_COOKIE = "bincio_session"
_COOKIE_MAX_AGE = 30 * 86400  # 30 days


def _check_id(activity_id: str) -> str:
    if not _VALID_ACTIVITY_ID.match(activity_id):
        raise HTTPException(400, "Invalid activity ID")
    return activity_id

# ── Rate limiting (simple in-memory, per IP) ──────────────────────────────────

_login_attempts: dict[str, list[float]] = {}
_register_attempts: dict[str, list[float]] = {}
_RATE_WINDOW = 900   # 15 minutes
_LOGIN_RATE_LIMIT    = 10
_REGISTER_RATE_LIMIT = 5


def _check_rate_limit(
    ip: str,
    store: dict[str, list[float]],
    limit: int,
    msg: str = "Too many attempts. Try again later.",
) -> None:
    now = time.time()
    attempts = [t for t in store.get(ip, []) if now - t < _RATE_WINDOW]
    store[ip] = attempts
    if len(attempts) >= limit:
        raise HTTPException(429, msg)
    attempts.append(now)
    store[ip] = attempts


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _current_user(bincio_session: Optional[str] = Cookie(default=None)) -> Optional[User]:
    if not bincio_session:
        return None
    return get_session(_get_db(), bincio_session)


def _require_user(bincio_session: Optional[str] = Cookie(default=None)) -> User:
    user = _current_user(bincio_session)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def _require_admin(bincio_session: Optional[str] = Cookie(default=None)) -> User:
    user = _require_user(bincio_session)
    if not user.is_admin:
        raise HTTPException(403, "Admin required")
    return user


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_SESSION_COOKIE,
        value=token,
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # nginx/caddy handles TLS termination
    )


# ── Image upload constants ────────────────────────────────────────────────────

_ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB


def _unique_image_name(directory: Path, filename: str) -> str:
    """Return a filename that does not collide with existing files in directory."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = filename
    counter = 1
    while (directory / candidate).exists():
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


# ── Post-write rebuild ────────────────────────────────────────────────────────

# Serialises concurrent rebuilds — only one full build runs at a time.
# A second upload that arrives while a build is in progress will queue and
# run after the first finishes, picking up all data written in between.
_rebuild_lock = threading.Lock()


def _trigger_rebuild(handle: str) -> None:
    """Asynchronously re-merge and optionally rebuild + rsync the site.

    - Without --webroot: fast path — merges sidecars + rewrites root manifest
      (~1 s).  New activity pages require the nginx try_files fallback to work.
    - With --webroot: full Astro build + rsync to the nginx webroot (~30–60 s,
      serialised).  New activity pages are immediately accessible.
    """
    if site_dir is None:
        return
    if not _VALID_HANDLE.match(handle):
        return  # safety: never pass untrusted strings to subprocess

    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    _data_dir = str(data_dir)
    _site_dir = str(site_dir)
    _webroot  = str(webroot) if webroot else None
    _handle   = handle

    def _run() -> None:
        try:
            if _webroot is None:
                # Fast: only update data, skip Astro build.
                # Serialised with the same lock: merge_all wipes and recreates
                # _merged/activities/ — concurrent runs would corrupt each other.
                log.info("rebuild[%s]: merge-only (no webroot)", _handle)
                with _rebuild_lock:
                    result = subprocess.run(
                        [uv, "run", "bincio", "render",
                         "--data-dir", _data_dir,
                         "--site-dir", _site_dir,
                         "--handle", _handle,
                         "--no-build"],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        log.error("rebuild[%s]: merge failed (rc=%d):\n%s\n%s",
                                  _handle, result.returncode, result.stdout, result.stderr)
                    else:
                        log.info("rebuild[%s]: merge done", _handle)
            else:
                # Full build + rsync — serialised so concurrent uploads don't race
                log.info("rebuild[%s]: full build + rsync to %s", _handle, _webroot)
                with _rebuild_lock:
                    result = subprocess.run(
                        [uv, "run", "bincio", "render",
                         "--data-dir", _data_dir,
                         "--site-dir", _site_dir,
                         "--handle", _handle],
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        log.error("rebuild[%s]: build failed (rc=%d):\n%s\n%s",
                                  _handle, result.returncode, result.stdout, result.stderr)
                    else:
                        log.info("rebuild[%s]: build done, rsyncing", _handle)
                        # Prune dist/data/ before rsync: Astro resolves the
                        # public/data symlink and copies all activity JSON into
                        # dist/, but nginx already serves /data/ directly from
                        # the live data dir — rsyncing it would duplicate GBs.
                        dist_data = Path(_site_dir) / "dist" / "data"
                        if dist_data.exists():
                            shutil.rmtree(dist_data)
                        rsync = subprocess.run(
                            ["rsync", "-a", "--delete", "--exclude=data/",
                             f"{_site_dir}/dist/", _webroot + "/"],
                            capture_output=True,
                            text=True,
                        )
                        if rsync.returncode != 0:
                            log.error("rebuild[%s]: rsync failed (rc=%d):\n%s\n%s",
                                      _handle, rsync.returncode, rsync.stdout, rsync.stderr)
                        else:
                            log.info("rebuild[%s]: rsync done", _handle)
        except Exception:
            log.exception("rebuild[%s]: unexpected error", _handle)

    threading.Thread(target=_run, daemon=True).start()


# ── Auth endpoints ────────────────────────────────────────────────────────────

@app.get("/api/me", response_model=CurrentUserResponse)
async def me(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _current_user(bincio_session)
    if not user:
        raise HTTPException(404, "Not authenticated")
    store_orig = get_setting(_get_db(), "store_originals")
    return JSONResponse({
        "handle": user.handle,
        "display_name": user.display_name,
        "is_admin": user.is_admin,
        "store_originals_default": store_orig != "false",
        "dem_configured": bool(dem_url),
    })


@app.get("/api/stats")
async def stats() -> JSONResponse:
    """Public endpoint: member count, join dates, and invitation tree."""
    import time as _time
    now = int(_time.time())
    members = get_member_tree(_get_db())
    return JSONResponse({
        "user_count": len(members),
        "members": [
            {
                "handle": m["handle"],
                "display_name": m["display_name"],
                "member_since": m["created_at"],
                "member_for_days": (now - m["created_at"]) // 86400,
                "invited_by": m["invited_by"],
            }
            for m in members
        ],
    })


@app.post("/api/auth/login", response_model=LoginResponse)
async def login(
    login_req: LoginRequest,
    request: Request,
) -> JSONResponse:
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip, _login_attempts, _LOGIN_RATE_LIMIT, "Too many login attempts. Try again later.")

    handle = login_req.handle.strip().lower()
    password = login_req.password

    user = authenticate(_get_db(), handle, password)
    if not user:
        raise HTTPException(401, "Invalid credentials")

    token = create_session(_get_db(), handle)
    resp = JSONResponse({"ok": True, "handle": user.handle, "display_name": user.display_name})
    _set_session_cookie(resp, token)
    return resp


@app.post("/api/auth/logout", response_model=GenericResponse)
async def logout(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    if bincio_session:
        delete_session(_get_db(), bincio_session)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@app.post("/api/auth/reset-password", response_model=GenericResponse)
async def reset_password(reset_req: ResetPasswordRequest) -> JSONResponse:
    """Validate a reset code and set a new password. Public endpoint."""
    from bincio.serve.db import use_reset_code, change_password
    handle = reset_req.handle.strip().lower()
    code   = reset_req.code.strip().upper()
    new_pw = reset_req.password
    if len(new_pw) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")
    db = _get_db()
    if not use_reset_code(db, code, handle):
        raise HTTPException(400, "Invalid or expired reset code")
    change_password(db, handle, new_pw)
    return JSONResponse({"ok": True})


# ── Registration ──────────────────────────────────────────────────────────────

@app.post("/api/register", response_model=RegisterResponse)
async def register(
    register_req: RegisterRequest,
    request: Request,
) -> JSONResponse:
    ip = request.client.host if request.client else "unknown"
    _check_rate_limit(ip, _register_attempts, _REGISTER_RATE_LIMIT, "Too many registration attempts. Try again later.")

    code     = register_req.code.strip().upper()
    handle   = register_req.handle.strip().lower()
    password = register_req.password
    display  = register_req.display_name.strip() or handle

    if not _VALID_HANDLE.match(handle):
        raise HTTPException(400, "Invalid handle (lowercase letters, numbers, _ - only; max 30 chars)")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters")

    invite = get_invite(_get_db(), code)
    if not invite or invite.used:
        raise HTTPException(400, "Invalid or already-used invite code")
    if get_user(_get_db(), handle):
        raise HTTPException(409, "Handle already taken")

    max_users_val = get_setting(_get_db(), "max_users")
    if max_users_val is not None:
        limit = int(max_users_val)
        if limit > 0 and count_users(_get_db()) >= limit:
            raise HTTPException(403, f"This instance has reached its user limit ({limit})")

    create_user(_get_db(), handle, display, password, is_admin=False)
    use_invite(_get_db(), code, handle)

    # Create per-user directories
    dd = _get_data_dir()
    user_dir = dd / handle
    (user_dir / "activities").mkdir(parents=True, exist_ok=True)
    (user_dir / "edits").mkdir(parents=True, exist_ok=True)

    # Write an empty index.json so the shard URL resolves immediately,
    # even before the user uploads any activities.
    from bincio.extract.writer import write_index
    index_path = user_dir / "index.json"
    if not index_path.exists():
        write_index([], user_dir, {"handle": handle, "display_name": display or handle})

    # Update root manifest so the new user's shard is discoverable immediately
    from bincio.render.cli import _write_root_manifest
    _write_root_manifest(dd)

    # Rebuild site so the new user's profile pages exist immediately
    _trigger_rebuild(handle)

    token = create_session(_get_db(), handle)
    resp = JSONResponse({"ok": True, "handle": handle})
    _set_session_cookie(resp, token)
    return resp


# ── Invites ───────────────────────────────────────────────────────────────────

@app.get("/api/invites")
async def get_invites(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _require_user(bincio_session)
    invites = list_invites(_get_db(), user.handle)
    return JSONResponse([{
        "code": i.code,
        "used": i.used,
        "used_by": i.used_by,
        "created_at": i.created_at,
        "used_at": i.used_at,
    } for i in invites])


@app.post("/api/invites")
async def post_invite(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _require_user(bincio_session)
    try:
        code = create_invite(_get_db(), user.handle)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return JSONResponse({"ok": True, "code": code})


# ── Admin ─────────────────────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_users(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    _require_admin(bincio_session)
    users = list_users(_get_db())
    return JSONResponse([{
        "handle": u.handle,
        "display_name": u.display_name,
        "is_admin": u.is_admin,
        "created_at": u.created_at,
    } for u in users])


@app.get("/api/admin/jobs")
async def admin_jobs(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Return currently active upload/processing jobs. Admin only."""
    _require_admin(bincio_session)
    with _jobs_lock:
        jobs = list(_active_jobs.values())
    return JSONResponse(jobs)


@app.get("/api/admin/disk")
async def admin_disk(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Per-user disk usage breakdown. Admin only."""
    _require_admin(bincio_session)
    import shutil

    data_dir = _get_data_dir()

    def _mb(path: Path) -> float:
        if not path.exists():
            return 0.0
        # Use lstat to count symlink entries (few bytes each) rather than following
        # the link to the target — prevents _merged/ from double-counting activities/.
        total = sum(f.lstat().st_size for f in path.rglob("*") if f.is_file() or f.is_symlink())
        return round(total / 1_048_576, 1)

    def _count(path: Path, pattern: str = "*") -> int:
        if not path.exists():
            return 0
        return sum(1 for f in path.glob(pattern) if f.is_file())

    db = _get_db()
    from bincio.serve.db import get_user as _get_user
    users = []
    for user_dir in sorted(data_dir.iterdir()):
        if not user_dir.is_dir() or user_dir.name.startswith("_"):
            continue
        # leaked tmp zips
        leaked = [f for f in user_dir.glob("tmp*.zip") if f.is_file()]
        users.append({
            "handle": user_dir.name,
            "in_db": _get_user(db, user_dir.name) is not None,
            "total_mb": _mb(user_dir),
            "activities_mb": _mb(user_dir / "activities"),
            "activities_count": _count(user_dir / "activities", "*.json"),
            "merged_mb": _mb(user_dir / "_merged"),
            "originals_mb": _mb(user_dir / "originals"),
            "originals_strava_mb": _mb(user_dir / "originals" / "strava"),
            "images_mb": _mb(user_dir / "edits" / "images"),
            "leaked_zips_mb": round(sum(f.stat().st_size for f in leaked) / 1_048_576, 1),
            "leaked_zips_count": len(leaked),
        })

    disk = shutil.disk_usage("/")
    return JSONResponse({
        "disk": {
            "total_gb": round(disk.total / 1_073_741_824, 1),
            "used_gb": round(disk.used / 1_073_741_824, 1),
            "free_gb": round(disk.free / 1_073_741_824, 1),
            "percent": round(disk.used / disk.total * 100, 1),
        },
        "users": users,
    })


@app.post("/api/admin/users/{handle}/reset-password-code", response_model=ResetPasswordCodeResponse)
async def admin_reset_password_code(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Generate a one-time password reset code for a user. Admin only."""
    from bincio.serve.db import create_reset_code
    admin = _require_admin(bincio_session)
    db = _get_db()
    if not get_user(db, handle):
        raise HTTPException(404, f"User '{handle}' not found")
    code = create_reset_code(db, handle, admin.handle)
    return JSONResponse({"ok": True, "code": code, "expires_in_hours": 24})


@app.post("/api/admin/users/{handle}/rebuild")
async def admin_rebuild(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Trigger a merge_all + site rebuild for a user. Admin only."""
    _require_admin(bincio_session)
    user_dir = _get_data_dir() / handle
    if not user_dir.is_dir():
        raise HTTPException(404, f"No data directory for user '{handle}'")
    _trigger_rebuild(handle)
    return JSONResponse({"ok": True})


@app.post("/api/admin/users/{handle}/rebuild-sync")
async def admin_rebuild_sync(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Run merge+rebuild synchronously and return full output. Admin only.

    Unlike /rebuild (fire-and-forget), this blocks until done and returns stdout/stderr.
    Use for debugging when you need to see what went wrong.
    """
    _require_admin(bincio_session)
    user_dir = _get_data_dir() / handle
    if not user_dir.is_dir():
        raise HTTPException(404, f"No data directory for user '{handle}'")
    if site_dir is None:
        raise HTTPException(503, "Server has no --site-dir configured; rebuild not available")

    uv = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    cmd = [uv, "run", "bincio", "render",
           "--data-dir", str(data_dir),
           "--site-dir", str(site_dir),
           "--handle", handle,
           "--no-build"]
    if webroot:
        cmd = [uv, "run", "bincio", "render",
               "--data-dir", str(data_dir),
               "--site-dir", str(site_dir),
               "--handle", handle]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    resp: dict[str, Any] = {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }

    if result.returncode == 0 and webroot:
        dist_data = site_dir / "dist" / "data"
        if dist_data.exists():
            shutil.rmtree(dist_data)
        rsync = subprocess.run(
            ["rsync", "-a", "--delete", "--exclude=data/",
             f"{site_dir}/dist/", str(webroot) + "/"],
            capture_output=True, text=True, timeout=120,
        )
        resp["rsync_returncode"] = rsync.returncode
        resp["rsync_stdout"] = rsync.stdout
        resp["rsync_stderr"] = rsync.stderr
        resp["ok"] = rsync.returncode == 0

    return JSONResponse(resp)


@app.post("/api/admin/users/{handle}/reextract-originals")
async def admin_reextract_originals(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> StreamingResponse:
    """Re-extract activities from stored Strava originals without hitting the API.

    Spawns `bincio reextract-originals` as a subprocess so heavy memory use
    is isolated from the server process. Streams its JSON-lines output as SSE.
    Triggers a full rebuild on completion.
    """
    import asyncio
    _require_admin(bincio_session)
    user_dir = _get_data_dir() / handle
    originals_dir = user_dir / "originals" / "strava"
    if not originals_dir.exists():
        raise HTTPException(404, f"No Strava originals directory for '{handle}'")

    # Use the bincio script from the same venv bin dir as the running Python.
    # This is reliable in systemd environments where PATH may not include uv.
    import sys as _sys
    bincio_exe = str(Path(_sys.executable).parent / "bincio")
    data_dir = str(_get_data_dir())

    # Count originals so we can split into memory-safe batches.
    total_originals = len(list(originals_dir.glob("*.json")))
    # Each activity can briefly peak at ~10–30 MB; 100 per batch keeps RSS
    # well under 3 GB even on a cheap VPS.
    _BATCH = 100
    log.info("reextract[%s]: %d originals, batch size %d, via %s",
             handle, total_originals, _BATCH, bincio_exe)

    async def event_stream():
        total_imported = total_skipped = total_errors = 0
        offset = 0

        while offset < total_originals:
            limit = min(_BATCH, total_originals - offset)
            proc = await asyncio.create_subprocess_exec(
                bincio_exe, "reextract-originals",
                "--data-dir", data_dir,
                "--handle", handle,
                "--offset", str(offset),
                "--limit", str(limit),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert proc.stdout is not None

            async for raw_line in proc.stdout:
                line = raw_line.decode(errors="replace").strip()
                if not line:
                    continue
                yield f"data: {line}\n\n"
                try:
                    evt = json.loads(line)
                    if evt.get("type") == "done":
                        total_imported += evt.get("imported", 0)
                        total_skipped += evt.get("skipped", 0)
                        total_errors += evt.get("errors", 0)
                except Exception:
                    pass

            await proc.wait()
            if proc.returncode != 0:
                stderr_out = await proc.stderr.read() if proc.stderr else b""
                log.error("reextract[%s]: batch offset=%d exited %d — stderr: %s",
                          handle, offset, proc.returncode,
                          stderr_out.decode(errors="replace")[:500])
                yield f"data: {json.dumps({'type': 'error', 'message': f'Batch {offset}–{offset+limit} exited with code {proc.returncode}'})}\n\n"
                return  # stop on batch failure

            offset += limit

        # All batches complete
        log.info("reextract[%s]: all batches done — imported=%d skipped=%d errors=%d; triggering rebuild",
                 handle, total_imported, total_skipped, total_errors)
        _trigger_rebuild(handle)
        yield f"data: {json.dumps({'type': 'done', 'imported': total_imported, 'skipped': total_skipped, 'errors': total_errors})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/admin/users/{handle}/diag")
async def admin_diag(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Return a diagnostic snapshot of a user's data directory. Admin only."""
    _require_admin(bincio_session)
    user_dir = _get_data_dir() / handle
    if not user_dir.is_dir():
        raise HTTPException(404, f"No data directory for user '{handle}'")

    def _count(path: Path, glob: str = "*") -> int:
        return sum(1 for f in path.glob(glob) if f.is_file()) if path.exists() else 0

    def _size_mb(path: Path) -> float:
        if not path.exists():
            return 0.0
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) / 1_048_576

    activities_dir = user_dir / "activities"
    merged_dir = user_dir / "_merged"
    originals_dir = user_dir / "originals"
    uploads_dir = user_dir / "_uploads"

    merged_index = merged_dir / "index.json"
    root_index = user_dir / "index.json"

    merged_activity_count: int | None = None
    if merged_index.exists():
        try:
            idx = json.loads(merged_index.read_text())
            merged_activity_count = len(idx.get("activities", []))
        except Exception:
            merged_activity_count = -1

    root_activity_count: int | None = None
    if root_index.exists():
        try:
            idx = json.loads(root_index.read_text())
            root_activity_count = len(idx.get("activities", []))
        except Exception:
            root_activity_count = -1

    # Peek at a few filenames in activities/ to understand the actual state
    acts_sample: list[str] = []
    acts_symlinks = 0
    if activities_dir.exists():
        for f in sorted(activities_dir.iterdir())[:10]:
            acts_sample.append(f.name + (" → symlink" if f.is_symlink() else ""))
            if f.is_symlink():
                acts_symlinks += 1

    # Check _merged/activities/ separately
    merged_acts_dir = merged_dir / "activities"
    merged_acts_json = _count(merged_acts_dir, "*.json")
    merged_acts_geojson = _count(merged_acts_dir, "*.geojson")

    # List pending files
    pending_files: list[str] = []
    if uploads_dir.exists():
        pending_files = [f.name for f in uploads_dir.iterdir() if f.is_file()]

    return JSONResponse({
        "handle": handle,
        "user_dir": str(user_dir),
        "activities": {
            "json_files": _count(activities_dir, "*.json"),
            "geojson_files": _count(activities_dir, "*.geojson"),
            "size_mb": round(_size_mb(activities_dir), 2),
            "sample": acts_sample,
            "symlink_count": acts_symlinks,
        },
        "originals": {
            "exists": originals_dir.exists(),
            "size_mb": round(_size_mb(originals_dir), 2),
            "strava_originals": _count(originals_dir / "strava", "*.json") if (originals_dir / "strava").exists() else 0,
        },
        "merged": {
            "exists": merged_dir.exists(),
            "activity_count_in_index": merged_activity_count,
            "size_mb": round(_size_mb(merged_dir), 2),
            "activities_json": merged_acts_json,
            "activities_geojson": merged_acts_geojson,
        },
        "root_index": {
            "exists": root_index.exists(),
            "activity_count": root_activity_count,
        },
        "pending_uploads": len(pending_files),
        "pending_files": pending_files,
        "dedup_cache_exists": (user_dir / ".bincio_cache.json").exists(),
        "athlete_json_exists": (user_dir / "athlete.json").exists(),
    })


def _wipe_user_activities(user_dir: Path) -> int:
    """Delete all extracted activity files and caches for a user.

    Removes activities/ (JSON + GeoJSON + timeseries), edits/, originals/,
    _merged/, index.json, athlete.json, and the dedup cache.
    Leaves the user directory itself intact (account remains in the DB).
    Returns the number of files deleted.
    """
    import shutil
    deleted = 0

    for subdir in ("activities", "edits", "originals"):
        d = user_dir / subdir
        if d.exists():
            for f in d.rglob("*"):
                if f.is_file():
                    deleted += 1
            shutil.rmtree(d)

    for name in ("_merged", ):
        d = user_dir / name
        if d.exists():
            shutil.rmtree(d)

    for name in ("index.json", "athlete.json", ".bincio_cache.json"):
        f = user_dir / name
        if f.exists():
            f.unlink()
            deleted += 1

    return deleted


@app.delete("/api/admin/users/{handle}/activities")
async def admin_delete_activities(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Delete all activity data for a user and wipe the merged cache."""
    _require_admin(bincio_session)
    user_dir = _get_data_dir() / handle
    if not user_dir.is_dir():
        raise HTTPException(404, f"No data directory for user '{handle}'")

    deleted = _wipe_user_activities(user_dir)
    _trigger_rebuild(handle)
    return JSONResponse({"ok": True, "deleted": deleted})


@app.delete("/api/admin/users/{handle}/directory")
async def admin_delete_user_directory(
    handle: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Delete the entire user directory from disk (for ghost users not in the DB).

    Refuses if the handle exists as an account in the database — use
    DELETE /api/admin/users/{handle}/activities for registered users.
    """
    import shutil
    _require_admin(bincio_session)
    db = _get_db()
    from bincio.serve.db import get_user as _get_user
    if _get_user(db, handle) is not None:
        raise HTTPException(
            400,
            f"User '{handle}' is still in the database. Remove the account first, "
            "or use 'Reset data' to wipe only activity files.",
        )
    user_dir = _get_data_dir() / handle
    if not user_dir.is_dir():
        raise HTTPException(404, f"No directory for '{handle}'")
    shutil.rmtree(user_dir)
    # Rebuild root manifest so the ghost shard disappears from the site
    from bincio.render.cli import _write_root_manifest
    try:
        _write_root_manifest(_get_data_dir())
    except Exception:
        pass
    return JSONResponse({"ok": True})



# ── Self-service user settings ────────────────────────────────────────────────

@app.get("/api/me/storage")
async def me_storage(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Return per-category disk usage for the logged-in user."""
    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle

    def _mb(path: Path) -> float:
        if not path.exists():
            return 0.0
        total = sum(f.lstat().st_size for f in path.rglob("*") if f.is_file() or f.is_symlink())
        return round(total / 1_048_576, 2)

    def _count(path: Path, pattern: str = "*") -> int:
        if not path.exists():
            return 0
        return sum(1 for f in path.glob(pattern) if f.is_file())

    activities_mb   = _mb(dd / "activities")
    originals_mb    = _mb(dd / "originals")
    strava_mb       = _mb(dd / "originals" / "strava")
    images_mb       = _mb(dd / "edits" / "images")
    total_mb        = _mb(dd)

    return JSONResponse({
        "total_mb":          total_mb,
        "activities_mb":     activities_mb,
        "activities_count":  _count(dd / "activities", "*.json"),
        "originals_mb":      originals_mb,
        "strava_originals_mb": strava_mb,
        "strava_originals_count": _count(dd / "originals" / "strava", "*.json"),
        "images_mb":         images_mb,
    })


@app.delete("/api/me/originals")
async def me_delete_originals(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Delete the user's originals/ directory (frees space after re-extraction)."""
    user = _require_user(bincio_session)
    originals = _get_data_dir() / user.handle / "originals"
    if not originals.exists():
        return JSONResponse({"ok": True, "freed_mb": 0.0})

    freed = round(
        sum(f.stat().st_size for f in originals.rglob("*") if f.is_file()) / 1_048_576, 2
    )
    shutil.rmtree(originals)
    return JSONResponse({"ok": True, "freed_mb": freed})


@app.delete("/api/me/activities")
async def me_delete_activities(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Wipe all extracted activity data (activities/, edits/, _merged/, index/athlete JSON).

    Requires the user's current password in the request body for confirmation.
    """
    user = _require_user(bincio_session)
    body = await request.json()
    password = body.get("password", "")
    if not authenticate(_get_db(), user.handle, password):
        raise HTTPException(401, "Wrong password")

    user_dir = _get_data_dir() / user.handle
    deleted = _wipe_user_activities(user_dir)
    _trigger_rebuild(user.handle)
    return JSONResponse({"ok": True, "deleted": deleted})


@app.delete("/api/me")
async def me_delete_account(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Delete the account and all data permanently.

    Requires the user's current password. Deletes the DB row, all sessions,
    and the entire user data directory. The root shard manifest is updated.
    """
    user = _require_user(bincio_session)
    body = await request.json()
    password = body.get("password", "")
    if not authenticate(_get_db(), user.handle, password):
        raise HTTPException(401, "Wrong password")

    # Wipe data directory
    user_dir = _get_data_dir() / user.handle
    if user_dir.is_dir():
        shutil.rmtree(user_dir)

    # Remove from DB (cascades to sessions, invites, reset_codes)
    from bincio.serve.db import delete_user as _delete_user
    _delete_user(_get_db(), user.handle)

    # Update root manifest so the shard disappears
    from bincio.render.cli import _write_root_manifest
    try:
        _write_root_manifest(_get_data_dir())
    except Exception:
        pass

    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


@app.put("/api/me/display-name")
async def me_update_display_name(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Update the logged-in user's display name."""
    user = _require_user(bincio_session)
    body = await request.json()
    display_name = str(body.get("display_name", "")).strip()
    if len(display_name) > 60:
        raise HTTPException(400, "Display name too long (max 60 characters)")
    db = _get_db()
    db.execute("UPDATE users SET display_name = ? WHERE handle = ?", (display_name, user.handle))
    db.commit()
    return JSONResponse({"ok": True, "display_name": display_name})


@app.get("/api/me/prefs")
async def me_get_prefs(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Return all user preferences as a key→value dict."""
    user = _require_user(bincio_session)
    return JSONResponse(get_user_prefs(_get_db(), user.handle))


@app.put("/api/me/prefs")
async def me_set_prefs(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Upsert one or more user preferences. Body: {key: value, ...} (all strings)."""
    user = _require_user(bincio_session)
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Body must be a JSON object")
    # Coerce all values to strings; ignore unknown keys silently
    prefs = {str(k): str(v) for k, v in body.items()}
    set_user_prefs(_get_db(), user.handle, prefs)
    return JSONResponse({"ok": True})


@app.get("/api/me/strava-credentials")
async def me_get_strava_credentials(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Return whether per-user Strava credentials are configured (never returns the secret)."""
    user = _require_user(bincio_session)
    creds_path = _get_data_dir() / user.handle / _STRAVA_CREDS_FILE
    has_user_creds = False
    client_id_hint = ""
    if creds_path.exists():
        try:
            d = json.loads(creds_path.read_text(encoding="utf-8"))
            cid = str(d.get("client_id", "")).strip()
            csec = str(d.get("client_secret", "")).strip()
            if cid and csec:
                has_user_creds = True
                client_id_hint = cid
        except Exception:
            pass
    return JSONResponse({
        "has_user_creds": has_user_creds,
        "client_id": client_id_hint,
        "instance_configured": bool(strava_client_id),
    })


@app.put("/api/me/strava-credentials")
async def me_set_strava_credentials(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Save per-user Strava credentials. Body: {client_id, client_secret}."""
    user = _require_user(bincio_session)
    body = await request.json()
    cid  = str(body.get("client_id",     "")).strip()
    csec = str(body.get("client_secret", "")).strip()
    if not cid:
        raise HTTPException(400, "client_id is required")
    creds_path = _get_data_dir() / user.handle / _STRAVA_CREDS_FILE
    # If client_secret is omitted, preserve existing secret (if any)
    if not csec:
        if creds_path.exists():
            try:
                existing = json.loads(creds_path.read_text(encoding="utf-8"))
                csec = str(existing.get("client_secret", "")).strip()
            except Exception:
                pass
    if not csec:
        raise HTTPException(400, "client_secret is required (no existing secret to preserve)")
    creds_path.write_text(
        json.dumps({"client_id": cid, "client_secret": csec}, indent=2),
        encoding="utf-8",
    )
    return JSONResponse({"ok": True})


@app.delete("/api/me/strava-credentials")
async def me_delete_strava_credentials(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Remove per-user Strava credentials (falls back to instance credentials)."""
    user = _require_user(bincio_session)
    creds_path = _get_data_dir() / user.handle / _STRAVA_CREDS_FILE
    creds_path.unlink(missing_ok=True)
    return JSONResponse({"ok": True})


@app.put("/api/me/password")
async def me_change_password(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Change the logged-in user's password. Requires current password."""
    from bincio.serve.db import change_password as _change_password
    user = _require_user(bincio_session)
    body = await request.json()
    current  = body.get("current_password", "")
    new_pw   = body.get("new_password", "")
    if not authenticate(_get_db(), user.handle, current):
        raise HTTPException(401, "Current password is wrong")
    if len(new_pw) < 8:
        raise HTTPException(400, "New password must be at least 8 characters")
    _change_password(_get_db(), user.handle, new_pw)
    return JSONResponse({"ok": True})


# ── Write API (ported from bincio edit, auth-gated) ───────────────────────────

def _user_data_dir(handle: str) -> Path:
    """Return the merged data dir for a user, for reading activity files."""
    dd = _get_data_dir()
    merged = dd / handle / "_merged"
    return merged if merged.exists() else dd / handle


def _require_owns(activity_id: str, user: User) -> Path:
    """Verify the user owns this activity (it lives in their data dir)."""
    activity_path = _user_data_dir(user.handle) / "activities" / f"{activity_id}.json"
    if not activity_path.exists():
        raise HTTPException(404, "Activity not found")
    return activity_path


@app.get("/api/activity/{activity_id}")
async def get_activity(
    activity_id: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)
    _check_id(activity_id)
    path = _require_owns(activity_id, user)
    detail = json.loads(path.read_text())
    # Normalise for EditDrawer: add `private` bool so the drawer works regardless
    # of whether the raw JSON uses the old "private" or the new "unlisted" value.
    detail["private"] = detail.get("privacy") in ("private", "unlisted")
    return JSONResponse(detail)


@app.post("/api/activity/{activity_id}", response_model=ActivityEditResponse)
async def post_activity(
    activity_id: str,
    edit_req: ActivityEditRequest,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)
    _check_id(activity_id)
    dd = _get_data_dir() / user.handle
    # Verify the activity belongs to this user before writing
    if not (dd / "activities" / f"{activity_id}.json").exists():
        raise HTTPException(404, "Activity not found")

    from bincio.edit.ops import apply_sidecar_edit
    body = edit_req.model_dump(exclude_none=True)
    # apply_sidecar_edit already calls merge_one internally — no full rebuild needed.
    apply_sidecar_edit(activity_id, body, dd)
    return JSONResponse({"ok": True})


@app.post("/api/activity/{activity_id}/recalculate-elevation/dem")
async def recalculate_elevation_dem_endpoint(
    activity_id: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Replace GPS altitude with DEM terrain elevation and recompute gain/loss.

    Requires --dem-url to be set when starting bincio serve.
    """
    user = _require_user(bincio_session)
    _check_id(activity_id)
    if not dem_url:
        raise HTTPException(503, "DEM URL not configured.")
    dd = _get_data_dir() / user.handle
    if not (dd / "activities" / f"{activity_id}.json").exists():
        raise HTTPException(404, "Activity not found")
    try:
        from bincio.extract.dem import recalculate_elevation
        from bincio.render.merge import merge_one
        result = recalculate_elevation(dd, activity_id, dem_url)
        merge_one(dd, activity_id)
        _trigger_rebuild(user.handle)
        return JSONResponse(result)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/activity/{activity_id}/recalculate-elevation/hysteresis")
async def recalculate_elevation_hysteresis_endpoint(
    activity_id: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Recompute gain/loss from original recorded elevation using source-aware hysteresis."""
    user = _require_user(bincio_session)
    _check_id(activity_id)
    dd = _get_data_dir() / user.handle
    if not (dd / "activities" / f"{activity_id}.json").exists():
        raise HTTPException(404, "Activity not found")
    try:
        from bincio.extract.dem import recalculate_elevation_hysteresis
        from bincio.render.merge import merge_one
        result = recalculate_elevation_hysteresis(dd, activity_id)
        merge_one(dd, activity_id)
        _trigger_rebuild(user.handle)
        return JSONResponse(result)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.delete("/api/activity/{activity_id}", response_model=GenericResponse)
async def delete_activity(
    activity_id: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Delete a single activity and all associated files for the logged-in user."""
    user = _require_user(bincio_session)
    _check_id(activity_id)
    dd = _get_data_dir() / user.handle
    acts_dir = dd / "activities"

    json_path = acts_dir / f"{activity_id}.json"
    if not json_path.exists():
        raise HTTPException(404, "Activity not found")

    import shutil

    # Remove the source files (activities dir)
    for suffix in (".json", ".geojson", ".timeseries.json"):
        p = acts_dir / f"{activity_id}{suffix}"
        p.unlink(missing_ok=True)

    # Remove sidecar edit and images
    sidecar = dd / "edits" / f"{activity_id}.md"
    sidecar.unlink(missing_ok=True)
    images_dir = dd / "edits" / "images" / activity_id
    if images_dir.exists():
        shutil.rmtree(images_dir)

    # Remove from the extract-level flat index so merge_all doesn't re-add
    # the summary even though the detail file is gone.
    index_path = dd / "index.json"
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            idx["activities"] = [a for a in idx.get("activities", []) if a.get("id") != activity_id]
            index_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False))
        except Exception:
            pass  # corrupt index — merge_all will clean up on next run

    # Remove from dedup cache so the file can be re-uploaded if needed
    cache_path = dd / ".bincio_cache.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cache, dict) and "activities" in cache:
                cache["activities"] = [
                    a for a in cache["activities"] if a.get("id") != activity_id
                ]
                cache_path.write_text(json.dumps(cache, indent=2, ensure_ascii=False))
        except Exception:
            pass  # corrupt cache — leave it; next extract will rebuild

    # Full merge needed: activity removed from index
    from bincio.render.merge import merge_all
    merge_all(dd)
    _trigger_rebuild(user.handle)

    return JSONResponse({"ok": True})


@app.get("/api/activity/{activity_id}/images")
async def list_images(
    activity_id: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)
    _check_id(activity_id)
    dd = _get_data_dir() / user.handle
    images_dir = dd / "edits" / "images" / activity_id
    images = sorted(p.name for p in images_dir.iterdir() if p.is_file()) if images_dir.exists() else []
    return JSONResponse({"images": images})


@app.post("/api/activity/{activity_id}/images")
async def upload_image(
    activity_id: str,
    file: UploadFile = File(...),
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)
    _check_id(activity_id)
    dd = _get_data_dir() / user.handle
    if not (dd / "activities" / f"{activity_id}.json").exists():
        raise HTTPException(404, "Activity not found")
    if not file.filename:
        raise HTTPException(400, "No filename")
    ct = file.content_type or ""
    if ct not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, "Only JPEG, PNG, WebP, or GIF images are accepted")
    contents = await file.read()
    if len(contents) > _MAX_IMAGE_BYTES:
        raise HTTPException(413, f"Image too large (max {_MAX_IMAGE_BYTES // (1024*1024)} MB)")
    images_dir = dd / "edits" / "images" / activity_id
    images_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _unique_image_name(images_dir, Path(file.filename).name)
    (images_dir / safe_name).write_bytes(contents)
    from bincio.render.merge import merge_one
    merge_one(dd, activity_id)
    return JSONResponse({"ok": True, "filename": safe_name})


@app.delete("/api/activity/{activity_id}/images/{filename}")
async def delete_image(
    activity_id: str,
    filename: str,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)
    _check_id(activity_id)
    dd = _get_data_dir() / user.handle
    import shutil
    safe_name = Path(filename).name
    target = dd / "edits" / "images" / activity_id / safe_name
    if target.exists() and target.is_file():
        target.unlink()
        if target.parent.exists() and not any(target.parent.iterdir()):
            shutil.rmtree(target.parent)
    from bincio.render.merge import merge_one
    merge_one(dd, activity_id)
    return JSONResponse({"ok": True})


@app.get("/api/athlete")
async def get_athlete(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle
    athlete_path = dd / "athlete.json"
    data: dict = {}
    if athlete_path.exists():
        data = json.loads(athlete_path.read_text(encoding="utf-8"))
    # Layer edits/athlete.yaml on top
    edits_path = dd / "edits" / "athlete.yaml"
    if edits_path.exists():
        try:
            import yaml
            edits = yaml.safe_load(edits_path.read_text(encoding="utf-8")) or {}
            for k in ("max_hr", "ftp_w", "hr_zones", "power_zones", "seasons", "gear"):
                if k in edits:
                    data[k] = edits[k]
        except Exception:
            pass
    return JSONResponse(data)


@app.post("/api/athlete")
async def save_athlete(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle
    athlete_path = dd / "athlete.json"
    if not athlete_path.exists():
        from datetime import datetime, timezone
        athlete_path.write_text(json.dumps({
            "bas_version": "1.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "power_curve": {},
        }), encoding="utf-8")
    payload = await request.json()
    edits_dir = dd / "edits"
    edits_dir.mkdir(exist_ok=True)
    overrides: dict[str, Any] = {}
    if payload.get("max_hr") is not None:
        overrides["max_hr"] = int(payload["max_hr"])
    if payload.get("ftp_w") is not None:
        overrides["ftp_w"] = int(payload["ftp_w"])
    if payload.get("hr_zones") is not None:
        overrides["hr_zones"] = [[int(lo), int(hi)] for lo, hi in payload["hr_zones"]]
    if payload.get("power_zones") is not None:
        overrides["power_zones"] = [[int(lo), int(hi)] for lo, hi in payload["power_zones"]]
    if payload.get("seasons") is not None:
        overrides["seasons"] = [
            {"name": str(s["name"]), "start": str(s["start"]), "end": str(s["end"])}
            for s in payload["seasons"]
        ]
    if payload.get("gear") is not None:
        overrides["gear"] = payload["gear"]
    import yaml
    (edits_dir / "athlete.yaml").write_text(
        yaml.dump(overrides, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    from bincio.render.merge import merge_all
    merge_all(dd)
    _trigger_rebuild(user.handle)
    return JSONResponse({"ok": True})


_SUPPORTED_SUFFIXES = {".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz"}


def _file_suffix(name: str) -> str:
    """Return the effective suffix, including .gz double-extension."""
    p = Path(name.lower())
    if p.suffix == ".gz":
        return p.stem.rsplit(".", 1)[-1].join([".", ".gz"]) if "." in p.stem else ".gz"
    return p.suffix


@app.post("/api/upload")
async def upload_activity(
    files: list[UploadFile] = File(...),
    store_original: bool = Form(False),
    overwrite: bool = Form(False),
    bincio_session: Optional[str] = Cookie(default=None),
) -> StreamingResponse:
    """Accept FIT/GPX/TCX files and/or activities.csv; stream SSE progress while processing.

    activities.csv (Strava export format) can be included in the batch to:
      - Enrich activity files in the same batch (matched by filename)
      - Retroactively update sidecars for existing activities (matched by strava_id)

    SSE events:
      {"type": "progress", "n": N, "total": T, "name": "...", "status": "imported"|"overwritten"|"duplicate"|"error"}
      {"type": "csv", "updates": N}   -- only when CSV was included
      {"type": "done", "added": N, "csv_updates": N, "duplicates": N, "overwritten": N, "errors": N}
    """
    from bincio.extract.ingest import ingest_parsed
    from bincio.extract.parsers.factory import parse_file
    from bincio.extract.writer import make_activity_id
    from bincio.render.merge import merge_all

    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle
    staging = dd / "_uploads"
    staging.mkdir(exist_ok=True)

    # Read all files into memory now (async), then process synchronously in the generator
    csv_bytes_list: list[bytes] = []
    activity_items: list[tuple[str, bytes]] = []   # (original_filename, bytes)

    for f in files:
        fname = Path(f.filename or "").name
        raw = await f.read()
        if fname.lower().endswith(".csv"):
            csv_bytes_list.append(raw)
        else:
            activity_items.append((fname, raw))

    # Build metadata from the first CSV
    metadata = None
    if csv_bytes_list:
        from bincio.extract.strava_csv import StravaMetadata
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
            tmp.write(csv_bytes_list[0])
            tmp_path = Path(tmp.name)
        try:
            metadata = StravaMetadata(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    total_files = len(activity_items)
    job_id = _job_start(user.handle, total_files) if total_files > 0 else None

    def event_stream():
        added = 0
        overwritten = 0
        duplicates = 0
        errors = 0
        any_added = False

        for n, (name, contents) in enumerate(activity_items, 1):
            if job_id:
                _job_update(job_id, n - 1, name)

            suffix = _file_suffix(name)
            if suffix not in _SUPPORTED_SUFFIXES:
                errors += 1
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'error', 'detail': 'unsupported type'})}\n\n"
                continue

            if len(contents) > 50 * 1024 * 1024:
                errors += 1
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'error', 'detail': 'file too large'})}\n\n"
                continue

            staged = staging / name
            staged.write_bytes(contents)
            kept = False
            try:
                activity = parse_file(staged)
                if metadata is not None:
                    metadata.enrich(name, activity)
                activity_id = make_activity_id(activity)
                was_overwrite = False
                if (dd / "activities" / f"{activity_id}.json").exists():
                    if not overwrite:
                        duplicates += 1
                        yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'duplicate'})}\n\n"
                        continue
                    # Overwrite: delete existing files before re-ingesting.
                    for ext in (".json", ".geojson", ".timeseries.json"):
                        (dd / "activities" / f"{activity_id}{ext}").unlink(missing_ok=True)
                    # Remove stale summary from index so ingest_parsed writes a clean one
                    index_path = dd / "index.json"
                    if index_path.exists():
                        idx = json.loads(index_path.read_text(encoding="utf-8"))
                        idx["activities"] = [a for a in idx.get("activities", []) if a.get("id") != activity_id]
                        index_path.write_text(json.dumps(idx, indent=2, ensure_ascii=False))
                    # Remove from dedup hash cache so the new file isn't blocked
                    cache_path = dd / ".bincio_cache.json"
                    if cache_path.exists():
                        try:
                            cache = json.loads(cache_path.read_text(encoding="utf-8"))
                            cache.pop(activity_id, None)
                            cache_path.write_text(json.dumps(cache, ensure_ascii=False))
                        except Exception:
                            pass
                    # Remove merged copies (merge_all will regenerate them after ingest)
                    merged_acts = dd / "_merged" / "activities"
                    if merged_acts.exists():
                        for ext in (".json", ".geojson", ".timeseries.json"):
                            p = merged_acts / f"{activity_id}{ext}"
                            if p.exists() or p.is_symlink():
                                p.unlink(missing_ok=True)
                    was_overwrite = True
                ingest_parsed(activity, dd, privacy="public")
                if store_original:
                    originals_dir = dd / "originals"
                    originals_dir.mkdir(exist_ok=True)
                    staged.rename(originals_dir / name)
                    kept = True
                if was_overwrite:
                    overwritten += 1
                else:
                    added += 1
                any_added = True
                status = 'overwritten' if was_overwrite else 'imported'
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': status})}\n\n"
            except Exception as exc:
                errors += 1
                log.error("upload[%s]: failed to process %s: %s", user.handle, name, exc, exc_info=True)
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'error', 'detail': str(exc)})}\n\n"
            finally:
                if not kept:
                    staged.unlink(missing_ok=True)

        # Retroactively apply CSV metadata to existing activities
        csv_updates = 0
        if metadata is not None:
            from bincio.extract.strava_csv import apply_csv_to_data_dir
            csv_updates = apply_csv_to_data_dir(dd, metadata)
            if csv_updates:
                yield f"data: {json.dumps({'type': 'csv', 'updates': csv_updates})}\n\n"

        if any_added or csv_updates:
            merge_all(dd)
            if any_added:
                _trigger_rebuild(user.handle)

        yield f"data: {json.dumps({'type': 'done', 'added': added, 'overwritten': overwritten, 'csv_updates': csv_updates, 'duplicates': duplicates, 'errors': errors})}\n\n"

        if job_id:
            _job_finish(job_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/upload/strava-zip")
async def upload_strava_zip(
    file: UploadFile = File(...),
    private: str = Form(default="false"),
    bincio_session: Optional[str] = Cookie(default=None),
) -> StreamingResponse:
    """Accept a Strava bulk export ZIP and stream SSE progress while processing.

    The ZIP is written to a temp file, processed activity-by-activity, then deleted.
    Originals are never kept — the UI informs the user of this upfront.
    """
    user = _require_user(bincio_session)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Please upload a .zip file")

    privacy = "unlisted" if private.lower() in ("true", "1", "yes") else "public"

    dd = _get_data_dir() / user.handle
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    zip_path = Path(tmp.name)
    try:
        while chunk := await file.read(1024 * 1024):  # 1 MB chunks
            tmp.write(chunk)
    finally:
        tmp.close()

    from bincio.extract.strava_zip import strava_zip_iter
    from bincio.render.merge import merge_all

    log.info("strava-zip[%s]: received %s, privacy=%s", user.handle, file.filename, privacy)

    def event_stream():
        any_imported = False
        imported_count = 0
        error_count = 0
        try:
            for event in strava_zip_iter(zip_path, dd, privacy=privacy):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "progress":
                    status = event.get("status")
                    if status == "imported":
                        any_imported = True
                        imported_count += 1
                    elif status == "error":
                        error_count += 1
                        log.warning("strava-zip[%s]: error on %s: %s",
                                    user.handle, event.get("name"), event.get("detail", ""))
                if event.get("type") == "done":
                    log.info("strava-zip[%s]: done — imported=%d errors=%d",
                             user.handle, imported_count, error_count)
                    if any_imported:
                        merge_all(dd)
                        _trigger_rebuild(user.handle)
        except Exception as exc:
            log.error("strava-zip[%s]: fatal error: %s", user.handle, exc, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            zip_path.unlink(missing_ok=True)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Feedback ──────────────────────────────────────────────────────────────────

_FEEDBACK_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic"}
_FEEDBACK_MAX_IMAGES = 3
_FEEDBACK_MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB


@app.post("/api/feedback")
async def submit_feedback(
    text: str = Form(""),
    images: list[UploadFile] = File(default=[]),
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    user = _require_user(bincio_session)

    text = text.strip()
    if not text and not any(f.filename for f in images):
        raise HTTPException(400, "Feedback must include text or at least one image")
    if len(images) > _FEEDBACK_MAX_IMAGES:
        raise HTTPException(400, f"Maximum {_FEEDBACK_MAX_IMAGES} images per submission")

    feedback_dir = _get_data_dir() / "_feedback"
    feedback_dir.mkdir(exist_ok=True)
    images_dir = feedback_dir / user.handle
    images_dir.mkdir(exist_ok=True)

    now = int(time.time())
    submission_id = f"{now}_{secrets.token_hex(4)}"
    saved_images: list[str] = []

    for img in images:
        if not img.filename:
            continue
        suffix = Path(img.filename).suffix.lower()
        if suffix not in _FEEDBACK_IMAGE_SUFFIXES:
            raise HTTPException(400, f"Unsupported image type '{suffix}'")
        contents = await img.read()
        if len(contents) > _FEEDBACK_MAX_IMAGE_BYTES:
            raise HTTPException(413, f"Image '{img.filename}' exceeds 2 MB limit")
        safe_name = f"{submission_id}_{Path(img.filename).name}"
        (images_dir / safe_name).write_bytes(contents)
        saved_images.append(safe_name)

    from datetime import datetime, timezone
    entry = {
        "id": submission_id,
        "handle": user.handle,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
        "text": text,
        "images": saved_images,
    }

    log_file = feedback_dir / f"{user.handle}.json"
    existing: list[dict] = []
    if log_file.exists():
        try:
            existing = json.loads(log_file.read_text())
        except Exception:
            existing = []
    existing.append(entry)
    log_file.write_text(json.dumps(existing, indent=2))

    return JSONResponse({"ok": True, "id": submission_id})


# ── Strava ────────────────────────────────────────────────────────────────────

_strava_oauth_states: set[str] = set()


@app.get("/api/strava/status")
async def strava_status(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _require_user(bincio_session)
    cid, _ = _strava_creds(user.handle)
    if not cid:
        return JSONResponse({"configured": False, "connected": False, "last_sync": None})
    dd = _get_data_dir() / user.handle
    from bincio.extract.strava_api import load_token
    token = load_token(dd)
    return JSONResponse({
        "configured": True,
        "connected": token is not None,
        "last_sync": token.get("last_sync_at") if token else None,
    })


@app.post("/api/strava/reset")
async def strava_reset(request: Request, bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Reset last_sync_at so the next sync re-fetches from a chosen point.

    mode=soft  — set to the started_at of the most recent activity on disk
                 (next sync only fetches activities newer than the last known one)
    mode=hard  — clear last_sync_at entirely
                 (next sync re-downloads full Strava history, skipping existing files)
    """
    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle
    from bincio.extract.strava_api import load_token, save_token
    token = load_token(dd)
    if token is None:
        raise HTTPException(400, "Not connected to Strava")

    body = await request.json()
    mode = body.get("mode", "soft")

    if mode == "hard":
        token.pop("last_sync_at", None)
        save_token(dd, token)
        return JSONResponse({"ok": True, "mode": "hard", "last_sync_at": None})

    # soft: find the most recent started_at across the user's merged index
    from datetime import datetime, timezone
    last_ts: int | None = None
    for index_path in [dd / "_merged" / "index.json", dd / "index.json"]:
        if not index_path.exists():
            continue
        try:
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            started_ats = [
                a.get("started_at") for a in index_data.get("activities", [])
                if a.get("started_at")
            ]
            if started_ats:
                latest = max(started_ats)
                dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
                last_ts = int(dt.astimezone(timezone.utc).timestamp())
                break
        except Exception:
            continue

    if last_ts is None:
        token.pop("last_sync_at", None)
    else:
        token["last_sync_at"] = last_ts
    save_token(dd, token)
    return JSONResponse({"ok": True, "mode": "soft", "last_sync_at": last_ts})


@app.get("/api/strava/auth-url")
async def strava_auth_url(request: Request, bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _require_user(bincio_session)
    cid, _ = _strava_creds(user.handle)
    if not cid:
        raise HTTPException(400, "Strava client ID not configured on this server")
    state = secrets.token_urlsafe(16)
    _strava_oauth_states.add(state)
    if public_url:
        redirect_uri = public_url.rstrip("/") + "/api/strava/callback"
    else:
        redirect_uri = str(request.url_for("strava_callback"))
    from bincio.extract.strava_api import auth_url
    return JSONResponse({"url": auth_url(cid, redirect_uri, state=state)})


@app.get("/api/strava/callback", name="strava_callback")
async def strava_callback(
    request: Request,
    code: str = "",
    error: str = "",
    state: str = "",
    bincio_session: Optional[str] = Cookie(default=None),
) -> RedirectResponse:
    site_origin = public_url.rstrip("/") if public_url else str(request.base_url).rstrip("/")
    if error or not code:
        return RedirectResponse(f"{site_origin}/?strava=error")
    if state not in _strava_oauth_states:
        return RedirectResponse(f"{site_origin}/?strava=error")
    _strava_oauth_states.discard(state)
    user = _current_user(bincio_session)
    if not user:
        return RedirectResponse(f"{site_origin}/?strava=error")
    cid, csec = _strava_creds(user.handle)
    if not cid or not csec:
        return RedirectResponse(f"{site_origin}/?strava=error")
    dd = _get_data_dir() / user.handle
    from bincio.extract.strava_api import StravaError, exchange_code, save_token
    try:
        token = exchange_code(cid, csec, code)
    except StravaError:
        return RedirectResponse(f"{site_origin}/?strava=error")
    save_token(dd, token)
    return RedirectResponse(f"{site_origin}/?strava=connected")


@app.get("/api/strava/sync/stream")
async def serve_strava_sync_stream(bincio_session: Optional[str] = Cookie(default=None)) -> StreamingResponse:
    """SSE endpoint — streams per-activity progress then a final summary event."""
    user = _require_user(bincio_session)
    cid, csec = _strava_creds(user.handle)
    if not cid or not csec:
        raise HTTPException(400, "Strava not configured on this server")
    dd = _get_data_dir() / user.handle
    store_orig_setting = get_setting(_get_db(), "store_originals")
    store_orig = store_orig_setting == "true"
    originals_dir = (dd / "originals" / "strava") if store_orig else None
    if originals_dir:
        originals_dir.mkdir(parents=True, exist_ok=True)

    from bincio.extract.ingest import strava_sync_iter

    def event_stream():
        try:
            for event in strava_sync_iter(dd, cid, csec, originals_dir):
                if event["type"] == "done":
                    _trigger_rebuild(user.handle)  # start before client closes connection
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/strava/sync")
async def serve_strava_sync(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    user = _require_user(bincio_session)
    cid, csec = _strava_creds(user.handle)
    if not cid or not csec:
        raise HTTPException(400, "Strava not configured on this server")
    dd = _get_data_dir() / user.handle
    store_orig_setting = get_setting(_get_db(), "store_originals")
    store_orig = store_orig_setting == "true"
    originals_dir = (dd / "originals" / "strava") if store_orig else None
    if originals_dir:
        originals_dir.mkdir(parents=True, exist_ok=True)
    from bincio.edit.ops import run_strava_sync
    try:
        result = run_strava_sync(dd, cid, csec, originals_dir=originals_dir)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    _trigger_rebuild(user.handle)
    return JSONResponse(result)


# ── Garmin Connect endpoints ──────────────────────────────────────────────────

def _garmin_user_message(exc: Exception) -> str:
    """Return a human-friendly error message for common Garmin login failures."""
    msg = str(exc)
    fallback = (
        " In the meantime, you can export your activities from Garmin Connect "
        "(garmin.com → Activities → Export) or Garmin Express as FIT files "
        "and upload them directly."
    )
    if "429" in msg or "rate limit" in msg.lower():
        return (
            "Garmin is rate-limiting this server's IP address (HTTP 429). "
            "Wait a few hours and try again." + fallback
        )
    if "403" in msg:
        return (
            "Cloudflare is blocking the login request (HTTP 403). "
            "This is a known upstream issue — try again later or update garminconnect "
            "(uv sync --extra garmin)." + fallback
        )
    if "GARMIN Authentication Application" in msg or "unexpected title" in msg.lower():
        return (
            "Garmin's login page returned a CAPTCHA or MFA challenge that "
            "cannot be completed automatically. Try again later, or disable "
            "two-factor authentication on your Garmin account." + fallback
        )
    return f"Login failed: {exc}" + fallback

@app.get("/api/garmin/status")
async def garmin_status(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Return whether Garmin credentials are stored for the current user."""
    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle
    from bincio.extract.garmin_api import has_credentials
    from bincio.extract.garmin_sync import _load_sync_state
    connected = has_credentials(dd)
    last_sync = None
    if connected:
        state = _load_sync_state(dd)
        last_sync = state.get("last_sync_at")
    return JSONResponse({"connected": connected, "last_sync": last_sync})


@app.post("/api/garmin/connect")
async def garmin_connect(
    request: Request,
    bincio_session: Optional[str] = Cookie(default=None),
) -> JSONResponse:
    """Test Garmin login with the supplied credentials and save them on success."""
    user = _require_user(bincio_session)
    body = await request.json()
    email    = (body.get("email") or "").strip()
    password = body.get("password") or ""
    if not email or not password:
        raise HTTPException(400, "email and password are required")

    data_dir = _get_data_dir()
    user_dir = data_dir / user.handle
    from bincio.extract.garmin_api import GarminError, test_login
    try:
        info = test_login(data_dir, user_dir, email, password)
    except GarminError as exc:
        raise HTTPException(400, _garmin_user_message(exc))
    return JSONResponse({"ok": True, **info})


@app.post("/api/garmin/disconnect")
async def garmin_disconnect(bincio_session: Optional[str] = Cookie(default=None)) -> JSONResponse:
    """Remove stored Garmin credentials and session for the current user."""
    user = _require_user(bincio_session)
    dd = _get_data_dir() / user.handle
    from bincio.extract.garmin_api import delete_credentials
    delete_credentials(dd)
    return JSONResponse({"ok": True})


@app.get("/api/garmin/sync/stream")
async def garmin_sync_stream(bincio_session: Optional[str] = Cookie(default=None)) -> StreamingResponse:
    """SSE endpoint — streams per-activity Garmin sync progress."""
    user = _require_user(bincio_session)
    data_dir = _get_data_dir()
    user_dir = data_dir / user.handle

    from bincio.extract.garmin_api import GarminError, has_credentials
    if not has_credentials(user_dir):
        raise HTTPException(400, "No Garmin credentials stored — connect first")

    from bincio.extract.garmin_sync import garmin_sync_iter

    def event_stream():
        try:
            for event in garmin_sync_iter(data_dir, user_dir):
                if event["type"] == "done":
                    _trigger_rebuild(user.handle)
                yield f"data: {json.dumps(event)}\n\n"
        except GarminError as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': _garmin_user_message(exc)})}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'message': _garmin_user_message(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
