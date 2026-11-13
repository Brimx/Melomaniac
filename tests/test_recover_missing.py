"""
tests/test_recover_missing.py — Pruebas de Bloque 2.6 (Recuperación de pistas faltantes)
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path


def _run_in_subprocess(code: str) -> None:
    """Aísla importaciones de Flet del proceso principal de ``unittest``."""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Subprocess check failed (rc={proc.returncode}):\n"
            f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
        )


_SUBPROC_TESTS = """
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flet as ft

sys.path.insert(0, str(Path.cwd()))

from core.config import AUDIO_FORMATS
from core.models import Track, TransferState
from core.transfer import TransferIssue, TransferReport
from services import authentication, settings_store
from ui import downloads_view
from ui.dialogs import transfer_report
from ui.downloads_view import (
    DownloadsView,
    candidate_to_recovery,
    extract_youtube_video_id,
    search_youtube_alternatives,
)
from ui.dialogs.recovery_panel import (
    AlternativeSearchPanel,
    default_search_query,
    track_from_entry,
)
from ui.dialogs.transfer_report import TransferReportDialog


class _DummyPage:
    def __init__(self) -> None:
        self.services = []
        self.overlay = []
        self.dialogs = []
        self.tasks = []
        self.updates = 0

    def run_task(self, fn, *args, **kwargs):
        coro = fn(*args, **kwargs)
        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(coro)
            self.tasks.append(task)
            return task
        except RuntimeError:
            coro.close()
            return None

    def show_dialog(self, dlg) -> None:
        self.dialogs.append(dlg)

    def update(self) -> None:
        self.updates += 1


class _FakeAppState:
    def __init__(self) -> None:
        self.download_state = TransferState.DONE
        self.download_total = 3
        self.download_progress = 3
        self.download_current = ""
        self.download_items = []
        self.download_done = []
        self.download_skipped = []
        self.download_failed = []
        self.download_missing = []
        self.download_paused = False
        self.selected_count = 0
        self.audio_format = "mp3"
        self._download_resume_ctx = {
            "format": "mp3",
            "destination_dir": "/tmp/music",
            "playlist_name": "Mi Mix",
            "platform": "Spotify",
        }
        self._subs = []
        self.enqueued = []
        self.research_calls = []
        self.research_response = {
            "query": "Band Song",
            "candidates": [],
            "error": "",
        }

    def subscribe(self, fn) -> None:
        self._subs.append(fn)

    def notify(self) -> None:
        for fn in list(self._subs):
            fn()

    def log(self, _msg: str) -> None:
        pass

    async def research_download(self, entry: dict, *, audio_format: str = "") -> dict:
        self.research_calls.append(entry)
        return dict(self.research_response)

    def download_tracks(self, tracks: list[Track], **kwargs):
        self.enqueued.append((list(tracks), dict(kwargs)))
        return mock.MagicMock()


class _FakeTransferState:
    def __init__(self) -> None:
        self.logs = []
        self.notified = 0
        self.search_calls = []
        self.added = []
        self.remembered = []

    def log(self, msg: str) -> None:
        self.logs.append(msg)

    def notify(self) -> None:
        self.notified += 1

    async def search_candidates(self, dest: str, query: str, limit: int = 5) -> list[Track]:
        self.search_calls.append((dest, query, limit))
        return [
            Track(
                id="yt_1",
                name="Cancion",
                artist="Artista",
                album="Album",
                duration="3:20",
                img_url="",
                platform=dest,
                duration_ms=200000,
                isrc="US1234567890",
            )
        ]

    async def add_track_to_remote(self, dest: str, pid: str, tid: str) -> tuple[bool, str]:
        self.added.append((dest, pid, tid))
        return True, ""

    def remember_resolution(self, *args, **kwargs) -> None:
        self.remembered.append((args, kwargs))


def _find_controls(root, predicate) -> list:
    found = []
    if predicate(root):
        found.append(root)
    for attr in ("content", "leading", "trailing", "title"):
        child = getattr(root, attr, None)
        if child is not None and isinstance(child, ft.Control):
            found.extend(_find_controls(child, predicate))
    for attr in ("controls", "actions"):
        children = getattr(root, attr, None)
        if isinstance(children, (list, tuple)):
            for child in children:
                if isinstance(child, ft.Control):
                    found.extend(_find_controls(child, predicate))
    return found


