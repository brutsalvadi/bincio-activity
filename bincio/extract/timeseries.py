"""Downsample a list of DataPoints to at most 1 sample/second and build
the BAS timeseries object (parallel arrays)."""

from datetime import datetime
from typing import Optional

from bincio.extract.models import DataPoint


def build_timeseries(
    points: list[DataPoint],
    started_at: datetime,
    privacy: str = "public",
) -> dict:
    """Return the BAS `timeseries` object.

    privacy='no_gps' → lat/lon set to null. All other privacy levels
    (including 'unlisted') retain GPS in the timeseries.
    Downsamples so at most one point per second is emitted.
    """
    if not points:
        return {"t": []}

    include_gps = privacy not in ("no_gps", "private")  # "private" = legacy alias for "unlisted"

    # Downsample: keep at most one point per second
    sampled: list[DataPoint] = []
    last_t: Optional[int] = None
    for p in points:
        t = int((p.timestamp - started_at).total_seconds())
        if t < 0:
            continue
        if last_t is not None and t <= last_t:
            continue  # skip sub-second duplicates and non-monotonic points
        sampled.append(p)
        last_t = t

    ts_vals   = [int((p.timestamp - started_at).total_seconds()) for p in sampled]
    lat_vals  = [round(p.lat, 7) if p.lat is not None else None for p in sampled] if include_gps else None
    lon_vals  = [round(p.lon, 7) if p.lon is not None else None for p in sampled] if include_gps else None
    ele_vals  = [round(p.elevation_m, 1) if p.elevation_m is not None else None for p in sampled]
    spd_vals  = [round(p.speed_kmh, 2) if p.speed_kmh is not None else None for p in sampled]
    hr_vals   = [p.hr_bpm for p in sampled]
    cad_vals  = [p.cadence_rpm for p in sampled]
    pwr_vals  = [p.power_w for p in sampled]
    tmp_vals  = [round(p.temperature_c, 1) if p.temperature_c is not None else None for p in sampled]

    result: dict = {
        "t":             ts_vals,
        "lat":           lat_vals,
        "lon":           lon_vals,
        "elevation_m":   ele_vals,
        "speed_kmh":     spd_vals,
        "hr_bpm":        hr_vals,
        "cadence_rpm":   cad_vals,
        "power_w":       pwr_vals,
        "temperature_c": tmp_vals,
    }
    return result
