"""DEM (Digital Elevation Model) lookup and elevation recalculation.

Queries any Open-Elevation-compatible HTTP API to replace noisy GPS altitude
with terrain altitude, then re-applies hysteresis-based accumulation.

Compatible APIs:
  - https://api.open-elevation.com          (free, SRTM, accepts large batches)
  - https://api.opentopodata.org/v1/srtm30m  (more reliable, max 100 pts/req)

Pass the base URL (without path) to bincio serve/edit via --dem-url.
"""

from __future__ import annotations

import json
import statistics
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

# Sample one GPS point per N seconds when building the DEM query.
# SRTM30 resolution is ~30 m; at 30 km/h cycling that's ~3 s per tile —
# sampling every 10 s is more than enough.
_DEFAULT_SAMPLE_INTERVAL_S = 10

# Maximum locations per API request.  Open-Elevation supports ~512 per POST.
_DEFAULT_BATCH_SIZE = 512

# Hysteresis threshold after DEM correction.
# SRTM30 at 1 Hz produces tile-boundary steps of ~1–3 m every few seconds.
# A 5 m threshold barely filters them; 10 m suppresses them reliably.
_DEM_HYSTERESIS_M = 10.0

# Median filter window (seconds / samples at 1 Hz) applied to DEM-interpolated
# series before hysteresis.  45 s smooths SRTM tile steps while keeping real
# climbs (typical cycling ramp > 100 m over > 2 min).
_MEDIAN_WINDOW_S = 60

# Moving-average window (seconds) applied to the 1 Hz elevation series before
# hysteresis in the on-demand recalculation.  Pre-smoothing lets us use a
# much lower dead-band (capturing real small climbs) while still suppressing
# GPS jitter and barometric quantization noise.
_MA_WINDOW_S = 30


def _moving_average(values: list[float], window: int) -> list[float]:
    """Apply a centred sliding-window moving average to *values*.

    Edge handling: window shrinks symmetrically at both ends (same effective
    behaviour as scipy's 'nearest' / numpy's 'reflect' mode).
    """
    half = window // 2
    n = len(values)
    out: list[float] = []
    cumsum = [0.0] * (n + 1)
    for i, v in enumerate(values):
        cumsum[i + 1] = cumsum[i] + v
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append((cumsum[hi] - cumsum[lo]) / (hi - lo))
    return out


def _median_filter(values: list[float], window: int) -> list[float]:
    """Apply a sliding-window median filter to *values*.

    The window is centred on each sample; edges are handled by shrinking the
    window symmetrically (same as scipy's 'reflect' / 'nearest' default).
    """
    half = window // 2
    n = len(values)
    out: list[float] = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(statistics.median(values[lo:hi]))
    return out


