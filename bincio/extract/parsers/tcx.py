"""TCX (Training Center XML) file parser."""

from datetime import datetime, timezone
from pathlib import Path

from lxml import etree

from bincio.extract.models import DataPoint, ParsedActivity
from bincio.extract.sport import normalise_sport, normalise_sub_sport

_NS_HTTP  = {
    "tcx": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
    "ext": "http://www.garmin.com/xmlschemas/ActivityExtension/v2",
}
_NS_HTTPS = {
    "tcx": "https://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2",
    "ext": "https://www.garmin.com/xmlschemas/ActivityExtension/v2",
}


class TcxParser:
    def parse(self, path: Path, raw_bytes: bytes) -> ParsedActivity:
        # Some exporters prepend whitespace before the XML declaration. Strip it.
        root = etree.fromstring(raw_bytes.lstrip())

        # Garmin sometimes uses https:// instead of http:// in the namespace URI.
        _NS = _NS_HTTPS if b"https://www.garmin.com" in raw_bytes else _NS_HTTP

        activities = root.findall(".//tcx:Activity", _NS)
        if not activities:
            raise ValueError(f"No Activity elements found in {path.name}")

        # Use the first activity
        act = activities[0]
        sport_attr = act.get("Sport", "Biking")
        sport     = normalise_sport(sport_attr)
        sub_sport = normalise_sub_sport(sport_attr)

        points: list[DataPoint] = []
        for tp in act.findall(".//tcx:Trackpoint", _NS):
            ts_el = tp.find("tcx:Time", _NS)
            if ts_el is None or not ts_el.text:
                continue
            ts = _parse_ts(ts_el.text)

            lat, lon = None, None
            pos = tp.find("tcx:Position", _NS)
            if pos is not None:
                lat_el = pos.find("tcx:LatitudeDegrees", _NS)
                lon_el = pos.find("tcx:LongitudeDegrees", _NS)
                lat = float(lat_el.text) if lat_el is not None and lat_el.text else None
                lon = float(lon_el.text) if lon_el is not None and lon_el.text else None

            ele_el = tp.find("tcx:AltitudeMeters", _NS)
            hr_el = tp.find(".//tcx:HeartRateBpm/tcx:Value", _NS)
            cad_el = tp.find("tcx:Cadence", _NS)
            dist_el = tp.find("tcx:DistanceMeters", _NS)

            # Extensions (speed, watts)
            speed_el = tp.find(".//ext:Speed", _NS)
            power_el = tp.find(".//ext:Watts", _NS)

            dp = DataPoint(
                timestamp=ts,
                lat=lat,
                lon=lon,
                elevation_m=float(ele_el.text) if ele_el is not None and ele_el.text else None,
                hr_bpm=int(float(hr_el.text)) if hr_el is not None and hr_el.text else None,
                cadence_rpm=int(float(cad_el.text)) if cad_el is not None and cad_el.text else None,
                distance_m=float(dist_el.text) if dist_el is not None and dist_el.text else None,
                speed_kmh=float(speed_el.text) * 3.6 if speed_el is not None and speed_el.text else None,
                power_w=int(float(power_el.text)) if power_el is not None and power_el.text else None,
            )
            points.append(dp)

        if not points:
            raise ValueError(f"No trackpoints found in {path.name}")

        return ParsedActivity(
            points=points,
            sport=sport,
            sub_sport=sub_sport,
            started_at=points[0].timestamp,
            source_file=path.name,
            source_hash="",
            altitude_source="gps",
        )


def _parse_ts(s: str) -> datetime:
    # ISO 8601 with or without fractional seconds, with Z or numeric offset (+02:00)
    import re as _re
    # Strip trailing Z → assume UTC
    if s.endswith("Z"):
        s = s[:-1]
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    # Numeric offset like +02:00, -05:30, or +0200 — parse with %z then convert to UTC
    m = _re.match(r"^(.+)([+-]\d{2}:?\d{2})$", s)
    if m:
        body, off = m.group(1), m.group(2).replace(":", "")
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                dt = datetime.strptime(body + off, fmt + "%z")
                return dt.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
    raise ValueError(f"Cannot parse timestamp: {s!r}")
