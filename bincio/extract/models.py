"""Core data models for the extract stage.

ParsedActivity is the internal representation produced by parsers.
It gets fed into metrics computation and the BAS JSON writer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class DataPoint:
    """One measurement sample from a GPS/sensor recording."""

    timestamp: datetime
    lat: Optional[float] = None
    lon: Optional[float] = None
    elevation_m: Optional[float] = None
    hr_bpm: Optional[int] = None
    cadence_rpm: Optional[int] = None
    # Speed from device (km/h). May be absent; we compute it from GPS if so.
    speed_kmh: Optional[float] = None
    power_w: Optional[int] = None
    temperature_c: Optional[float] = None
    # Cumulative distance from device (metres), if recorded.
    distance_m: Optional[float] = None


@dataclass
class LapData:
    index: int
    started_at: datetime
    duration_s: Optional[int] = None
    distance_m: Optional[float] = None
    elevation_gain_m: Optional[float] = None
    avg_speed_kmh: Optional[float] = None
    avg_hr_bpm: Optional[int] = None
    avg_power_w: Optional[int] = None


@dataclass
class ParsedActivity:
    """Raw activity data as produced by a parser, before metric computation."""

    points: list[DataPoint]
    sport: str                         # normalised to BAS sport enum
    started_at: datetime
    source_file: str                   # basename of original file
    source_hash: str                   # "sha256:{hex}"

    sub_sport: Optional[str] = None
    device: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    gear: Optional[str] = None
    strava_id: Optional[str] = None
    privacy: Optional[str] = None   # "public", "private", or None (caller decides)
    laps: list[LapData] = field(default_factory=list)
    # "barometric" = device has a barometric altimeter (FIT enhanced_altitude present)
    # "gps"        = altitude derived from GPS triangulation (GPX, TCX, FIT altitude-only)
    # "unknown"    = source not detected (treated as gps for threshold purposes)
    altitude_source: str = "unknown"
