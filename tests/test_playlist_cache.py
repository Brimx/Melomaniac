"""Pruebas del caché en disco de playlists de plataforma."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from core.models import PlaylistMeta, Track
from services import playlist_cache


def _track(tid: str, name: str, **extra) -> Track:
    return Track(
        id=tid,
        name=name,
        artist="Artista",
        album="Álbum",
        duration="3:00",
        img_url="https://img.example/cover.jpg",
        platform="Spotify",
        duration_ms=180_000,
        **extra,
    )


class PlaylistCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache_dir = Path(self._tmp.name) / "playlist_cache"
        patcher = mock.patch.object(playlist_cache, "CACHE_DIR", self.cache_dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_roundtrip_preserves_meta_and_tracks(self) -> None:
        tracks = [_track("t1", "Uno", isrc="ABC123456789"), _track("t2", "Dos")]
        playlist_cache.save("Spotify", "pl1", PlaylistMeta("Mix", "desc"), tracks)

        entry = playlist_cache.load("Spotify", "pl1")

        self.assertIsNotNone(entry)
        assert entry is not None
        self.assertEqual(entry.meta.name, "Mix")
        self.assertEqual(entry.meta.description, "desc")
        self.assertEqual([t.id for t in entry.tracks], ["t1", "t2"])
        self.assertEqual(entry.tracks[0].isrc, "ABC123456789")
        self.assertEqual(entry.tracks[0].duration_ms, 180_000)
        self.assertTrue(entry.is_fresh())

    def test_expired_entry_is_stale(self) -> None:
        playlist_cache.save("Spotify", "pl1", PlaylistMeta("Mix", ""), [_track("t1", "Uno")])
        entry = playlist_cache.load("Spotify", "pl1")
        assert entry is not None
        entry.fetched_at = time.time() - 10_000

        self.assertFalse(entry.is_fresh())
        self.assertFalse(entry.is_fresh(60))

    def test_corrupt_or_foreign_file_returns_none(self) -> None:
        path = playlist_cache._cache_path("Spotify", "pl1")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{no es json", encoding="utf-8")
        self.assertIsNone(playlist_cache.load("Spotify", "pl1"))

        path.write_text(json.dumps({"version": 99, "tracks": []}), encoding="utf-8")
        self.assertIsNone(playlist_cache.load("Spotify", "pl1"))

    def test_prune_drops_oldest_entries(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        for i in range(6):
            path = self.cache_dir / f"fake__{i}.json"
            path.write_text("{}", encoding="utf-8")
            os.utime(path, (now - 100 + i, now - 100 + i))

        playlist_cache.prune(max_files=3)

        left = sorted(p.name for p in self.cache_dir.glob("*.json"))
        self.assertEqual(left, ["fake__3.json", "fake__4.json", "fake__5.json"])


if __name__ == "__main__":
    unittest.main()