class SharedRecoveryPatternTest(unittest.TestCase):
    def test_default_query_and_track_from_entry_clones_cleanly(self) -> None:
        q = default_search_query("Song Name (Remastered 2020) feat. Guest", "The Band")
        self.assertIn("Song Name", q)
        self.assertIn("The Band", q)
        self.assertNotIn("Remastered", q)

        orig = Track(
            id="t1", name="Song", artist="Band", album="LP",
            duration="3:00", img_url="", platform="Spotify",
            duration_ms=180000, source_url="https://open.spotify.com/track/t1",
        )
        cloned = track_from_entry({"_track": orig})
        self.assertIsNot(cloned, orig)
        cloned.source_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(orig.source_url, "https://open.spotify.com/track/t1")

    def test_alternative_search_panel_search_and_select(self) -> None:
        page = _DummyPage()
        orig = Track(
            id="1", name="Song (Live)", artist="Band", album="",
            duration="3:10", img_url="", platform="Spotify", duration_ms=190000,
        )
        picked = []

        async def _search(query: str) -> list[Track]:
            return [
                Track(
                    id="cand1", name="Song", artist="Band", album="LP",
                    duration="3:10", img_url="", platform="YouTube Music",
                    duration_ms=190000,
                )
            ]

        panel = AlternativeSearchPanel(
            page,
            orig,
            reason="Sin coincidencia",
            search_fn=_search,
            on_select=lambda c: picked.append(c),
            action_label="Usar",
        )

        async def _run() -> None:
            panel.query_field.value = "   "
            await panel.run_search()
            self.assertEqual(panel.status_text.value, "Escribe una búsqueda.")
            panel.query_field.value = "Band Song"
            await panel.run_search()
            self.assertEqual(len(panel.results_column.controls), 1)
            buttons = _find_controls(
                panel.results_column,
                lambda c: isinstance(c, ft.Button) or (hasattr(c, "on_click") and callable(c.on_click)),
            )
            self.assertTrue(buttons)
            buttons[-1].on_click(None)
            self.assertEqual(len(picked), 1)
            self.assertEqual(picked[0].track.id, "cand1")

        asyncio.run(_run())

    def test_transfer_report_reuses_alternative_search_panel(self) -> None:
        page = _DummyPage()
        st = _FakeTransferState()
        orig = Track(
            id="s1", name="Cancion (feat. X)", artist="Artista", album="",
            duration="3:20", img_url="", platform="Spotify", duration_ms=200000,
        )
        issue = TransferIssue(track=orig, kind="not_found", reason="No encontrada")
        report = TransferReport(
            started_at="2026-11-12T20:00:00",
            finished_at="2026-11-12T20:00:02",
            elapsed_s=1.5,
            destination="YouTube Music",
            status="DONE",
            title="Lista",
            description="",
            playlist_id="PL_1",
            detected=1,
            processed=1,
            confirmed=0,
            issues=[issue],
        )
        dlg = TransferReportDialog(page, st, report)
        dlg._show_search(issue)
        self.assertIsInstance(dlg._search_panel, AlternativeSearchPanel)

        async def _run() -> None:
            assert dlg._search_panel is not None
            await dlg._search_panel.run_search()
            self.assertEqual(st.search_calls[0][0], "YouTube Music")
            self.assertEqual(len(dlg._search_panel.results_column.controls), 1)
            await dlg._insert(
                Track(
                    id="yt_1", name="Cancion", artist="Artista", album="Album",
                    duration="3:20", img_url="", platform="YouTube Music",
                )
            )
            self.assertTrue(issue.resolved)
            self.assertEqual(report.confirmed, 1)
            self.assertEqual(st.added, [("YouTube Music", "PL_1", "yt_1")])

        with mock.patch.object(transfer_report, "append_resolution_log"):
            asyncio.run(_run())


