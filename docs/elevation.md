# Elevation gain calculation — problem analysis and roadmap

## The problem

Bincio's current algorithm naively accumulates every positive elevation delta between
consecutive track points. This **always overestimates** real climbing because it treats
sensor noise as genuine ascent. The overestimation ranges from insignificant on long
mountain rides to catastrophic on flat routes where 100% of the reported gain is noise.

---

## Current algorithm (`metrics.py:_elevation`)

```python
def _elevation(pts):
    elevations = [p.elevation_m for p in pts if p.elevation_m is not None]
    gain = loss = 0.0
    for a, b in zip(elevations, elevations[1:]):
        diff = b - a
        if diff > 0:
            gain += diff
        else:
            loss += diff
    return gain, loss
```

Every positive step — including 0.1m GPS jitter, barometric quantization steps of
0.2m, and random-walk noise — is counted as climbing. There is no filtering,
smoothing, or minimum-step threshold of any kind.

---

## Root causes of overestimation

### 1. GPS-derived altitude noise

GPS units measure altitude from satellite triangulation. This is inherently less
accurate than horizontal positioning: typical GPS altitude error is ±5–15m, and the
error follows a correlated random walk. On a flat route, the track oscillates above
and below the true elevation, and the positive half of those oscillations accumulates
as phantom climbing.

**Characteristic signature:** elevation range far smaller than reported gain; nearly
100% of deltas are sub-1m; median step size is 0.0m.

### 2. Barometric altimeter quantization

Devices with a barometric sensor report higher-quality data, but they still apply
internal smoothing and quantise the output to fixed increments (commonly 0.2m or
0.4m). The device holds the reading steady for several seconds, then steps to the
next quantised value. Small-but-real oscillations at the quantisation boundary
(e.g. hovering between 128.2m and 128.4m while essentially flat) produce repeated
tiny up/down steps that accumulate.

**Characteristic signature:** many repeated identical elevations (device holding);
most transitions are 0.0m, 0.2m or 0.4m; significant fraction of gain from sub-1m
steps even on a real climb.

### 3. High sampling rate amplifying both effects

At 1 Hz, both GPS and barometric sensors produce more noise steps per meter of
real climbing than at lower rates. Downsampling to 1 Hz (as the timeseries writer
does) does not eliminate noise already present in the source data.

---

## Case studies

### Activity 1 — diego_p, 2026-04-11T051441Z (Wahoo ELEMNT, GPX)

- **URL:** https://bincio.org/activity/2026-04-11T051441Z/
- **Bincio reports:** 353.6m gain
- **Correct estimate:** ~150–160m (Wahoo device's own reading, which applies internal
  thresholding)
- **Excess:** ~200m (56% overestimate)

| Metric | Value |
|--------|-------|
| Points | 15,721 |
| Elevation range | −10.6m to +5.4m (16m total span) |
| Median \|delta\| | 0.000m |
| Zero-change steps | 12,358 (79%) |
| Sub-0.5m steps | 3,339 (21%) |
| Steps ≥ 1m | 0 (0%) |
| Gain from sub-1m steps | 353.6m (100% of total) |

**Diagnosis:** This is a flat coastal route. The GPS altitude range is only 16m.
Every single metre of reported gain is sub-1m GPS jitter — no real climbing is
recorded at all. Even a 1m threshold would produce exactly 0m gain, which is wrong
in the other direction (the route may have minor real undulation). The Wahoo device's
own algorithm uses internal hysteresis to report ~153m.

### Activity 2 — m4xw3ll__, 2026-04-14T161945Z (Bryton Rider, FIT)

- **URL:** http://your-instance/activity/2026-04-14T161945Z/
- **Bincio reports:** 1285.2m gain
- **Correct estimate:** ~885m (Strava / device reading)
- **Excess:** ~400m (45% overestimate)

| Metric | Value |
|--------|-------|
| Points | 6,583 |
| Elevation range | 0.0m to 454.0m (454m total span) |
| Median \|delta\| | 0.000m |
| Zero-change steps | 4,077 (62%) |
| Sub-0.5m steps | 769 (12%) |
| 0.5–1m steps | 647 (10%) |
| 1–2m steps | 921 (14%) |
| 2m+ steps | 168 (3%) |
| Gain from sub-1m steps | 484.0m (38% of total) |

**Diagnosis:** Real climbing exists (0–454m) but 38% of the reported gain comes from
sub-1m barometric quantization noise. The Bryton records elevation at ≈0.2m
increments. At quantization boundaries the device oscillates producing repeated
tiny up/down steps. A simple 1m threshold gives 801m (10% below truth); 2m gives
only 221m (too aggressive). Pure threshold-based filtering doesn't work well here.

