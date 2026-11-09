"""
tests/test_local_index.py — Índice persistente de audio local (unittest)
════════════════════════════════════════════════════════════════════
Sin Flet y sin Mutagen real: valida la validez por ``mtime``/tamaño, la
persistencia atómica y el uso del índice desde ``build_local_tracks`` y
``scan_audio_folder``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import parsers
from engine.local_index import LocalIndex


class IndexValidityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.json_path = self.dir / "index.json"
        self.audio = self.dir / "Artist - Song.mp3"
        self.audio.write_bytes(b"x" * 64)
        self.index = LocalIndex(self.json_path)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_remember_then_hit(self) -> None:
        self.index.remember(str(self.audio), {"title": "Song"})
        self.assertEqual(self.index.metadata_for(str(self.audio)), {"title": "Song"})

    def test_bump_mtime_invalidates(self) -> None:
        self.index.remember(str(self.audio), {"title": "Song"})
        st = self.audio.stat()
        os.utime(self.audio, (st.st_atime, st.st_mtime + 10))
        self.assertIsNone(self.index.metadata_for(str(self.audio)))

    def test_change_size_invalidates(self) -> None:
        self.index.remember(str(self.audio), {"title": "Song"})
        st = self.audio.stat()
        self.audio.write_bytes(b"y" * 128)
        os.utime(self.audio, (st.st_atime, st.st_mtime))
        self.assertIsNone(self.index.metadata_for(str(self.audio)))

    def test_missing_file_returns_none(self) -> None:
        self.index.remember(str(self.audio), {"title": "Song"})
        self.audio.unlink()
        self.assertIsNone(self.index.metadata_for(str(self.audio)))

    def test_roundtrip_persists_metadata(self) -> None:
        self.index.remember(str(self.audio), {"title": "Song", "artist": "Artist"})
        self.assertTrue(self.index.save())
        reloaded = LocalIndex(self.json_path)
        self.assertEqual(reloaded.metadata_for(str(self.audio))["artist"], "Artist")

    def test_corrupt_json_is_ignored(self) -> None:
        self.json_path.write_text("{no es json", encoding="utf-8")
        index = LocalIndex(self.json_path)
        self.assertIsNone(index.metadata_for(str(self.audio)))

    def test_save_without_changes_is_noop(self) -> None:
        self.assertFalse(self.index.save())

    def test_prune_folder_removes_dead_paths(self) -> None:
        self.index.remember(str(self.audio), {"title": "Song"})
        self.index.prune_folder(str(self.dir), set())
        self.assertIsNone(self.index.metadata_for(str(self.audio)))


class BuildLocalTracksWithIndexTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.audio = self.dir / "Artist - Song.mp3"
        self.audio.write_bytes(b"x" * 64)
        self.index = LocalIndex(self.dir / "index.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_mutagen_is_read_only_once(self) -> None:
        calls: list[str] = []

        def _fake_read(path):
            calls.append(path)
            return {"title": "Song", "artist": "Artist", "album": "LP", "duration_ms": 1000}

        with mock.patch.object(parsers, "read_audio_metadata", side_effect=_fake_read):
            parsers.build_local_tracks([str(self.audio)], index=self.index)
            parsers.build_local_tracks([str(self.audio)], index=self.index)

        self.assertEqual(len(calls), 1, "el segundo pase debe salir del índice")

    def test_without_index_always_reads(self) -> None:
        calls: list[str] = []
        with mock.patch.object(
            parsers, "read_audio_metadata",
            side_effect=lambda p: calls.append(p) or {"title": "Song"},
        ):
            parsers.build_local_tracks([str(self.audio)])
            parsers.build_local_tracks([str(self.audio)])

        self.assertEqual(len(calls), 2)

    def test_scan_prunes_removed_files(self) -> None:
        other = self.dir / "Other - Track.mp3"
        other.write_bytes(b"z" * 32)
        with mock.patch.object(parsers, "read_audio_metadata", return_value={"title": "T"}):
            parsers.scan_audio_folder(self.dir, index=self.index)
        other.unlink()
        with mock.patch.object(parsers, "read_audio_metadata", return_value={"title": "T"}):
            parsers.scan_audio_folder(self.dir, index=self.index)

        self.assertIsNone(self.index.metadata_for(str(other)))
        self.assertIsNotNone(self.index.metadata_for(str(self.audio)))


class CoverCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.index = LocalIndex(self.dir / "index.json")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_embedded_cover_cached_in_memory(self) -> None:
        calls: list[str] = []
        builder = lambda p: calls.append(p) or "data:image/jpeg;base64,AA=="  # noqa: E731

        self.assertTrue(self.index.embedded_cover("/music/a.mp3", builder))
        self.assertTrue(self.index.embedded_cover("/music/a.mp3", builder))
        self.assertEqual(len(calls), 1)

    def test_folder_cover_invalidated_by_sidecar_mtime(self) -> None:
        sidecar = self.dir / "cover.jpg"
        sidecar.write_bytes(b"img")
        uri = self.index.folder_cover(str(self.dir), str(sidecar), lambda p: "data:1")
        self.assertEqual(uri, "data:1")

        st = sidecar.stat()
        os.utime(sidecar, (st.st_atime, st.st_mtime + 5))
        rebuilt = self.index.folder_cover(str(self.dir), str(sidecar), lambda p: "data:2")
        self.assertEqual(rebuilt, "data:2")

    def test_missing_sidecar_returns_empty(self) -> None:
        self.assertEqual(
            self.index.folder_cover(str(self.dir), str(self.dir / "nope.jpg"), lambda p: "x"),
            "",
        )


class ScanFolderAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_scan_folder_async_returns_track_list(self) -> None:
        from services.local_index import scan_folder_async

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            audio = d / "Artist - Song.mp3"
            audio.write_bytes(b"x" * 64)
            with mock.patch.object(parsers, "read_audio_metadata", return_value={"title": "Song", "artist": "Artist"}):
                tracks = await scan_folder_async(str(d))
            self.assertIsInstance(tracks, list)
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0].name, "Song")


if __name__ == "__main__":
    unittest.main()
