"""Strava OAuth + activity API sync.

Token is stored in <data_dir>/strava_token.json:
  {access_token, refresh_token, expires_at, last_sync_at?}

Usage:
  1. Build an auth URL and redirect the user to it.
  2. Exchange the returned code for a token (exchange_code).
  3. On subsequent syncs, call ensure_fresh() then fetch_activities() + fetch_streams().
  4. Convert each result to ParsedActivity with strava_to_parsed().
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bincio.extract.models import DataPoint, LapData, ParsedActivity
from bincio.extract.sport import normalise_sport

_TOKEN_FILE = "strava_token.json"
_AUTH_URL = "https://www.strava.com/oauth/authorize"
_TOKEN_URL = "https://www.strava.com/oauth/token"
_API_BASE = "https://www.strava.com/api/v3"


class StravaError(Exception):
    pass


# ── OAuth helpers ──────────────────────────────────────────────────────────────

def auth_url(client_id: str, redirect_uri: str, state: str = "") -> str:
    """Return the Strava OAuth authorization URL."""
    params: dict[str, str] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "activity:read_all",
        "approval_prompt": "auto",
    }
    if state:
        params["state"] = state
    return f"{_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    """Exchange an authorization code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise StravaError(f"Token exchange failed: {e.code} {e.read().decode()[:200]}")


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> dict:
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise StravaError(f"Token refresh failed: {e.code} {e.read().decode()[:200]}")


# ── Token storage ──────────────────────────────────────────────────────────────

def load_token(data_dir: Path) -> Optional[dict]:
    p = data_dir / _TOKEN_FILE
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception:
        return None


def save_token(data_dir: Path, token: dict) -> None:
    (data_dir / _TOKEN_FILE).write_text(json.dumps(token, indent=2))


def ensure_fresh(data_dir: Path, client_id: str, client_secret: str) -> dict:
    """Load the stored token, refresh if expiring soon, persist and return it."""
    token = load_token(data_dir)
    if token is None:
        raise StravaError("Not connected to Strava")
    if time.time() > token.get("expires_at", 0) - 60:
        refreshed = _refresh(client_id, client_secret, token["refresh_token"])
        token.update(refreshed)
        save_token(data_dir, token)
    return token


# ── API calls ──────────────────────────────────────────────────────────────────

def _api_get(url: str, access_token: str) -> dict | list:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {access_token}"})
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise StravaError(f"Strava API {e.code}: {e.read().decode()[:200]}")


def fetch_activities(access_token: str, after: Optional[int] = None) -> list[dict]:
    """Fetch all activity summaries, paged, optionally after a Unix timestamp."""
    results: list[dict] = []
    page = 1
    while True:
        params: dict = {"per_page": 200, "page": page}
        if after:
            params["after"] = after
        qs = urllib.parse.urlencode(params)
        batch = _api_get(f"{_API_BASE}/athlete/activities?{qs}", access_token)
        if not isinstance(batch, list) or not batch:
            break
        results.extend(batch)
        if len(batch) < 200:
            break
        page += 1
    return results


def fetch_streams(access_token: str, activity_id: int) -> dict:
    """Fetch time-series streams for a single activity."""
    keys = "time,latlng,altitude,heartrate,cadence,watts,velocity_smooth"
    result = _api_get(
        f"{_API_BASE}/activities/{activity_id}/streams?keys={keys}&key_by_type=true",
        access_token,
    )
    return result if isinstance(result, dict) else {}


# ── Model conversion ───────────────────────────────────────────────────────────

def strava_meta_to_partial(meta: dict) -> ParsedActivity:
    """Build a minimal ParsedActivity from activity meta (no streams) — enough to compute the ID."""
    started_at = datetime.fromisoformat(meta["start_date"].replace("Z", "+00:00"))
    return ParsedActivity(
        points=[],
        sport=normalise_sport(meta.get("sport_type") or meta.get("type") or ""),
        started_at=started_at,
        source_file=f"strava:{meta['id']}",
        source_hash="",
        title=meta.get("name") or None,
    )


def strava_to_parsed(meta: dict, streams: dict) -> ParsedActivity:
    """Convert a Strava activity summary + streams dict to ParsedActivity."""
    started_at = datetime.fromisoformat(meta["start_date"].replace("Z", "+00:00"))
    start_ts = started_at.timestamp()

    time_data = streams.get("time", {}).get("data", [])
    latlng_data = streams.get("latlng", {}).get("data", [])
    alt_data = streams.get("altitude", {}).get("data", [])
    hr_data = streams.get("heartrate", {}).get("data", [])
    cad_data = streams.get("cadence", {}).get("data", [])
    pwr_data = streams.get("watts", {}).get("data", [])
    vel_data = streams.get("velocity_smooth", {}).get("data", [])

    def _get(lst: list, i: int):
        return lst[i] if i < len(lst) else None

    points: list[DataPoint] = []
    for i, t_offset in enumerate(time_data):
        ll = _get(latlng_data, i)
        lat, lon = (ll[0], ll[1]) if ll else (None, None)
        vel = _get(vel_data, i)
        points.append(DataPoint(
            timestamp=datetime.fromtimestamp(start_ts + t_offset, tz=timezone.utc),
            lat=lat,
            lon=lon,
            elevation_m=_get(alt_data, i),
            hr_bpm=_get(hr_data, i),
            cadence_rpm=_get(cad_data, i),
            power_w=_get(pwr_data, i),
            speed_kmh=(vel * 3.6) if vel is not None else None,
        ))

    # Deterministic source hash based on the Strava activity ID
    source = f"strava:{meta['id']}"
    source_hash = "sha256:" + hashlib.sha256(source.encode()).hexdigest()

    # Map Strava visibility to BAS privacy: only_me → unlisted, everything else → public
    visibility = meta.get("visibility") or ""
    is_private = meta.get("private", False) or visibility == "only_me"

    return ParsedActivity(
        points=points,
        sport=normalise_sport(meta.get("sport_type") or meta.get("type") or ""),
        started_at=started_at,
        source_file=source,
        source_hash=source_hash,
        title=meta.get("name") or None,
        description=meta.get("description") or None,
        strava_id=str(meta["id"]),
        privacy="unlisted" if is_private else "public",
    )
