"""Parser factory — selects the right parser based on file extension."""

from pathlib import Path

from bincio.extract.models import ParsedActivity
from bincio.extract.parsers.base import BaseParser
from bincio.extract.parsers.fit import FitParser
from bincio.extract.parsers.gpx import GpxParser
from bincio.extract.parsers.tcx import TcxParser

# Supported extensions (including .gz variants)
SUPPORTED = {".fit", ".gpx", ".tcx", ".fit.gz", ".gpx.gz", ".tcx.gz"}

_PARSERS: dict[str, type[BaseParser]] = {
    ".fit": FitParser,
    ".gpx": GpxParser,
    ".tcx": TcxParser,
}


def _base_ext(path: Path) -> str:
    """Return the meaningful extension, stripping .gz if present."""
    if path.suffix == ".gz":
        return Path(path.stem).suffix  # e.g. ".fit" from "ride.fit.gz"
    return path.suffix


def is_supported(path: Path) -> bool:
    suffix = "".join(path.suffixes[-2:]) if path.suffix == ".gz" else path.suffix
    return suffix in SUPPORTED


def parse_file(path: Path) -> ParsedActivity:
    """Parse an activity file, handling .gz transparently."""
    ext = _base_ext(path)
    parser_cls = _PARSERS.get(ext)
    if parser_cls is None:
        raise ValueError(f"Unsupported file type: {path.name!r}")

    raw_bytes, content_bytes = BaseParser._read_file(path)
    parser = parser_cls()
    activity = parser.parse(path, content_bytes)
    # Attach hash of the *original* bytes (compressed if .gz) for dedup
    activity.source_hash = BaseParser._sha256(raw_bytes)
    activity.source_file = path.name
    return activity
