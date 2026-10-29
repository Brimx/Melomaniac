"""
tests/test_audio_metadata_covers.py — Portada incrustada y sidecar (unittest)
════════════════════════════════════════════════════════════════════════
Regresión del bug donde ``read_cover`` perdía el APIC de todo MP3 real:
``MutagenFile`` devuelve un ``MP3`` cuyos tags son el ``ID3``, y la
comparación anterior exigía que el objeto fuera un ``ID3``.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.audio_metadata import (
    SIDECAR_COVER_FORMATS,
    SIDECAR_COVER_NAME,
    read_cover,
    write_cover_sidecar,
)

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:  # pragma: no cover
    HAS_PIL = False

try:
    from mutagen.id3 import APIC, ID3, TIT2
    from mutagen.mp3 import MP3
    HAS_MUTAGEN = True
except ImportError:  # pragma: no cover
    HAS_MUTAGEN = False


def _jpeg(size: int = 600) -> bytes:
    img = Image.new("RGB", (size, size), (10, 90, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@unittest.skipUnless(HAS_PIL and HAS_MUTAGEN, "Pillow/mutagen no disponibles")
class ReadCoverMp3Test(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "track.mp3"
        # Un MP3 mínimo pero válido: frames MPEG silenciosos.
        self.path.write_bytes(b"\xff\xf3\x84\xc4" + b"\0" * 4096)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["Track"]))
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=_jpeg()))
        tags.save(str(self.path))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mp3_cover_is_returned(self) -> None:
        data = read_cover(str(self.path))
        self.assertIsNotNone(data, "el APIC de un MP3 real debe leerse")
        self.assertTrue(data.startswith(b"\xff\xd8\xff"), "debe ser JPEG")

    def test_file_without_cover_returns_none(self) -> None:
        other = Path(self.tmp.name) / "nocover.mp3"
        other.write_bytes(b"\xff\xf3\x84\xc4" + b"\0" * 4096)
        tags = ID3()
        tags.add(TIT2(encoding=3, text=["Solo tags"]))
        tags.save(str(other))
        self.assertIsNone(read_cover(str(other)))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(read_cover(str(Path(self.tmp.name) / "nope.mp3")))


class SidecarTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_sidecar_is_written_beside_audio(self) -> None:
        audio = Path(self.tmp.name) / "track.opus"
        audio.write_bytes(b"x")
        target = write_cover_sidecar(str(audio), b"\xff\xd8\xffjpeg")
        self.assertTrue(target)
        self.assertEqual(Path(target).name, SIDECAR_COVER_NAME)
        self.assertEqual(Path(target).read_bytes(), b"\xff\xd8\xffjpeg")

    def test_sidecar_skips_empty_data(self) -> None:
        audio = Path(self.tmp.name) / "track.opus"
        audio.write_bytes(b"x")
        self.assertEqual(write_cover_sidecar(str(audio), b""), "")

    def test_opus_and_wav_need_sidecar(self) -> None:
        self.assertIn("opus", SIDECAR_COVER_FORMATS)
        self.assertIn("wav", SIDECAR_COVER_FORMATS)
        self.assertNotIn("mp3", SIDECAR_COVER_FORMATS)


if __name__ == "__main__":
    unittest.main()
