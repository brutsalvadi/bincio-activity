"""FastAPI edit server — serves the activity edit UI and writes sidecar .md files."""

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from bincio.edit.ops import SPORTS, STAT_PANELS, VALID_ACTIVITY_ID

# Populated by the CLI before uvicorn starts
data_dir: Path | None = None
site_url: str = "http://localhost:4321"
strava_client_id: str = ""
strava_client_secret: str = ""
dem_url: str = "https://api.open-elevation.com"  # Open-Elevation-compatible API base URL

# In-memory CSRF state tokens for OAuth flows (token → True); cleared after use
_oauth_states: set[str] = set()

app = FastAPI(title="BincioActivity Edit Server", docs_url=None, redoc_url=None)

app.add_middleware(GZipMiddleware, minimum_size=1024)
# Allow localhost origins only — this server is never meant to be public
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https?://localhost(:\d+)?",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)

def _check_id(activity_id: str) -> str:
    """Reject activity IDs that contain path traversal sequences."""
    if not VALID_ACTIVITY_ID.match(activity_id):
        raise HTTPException(400, "Invalid activity ID")
    return activity_id


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


# ── HTML UI ───────────────────────────────────────────────────────────────────

_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Edit Activity</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg: #09090b; --surface: #18181b; --border: #27272a;
  --text: #fafafa; --muted: #71717a; --accent: #3b82f6;
  --accent-dim: #1d3461; --danger: #ef4444;
  --radius: 10px; --font: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
body { background: var(--bg); color: var(--text); font-family: var(--font);
  font-size: 14px; line-height: 1.5; padding: 24px; min-height: 100vh; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.25rem; font-weight: 700; }