---

## Alternative algorithms

### A. Simple threshold

Only count a step if it exceeds `min_step_m`:

```python
gain += diff if diff >= min_step_m else 0
```

**Pros:** trivial to implement, zero overhead.  
**Cons:** flat/hiking routes with gradual slopes produce many steps < threshold
that together represent real climbing. A 2m threshold already loses 30% of real
gain on the Bryton activity. Requires per-device tuning that is impractical.

---

### B. Hysteresis / dead-band accumulation

Track a "committed" elevation. Only commit a new elevation when it differs from
the last committed value by more than `threshold_m`. Accumulate from committed to
committed only.

```python
def _elevation_hysteresis(elevations, threshold_m=10.0):
    gain = loss = 0.0
    committed = elevations[0]
    for e in elevations[1:]:
        diff = e - committed
        if abs(diff) >= threshold_m:
            if diff > 0:
                gain += diff
            else:
                loss += abs(diff)
            committed = e
    return gain, loss
```

**Pros:** naturally handles both GPS drift and barometric quantization; used by
Strava (proprietary variant), RideWithGPS (10m default), and GPSies (5m).  
**Cons:** threshold choice is critical and device-dependent. On a genuine 8m climb
followed by descent, a 10m threshold records zero. Needs to be lower for cycling
than hiking (slopes are smoother, sensors better).

**Results on our case studies with threshold=10m:**
- Wahoo flat (correct ~153m): would likely produce 0–30m. Fixes the gross overcount
  but may undercount real minor undulation.
- Bryton climb (correct ~885m): would need evaluation on the raw data.

---

### C. Moving-average pre-smoothing

Apply a sliding-window mean or Gaussian blur to the elevation series, then
accumulate naively.

```python
import statistics

def smooth(elevations, window=30):
    half = window // 2
    out = []
    for i, e in enumerate(elevations):
        lo, hi = max(0, i - half), min(len(elevations), i + half + 1)
        out.append(statistics.mean(elevations[lo:hi]))
    return out

gain, loss = _elevation(smooth(elevations, window=30))
```

**Pros:** easy to implement; smoothing removes high-frequency noise while preserving
long-wavelength terrain.  
**Cons:** loses real short climbs (e.g. a 20m ramp over 20 seconds is averaged to
near-flat). Window size needs tuning per sample rate. Edge effects at start/end.

---

### D. Savitzky-Golay filter

A polynomial least-squares smoothing filter that better preserves peaks and
troughs than a simple moving average. Available in `scipy.signal.savgol_filter`
(scipy is already an indirect dependency via numpy, which is used nowhere critical —
but adding scipy is a dependency choice).

**Pros:** better terrain shape preservation than moving average; standard in
scientific signal processing.  
**Cons:** requires scipy; harder to implement without it; window/order tuning still
required.

---

### E. Kalman filter (device-class-aware)

A Kalman filter can be tuned with separate process noise and measurement noise
parameters for GPS vs barometric data. This is what high-end cycling computers do
internally.

**Pros:** theoretically optimal; can be parameterised per device class.  
**Cons:** significantly more complex; requires knowing the device class (GPS-only vs
barometric); still requires parameter tuning.

---

### F. Source-aware strategy

Use different algorithms depending on the file type and whether the device reported
enhanced (barometric) altitude:

- **FIT file with `enhanced_altitude` field**: barometric data, use hysteresis 5m
- **FIT file with GPS altitude only**: treat as GPS, use hysteresis 10–15m or
  discard altitude entirely and use a DEM lookup
- **GPX with `<ele>` tag**: assume GPS unless `<extensions>` contains barometric
  fields; use hysteresis 10–15m
- **Strava-enriched data**: Strava's API provides corrected `altitude` arrays; use
  as-is with hysteresis 2m to catch quantization

---

## What Strava/Garmin/others do

| Platform | Method |
|---|---|
| Strava | Proprietary; replaces raw altitude with DEM-corrected data for GPS-only devices; applies internal smoothing before accumulation |
| Garmin Connect | Uses enhanced\_altitude (barometric), applies Kalman filter on-device; Connect re-applies server-side smoothing |
| Wahoo | On-device hysteresis (≈3m threshold); the GPX file contains already-smoothed altitude |
| RideWithGPS | 10m hysteresis by default, configurable |
| Komoot | DEM correction + smoothing |
| TrainingPeaks | Configurable threshold (5m default) |

