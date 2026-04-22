"""Strava API importer for BincioActivity.

Converts Strava activities + streams into BAS JSON using the same extract
pipeline (ParsedActivity → compute() → write_activity()) as local files.

OAuth tokens are stored in ~/.config/bincio/strava.json and refreshed
automatically. No server needed — the OAuth dance uses a one-shot local
callback server (same pattern as `gh auth login`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import secrets
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from rich.console import Console
from rich.progress import BarColumn, MofNCompleteColumn, Progress, TextColumn, TimeElapsedColumn

from bincio.extract.models import DataPoint, ParsedActivity
from bincio.extract.sport import normalise_sport, normalise_sub_sport

STRAVA_AUTH_URL = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"

TOKENS_FILE    = Path.home() / ".config" / "bincio" / "strava.json"
SYNC_FILE      = "_strava_sync.json"   # lives in output_dir
CALLBACK_PORT  = 8976
STREAM_KEYS    = "time,latlng,altitude,heartrate,cadence,watts,velocity_smooth"


# ── API client ────────────────────────────────────────────────────────────────

class StravaClient:
    def __init__(self, client_id: str, client_secret: str, console: Console) -> None:
        self.client_id     = client_id
        self.client_secret = client_secret
        self._console      = console
        self._tokens: dict = {}
        self._15min_used   = 0
        self._daily_used   = 0

    # ── auth ──────────────────────────────────────────────────────────────────

    def authenticate(self) -> None:
        """Load saved tokens (refreshing if needed) or run the OAuth dance."""
        if TOKENS_FILE.exists():
            saved = json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
            if saved.get("client_id") == self.client_id:
                self._tokens = saved
                self._ensure_fresh()
                self._console.print("[green]✓[/green] Authenticated via saved tokens.")
                return
        self._oauth_dance()

    def _ensure_fresh(self) -> None:
        if time.time() > self._tokens.get("expires_at", 0) - 60:
            self._refresh()

    def _refresh(self) -> None:
        import requests
        r = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
            "grant_type":    "refresh_token",
            "refresh_token": self._tokens["refresh_token"],
        }, timeout=30)
        r.raise_for_status()
        self._tokens.update(r.json())
        self._tokens["client_id"] = self.client_id
        self._save_tokens()

    def _oauth_dance(self) -> None:
        """Open browser for OAuth2 authorization, receive callback."""
        import requests
        state       = secrets.token_urlsafe(16)
        code_holder: dict[str, str] = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                qs = parse_qs(urlparse(self.path).query)
                if qs.get("state", [None])[0] == state:
                    code_holder["code"] = qs.get("code", [None])[0] or ""
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    b"<html><body style='font-family:sans-serif;padding:2rem'>"
                    b"<h2>Authorized! You can close this tab.</h2></body></html>"
                )

            def log_message(self, *_: Any) -> None:
                pass

        server = HTTPServer(("127.0.0.1", CALLBACK_PORT), _Handler)

        params = urlencode({
            "client_id":     self.client_id,
            "redirect_uri":  f"http://localhost:{CALLBACK_PORT}/callback",
            "response_type": "code",
            "scope":         "activity:read_all",
            "state":         state,
        })
        url = f"{STRAVA_AUTH_URL}?{params}"
        self._console.print(f"Opening browser for Strava authorization…")
        self._console.print(f"If nothing opens, visit: [cyan]{url}[/cyan]")
        webbrowser.open(url)

        server.handle_request()   # blocks until one request received
        server.server_close()

        code = code_holder.get("code")
        if not code:
            raise RuntimeError("Authorization failed — no code received from Strava.")

        r = requests.post(STRAVA_TOKEN_URL, data={
            "client_id":     self.client_id,
            "client_secret": self.client_secret,
            "code":          code,
            "grant_type":    "authorization_code",
            "redirect_uri":  f"http://localhost:{CALLBACK_PORT}/callback",
        }, timeout=30)
        if not r.ok:
            raise RuntimeError(f"Token exchange failed ({r.status_code}): {r.text}")
        self._tokens = r.json()
        self._tokens["client_id"] = self.client_id
        self._save_tokens()
        self._console.print("[green]✓[/green] Authorized!")

    def _save_tokens(self) -> None:
        TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        TOKENS_FILE.write_text(json.dumps(self._tokens, indent=2), encoding="utf-8")

    # ── HTTP ──────────────────────────────────────────────────────────────────

    def _get(self, path: str, **params: Any) -> Any:
        import requests as req
        self._ensure_fresh()
        headers = {"Authorization": f"Bearer {self._tokens['access_token']}"}

        while True:
            r = req.get(f"{STRAVA_API_BASE}{path}", params=params, headers=headers, timeout=30)

            # Track rate limits
            usage = r.headers.get("X-RateLimit-Usage", "")
            if usage:
                parts = usage.split(",")
                if len(parts) == 2:
                    self._15min_used = int(parts[0])
                    self._daily_used  = int(parts[1])

            if r.status_code == 429:
                self._console.print("[yellow]Rate limit reached, waiting 60 s…[/yellow]")
                time.sleep(60)
                continue

            r.raise_for_status()

            limit_hdr = r.headers.get("X-RateLimit-Limit", "")
            if limit_hdr:
                lparts = limit_hdr.split(",")
                if len(lparts) == 2:
                    l15 = int(lparts[0])
                    if self._15min_used > int(l15 * 0.85):
                        self._console.print(
                            f"[yellow]Warning:[/yellow] {self._15min_used}/{l15} requests used this 15-min window."
                        )

            return r.json()

    # ── API calls ─────────────────────────────────────────────────────────────

    def get_activities(self, after: int | None = None, per_page: int = 200) -> list[dict]:
        """Fetch full paginated activity list. `after` is a Unix timestamp."""
        activities: list[dict] = []
        page = 1
        while True:
            params: dict[str, Any] = {"per_page": per_page, "page": page}
            if after is not None:
                params["after"] = after
            batch: list[dict] = self._get("/athlete/activities", **params)
            if not batch:
                break
            activities.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return activities

    def get_streams(self, activity_id: int) -> dict[str, list]:
        """Return {stream_type: [values...]}. Empty dict on any failure."""
        try:
            data: dict = self._get(
                f"/activities/{activity_id}/streams",
                keys=STREAM_KEYS,
                key_by_type="true",
            )
            return {k: v["data"] for k, v in data.items() if isinstance(v, dict) and "data" in v}
        except Exception:
            return {}


# ── conversion ────────────────────────────────────────────────────────────────

def _strava_to_parsed(act: dict, streams: dict[str, list]) -> ParsedActivity:
    """Build a ParsedActivity from a Strava activity dict + its streams."""
    started_at = datetime.fromisoformat(act["start_date"].replace("Z", "+00:00"))

    raw_sport = act.get("sport_type") or act.get("type") or ""
    sport     = normalise_sport(raw_sport)
    sub_sport = normalise_sub_sport(raw_sport)

    times      = streams.get("time", [])          # seconds since start
    latlngs    = streams.get("latlng", [])         # [[lat, lon], ...]
    altitudes  = streams.get("altitude", [])       # metres
    heartrates = streams.get("heartrate", [])      # bpm
    cadences   = streams.get("cadence", [])        # rpm
    watts      = streams.get("watts", [])          # W
    velocities = streams.get("velocity_smooth", [])  # m/s

    points: list[DataPoint] = []
    for i, t in enumerate(times):
        ll = latlngs[i] if i < len(latlngs) else None
        points.append(DataPoint(
            timestamp    = started_at + timedelta(seconds=int(t)),
            lat          = float(ll[0]) if ll else None,
            lon          = float(ll[1]) if ll else None,
            elevation_m  = float(altitudes[i])  if i < len(altitudes)  else None,
            hr_bpm       = int(heartrates[i])   if i < len(heartrates) else None,
            cadence_rpm  = int(cadences[i])     if i < len(cadences)   else None,
            power_w      = int(watts[i])        if i < len(watts)      else None,
            speed_kmh    = float(velocities[i]) * 3.6 if i < len(velocities) else None,
        ))

    strava_id   = str(act["id"])
    source_hash = "sha256:" + hashlib.sha256(f"strava:{strava_id}".encode()).hexdigest()

    return ParsedActivity(
        points      = points,
        sport       = sport,
        sub_sport   = sub_sport,
        started_at  = started_at,
        source_file = f"strava_{strava_id}",
        source_hash = source_hash,
        title       = act.get("name") or None,
        strava_id   = strava_id,
    )


def _patch_from_summary(metrics: Any, act: dict) -> Any:
    """Fill None metric fields using Strava activity summary values.

    Useful for activities without streams (manual entries, indoor rides with
    no sensors) where compute() returns _empty().
    """
    patches: dict[str, Any] = {}
    if metrics.distance_m     is None and act.get("distance"):
        patches["distance_m"]     = float(act["distance"])
    if metrics.moving_time_s  is None and act.get("moving_time"):
        patches["moving_time_s"]  = int(act["moving_time"])
    if metrics.duration_s     is None and act.get("elapsed_time"):
        patches["duration_s"]     = int(act["elapsed_time"])
    if metrics.elevation_gain_m is None and act.get("total_elevation_gain"):
        patches["elevation_gain_m"] = float(act["total_elevation_gain"])
    if metrics.avg_hr_bpm     is None and act.get("average_heartrate"):
        patches["avg_hr_bpm"]     = int(act["average_heartrate"])
    if metrics.max_hr_bpm     is None and act.get("max_heartrate"):
        patches["max_hr_bpm"]     = int(act["max_heartrate"])
    if metrics.avg_power_w    is None and act.get("average_watts"):
        patches["avg_power_w"]    = int(act["average_watts"])
    if metrics.avg_cadence_rpm is None and act.get("average_cadence"):
        patches["avg_cadence_rpm"] = int(act["average_cadence"])
    if metrics.avg_speed_kmh  is None and act.get("average_speed"):
        patches["avg_speed_kmh"]  = float(act["average_speed"]) * 3.6
    return dataclasses.replace(metrics, **patches) if patches else metrics


# ── main sync ─────────────────────────────────────────────────────────────────

def sync(
    client: StravaClient,
    output_dir: Path,
    since: datetime | None,
    console: Console,
    limit: int | None = None,
) -> None:
    """Fetch new Strava activities and write BAS JSON files.

    Idempotent: already-imported Strava IDs (tracked in _strava_sync.json)
    are skipped. `since` overrides the auto-detected checkpoint.
    """
    from bincio.extract.metrics import compute
    from bincio.extract.writer import build_summary, make_activity_id, write_activity, write_index

    output_dir.mkdir(parents=True, exist_ok=True)

    # ── load sync state ───────────────────────────────────────────────────────
    sync_path = output_dir / SYNC_FILE
    sync_state: dict = json.loads(sync_path.read_text(encoding="utf-8")) if sync_path.exists() else {}
    imported_ids: set[str] = set(sync_state.get("imported_ids", []))

    # ── determine `after` timestamp ───────────────────────────────────────────
    after_ts: int | None = None
    if since:
        after_ts = int(since.timestamp())
    elif sync_state.get("last_sync"):
        # 1-hour overlap to catch delayed Strava saves
        last = datetime.fromisoformat(sync_state["last_sync"])
        after_ts = int((last - timedelta(hours=1)).timestamp())
    # else: full sync (first run)

    # ── fetch activity list ───────────────────────────────────────────────────
    since_label = f" since {since.date()}" if since else (" (incremental)" if after_ts else " (full sync)")
    console.print(f"Fetching Strava activity list{since_label}…")
    all_acts = client.get_activities(after=after_ts)
    new_acts  = [a for a in all_acts if str(a["id"]) not in imported_ids]

    console.print(
        f"Found [bold]{len(new_acts)}[/bold] new activities "
        f"([bold]{len(all_acts) - len(new_acts)}[/bold] already imported)."
    )
    if limit is not None and len(new_acts) > limit:
        new_acts = new_acts[:limit]
        console.print(f"[yellow]Dev mode:[/yellow] capped to {limit} activities.")
    if not new_acts:
        console.print("[green]All up to date.[/green]")
        return

    # ── load existing index ───────────────────────────────────────────────────
    index_path = output_dir / "index.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"owner": {"handle": "strava_user"}, "activities": []}
    owner = index_data.get("owner", {})
    summaries: dict[str, dict] = {s["id"]: s for s in index_data.get("activities", [])}

    # ── import loop ───────────────────────────────────────────────────────────
    errors: list[tuple[str, str]] = []
    imported = 0

    with Progress(
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Importing…", total=len(new_acts))

        for act in new_acts:
            progress.advance(task)
            strava_id = str(act["id"])
            try:
                streams  = client.get_streams(act["id"])
                parsed   = _strava_to_parsed(act, streams)
                metrics  = compute(parsed)
                metrics  = _patch_from_summary(metrics, act)
                act_id   = make_activity_id(parsed)
                write_activity(parsed, metrics, output_dir, privacy="public")
                summaries[act_id] = build_summary(parsed, metrics, act_id, "public")
                imported_ids.add(strava_id)
                imported += 1
            except Exception as exc:
                errors.append((strava_id, str(exc)))

    # ── write index + sync state ──────────────────────────────────────────────
    write_index(list(summaries.values()), output_dir, owner)

    sync_state["imported_ids"] = sorted(imported_ids)
    sync_state["last_sync"]    = datetime.now(timezone.utc).isoformat()
    sync_path.write_text(json.dumps(sync_state, indent=2), encoding="utf-8")

    # Trigger merge if sidecar edits directory exists
    if (output_dir / "edits").exists():
        from bincio.render.merge import merge_all
        merge_all(output_dir)

    console.print(
        f"\n[green]Done.[/green] "
        f"Imported [bold]{imported}[/bold] activities, "
        f"errors [bold]{len(errors)}[/bold]."
    )
    if errors:
        console.print("\n[red]Errors:[/red]")
        for sid, msg in errors[:20]:
            console.print(f"  Strava {sid}: {msg}")
        if len(errors) > 20:
            console.print(f"  … and {len(errors) - 20} more.")
