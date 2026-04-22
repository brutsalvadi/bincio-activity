from bincio.extract.sport import normalise_sport, normalise_sub_sport


def test_cycling_variants():
    for raw in ("cycling", "Biking", "road_biking", "virtual_ride", "e-biking"):
        assert normalise_sport(raw) == "cycling", raw


def test_running_variants():
    for raw in ("running", "Run", "trail_running", "virtual_run"):
        assert normalise_sport(raw) == "running", raw


def test_skiing_variants():
    for raw in ("skiing", "alpine_skiing", "nordic_skiing", "backcountry_ski"):
        assert normalise_sport(raw) == "skiing", raw


def test_swimming_variants():
    for raw in ("swimming", "swim", "open_water_swimming", "lap_swimming"):
        assert normalise_sport(raw) == "swimming", raw


def test_unknown_falls_back_to_other():
    assert normalise_sport("yoga") == "other"
    assert normalise_sport(None) == "other"


def test_sub_sport_strava_camelcase():
    assert normalise_sub_sport("MountainBikeRide") == "mountain"
    assert normalise_sub_sport("GravelRide") == "gravel"
    assert normalise_sub_sport("VirtualRide") == "indoor"
    assert normalise_sub_sport("Ride") == "road"


def test_sub_sport_ski_variants():
    assert normalise_sub_sport("AlpineSki") == "alpine"
    assert normalise_sub_sport("NordicSki") == "nordic"
    assert normalise_sub_sport("BackcountrySki") == "nordic"


def test_sub_sport_unknown_returns_none():
    assert normalise_sub_sport("yoga") is None
    assert normalise_sub_sport(None) is None
    assert normalise_sub_sport("generic") is None
