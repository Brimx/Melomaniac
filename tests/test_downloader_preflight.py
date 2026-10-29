"""
tests/test_downloader_preflight.py — Preflight de FFmpeg en Descargas (unittest)
════════════════════════════════════════════════════════════════════════
Sin red, sin Flet y sin yt-dlp: valida la detección de FFmpeg, el código de
error estable y la coerción de entradas locales de ``build_local_tracks``.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import downloader
from engine.downloader import DOWNLOAD_ERROR_NO_FFMPEG
from engine.parsers import build_local_tracks


class FfmpegAvailableTest(unittest.TestCase):
    def setUp(self) -> None:
        # La detección se cachea por proceso; se aísla en cada prueba.
        self._original = downloader._ffmpeg_path_cache
        downloader._ffmpeg_path_cache = False

    def tearDown(self) -> None:
        downloader._ffmpeg_path_cache = self._original

    def test_detects_ffmpeg_on_path(self) -> None:
        with mock.patch.object(downloader.shutil, "which", return_value="/usr/bin/ffmpeg"):
            self.assertTrue(downloader.ffmpeg_available())

    def test_missing_ffmpeg_returns_false(self) -> None:
        with mock.patch.object(downloader.shutil, "which", return_value=None):
            self.assertFalse(downloader.ffmpeg_available())

    def test_result_is_cached(self) -> None:
        with mock.patch.object(
            downloader.shutil, "which", return_value="/usr/bin/ffmpeg"
        ) as which:
            downloader.ffmpeg_available()
            downloader.ffmpeg_available()
        self.assertEqual(which.call_count, 1)


class DownloadWithoutFfmpegTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original = downloader._ffmpeg_path_cache
        downloader._ffmpeg_path_cache = None  # simula ausencia sin tocar PATH

    def tearDown(self) -> None:
        downloader._ffmpeg_path_cache = self._original

    def test_returns_stable_error_code(self) -> None:
        with mock.patch.object(downloader, "search_video_id") as search:
            result = downloader.download_audio({"name": "X", "artist": "Y"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, DOWNLOAD_ERROR_NO_FFMPEG)
        self.assertIn("FFmpeg", result.error)
        # No debe gastar una búsqueda en YouTube si falta FFmpeg.
        search.assert_not_called()


class SkipExistingTest(unittest.TestCase):
    """«Omitir existentes»: no rebuscar ni sobrescribir lo ya descargado."""

    def setUp(self) -> None:
        import tempfile

        self._original = downloader._ffmpeg_path_cache
        downloader._ffmpeg_path_cache = "/usr/bin/ffmpeg"  # preflight pasa
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        downloader._ffmpeg_path_cache = self._original
        self.tmp.cleanup()

    def _track(self):
        t = mock.Mock()
        t.name, t.artist = "Song", "Artist"
        t.duration_ms, t.source_url = 180000, ""
        return t

    def test_existing_file_is_skipped_without_searching(self) -> None:
        from core.config import DEFAULT_AUDIO_FORMAT, DOWNLOAD_MIN_BYTES
        target = self.dir / f"Artist - Song.{DEFAULT_AUDIO_FORMAT}"
        target.write_bytes(b"\0" * (DOWNLOAD_MIN_BYTES + 1))
        with mock.patch.object(downloader, "search_video_id") as search:
            result = downloader.download_audio(
                self._track(), destination_dir=self.dir, skip_existing=True
            )
        self.assertTrue(result.ok)
        self.assertTrue(result.skipped)
        self.assertEqual(result.code, downloader.DOWNLOAD_SKIPPED)
        search.assert_not_called()

    def test_without_skip_flag_it_downloads(self) -> None:
        from core.config import DEFAULT_AUDIO_FORMAT, DOWNLOAD_MIN_BYTES
        target = self.dir / f"Artist - Song.{DEFAULT_AUDIO_FORMAT}"
        target.write_bytes(b"\0" * (DOWNLOAD_MIN_BYTES + 1))
        with mock.patch.object(downloader, "search_video_id") as search:
            search.return_value = mock.Mock(video_id="")
            result = downloader.download_audio(
                self._track(), destination_dir=self.dir, skip_existing=False
            )
        self.assertFalse(result.skipped)
        search.assert_called_once()

    def test_tiny_existing_file_is_not_trusted(self) -> None:
        """Un archivo truncado no cuenta como descarga válida."""
        from core.config import DEFAULT_AUDIO_FORMAT
        target = self.dir / f"Artist - Song.{DEFAULT_AUDIO_FORMAT}"
        target.write_bytes(b"\0" * 10)
        with mock.patch.object(downloader, "search_video_id") as search:
            search.return_value = mock.Mock(video_id="")
            result = downloader.download_audio(
                self._track(), destination_dir=self.dir, skip_existing=True
            )
        self.assertFalse(result.skipped)
        search.assert_called_once()


class BuildLocalTracksContractTest(unittest.TestCase):
    def test_accepts_bare_path_string(self) -> None:
        tracks = build_local_tracks(["/tmp/Album/My Song.flac"])
        self.assertEqual(len(tracks), 1)
        # Antes se tomaban los dos primeros caracteres del path como
        # (artist, title); ahora el título sale del nombre del archivo.
        self.assertEqual(tracks[0].name, "My Song")
        self.assertEqual(tracks[0].artist, "")
        self.assertEqual(tracks[0].source_path, "/tmp/Album/My Song.flac")

    def test_accepts_path_object(self) -> None:
        tracks = build_local_tracks([Path("/tmp/x/Track Name.mp3")])
        self.assertEqual(tracks[0].name, "Track Name")

    def test_accepts_artist_title_tuple(self) -> None:
        tracks = build_local_tracks([("Artist", "Title", "")])
        self.assertEqual((tracks[0].artist, tracks[0].name), ("Artist", "Title"))

    def test_accepts_pair_without_path(self) -> None:
        tracks = build_local_tracks([("Artist", "Title")])
        self.assertEqual(tracks[0].source_path, "")


if __name__ == "__main__":
    unittest.main()