def lookup_elevations(
    latlons: list[tuple[float, float]],
    api_url: str,
    batch_size: int = _DEFAULT_BATCH_SIZE,
    timeout_s: int = 30,
) -> list[Optional[float]]:
    """Query a DEM API for terrain elevation at the given (lat, lon) pairs.

    Uses the Open-Elevation API format::

        POST {api_url}/api/v1/lookup
        {"locations": [{"latitude": lat, "longitude": lon}, ...]}

    Returns a list the same length as *latlons*.  Elements are ``None``
    wherever the API returned no data (network error, ocean, etc.).
    """
    if not latlons:
        return []

    results: list[Optional[float]] = [None] * len(latlons)
    url = f"{api_url.rstrip('/')}/api/v1/lookup"

    for start in range(0, len(latlons), batch_size):
        batch = latlons[start : start + batch_size]
        payload = json.dumps(
            {"locations": [{"latitude": lat, "longitude": lon} for lat, lon in batch]}
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for i, item in enumerate(data.get("results", [])):
                elev = item.get("elevation")
                if elev is not None:
                    results[start + i] = float(elev)
        except (urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError):
            pass  # leave this batch as None; caller checks overall coverage

    return results


def _hysteresis_gain_loss(
    elevations: list[float], threshold_m: float
) -> tuple[float, float]:
    """Compute elevation gain and loss using a hysteresis dead-band.

    Only commits a new elevation level when it differs from the last committed
    value by at least *threshold_m*.  Returns (gain, loss) in metres, both
    as positive numbers.
    """
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


def recalculate_elevation(
    user_dir: Path,
    activity_id: str,
    dem_url: str,
    sample_interval_s: int = _DEFAULT_SAMPLE_INTERVAL_S,
) -> dict:
    """Replace GPS elevation with DEM terrain elevation and recompute gain/loss.

    Algorithm
    ---------
    1. Read the activity's 1 Hz timeseries for lat / lon / t arrays.
    2. Subsample GPS points every *sample_interval_s* seconds.
    3. Query the DEM API for those points (batched).
    4. Linearly interpolate DEM elevation back to every GPS-valid second.
    5. Apply a 45 s median filter to smooth SRTM tile-boundary noise.
    6. Apply :data:`_DEM_HYSTERESIS_M` (10 m) hysteresis to compute gain/loss.
    7. Preserve the original elevation as ``elevation_m_original`` in the
       timeseries (only on the first DEM run — never overwrites a prior backup).
    8. Write the smoothed DEM elevation array as ``elevation_m``.
    9. Patch ``elevation_gain_m`` / ``elevation_loss_m`` in the detail JSON.
    10. Patch ``elevation_gain_m`` in ``index.json`` (summary entry for feed).

    Returns
    -------
    dict with keys ``elevation_gain_m``, ``elevation_loss_m``,
    ``points_queried`` (DEM responses received).

    Raises
    ------
    FileNotFoundError
        Activity detail or timeseries file not found.
    ValueError
        Activity has no GPS coordinates, or the DEM API returned too few results.
    """
    acts_dir = user_dir / "activities"
    json_path = acts_dir / f"{activity_id}.json"
    ts_path   = acts_dir / f"{activity_id}.timeseries.json"

    if not json_path.exists():
        raise FileNotFoundError(f"Activity not found: {activity_id}")
    if not ts_path.exists():
        raise ValueError("Activity has no timeseries data")

    ts      = json.loads(ts_path.read_text(encoding="utf-8"))
    lat_arr: list[Optional[float]] = ts.get("lat") or []
    lon_arr: list[Optional[float]] = ts.get("lon") or []
    t_arr:   list[int]             = ts.get("t") or []

    if not lat_arr or not lon_arr:
        raise ValueError(
            "Activity has no GPS coordinates "
            "(privacy=no_gps or data recorded without GPS)"
        )

    n = len(t_arr)

    # ── 1. Subsample GPS-valid indices ────────────────────────────────────────
    gps_idx = [
        i for i in range(n)
        if lat_arr[i] is not None and lon_arr[i] is not None
    ]
    if len(gps_idx) < 2:
        raise ValueError("Too few GPS points for DEM lookup")

    sampled_idx = gps_idx[::sample_interval_s]
    if gps_idx[-1] not in sampled_idx:
        sampled_idx.append(gps_idx[-1])  # always include the last point

    # ── 2. Query DEM ──────────────────────────────────────────────────────────
    latlons = [(float(lat_arr[i]), float(lon_arr[i])) for i in sampled_idx]  # type: ignore[arg-type]
    dem_elev = lookup_elevations(latlons, dem_url)

    # Build (t, elevation) pairs for valid DEM responses only
    valid_pairs: list[tuple[int, float]] = [
        (t_arr[sampled_idx[k]], dem_elev[k])
        for k in range(len(sampled_idx))
        if dem_elev[k] is not None
    ]
    n_queried = len(valid_pairs)
    if n_queried < 2:
        raise ValueError(
            f"DEM API returned too few results "
            f"({n_queried} of {len(sampled_idx)} points). "
            f"Check the DEM URL: {dem_url}"
        )

    # ── 3. Linear interpolation → full 1 Hz series ───────────────────────────
    new_ele: list[Optional[float]] = [None] * n
    j = 0
    for i in gps_idx:
        t = t_arr[i]
        while j + 1 < len(valid_pairs) - 1 and valid_pairs[j + 1][0] <= t:
            j += 1
        t0, e0 = valid_pairs[j]
        if j + 1 < len(valid_pairs):
            t1, e1 = valid_pairs[j + 1]
            frac = max(0.0, min(1.0, (t - t0) / (t1 - t0))) if t1 > t0 else 0.0
            new_ele[i] = round(e0 + frac * (e1 - e0), 1)
        else:
            new_ele[i] = round(e0, 1)

    # ── 4. Median filter to suppress SRTM tile-boundary steps ────────────────
    valid_indices = [i for i, e in enumerate(new_ele) if e is not None]
    valid_eles_raw = [new_ele[i] for i in valid_indices]  # type: ignore[misc]
    smoothed = _median_filter(valid_eles_raw, _MEDIAN_WINDOW_S)  # type: ignore[arg-type]

    # Write smoothed values back into new_ele
    for idx, e in zip(valid_indices, smoothed):
        new_ele[idx] = round(e, 1)

    # ── 5. Hysteresis accumulation on smoothed series ─────────────────────────
    smoothed_valid = [e for e in new_ele if e is not None]
    gain, loss = _hysteresis_gain_loss(smoothed_valid, _DEM_HYSTERESIS_M)  # type: ignore[arg-type]

    gain_r = round(gain, 1)
    loss_r = round(loss, 1)

    # ── 6. Preserve original elevation (only if not already backed up) ────────
    if "elevation_m_original" not in ts:
        ts["elevation_m_original"] = ts.get("elevation_m")

    # ── 7. Write timeseries ───────────────────────────────────────────────────
    ts["elevation_m"] = new_ele
    ts_path.write_text(json.dumps(ts, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── 8. Patch activity detail JSON ─────────────────────────────────────────
    detail = json.loads(json_path.read_text(encoding="utf-8"))
    detail["elevation_gain_m"] = gain_r
    detail["elevation_loss_m"] = loss_r
    json_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")

    # ── 9. Patch index.json summary ───────────────────────────────────────────
    index_path = user_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for s in index.get("activities", []):
            if s.get("id") == activity_id:
                s["elevation_gain_m"] = gain_r
                break
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return {
        "elevation_gain_m": gain_r,
        "elevation_loss_m": loss_r,
        "points_queried": n_queried,
    }


def recalculate_elevation_hysteresis(user_dir: Path, activity_id: str) -> dict:
    """Recompute elevation gain/loss from the original recorded elevation data.

    Algorithm
    ---------
    1. Read ``elevation_m_original`` (backup from a prior DEM run) if present,
       otherwise read ``elevation_m`` from the timeseries.
    2. Apply a :data:`_MA_WINDOW_S` (30 s) moving average to smooth out
       barometric quantization steps and GPS jitter.
    3. Apply a low dead-band threshold to the smoothed series:
       - **1 m** for barometric altimeters (FIT files with ``enhanced_altitude``)
       - **3 m** for GPS-derived altitude (GPX, TCX, FIT without enhanced_altitude)

    The 30 s pre-smoothing makes the low thresholds safe: after averaging,
    0.2 m barometric quantization noise and short-period GPS jitter are
    suppressed below the threshold, while real terrain changes (which persist
    across the window) are preserved.

    The elevation array in the timeseries is **not** modified — only the
    summary stats in the detail JSON and ``index.json`` are patched.

    ``altitude_source`` is read from the detail JSON (written by the extractor
    for activities recorded after this field was added).  For older activities
    it falls back to ``"unknown"`` → 3 m GPS threshold.

    Returns
    -------
    dict with keys ``elevation_gain_m``, ``elevation_loss_m``,
    ``threshold_m``, ``altitude_source``.
    """
    acts_dir = user_dir / "activities"
    json_path = acts_dir / f"{activity_id}.json"
    ts_path   = acts_dir / f"{activity_id}.timeseries.json"

    if not json_path.exists():
        raise FileNotFoundError(f"Activity not found: {activity_id}")
    if not ts_path.exists():
        raise ValueError("Activity has no timeseries data")

    ts = json.loads(ts_path.read_text(encoding="utf-8"))

    # Prefer the pre-DEM backup; fall back to the current elevation array
    ele_arr: list[Optional[float]] = (
        ts.get("elevation_m_original") or ts.get("elevation_m") or []
    )
    elevations = [e for e in ele_arr if e is not None]
    if len(elevations) < 2:
        raise ValueError("Not enough elevation data to compute gain/loss")

    # Determine source-aware threshold
    detail = json.loads(json_path.read_text(encoding="utf-8"))
    altitude_source = detail.get("altitude_source", "unknown")
    threshold = 1.0 if altitude_source == "barometric" else 3.0

    # Pre-smooth to suppress noise, then accumulate with low dead-band
    smoothed = _moving_average(elevations, _MA_WINDOW_S)
    gain, loss = _hysteresis_gain_loss(smoothed, threshold)
    gain_r = round(gain, 1)
    loss_r = round(loss, 1)

    # Patch detail JSON
    detail["elevation_gain_m"] = gain_r
    detail["elevation_loss_m"] = loss_r
    json_path.write_text(json.dumps(detail, indent=2, ensure_ascii=False), encoding="utf-8")

    # Patch index.json summary
    index_path = user_dir / "index.json"
    if index_path.exists():
        index = json.loads(index_path.read_text(encoding="utf-8"))
        for s in index.get("activities", []):
            if s.get("id") == activity_id:
                s["elevation_gain_m"] = gain_r
                break
        index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    return {
        "elevation_gain_m": gain_r,
        "elevation_loss_m": loss_r,
        "threshold_m": threshold,
        "altitude_source": altitude_source,
    }
