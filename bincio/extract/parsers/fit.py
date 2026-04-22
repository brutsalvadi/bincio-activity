"""FIT file parser (Garmin binary format)."""

from datetime import timezone
from pathlib import Path
from typing import Any

import fitdecode

from bincio.extract.models import DataPoint, LapData, ParsedActivity
from bincio.extract.sport import normalise_sport


class FitParser:
    def parse(self, path: Path, raw_bytes: bytes) -> ParsedActivity:
        import io

        points: list[DataPoint] = []
        laps: list[LapData] = []
        sport: str = "other"
        sub_sport: str | None = None
        device: str | None = None

        has_baro_alt = False  # True if any record used enhanced_altitude

        with fitdecode.FitReader(io.BytesIO(raw_bytes)) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue

                if frame.name == "sport":
                    sport = normalise_sport(_get(frame, "sport"))
                    sub_sport = _normalise_sub_sport(_get(frame, "sub_sport"))

                elif frame.name == "session":
                    # Karoo and Strava-generated FIT files store sport here
                    # instead of (or in addition to) a separate 'sport' message.
                    # Only use session sport if no 'sport' frame was seen yet.
                    if sport == "other":
                        raw_sport = _get(frame, "sport")
                        if raw_sport is not None:
                            sport = normalise_sport(raw_sport)
                            sub_sport = _normalise_sub_sport(_get(frame, "sub_sport"))

                elif frame.name == "device_info":
                    mfr = _get(frame, "manufacturer")
                    prod = _get(frame, "product_name") or _get(frame, "garmin_product")
                    if mfr and prod:
                        device = f"{mfr} {prod}"
                    elif prod:
                        device = str(prod)

                elif frame.name == "record":
                    ts = _get(frame, "timestamp")
                    if ts is None:
                        continue
                    if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)

                    lat = _semicircles_to_deg(_get(frame, "position_lat"))
                    lon = _semicircles_to_deg(_get(frame, "position_long"))
                    speed_raw = _get(frame, "speed")  # m/s
                    # enhanced_altitude is written by barometric altimeters (most
                    # modern Garmins). Fall back to GPS-derived altitude if absent.
                    _alt = _get(frame, "enhanced_altitude")
                    if _alt is not None:
                        has_baro_alt = True
                    else:
                        _alt = _get(frame, "altitude")

                    dp = DataPoint(
                        timestamp=ts,
                        lat=lat,
                        lon=lon,
                        elevation_m=_alt,
                        hr_bpm=_get(frame, "heart_rate"),
                        cadence_rpm=_get(frame, "cadence"),
                        speed_kmh=speed_raw * 3.6 if speed_raw is not None else None,
                        power_w=_get(frame, "power"),
                        temperature_c=_get(frame, "temperature"),
                        distance_m=_get(frame, "distance"),
                    )
                    points.append(dp)

                elif frame.name == "lap":
                    ts = _get(frame, "start_time")
                    if ts is not None:
                        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
                            ts = ts.replace(tzinfo=timezone.utc)
                        elapsed = _get(frame, "total_elapsed_time")
                        speed_raw = _get(frame, "avg_speed")
                        laps.append(
                            LapData(
                                index=len(laps),
                                started_at=ts,
                                duration_s=int(elapsed) if elapsed else None,
                                distance_m=_get(frame, "total_distance"),
                                elevation_gain_m=_get(frame, "total_ascent"),
                                avg_speed_kmh=speed_raw * 3.6 if speed_raw is not None else None,
                                avg_hr_bpm=_get(frame, "avg_heart_rate"),
                                avg_power_w=_get(frame, "avg_power"),
                            )
                        )

        if not points:
            raise ValueError(f"No record messages found in {path.name}")

        altitude_source = "barometric" if has_baro_alt else "gps"

        return ParsedActivity(
            points=points,
            sport=sport,
            sub_sport=sub_sport,
            started_at=points[0].timestamp,
            device=device,
            laps=laps,
            source_file=path.name,
            source_hash="",
            altitude_source=altitude_source,
        )


def _get(frame: fitdecode.FitDataMessage, field: str, default: Any = None) -> Any:
    try:
        return frame.get_value(field)
    except KeyError:
        return default


def _semicircles_to_deg(value: Any) -> float | None:
    if value is None:
        return None
    try:
        deg = float(value) * (180.0 / 2**31)
        # Sanity check: invalid semicircle values often come out as ±180+
        if abs(deg) > 180:
            return None
        return deg
    except (TypeError, ValueError):
        return None


def _normalise_sub_sport(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).lower().replace(" ", "_")
    mapping = {
        "generic":           None,       # FIT default — unspecified
        "virtual_activity":  "indoor",
        "road":              "road",
        "mountain":          "mountain",
        "gravel_cycling":    "gravel",
        "cyclocross":        "gravel",
        "indoor_cycling":    "indoor",
        "trail":             "trail",
        "track":             "track",
        "cross_country_skiing": "nordic",
        "nordic_skiing":     "nordic",
        "skate_skiing":      "nordic",
        "backcountry_skiing":"nordic",
    }
    return mapping.get(s, s) or None
