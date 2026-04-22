"""Sport name normalisation."""

import re

_MAPPING: dict[str, str] = {
    # cycling variants (FIT enums, GPX types, Strava API/CSV types)
    "cycling": "cycling",
    "biking": "cycling",
    "bike": "cycling",
    "ride": "cycling",
    "road_biking": "cycling",
    "road_cycling": "cycling",
    "mountain_biking": "cycling",
    "mountain_bike_ride": "cycling",
    "gravel_cycling": "cycling",
    "gravel_ride": "cycling",
    "cyclocross": "cycling",
    "indoor_cycling": "cycling",
    "indoor_ride": "cycling",
    "virtual_ride": "cycling",
    "e_biking": "cycling",
    "ebikeride": "cycling",
    "e_bike_ride": "cycling",
    "ebike_ride": "cycling",
    "handcycle": "cycling",
    "velomobile": "cycling",
    # running
    "running": "running",
    "run": "running",
    "trail_running": "running",
    "trail_run": "running",
    "treadmill_running": "running",
    "treadmill": "running",
    "virtual_run": "running",
    "outdoor_run": "running",
    "indoor_run": "running",
    "track_run": "running",
    # hiking
    "hiking": "hiking",
    "hike": "hiking",
    "outdoor_hike": "hiking",
    # walking
    "walking": "walking",
    "walk": "walking",
    "outdoor_walk": "walking",
    # skiing
    "skiing": "skiing",
    "cross_country_skiing": "skiing",
    "nordic_skiing": "skiing",
    "nordic_ski": "skiing",
    "downhill_skiing": "skiing",
    "alpine_skiing": "skiing",
    "alpine_ski": "skiing",
    "skate_skiing": "skiing",
    "backcountry_skiing": "skiing",
    "backcountry_ski": "skiing",
    # swimming
    "swimming": "swimming",
    "swim": "swimming",
    "open_water_swimming": "swimming",
    "lap_swimming": "swimming",
}

_SUB_SPORT_MAPPING: dict[str, str] = {
    # cycling
    "ride":               "road",
    "road_biking":        "road",
    "road_cycling":       "road",
    "mountain_biking":    "mountain",
    "mountain_bike_ride": "mountain",
    "gravel_cycling":     "gravel",
    "gravel_ride":        "gravel",
    "cyclocross":         "gravel",
    "indoor_cycling":     "indoor",
    "indoor_ride":        "indoor",
    "virtual_ride":       "indoor",
    # running
    "trail_running":      "trail",
    "trail_run":          "trail",
    "treadmill_running":  "indoor",
    "treadmill":          "indoor",
    "indoor_run":         "indoor",
    "virtual_run":        "indoor",
    "track_run":          "track",
    # skiing
    "cross_country_skiing":  "nordic",
    "nordic_skiing":         "nordic",
    "nordic_ski":            "nordic",
    "skate_skiing":          "nordic",
    "backcountry_skiing":    "nordic",
    "backcountry_ski":       "nordic",
    "downhill_skiing":       "alpine",
    "alpine_skiing":         "alpine",
    "alpine_ski":            "alpine",
    # swimming
    "open_water_swimming":   "open_water",
    "lap_swimming":          "pool",
}

BAS_SPORTS = {"cycling", "running", "hiking", "walking", "swimming", "skiing", "other"}


def _normalise_key(raw: object) -> str:
    key = str(raw).strip()
    # CamelCase → snake_case  ("MountainBikeRide" → "mountain_bike_ride")
    key = re.sub(r"([A-Z])", r"_\1", key).lower().lstrip("_")
    key = key.replace(" ", "_").replace("-", "_")
    return re.sub(r"^\d+", "", key)


def normalise_sport(raw: object) -> str:
    if raw is None:
        return "other"
    return _MAPPING.get(_normalise_key(raw), "other")


def normalise_sub_sport(raw: object) -> str | None:
    """Infer sub_sport from a raw sport type string (e.g. 'mountain_bike_ride' → 'mountain').

    Returns None when no sub_sport is implied (e.g. plain 'ride', 'run').
    """
    if raw is None:
        return None
    return _SUB_SPORT_MAPPING.get(_normalise_key(raw))
