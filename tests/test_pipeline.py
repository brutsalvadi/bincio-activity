"""End-to-end pipeline test: extract → merge → root manifest for two users.

Uses the 20 real FIT files checked into tests/data/dave/ and tests/data/brut/.
Run with:

    uv run pytest tests/test_pipeline.py -v

Skip during normal CI runs:

    uv run pytest -m "not integration"
"""

import json
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from bincio.extract.cli import extract as extract_cmd

TESTS_DIR = Path(__file__).parent
DATA_DIR = TESTS_DIR / "data"
DAVE_INPUT = DATA_DIR / "dave"
BRUT_INPUT = DATA_DIR / "brut"


@pytest.fixture(scope="module")
def data_root():
    """Run extract for dave and brut into a shared temp dir, yield the data root."""
    with tempfile.TemporaryDirectory(prefix="bincio_test_") as tmp:
        root = Path(tmp)
        runner = CliRunner()

        for handle, input_dir in [("dave", DAVE_INPUT), ("brut", BRUT_INPUT)]:
            cfg_path = root / f"cfg_{handle}.yaml"
            cfg_path.write_text(
                f"owner:\n  handle: {handle}\n"
                f"input:\n  dirs:\n    - {input_dir}\n"
                f"output:\n  dir: {root}\n"
            )
            result = runner.invoke(extract_cmd, ["--config", str(cfg_path)])
            assert result.exit_code == 0, (
                f"bincio extract failed for {handle}:\n{result.output}"
            )

        yield root


@pytest.mark.integration
@pytest.mark.slow
class TestPipeline:
    def test_activities_extracted_dave(self, data_root):
        acts = list((data_root / "dave" / "activities").glob("*.json"))
        assert len(acts) >= 8, f"Expected ≥8 activities for dave, got {len(acts)}"

    def test_activities_extracted_brut(self, data_root):
        acts = list((data_root / "brut" / "activities").glob("*.json"))
        assert len(acts) >= 8, f"Expected ≥8 activities for brut, got {len(acts)}"

    def test_index_json_dave(self, data_root):
        index = json.loads((data_root / "dave" / "index.json").read_text())
        assert len(index["activities"]) >= 8
        assert index["owner"]["handle"] == "dave"

    def test_index_json_brut(self, data_root):
        index = json.loads((data_root / "brut" / "index.json").read_text())
        assert len(index["activities"]) >= 8
        assert index["owner"]["handle"] == "brut"

    def test_merge_produces_merged_dir(self, data_root):
        from bincio.render.merge import merge_all
        merge_all(data_root / "dave")
        merge_all(data_root / "brut")

        assert (data_root / "dave" / "_merged" / "index.json").exists()
        assert (data_root / "brut" / "_merged" / "index.json").exists()

    def test_merged_index_has_activities(self, data_root):
        # Ensure merge ran (idempotent if already done by earlier test in class)
        from bincio.render.merge import merge_all
        merge_all(data_root / "dave")
        merge_all(data_root / "brut")

        for handle in ("dave", "brut"):
            merged_dir = data_root / handle / "_merged"
            root = json.loads((merged_dir / "index.json").read_text())
            # Root index now has year shards; collect all activities across them
            all_acts: list = list(root.get("activities", []))
            for shard in root.get("shards", []):
                sp = merged_dir / shard["url"]
                if sp.exists():
                    all_acts.extend(json.loads(sp.read_text()).get("activities", []))
            assert len(all_acts) >= 8, f"Expected ≥8 merged activities for {handle}"

    def test_root_manifest(self, data_root):
        from bincio.render.cli import _user_dirs, _write_root_manifest
        from rich.console import Console

        # _write_root_manifest uses the module-level console; patch it to suppress output
        import bincio.render.cli as render_cli
        render_cli.console = Console(quiet=True)

        _write_root_manifest(data_root)

        manifest = json.loads((data_root / "index.json").read_text())
        handles = {s["handle"] for s in manifest["shards"]}
        assert "dave" in handles
        assert "brut" in handles
        assert manifest["bas_version"] == "1.0"
        # Single-user path: no instance.db → private must be False
        assert manifest["instance"].get("private") is False

    def test_activity_json_structure(self, data_root):
        """Spot-check that extracted JSON has the required BAS fields."""
        acts = sorted((data_root / "dave" / "activities").glob("*.json"))
        detail = json.loads(acts[0].read_text())
        for field in ("id", "title", "sport", "started_at", "duration_s"):
            assert field in detail, f"Missing field '{field}' in activity JSON"

    def test_geojson_exists_for_gps_activities(self, data_root):
        """Each activity with GPS data should have a companion .geojson file."""
        acts_dir = data_root / "dave" / "activities"
        json_ids = {p.stem for p in acts_dir.glob("*.json")}
        geojson_ids = {p.stem for p in acts_dir.glob("*.geojson")}
        # At least some activities should have tracks (Karoo FIT files always have GPS)
        assert len(geojson_ids) >= 5, "Expected ≥5 GeoJSON track files for dave"
        assert geojson_ids.issubset(json_ids), "GeoJSON without matching detail JSON"
