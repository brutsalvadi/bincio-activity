"""Extract stage configuration — loaded from extract_config.yaml."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class TrackConfig:
    simplify: str = "rdp"
    rdp_epsilon: float = 0.0001
    timeseries_hz: int = 1


@dataclass
class SensorsConfig:
    heart_rate: bool = True
    cadence: bool = True
    temperature: bool = True
    power: bool = True


@dataclass
class ClassifierConfig:
    enabled: bool = False  # off by default; opt-in


@dataclass
class StravaConfig:
    client_id: str = ""
    client_secret: str = ""


@dataclass
class AthleteConfig:
    max_hr: int | None = None
    ftp_w: int | None = None
    hr_zones: list[list[int]] | None = None   # [[lo, hi], ...]
    power_zones: list[list[int]] | None = None


@dataclass
class ExtractConfig:
    input_dirs: list[Path]
    output_dir: Path
    metadata_csv: Optional[Path] = None
    default_privacy: str = "public"
    sensors: SensorsConfig = field(default_factory=SensorsConfig)
    track: TrackConfig = field(default_factory=TrackConfig)
    classifier: ClassifierConfig = field(default_factory=ClassifierConfig)
    incremental: bool = True
    workers: Optional[int] = None   # None → use CPU count
    owner_handle: str = "me"
    owner_display_name: str = "Me"
    athlete: AthleteConfig | None = None
    strava: StravaConfig | None = None


def load_config(path: Path) -> ExtractConfig:
    raw = yaml.safe_load(path.read_text()) or {}

    inp = raw.get("input", {})
    dirs = [Path(d).expanduser() for d in inp.get("dirs", [])]
    csv_path = inp.get("metadata_csv")

    out = Path(raw.get("output", {}).get("dir", "./bincio_data")).expanduser()

    owner = raw.get("owner", {})

    sensors_raw = raw.get("sensors", {})
    sensors = SensorsConfig(
        heart_rate=sensors_raw.get("heart_rate", True),
        cadence=sensors_raw.get("cadence", True),
        temperature=sensors_raw.get("temperature", True),
        power=sensors_raw.get("power", True),
    )

    track_raw = raw.get("track", {})
    track = TrackConfig(
        simplify=track_raw.get("simplify", "rdp"),
        rdp_epsilon=track_raw.get("rdp_epsilon", 0.0001),
        timeseries_hz=track_raw.get("timeseries_hz", 1),
    )

    cls_raw = raw.get("classifier", {})
    classifier = ClassifierConfig(enabled=cls_raw.get("enabled", False))

    ath_raw = raw.get("athlete", {})
    athlete = AthleteConfig(
        max_hr=ath_raw.get("max_hr"),
        ftp_w=ath_raw.get("ftp_w"),
        hr_zones=ath_raw.get("hr_zones"),
        power_zones=ath_raw.get("power_zones"),
    ) if ath_raw else None

    strava_raw = (raw.get("import") or {}).get("strava") or {}
    strava = StravaConfig(
        client_id=str(strava_raw["client_id"]) if strava_raw.get("client_id") else "",
        client_secret=str(strava_raw["client_secret"]) if strava_raw.get("client_secret") else "",
    ) if strava_raw else None

    return ExtractConfig(
        input_dirs=dirs,
        output_dir=out,
        metadata_csv=Path(csv_path).expanduser() if csv_path else None,
        default_privacy=raw.get("default_privacy", "public"),
        sensors=sensors,
        track=track,
        classifier=classifier,
        incremental=raw.get("incremental", True),
        workers=raw.get("workers"),
        owner_handle=owner.get("handle", "me"),
        owner_display_name=owner.get("display_name", "Me"),
        athlete=athlete,
        strava=strava,
    )


def default_config(input_dir: Path, output_dir: Path) -> ExtractConfig:
    return ExtractConfig(input_dirs=[input_dir], output_dir=output_dir)
