"""Smoke tests: import both FastAPI apps so missing names and bad syntax fail fast."""


def test_serve_server_importable():
    import bincio.serve.server  # noqa: F401


def test_edit_server_importable():
    import bincio.edit.server  # noqa: F401


def test_serve_app_has_routes():
    from bincio.serve.server import app
    paths = {r.path for r in app.routes}
    assert "/api/me" in paths
    assert "/api/upload" in paths
    assert "/api/upload/strava-zip" in paths
    assert "/api/strava/status" in paths
    assert "/api/strava/auth-url" in paths
    assert "/api/strava/callback" in paths
    assert "/api/strava/sync" in paths
    assert "/api/strava/sync/stream" in paths
    assert "/api/register" in paths


def test_edit_app_has_routes():
    from bincio.edit.server import app
    paths = {r.path for r in app.routes}
    assert "/api/upload" in paths
    assert "/api/upload/strava-zip" in paths
    assert "/api/activity/{activity_id}" in paths
    assert "/api/strava/sync" in paths