Strava's approach of DEM (Digital Elevation Model) correction is the gold standard
for GPS-only tracks: replace the noisy GPS altitude entirely with the ground truth
from a 30m-resolution DEM such as SRTM. This requires an additional data source
(e.g. the Open-Elevation API or a locally hosted SRTM tile set) but completely
eliminates GPS altitude noise.

---

## Recommended fix

Given the two failure modes observed:

### Short term — ✅ Implemented (2026-04-20)

**Hysteresis accumulation** with source-aware thresholds, applied at extract time:

| Source | Threshold |
|---|---|
| FIT with `enhanced_altitude` (barometric) | 5 m |
| FIT with GPS altitude | 10 m |
| GPX | 10 m |
| TCX | 10 m |

`ParsedActivity.altitude_source` is set by each parser (`"barometric"` / `"gps"` /
`"unknown"`). `_elevation()` in `metrics.py` selects the threshold from this value.

New activities extracted after this change benefit automatically. Existing activities
require re-extraction from source files.

### Medium term — ✅ Implemented (2026-04-20)

**Two on-demand recalculation options** in the activity edit drawer:

#### Option 1 — Hysteresis (fast, offline)

Re-applies the same source-aware hysteresis accumulation as the extract
pipeline directly to the recorded elevation, with no network calls.

- Uses `elevation_m_original` from the timeseries (the backup saved on the
  first DEM run) if present; otherwise uses the current `elevation_m`.
- Threshold: **5 m** for barometric sources, **10 m** for GPS.
- Does not modify the elevation array in the timeseries — only patches
  `elevation_gain_m` / `elevation_loss_m`.
- Best for: devices with a barometric altimeter (e.g. Karoo 2, Garmin with
  `enhanced_altitude`) where the recorded elevation is already accurate but
  was extracted before the hysteresis fix was deployed.

#### Option 2 — DEM terrain correction (SRTM30, requires network)

Replaces the recorded GPS altitude with terrain data from an
Open-Elevation-compatible API (SRTM30, ~30 m resolution):

1. GPS track subsampled to one point per 10 s to minimise API calls.
2. Terrain elevation fetched via `POST https://api.open-elevation.com/api/v1/lookup`
   in batches of 512.
3. DEM elevation linearly interpolated back to the full 1 Hz series.
4. **45 s sliding median filter** applied to suppress SRTM tile-boundary
   steps (these occur every ~7 s at cycling speed and accumulate as phantom
   gain through a naive threshold).
5. **10 m hysteresis** applied to the smoothed series.
6. Original elevation backed up as `elevation_m_original` in the timeseries
   (only on the first DEM run — never overwrites an existing backup).
7. Timeseries and activity JSON patched in place; chart and stats update.

Best for: GPS-only devices (no barometric sensor) where the recorded
altitude is noisy and the DEM terrain is a better ground truth.

> **Why median + 10 m, not 5 m?**  SRTM30 at 1 Hz produces step changes at
> tile boundaries of 1–3 m every few seconds.  A 5 m threshold lets most of
> these through; they accumulate and can inflate the result by 50 %+.  The
> 45 s median smooths the steps before the dead-band sees them; 10 m catches
> any residual outliers.

Implementation: `bincio/extract/dem.py` — `lookup_elevations()`,
`recalculate_elevation()`, `recalculate_elevation_hysteresis()`.
API endpoints: `POST /api/activity/{id}/recalculate-elevation/dem` and
`POST /api/activity/{id}/recalculate-elevation/hysteresis` on both servers.
Default DEM endpoint: `https://api.open-elevation.com`; override with
`--dem-url` or `DEM_URL` env var.

---

## Implementation status

| File | Status |
|---|---|
| `bincio/extract/models.py` | ✅ `altitude_source` field added |
| `bincio/extract/parsers/fit.py` | ✅ detects `enhanced_altitude` vs GPS |
| `bincio/extract/parsers/gpx.py` | ✅ sets `altitude_source = "gps"` |
| `bincio/extract/parsers/tcx.py` | ✅ sets `altitude_source = "gps"` |
| `bincio/extract/metrics.py` | ✅ hysteresis `_elevation()` with source-aware threshold |
| `bincio/extract/dem.py` | ✅ `lookup_elevations()` + `recalculate_elevation()` (median+10m) + `recalculate_elevation_hysteresis()` |
| `bincio/serve/server.py` | ✅ `POST /api/activity/{id}/recalculate-elevation/{dem\|hysteresis}` |
| `bincio/edit/server.py` | ✅ same endpoints (single-user) |
| `site/src/components/EditDrawer.svelte` | ✅ two buttons: "Recalculate (hysteresis)" + "Recalculate (DEM)" |
| `tests/test_metrics.py` | ✅ 5 parametric tests |