class YouTubeAlternativeSearchTest(unittest.TestCase):
    def test_extract_youtube_video_id_variants(self) -> None:
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5s"),
            "dQw4w9WgXcQ",
        )
        self.assertEqual(
            extract_youtube_video_id("https://youtu.be/abc123DEF45"),
            "abc123DEF45",
        )
        self.assertEqual(
            extract_youtube_video_id("https://www.youtube.com/shorts/xyz987UVW12"),
            "xyz987UVW12",
        )
        self.assertEqual(extract_youtube_video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(extract_youtube_video_id("consulta libre"), "")

    def test_search_alternatives_uses_custom_query_when_edited(self) -> None:
        orig = Track(
            id="1", name="Track One", artist="Artist", album="",
            duration="3:20", img_url="", platform="Spotify", duration_ms=200000,
        )
        st = _FakeAppState()
        fake_entries = [
            {
                "id": "vidGood1234",
                "title": "Artist - Track One (Acoustic)",
                "channel": "Artist Official",
                "duration": 205,
                "is_live": False,
            },
            {
                "id": "vidLive9999",
                "title": "Stream 4h",
                "channel": "Random",
                "duration": 14400,
                "is_live": True,
            },
        ]

        class _FakeYDL:
            def __init__(self, _opts) -> None:
                self.cookiejar = None

            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                pass

            def extract_info(self, query: str, download: bool = False) -> dict:
                self.last_query = query
                return {"entries": fake_entries}

        async def _run() -> None:
            direct = await search_youtube_alternatives(
                orig, "https://youtu.be/dQw4w9WgXcQ", app_state=st,
            )
            self.assertEqual(direct["candidates"][0]["video_id"], "dQw4w9WgXcQ")

            with mock.patch("yt_dlp.YoutubeDL", _FakeYDL):
                custom = await search_youtube_alternatives(
                    orig,
                    "Artist Track One acoustic",
                    entry={"id": "1", "name": "Track One", "artist": "Artist"},
                    app_state=st,
                )
            self.assertEqual(custom["query"], "Artist Track One acoustic")
            ids = [c["video_id"] for c in custom["candidates"]]
            self.assertEqual(ids, ["vidGood1234"])
            self.assertEqual(st.research_calls, [])

        asyncio.run(_run())


class DownloadsViewRecoveryTest(unittest.TestCase):
    def _make_view(self, tmp_path: Path, state: _FakeAppState) -> tuple[_DummyPage, DownloadsView]:
        page = _DummyPage()
        with (
            mock.patch.object(authentication, "CONFIG_DIR", tmp_path),
            mock.patch.object(settings_store, "SETTINGS_JSON", tmp_path / "settings.json"),
            mock.patch.object(settings_store, "ensure_config_dir", lambda: tmp_path.mkdir(parents=True, exist_ok=True)),
            mock.patch.object(downloads_view, "ffmpeg_available", return_value=True),
        ):
            view = DownloadsView(page, state)
        return page, view

    def test_failed_missing_and_skipped_rows_expose_recovery_actions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            st = _FakeAppState()
            st.download_done = [
                {"id": "d1", "name": "Done", "artist": "A", "format": "mp3", "path": "/tmp/d.mp3"},
            ]
            st.download_skipped = [
                {"id": "s1", "name": "Skipped", "artist": "B", "format": "mp3", "path": "/tmp/s.mp3"},
            ]
            st.download_failed = [
                {"id": "f1", "name": "Failed", "artist": "C", "format": "mp3", "error": "HTTP 403"},
            ]
            st.download_missing = [
                {"id": "m1", "name": "Missing", "artist": "D", "format": "mp3", "error": "Pausada"},
            ]

            _page, view = self._make_view(tmp_path, st)

            skipped_row = view._done_col.controls[1]
            done_row = view._done_col.controls[2]
            failed_row = view._failed_col.controls[1]
            missing_row = view._missing_col.controls[1]

            self.assertTrue(_find_controls(skipped_row, lambda c: isinstance(c, ft.PopupMenuButton)))
            self.assertFalse(_find_controls(done_row, lambda c: isinstance(c, ft.PopupMenuButton)))
            self.assertTrue(_find_controls(failed_row, lambda c: isinstance(c, ft.PopupMenuButton)))
            self.assertTrue(_find_controls(missing_row, lambda c: isinstance(c, ft.PopupMenuButton)))

    def test_open_recovery_search_change_format_and_reenqueue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            st = _FakeAppState()
            entry = {
                "id": "f1",
                "name": "Failed Song",
                "artist": "The Band",
                "album": "LP",
                "platform": "Spotify",
                "duration_ms": 210000,
                "isrc": "USXYZ9999999",
                "error": "No match",
                "format": "mp3",
                "video_id": "oldVid12345",
            }
            st.download_failed = [entry]
            st.research_response = {
                "query": "The Band Failed Song",
                "candidates": [
                    {
                        "video_id": "oldVid12345",
                        "title": "Wrong Video",
                        "channel": "Other",
                        "duration_s": 210,
                        "score": 65.0,
                        "url": "https://www.youtube.com/watch?v=oldVid12345",
                    },
                    {
                        "video_id": "newVid67890",
                        "title": "The Band - Failed Song (Official Audio)",
                        "channel": "The Band",
                        "duration_s": 210,
                        "score": 98.0,
                        "url": "https://www.youtube.com/watch?v=newVid67890",
                    },
                ],
                "error": "",
            }

            page, view = self._make_view(tmp_path, st)

            async def _run() -> None:
                panel = await view._open_recovery(entry, kind="failed")
                self.assertEqual(len(page.dialogs), 1)
                dlg = page.dialogs[0]
                self.assertEqual(len(panel.results_column.controls), 2)

                dds = _find_controls(panel, lambda c: isinstance(c, ft.Dropdown))
                self.assertEqual(len(dds), 1)
                self.assertEqual(set(o.key for o in dds[0].options), set(AUDIO_FORMATS))
                dds[0].value = "flac"

                row2 = panel.results_column.controls[1]
                pick_btns = _find_controls(
                    row2,
                    lambda c: isinstance(c, ft.Button) and callable(getattr(c, "on_click", None)),
                )
                self.assertTrue(pick_btns)
                pick_btns[-1].on_click(None)

                self.assertFalse(dlg.open)
                self.assertEqual(len(st.enqueued), 1)
                tracks, kwargs = st.enqueued[0]
                self.assertEqual(len(tracks), 1)
                self.assertEqual(tracks[0].name, "Failed Song")
                self.assertEqual(
                    tracks[0].source_url,
                    "https://www.youtube.com/watch?v=newVid67890",
                )
                self.assertEqual(kwargs["audio_format"], "flac")
                self.assertEqual(kwargs["destination_dir"], "/tmp/music")
                self.assertEqual(kwargs["playlist_name"], "Mi Mix")
                self.assertEqual(kwargs["platform"], "Spotify")

            asyncio.run(_run())

    def test_retry_with_format_button_and_running_guard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            st = _FakeAppState()
            entry = {
                "id": "s1",
                "name": "Skipped Song",
                "artist": "Artist",
                "format": "mp3",
                "path": "/tmp/Artist - Skipped Song.mp3",
            }
            st.download_skipped = [entry]
            page, view = self._make_view(tmp_path, st)

            async def _run() -> None:
                panel = await view._open_recovery(entry, kind="skipped")
                dds = _find_controls(panel, lambda c: isinstance(c, ft.Dropdown))
                dds[0].value = "opus"
                retry_btns = _find_controls(
                    panel,
                    lambda c: isinstance(c, ft.OutlinedButton) and callable(getattr(c, "on_click", None)),
                )
                self.assertTrue(retry_btns)
                retry_btns[0].on_click(None)
                self.assertEqual(len(st.enqueued), 1)
                self.assertEqual(st.enqueued[0][1]["audio_format"], "opus")

                st.download_state = TransferState.RUNNING
                ok = view._enqueue_recovery(entry, audio_format="m4a")
                self.assertFalse(ok)
                self.assertEqual(len(st.enqueued), 1)

            asyncio.run(_run())

    def test_candidate_to_recovery_clamps_score_and_marks_used(self) -> None:
        rc = candidate_to_recovery(
            {
                "video_id": "v1",
                "title": "Song",
                "channel": "Chan",
                "duration_s": 120,
                "score": 115.0,
            },
            used_video_id="v1",
        )
        self.assertEqual(rc.score, 100.0)
        self.assertTrue(rc.used)
        self.assertEqual(rc.track.duration, "2:00")


unittest.main()
"""


class TestRecoverMissing(unittest.TestCase):
    def test_recovery_flows_in_subprocess(self) -> None:
        _run_in_subprocess(_SUBPROC_TESTS)

    def test_no_hardcoded_hex_animation_or_dropdown_on_change(self) -> None:
        root = Path(__file__).resolve().parents[1]
        hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
        anim_re = re.compile(r"ft\.Animation\(\s*\d+")
        for rel in (
            "ui/dialogs/recovery_panel.py",
            "ui/dialogs/transfer_report.py",
            "ui/dialogs/transfer_dialog.py",
            "ui/dialogs/export_dialog.py",
            "ui/dialogs/merge_dialog.py",
            "ui/downloads_view.py",
        ):
            src = (root / rel).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    self.assertIsNone(
                        hex_re.search(node.value),
                        f"Hex literal encontrado en {rel}: {node.value!r}",
                    )
                if isinstance(node, ast.Attribute) and node.attr == "on_change":
                    if "dd" in ast.unparse(node.value).lower():
                        self.fail(f"Dropdown usa on_change en {rel}: {ast.unparse(node)}")
            self.assertIsNone(
                anim_re.search(src),
                f"ft.Animation(<número>) literal encontrado en {rel}",
            )


if __name__ == "__main__":
    unittest.main()
