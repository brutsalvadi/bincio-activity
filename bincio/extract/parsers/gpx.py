"""GPX file parser."""

from datetime import timezone
from pathlib import Path

import gpxpy
import gpxpy.gpx

from bincio.extract.models import DataPoint, ParsedActivity
from bincio.extract.parsers.base import BaseParser
from bincio.extract.sport import normalise_sport, normalise_sub_sport

# Known GPX extension namespaces
_NS_GARMIN = "http://www.garmin.com/xmlschemas/TrackPointExtension/v1"
_NS_GARMIN_V2 = "http://www.garmin.com/xmlschemas/TrackPointExtension/v2"


class GpxParser(BaseParser):
    def parse(self, path: Path, raw_bytes: bytes) -> ParsedActivity:
        gpx = gpxpy.parse(raw_bytes.decode("utf-8", errors="replace"))

        points: list[DataPoint] = []
        for track in gpx.tracks:
            for segment in track.segments:
                for pt in segment.points:
                    if pt.time is None:
                        continue
                    ts = pt.time
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)

                    dp = DataPoint(
                        timestamp=ts,
                        lat=pt.latitude,
                        lon=pt.longitude,
                        elevation_m=pt.elevation,
                    )
                    _apply_extensions(pt, dp)
                    points.append(dp)

        if not points:
            raise ValueError(f"No trackpoints found in {path.name}")

        raw_sport = (gpx.tracks[0].type if gpx.tracks else None) or "cycling"
        sport     = normalise_sport(raw_sport)
        sub_sport = normalise_sub_sport(raw_sport)
        started_at = points[0].timestamp

        return ParsedActivity(
            points=points,
            sport=sport,
            sub_sport=sub_sport,
            started_at=started_at,
            source_file=path.name,
            source_hash="",  # set by factory
            altitude_source="gps",
        )


def _apply_extensions(pt: gpxpy.gpx.GPXTrackPoint, dp: DataPoint) -> None:
    """Extract HR, cadence, temperature from Garmin TrackPointExtension."""
    if pt.extensions is None:
        return
    for ext in pt.extensions:
        ns = _strip_ns(ext.tag)
        if ns == "TrackPointExtension":
            for child in ext:
                tag = _strip_ns(child.tag)
                val = child.text
                if val is None:
                    continue
                if tag == "hr":
                    dp.hr_bpm = int(float(val))
                elif tag == "cad":
                    dp.cadence_rpm = int(float(val))
                elif tag == "atemp":
                    dp.temperature_c = float(val)
                elif tag == "speed":
                    dp.speed_kmh = float(val) * 3.6  # m/s → km/h
                elif tag in ("pwr", "power", "watts"):
                    dp.power_w = int(float(val))


def _strip_ns(tag: str) -> str:
    """'{namespace}localname' → 'localname'."""
    return tag.split("}")[-1] if "}" in tag else tag
