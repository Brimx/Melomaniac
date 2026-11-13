"""
tests/test_queue_push_and_library_wrap.py — Pruebas de Fase 1 (Cola Push + Wrap Biblioteca)
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

_SUBPROC_SCRIPT = """
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flet as ft

from core.library_models import LibraryLoadState, LibrarySnapshot, PlaylistSummary
from core.playback import PlayQueue
from services import settings_store
from ui.views.library_view import LibraryView
from ui.player.player_queue import PlayerQueuePanel
from ui.theme.tokens import BORDER_MUTED, MOTION_PANEL_MS, PLAYER_QUEUE_W


class _DummyPage:
    def __init__(self) -> None:
        self.services = []

    def run_task(self, *_args, **_kwargs):
        raise RuntimeError("headless")


class _DummyPlayerBar:
    def __init__(self) -> None:
        self.queue = PlayQueue([])
        self._subs = []

    def subscribe(self, fn) -> None:
        self._subs.append(fn)

    def clear_queue(self) -> None:
        self.queue = PlayQueue([])

    def move_queue_item(self, old_idx: int, new_idx: int) -> None:
        pass

    def select_queue_index(self, pos: int) -> None:
        pass

    def remove_queue_index(self, pos: int) -> None:
        pass


class _DummyLibraryState:
    def __init__(self, items=None) -> None:
        self._items = items or []
        self._subs = []

    def set_service(self, _service) -> None:
        pass

    def subscribe(self, fn) -> None:
        self._subs.append(fn)

    def select_platform(self, _plat: str) -> None:
        for fn in self._subs:
            fn()

    def get_snapshot(self, plat: str) -> LibrarySnapshot:
        return LibrarySnapshot(
            platform=plat,
            state=LibraryLoadState.READY,
            items=list(self._items),
            synced_at="2026-11-12T00:00:00",
        )

    def local_summaries(self):
        return []


tc = unittest.TestCase()
with tempfile.TemporaryDirectory() as tmp:
    settings_path = Path(tmp) / "settings.json"
    with (
        mock.patch.object(settings_store, "SETTINGS_JSON", settings_path),
        mock.patch.object(settings_store, "ensure_config_dir", lambda: None),
    ):
        panel = PlayerQueuePanel(_DummyPage(), _DummyPlayerBar())
        tc.assertFalse(panel.is_open)
        tc.assertEqual(panel.width, 0)
        tc.assertFalse(panel.visible)
        tc.assertIsNone(panel.shadow)
        tc.assertIsNone(panel.offset)
        tc.assertEqual(panel.border, ft.Border.only(left=ft.BorderSide(0.8, BORDER_MUTED)))
        tc.assertEqual(panel.animate.duration, MOTION_PANEL_MS)
        tc.assertEqual(panel.active_tab, "cola")
        tc.assertTrue(panel._cola_view.visible)
        tc.assertFalse(panel._letra_view.visible)

        panel.set_open(True)
        tc.assertTrue(panel.is_open)
        tc.assertTrue(panel.visible)
        tc.assertEqual(panel.width, PLAYER_QUEUE_W)
        tc.assertEqual(panel._body_box.opacity, 1.0)

        panel.set_tab("letra")
        tc.assertEqual(panel.active_tab, "letra")
        tc.assertFalse(panel._cola_view.visible)
        tc.assertTrue(panel._letra_view.visible)

        restored = PlayerQueuePanel(_DummyPage(), _DummyPlayerBar())
        tc.assertTrue(restored.is_open)
        tc.assertTrue(restored.visible)
        tc.assertEqual(restored.width, PLAYER_QUEUE_W)
        tc.assertEqual(restored.active_tab, "letra")
        tc.assertFalse(restored._cola_view.visible)
        tc.assertTrue(restored._letra_view.visible)

        restored.set_open(False)
        tc.assertFalse(restored.is_open)
        tc.assertEqual(restored.width, 0)
        tc.assertFalse(restored.visible)

items = [
    PlaylistSummary(
        id="p1",
        name="Test Playlist",
        platform="Spotify",
        track_count=12,
        cover_urls=[],
    )
]
view = LibraryView(_DummyPage(), _DummyLibraryState(items), service=None)
tc.assertIsInstance(view._grid, ft.Column)
tc.assertEqual(view._grid.scroll, ft.ScrollMode.AUTO)
tc.assertTrue(view._grid.expand)
tc.assertIsInstance(view._grid_row, ft.Row)
tc.assertTrue(view._grid_row.wrap)
tc.assertEqual(view._grid_row.spacing, 12)
tc.assertEqual(view._grid_row.run_spacing, 12)
tc.assertEqual(len(view._grid_row.controls), 1)
tc.assertEqual(view._grid_row.controls[0].width, 160)
tc.assertTrue(view._grid.visible)
tc.assertFalse(view._empty.visible)

empty_view = LibraryView(_DummyPage(), _DummyLibraryState([]), service=None)
tc.assertFalse(empty_view._grid.visible)
tc.assertTrue(empty_view._empty.visible)
"""


class TestQueuePushAndLibraryWrap(unittest.TestCase):
    def test_queue_push_and_library_wrap_widgets(self) -> None:
        # Se ejecuta en subproceso para no importar flet en el proceso principal
        # (test_playback verifica que core.playback no cargue flet en sys.modules).
        res = subprocess.run(
            [sys.executable, "-c", _SUBPROC_SCRIPT],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr or res.stdout)

    def test_main_layout_places_player_queue_as_third_row_child(self) -> None:
        src = Path("ui/shell/main_layout.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found_row = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and target.attr == "root":
                        for sub in ast.walk(node.value):
                            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute) and sub.func.attr == "Row":
                                for kw in sub.keywords:
                                    if kw.arg == "controls" and isinstance(kw.value, ast.List) and len(kw.value.elts) == 3:
                                        attrs = [
                                            elt.attr for elt in kw.value.elts
                                            if isinstance(elt, ast.Attribute)
                                        ]
                                        if attrs == ["_nav_rail", "_module_stack", "player_queue"]:
                                            found_row = True
        self.assertTrue(found_row)

    def test_album_column_sync_on_queue_open(self) -> None:
        from unittest.mock import MagicMock, patch

        from core.models import Track
        from ui.player.player_queue import PlayerQueuePanel
        from ui.components.song_row import SongRow

        tr = Track(id="t1", name="Song", artist="Artist", album="Album", duration="3:00", img_url="", platform="Spotify")
        row = SongRow(tr, 1, lambda _tid: None, show_album=False)
        self.assertFalse(row._album_cell.visible)
        row.set_album_visible(True, update=False)
        self.assertTrue(row._album_cell.visible)

        events: list[bool] = []
        dummy_bar = MagicMock()
        dummy_bar.queue = MagicMock()
        dummy_bar.queue.ordered_tracks.return_value = []
        dummy_bar.queue.current_index = -1
        dummy_bar.subscribe = MagicMock()
        page = MagicMock()
        with patch("ui.player.player_queue._load_queue_prefs", return_value=(False, "cola")), \
             patch("ui.player.player_queue._save_queue_prefs"):
            panel = PlayerQueuePanel(page, dummy_bar, on_open_change=events.append)
            panel.set_open(True)
            panel.set_open(False)
        self.assertEqual(events, [True, False])

        layout_src = (Path(__file__).resolve().parent.parent / "ui/shell/main_layout.py").read_text(encoding="utf-8")
        self.assertIn("def _sync_queue_album_column(", layout_src)
        self.assertIn("on_open_change=self._sync_queue_album_column", layout_src)


if __name__ == "__main__":
    unittest.main()