label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
input, select, textarea {
  width: 100%; padding: 8px 12px; background: var(--surface); border: 1px solid var(--border);
  border-radius: 6px; color: var(--text); font-size: 14px; font-family: var(--font);
  outline: none; transition: border-color .15s;
}
input:focus, select:focus, textarea:focus { border-color: var(--accent); }
textarea { resize: vertical; min-height: 140px; }
.field { margin-bottom: 16px; }
.row { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
.check-group { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 4px; }
.check-item { display: flex; align-items: center; gap: 6px; cursor: pointer;
  padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px;
  user-select: none; transition: border-color .15s, background .15s; }
.check-item:hover { border-color: var(--muted); }
.check-item input[type=checkbox] { width: auto; accent-color: var(--accent); }
.check-item.active { border-color: var(--accent); background: var(--accent-dim); }
.toggle-row { display: flex; gap: 16px; }
.toggle { display: flex; align-items: center; gap: 8px; cursor: pointer;
  padding: 6px 12px; border: 1px solid var(--border); border-radius: 6px;
  transition: border-color .15s, background .15s; }
.toggle:hover { border-color: var(--muted); }
.toggle.active { border-color: var(--accent); background: var(--accent-dim); }
.toggle input { width: auto; accent-color: var(--accent); }
.drop-zone { border: 2px dashed var(--border); border-radius: var(--radius);
  padding: 24px; text-align: center; color: var(--muted); cursor: pointer;
  transition: border-color .15s; margin-top: 4px; }
.drop-zone:hover, .drop-zone.drag-over { border-color: var(--accent); color: var(--text); }
.image-list { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.image-chip { display: flex; align-items: center; gap: 6px; padding: 4px 10px;
  background: var(--surface); border: 1px solid var(--border); border-radius: 20px;
  font-size: 12px; }
.image-chip button { background: none; border: none; color: var(--muted);
  cursor: pointer; font-size: 14px; line-height: 1; padding: 0 2px; }
.image-chip button:hover { color: var(--danger); }
.actions { display: flex; gap: 12px; align-items: center; margin-top: 8px; }
.btn { padding: 8px 20px; border-radius: 6px; font-size: 14px; font-weight: 500;
  cursor: pointer; border: none; transition: opacity .15s; }
.btn:disabled { opacity: .4; cursor: default; }
.btn-primary { background: var(--accent); color: #fff; }
.btn-primary:hover:not(:disabled) { opacity: .85; }
.btn-ghost { background: transparent; border: 1px solid var(--border); color: var(--text); }
.btn-ghost:hover:not(:disabled) { border-color: var(--muted); }
.status { font-size: 13px; }
.status.ok { color: #4ade80; }
.status.err { color: var(--danger); }
.header { display: flex; align-items: baseline; gap: 16px; margin-bottom: 24px; }
.back { font-size: 13px; color: var(--muted); }
.meta { font-size: 12px; color: var(--muted); margin-top: 4px; }
.card { background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); padding: 20px; max-width: 780px; margin: 0 auto; }
.section-title { font-size: 11px; text-transform: uppercase; letter-spacing: .08em;
  color: var(--muted); margin-bottom: 14px; padding-bottom: 6px;
  border-bottom: 1px solid var(--border); }
</style>
</head>
<body>
<div style="max-width:780px;margin:0 auto">
  <div class="header">
    <a class="back" href="__SITE_URL__">← Back to site</a>
    <h1 id="page-title">Edit Activity</h1>
  </div>
  <p id="meta" class="meta" style="margin-bottom:16px"></p>

  <div class="card">
    <form id="form" autocomplete="off">
      <p class="section-title">Identity</p>
      <div class="row">
        <div class="field">
          <label for="title">Title</label>
          <input id="title" name="title" type="text" placeholder="Leave blank to keep extracted title">
        </div>
        <div class="field">
          <label for="sport">Sport</label>
          <select id="sport" name="sport">
            __SPORT_OPTIONS__
          </select>
        </div>
      </div>
      <div class="field">
        <label for="gear">Gear</label>
        <input id="gear" name="gear" type="text" placeholder="e.g. Trek Domane SL6">
      </div>

      <p class="section-title" style="margin-top:20px">Description</p>
      <div class="field">
        <label for="description">Markdown supported</label>
        <textarea id="description" name="description" placeholder="Write about this activity…"></textarea>
      </div>

      <p class="section-title" style="margin-top:20px">Display</p>
      <div class="field">
        <label>Hide stat panels</label>
        <div class="check-group" id="hide-stats-group">
          __STAT_CHECKBOXES__
        </div>
      </div>
      <div class="field" style="margin-top:12px">
        <label>Flags</label>
        <div class="toggle-row">
          <label class="toggle" id="toggle-highlight">
            <input type="checkbox" id="highlight" name="highlight"> Highlight in feed
          </label>
          <label class="toggle" id="toggle-private">
            <input type="checkbox" id="private" name="private"> Unlisted (hide from feed)
          </label>
        </div>
      </div>

      <p class="section-title" style="margin-top:20px">Images</p>
      <div class="field">
        <label>Drag & drop images or click to browse</label>
        <div class="drop-zone" id="drop-zone">
          <span id="drop-label">Drop images here or click to upload</span>
          <input type="file" id="file-input" accept="image/*" multiple style="display:none">
        </div>
        <div class="image-list" id="image-list"></div>
      </div>

      <div class="actions">
        <button type="submit" class="btn btn-primary" id="save-btn">Save</button>
        <span class="status" id="status"></span>
      </div>
    </form>
  </div>
</div>

<script>
const id = location.pathname.split('/edit/')[1];
const api = '/api/activity/' + id;
let uploadedImages = [];

// Fetch current data
fetch(api).then(r => r.json()).then(data => {
  document.getElementById('page-title').textContent = 'Edit: ' + (data.title || id);
  document.getElementById('meta').textContent = data.started_at
    ? new Date(data.started_at).toLocaleString() : '';
  document.getElementById('title').value = data.title || '';
  document.getElementById('sport').value = data.sport || 'other';
  document.getElementById('gear').value = data.gear || '';
  document.getElementById('description').value = data.description || '';
  if (data.highlight) setToggle('highlight', true);
  if (data.private) setToggle('private', true);
  (data.hide_stats || []).forEach(s => {
    const cb = document.querySelector(`input[data-stat="${s}"]`);
    if (cb) { cb.checked = true; cb.closest('.check-item').classList.add('active'); }
  });
  uploadedImages = data.images || [];
  renderImageList();
}).catch(() => {
  document.getElementById('status').textContent = 'Could not load activity data.';
  document.getElementById('status').className = 'status err';
});

// Toggle active class on check items
document.querySelectorAll('.check-item input[type=checkbox]').forEach(cb => {
  cb.addEventListener('change', () => {
    cb.closest('.check-item').classList.toggle('active', cb.checked);
  });
});

function setToggle(name, val) {
  const cb = document.getElementById(name);
  cb.checked = val;
  document.getElementById('toggle-' + name).classList.toggle('active', val);
}
document.getElementById('highlight').addEventListener('change', e => {
  document.getElementById('toggle-highlight').classList.toggle('active', e.target.checked);
});
document.getElementById('private').addEventListener('change', e => {
  document.getElementById('toggle-private').classList.toggle('active', e.target.checked);
});

// Image upload
const dropZone = document.getElementById('drop-zone');
const fileInput = document.getElementById('file-input');

dropZone.addEventListener('click', () => fileInput.click());
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  uploadFiles([...e.dataTransfer.files]);
});
fileInput.addEventListener('change', () => uploadFiles([...fileInput.files]));

async function uploadFiles(files) {
  for (const file of files) {
    const fd = new FormData();
    fd.append('file', file);
    const r = await fetch(api + '/images', { method: 'POST', body: fd });
    if (r.ok) {
      const d = await r.json();
      if (!uploadedImages.includes(d.filename)) uploadedImages.push(d.filename);
      renderImageList();
      // Insert markdown image reference at end of description
      const ta = document.getElementById('description');
      const ref = '\\n![' + d.filename.replace(/\\.[^.]+$/, '') + '](' + d.filename + ')';
      ta.value = ta.value.trimEnd() + ref;
    }
  }
}

function escapeHtml(s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function renderImageList() {
  const list = document.getElementById('image-list');
  list.innerHTML = uploadedImages.map(f =>
    `<span class="image-chip">${escapeHtml(f)}
      <button type="button" onclick="removeImage('${escapeHtml(f)}')" title="Remove">×</button>
    </span>`
  ).join('');
}

async function removeImage(filename) {
  await fetch(api + '/images/' + encodeURIComponent(filename), { method: 'DELETE' });
  uploadedImages = uploadedImages.filter(f => f !== filename);
  renderImageList();
}

// Save
document.getElementById('form').addEventListener('submit', async e => {
  e.preventDefault();
  const btn = document.getElementById('save-btn');
  const status = document.getElementById('status');
  btn.disabled = true;
  status.textContent = 'Saving…';
  status.className = 'status';

  const hideStats = [...document.querySelectorAll('input[data-stat]:checked')]
    .map(cb => cb.dataset.stat);

  const payload = {
    title: document.getElementById('title').value.trim(),
    sport: document.getElementById('sport').value,
    gear: document.getElementById('gear').value.trim(),
    description: document.getElementById('description').value.trim(),
    highlight: document.getElementById('highlight').checked,
    private: document.getElementById('private').checked,
    hide_stats: hideStats,
  };

  try {
    const r = await fetch(api, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!r.ok) throw new Error(await r.text());
    status.textContent = 'Saved! Re-run `bincio render` to rebuild.';
    status.className = 'status ok';
  } catch (err) {
    status.textContent = 'Error: ' + err.message;
    status.className = 'status err';
  } finally {
    btn.disabled = false;
  }
});
</script>
</body>
</html>
"""

# ── Routes ────────────────────────────────────────────────────────────────────


def _get_data_dir() -> Path:
    if data_dir is None:
        raise HTTPException(500, "Edit server not configured (data_dir is None)")
    return data_dir


@app.get("/")
async def root() -> RedirectResponse:
    return RedirectResponse(url=site_url)


@app.get("/edit/{activity_id}", response_class=HTMLResponse)
async def edit_page(activity_id: str) -> str:
    sport_opts = "\n".join(
        f'<option value="{s}">{s.capitalize()}</option>' for s in SPORTS
    )
    stat_cbs = "\n".join(
        f'<label class="check-item"><input type="checkbox" data-stat="{s}"> {s.replace("_", " ").capitalize()}</label>'
        for s in STAT_PANELS
    )
    html = (
        _HTML
        .replace("__SITE_URL__", site_url)
        .replace("__SPORT_OPTIONS__", sport_opts)
        .replace("__STAT_CHECKBOXES__", stat_cbs)
    )
    return html


@app.get("/api/activity/{activity_id}")
async def get_activity(activity_id: str) -> JSONResponse:
    dd = _get_data_dir()
    _check_id(activity_id)
    json_path = dd / "activities" / f"{activity_id}.json"
    if not json_path.exists():
        raise HTTPException(404, f"Activity {activity_id!r} not found")

    detail: dict[str, Any] = json.loads(json_path.read_text(encoding="utf-8"))

    # Read existing sidecar if any — these are the "user" values shown in the form
    from bincio.render.merge import parse_sidecar
    sidecar_path = dd / "edits" / f"{activity_id}.md"
    fm: dict = {}
    body = ""
    if sidecar_path.exists():
        fm, body = parse_sidecar(sidecar_path)

    # Existing uploaded images for this activity
    images_dir = dd / "edits" / "images" / activity_id
    images = sorted(p.name for p in images_dir.iterdir() if p.is_file()) if images_dir.exists() else []

    return JSONResponse({
        "id": activity_id,
        "started_at": detail.get("started_at", ""),
        "title": fm.get("title", detail.get("title", "")),
        "sport": fm.get("sport", detail.get("sport", "other")),
        "gear": fm.get("gear", detail.get("gear") or ""),
        "description": body or fm.get("description") or detail.get("description") or "",
        "highlight": fm.get("highlight", detail.get("custom", {}).get("highlight", False)),
        "private": fm.get("private", detail.get("privacy") in ("private", "unlisted")),
        "hide_stats": fm.get("hide_stats", detail.get("custom", {}).get("hide_stats", [])),
        "images": images,
    })


@app.post("/api/activity/{activity_id}")
async def save_activity(activity_id: str, payload: dict[str, Any]) -> JSONResponse:
    dd = _get_data_dir()
    _check_id(activity_id)
    if not (dd / "activities" / f"{activity_id}.json").exists():
        raise HTTPException(404, f"Activity {activity_id!r} not found")

    from bincio.edit.ops import apply_sidecar_edit
    apply_sidecar_edit(activity_id, payload, dd)

    sidecar_path = dd / "edits" / f"{activity_id}.md"
    return JSONResponse({"ok": True, "sidecar": str(sidecar_path)})


@app.post("/api/activity/{activity_id}/recalculate-elevation/dem")
async def recalculate_elevation_dem_endpoint(activity_id: str) -> JSONResponse:
    """Replace GPS altitude with DEM terrain elevation and recompute gain/loss.

    Requires --dem-url to be set when starting bincio edit.
    """
    if not dem_url:
        raise HTTPException(503, "DEM URL not configured.")
    dd = _get_data_dir()
    _check_id(activity_id)
    try:
        from bincio.extract.dem import recalculate_elevation
        from bincio.render.merge import merge_one
        result = recalculate_elevation(dd, activity_id, dem_url)
        merge_one(dd, activity_id)
        return JSONResponse(result)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/activity/{activity_id}/recalculate-elevation/hysteresis")
async def recalculate_elevation_hysteresis_endpoint(activity_id: str) -> JSONResponse:
    """Recompute gain/loss from original recorded elevation using source-aware hysteresis."""
    dd = _get_data_dir()
    _check_id(activity_id)
    try:
        from bincio.extract.dem import recalculate_elevation_hysteresis
        from bincio.render.merge import merge_one
        result = recalculate_elevation_hysteresis(dd, activity_id)
        merge_one(dd, activity_id)
        return JSONResponse(result)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.post("/api/activity/{activity_id}/images")
async def upload_image(activity_id: str, file: UploadFile = File(...)) -> JSONResponse:
    dd = _get_data_dir()
    _check_id(activity_id)
    if not (dd / "activities" / f"{activity_id}.json").exists():
        raise HTTPException(404, f"Activity {activity_id!r} not found")
    if not file.filename:
        raise HTTPException(400, "No filename")

    images_dir = dd / "edits" / "images" / activity_id
    images_dir.mkdir(parents=True, exist_ok=True)
    ct = file.content_type or ""
    if ct not in _ALLOWED_IMAGE_TYPES:
        raise HTTPException(400, "Only JPEG, PNG, WebP, or GIF images are accepted")
    contents = await file.read()
    if len(contents) > _MAX_IMAGE_BYTES:
        raise HTTPException(413, f"Image too large (max {_MAX_IMAGE_BYTES // (1024 * 1024)} MB)")
    safe_name = _unique_image_name(images_dir, Path(file.filename).name)
    (images_dir / safe_name).write_bytes(contents)
    return JSONResponse({"ok": True, "filename": safe_name})


@app.get("/api/athlete")
async def get_athlete() -> JSONResponse:
    dd = _get_data_dir()
    athlete_path = dd / "athlete.json"
    if not athlete_path.exists():
        raise HTTPException(404, "athlete.json not found — run bincio extract first")

    data = json.loads(athlete_path.read_text(encoding="utf-8"))

    # Layer edits/athlete.yaml overrides on top
    overrides = _read_athlete_edits(dd)
    for key in ("max_hr", "ftp_w", "hr_zones", "power_zones", "seasons", "gear"):
        if key in overrides:
            data[key] = overrides[key]

    return JSONResponse({
        "max_hr": data.get("max_hr"),
        "ftp_w": data.get("ftp_w"),
        "hr_zones": data.get("hr_zones"),
        "power_zones": data.get("power_zones"),
        "seasons": data.get("seasons", []),
        "gear": data.get("gear", {}),
    })


@app.post("/api/athlete")
async def save_athlete(payload: dict[str, Any]) -> JSONResponse:
    dd = _get_data_dir()
    athlete_path = dd / "athlete.json"
    if not athlete_path.exists():
        raise HTTPException(404, "athlete.json not found — run bincio extract first")

    # Write edits/athlete.yaml with validated fields
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

    # Re-merge — merge_all() applies edits/athlete.yaml on top of athlete.json
    from bincio.render.merge import merge_all
    merge_all(dd)

    return JSONResponse({"ok": True})


def _read_athlete_edits(data_dir: Path) -> dict:
    path = data_dir / "edits" / "athlete.yaml"
    if not path.exists():
        return {}
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


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
) -> StreamingResponse:
    """Accept FIT/GPX/TCX files and/or activities.csv; stream SSE progress while processing.

    activities.csv (Strava export format) can be included in the batch to:
      - Enrich activity files in the same batch (matched by filename)
      - Retroactively update sidecars for existing activities (matched by strava_id)
    """
    from bincio.extract.ingest import ingest_parsed
    from bincio.extract.parsers.factory import parse_file
    from bincio.extract.writer import make_activity_id
    from bincio.render.merge import merge_all

    dd = _get_data_dir()
    staging = dd / "_uploads"
    staging.mkdir(exist_ok=True)

    # Read all files into memory now (async), then process synchronously in the generator
    csv_bytes_list: list[bytes] = []
    activity_items: list[tuple[str, bytes]] = []

    for f in files:
        fname = Path(f.filename or "").name
        raw = await f.read()
        if fname.lower().endswith(".csv"):
            csv_bytes_list.append(raw)
        else:
            activity_items.append((fname, raw))

    # Build metadata from the first CSV found (activities.csv from Strava export)
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

    def event_stream():
        added = 0
        duplicates = 0
        errors = 0
        any_added = False

        for n, (name, contents) in enumerate(activity_items, 1):
            suffix = _file_suffix(name)
            if suffix not in _SUPPORTED_SUFFIXES:
                errors += 1
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'error', 'detail': 'unsupported type'})}\n\n"
                continue

            if len(contents) > _MAX_UPLOAD_BYTES:
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
                if (dd / "activities" / f"{activity_id}.json").exists():
                    duplicates += 1
                    yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'duplicate'})}\n\n"
                    continue
                ingest_parsed(activity, dd, privacy="public")
                if store_original:
                    originals_dir = dd / "originals"
                    originals_dir.mkdir(exist_ok=True)
                    staged.rename(originals_dir / name)
                    kept = True
                added += 1
                any_added = True
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'imported'})}\n\n"
            except Exception:
                errors += 1
                yield f"data: {json.dumps({'type': 'progress', 'n': n, 'total': total_files, 'name': name, 'status': 'error'})}\n\n"
            finally:
                if not kept:
                    staged.unlink(missing_ok=True)

        csv_updates = 0
        if metadata is not None:
            from bincio.extract.strava_csv import apply_csv_to_data_dir
            csv_updates = apply_csv_to_data_dir(dd, metadata)
            if csv_updates:
                yield f"data: {json.dumps({'type': 'csv', 'updates': csv_updates})}\n\n"

        if any_added or csv_updates:
            merge_all(dd)

        yield f"data: {json.dumps({'type': 'done', 'added': added, 'csv_updates': csv_updates, 'duplicates': duplicates, 'errors': errors})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/import-bas")
async def import_bas(payload: dict[str, Any]) -> JSONResponse:
    """Accept a pre-converted BAS detail JSON (from the /convert/ page) and save it."""
    dd = _get_data_dir()
    detail = payload.get("detail")
    geojson = payload.get("geojson")

    if not isinstance(detail, dict) or not detail.get("id"):
        raise HTTPException(400, "Missing or invalid 'detail' field")

    activity_id = detail["id"]
    _check_id(activity_id)

    acts_dir = dd / "activities"
    acts_dir.mkdir(exist_ok=True)

    dest = acts_dir / f"{activity_id}.json"
    if dest.exists():
        raise HTTPException(409, f"Activity already exists: {activity_id}")

    dest.write_text(json.dumps(detail, indent=2, ensure_ascii=False))

    if geojson:
        (acts_dir / f"{activity_id}.geojson").write_text(
            json.dumps(geojson, indent=2, ensure_ascii=False)
        )

    # Rebuild index
    index_path = dd / "index.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"owner": {"handle": "unknown"}, "activities": []}
    owner = index_data.get("owner", {})
    existing = {s["id"]: s for s in index_data.get("activities", [])}

    # Build a minimal summary from the detail
    summary_keys = [
        "id", "title", "sport", "sub_sport", "started_at", "distance_m",
        "duration_s", "moving_time_s", "elevation_gain_m", "avg_speed_kmh",
        "avg_hr_bpm", "avg_cadence_rpm", "avg_power_w", "privacy",
        "detail_url", "track_url", "preview_coords", "highlight", "duplicate_of",
    ]
    summary = {k: detail[k] for k in summary_keys if k in detail}
    existing[activity_id] = summary

    from bincio.extract.writer import write_index
    write_index(list(existing.values()), dd, owner)

    from bincio.render.merge import merge_all
    merge_all(dd)

    return JSONResponse({"ok": True, "id": activity_id})


@app.delete("/api/activity/{activity_id}/images/{filename}")
async def delete_image(activity_id: str, filename: str) -> JSONResponse:
    dd = _get_data_dir()
    _check_id(activity_id)
    safe_name = Path(filename).name  # strip any path traversal
    if not safe_name:
        raise HTTPException(400, "Invalid filename")
    target = dd / "edits" / "images" / activity_id / safe_name
    if target.exists() and target.is_file():
        target.unlink()
        # Remove empty parent dir
        if not any(target.parent.iterdir()):
            shutil.rmtree(target.parent)
    return JSONResponse({"ok": True})


# ── Strava sync ───────────────────────────────────────────────────────────────

@app.get("/api/strava/status")
async def strava_status() -> JSONResponse:
    """Return whether Strava is configured and whether a token is stored."""
    dd = _get_data_dir()
    from bincio.extract.strava_api import load_token
    token = load_token(dd)
    return JSONResponse({
        "configured": bool(strava_client_id),
        "connected": token is not None,
        "last_sync": token.get("last_sync_at") if token else None,
    })


@app.get("/api/strava/auth-url")
async def strava_auth_url(request: Request) -> JSONResponse:
    """Return the Strava OAuth URL the browser should open."""
    if not strava_client_id:
        raise HTTPException(400, "Strava client ID not configured. Pass --strava-client-id to bincio edit.")
    state = secrets.token_urlsafe(16)
    _oauth_states.add(state)
    redirect_uri = str(request.url_for("strava_callback"))
    from bincio.extract.strava_api import auth_url
    return JSONResponse({"url": auth_url(strava_client_id, redirect_uri, state=state)})


@app.get("/api/strava/callback", name="strava_callback")
async def strava_callback(code: str = "", error: str = "", state: str = "") -> RedirectResponse:
    """Strava OAuth callback — exchange code for token then redirect to the site."""
    if error or not code:
        return RedirectResponse(f"{site_url}?strava=error")
    if state not in _oauth_states:
        return RedirectResponse(f"{site_url}?strava=error")
    _oauth_states.discard(state)
    if not strava_client_id or not strava_client_secret:
        return RedirectResponse(f"{site_url}?strava=error")
    dd = _get_data_dir()
    from bincio.extract.strava_api import StravaError, exchange_code, save_token
    try:
        token = exchange_code(strava_client_id, strava_client_secret, code)
    except StravaError:
        return RedirectResponse(f"{site_url}?strava=error")
    save_token(dd, token)
    return RedirectResponse(f"{site_url}?strava=connected")


@app.post("/api/strava/sync")
async def strava_sync() -> JSONResponse:
    """Fetch new Strava activities since last sync and add them to the data store."""
    if not strava_client_id or not strava_client_secret:
        raise HTTPException(400, "Strava not configured. Pass --strava-client-id and --strava-client-secret to bincio edit.")
    dd = _get_data_dir()
    from bincio.edit.ops import run_strava_sync
    try:
        result = run_strava_sync(dd, strava_client_id, strava_client_secret)
    except RuntimeError as e:
        raise HTTPException(502, str(e))
    return JSONResponse(result)


@app.post("/api/strava/reset")
async def strava_reset(request: Request) -> JSONResponse:
    """Reset last_sync_at.

    mode=soft  — set to the started_at of the most recent activity already on disk
                 (next sync only fetches activities newer than the last known one)
    mode=hard  — clear last_sync_at entirely
                 (next sync re-downloads the full Strava history, skipping existing files)
    """
    dd = _get_data_dir()
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

    # soft: find the most recent started_at in the current index
    from datetime import datetime, timezone
    index_path = dd / "index.json"
    last_ts: int | None = None
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        started_ats = [
            a.get("started_at") for a in index_data.get("activities", [])
            if a.get("started_at")
        ]
        if started_ats:
            latest = max(started_ats)
            dt = datetime.fromisoformat(latest.replace("Z", "+00:00"))
            last_ts = int(dt.astimezone(timezone.utc).timestamp())

    if last_ts is None:
        token.pop("last_sync_at", None)
    else:
        token["last_sync_at"] = last_ts
    save_token(dd, token)
    return JSONResponse({"ok": True, "mode": "soft", "last_sync_at": last_ts})


@app.post("/api/upload/strava-zip")
async def upload_strava_zip(
    file: UploadFile = File(...),
    private: str = Form(default="false"),
) -> StreamingResponse:
    """Accept a Strava bulk export ZIP and stream SSE progress while processing.

    The ZIP is written to a temp file, processed activity-by-activity, then deleted.
    Originals are never kept — the UI informs the user of this upfront.
    """
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "Please upload a .zip file")

    privacy = "private" if private.lower() in ("true", "1", "yes") else "public"

    dd = _get_data_dir()
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False, dir=dd)
    zip_path = Path(tmp.name)
    try:
        while chunk := await file.read(1024 * 1024):  # 1 MB chunks
            tmp.write(chunk)
    finally:
        tmp.close()

    from bincio.extract.strava_zip import strava_zip_iter
    from bincio.render.merge import merge_all

    def event_stream():
        any_imported = False
        try:
            for event in strava_zip_iter(zip_path, dd, privacy=privacy):
                yield f"data: {json.dumps(event)}\n\n"
                if event.get("type") == "progress" and event.get("status") == "imported":
                    any_imported = True
                if event.get("type") == "done" and any_imported:
                    merge_all(dd)
        except Exception as exc:
            zip_path.unlink(missing_ok=True)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
