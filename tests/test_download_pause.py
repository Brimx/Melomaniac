"""
tests/test_download_pause.py — Transición de estado al pausar Descargas
═════════════════════════════════════════════════════════════════════
Sin red, sin Flet y sin yt-dlp: la pausa marcaba la pista activa como
``failed`` y la UI la pintaba «Error» mientras yt-dlp abortaba, así que una
pausa pedida por el usuario parecía un fallo real.
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import TransferState
from core.state import AppState


def _items() -> list[dict]:
    return [
        {"id": "1", "title": "A", "status": "done", "percent": 100.0, "error": ""},
        {"id": "2", "title": "B", "status": "running", "percent": 42.0, "error": ""},
        {"id": "3", "title": "C", "status": "queued", "percent": 0.0, "error": ""},
    ]


class PauseTransitionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.state = AppState(service=None)
        self.state.download_state = TransferState.RUNNING
        self.state.download_items = _items()
        self.state._download_pause = asyncio.Event()

    def test_active_track_becomes_paused_not_failed(self) -> None:
        self.state.pause_downloads()
        self.assertEqual(self.state.download_items[1]["status"], "paused")

    def test_pause_keeps_progress(self) -> None:
        self.state.pause_downloads()
        self.assertEqual(self.state.download_items[1]["percent"], 42.0)

    def test_pause_does_not_touch_finished_tracks(self) -> None:
        self.state.pause_downloads()
        self.assertEqual(self.state.download_items[0]["status"], "done")
        self.assertEqual(self.state.download_items[2]["status"], "queued")

    def test_event_is_set_and_flag_raised(self) -> None:
        self.state.pause_downloads()
        self.assertTrue(self.state._download_pause.is_set())
        self.assertTrue(self.state.download_paused)

    def test_pause_ignored_when_not_running(self) -> None:
        self.state.download_state = TransferState.IDLE
        self.state.pause_downloads()
        self.assertFalse(self.state.download_paused)
        self.assertEqual(self.state.download_items[1]["status"], "running")

    def test_paused_items_stay_out_of_failed_list(self) -> None:
        # La pista pausada nunca debe aparecer como fallida en el panel.
        self.state.pause_downloads()
        self.assertEqual(self.state.download_failed, [])

    def test_cleanup_stale_part_files_removes_dead_pid_and_keeps_active_pid(self) -> None:
        import os
        import tempfile
        from unittest.mock import patch
        from engine.downloader import _cleanup_stale_part_files

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dead_part = root / ".Artist - Song.999999.temp.part"
            dead_ytdl = root / ".Artist - Song.999999.temp.ytdl"
            alive_part = root / f".Artist - Song.{os.getpid()}.temp.part"
            unrelated = root / ".keep.hidden"
            for f in (dead_part, dead_ytdl, alive_part, unrelated):
                f.write_bytes(b"partial")

            with patch("engine.downloader._is_pid_alive", side_effect=lambda pid: pid == os.getpid()):
                _cleanup_stale_part_files(root, "Artist - Song")

            self.assertFalse(dead_part.exists())
            self.assertFalse(dead_ytdl.exists())
            self.assertTrue(alive_part.exists())
            self.assertTrue(unrelated.exists())


if __name__ == "__main__":
    unittest.main()
