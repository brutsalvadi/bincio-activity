"""Abstract base class for all activity parsers."""

import gzip
import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from bincio.extract.models import ParsedActivity


class BaseParser(ABC):
    @abstractmethod
    def parse(self, path: Path, raw_bytes: bytes) -> ParsedActivity:
        """Parse activity from raw file bytes.

        Receives pre-read bytes so the factory can compute the hash once and
        handle decompression transparently before dispatching.
        """

    @staticmethod
    def _sha256(data: bytes) -> str:
        return "sha256:" + hashlib.sha256(data).hexdigest()

    @staticmethod
    def _read_file(path: Path) -> tuple[bytes, bytes]:
        """Return (raw_bytes, decompressed_bytes).

        raw_bytes is the original file content (used for hashing).
        decompressed_bytes is what parsers should actually parse.

        Gzip is handled both by extension (.gz) and by magic bytes (0x1f 0x8b),
        so files that are gzip-compressed but named without .gz still parse correctly.
        """
        raw = path.read_bytes()
        if path.suffix == ".gz" or raw[:2] == b'\x1f\x8b':
            try:
                return raw, gzip.decompress(raw)
            except Exception:
                pass  # not actually gzip despite the magic bytes — fall through
        return raw, raw
