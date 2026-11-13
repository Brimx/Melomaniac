"""
tests/test_context_menus.py — Pruebas para menús contextuales, selección múltiple y Aleatorio en detalle
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path


def _run_in_subprocess(code: str) -> None:
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
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flet as ft

from core.library_models import LibraryLoadState, PlaylistSummary
from core.models import Track, TransferState
from core.playback import PlayQueue, build_playable_track
from ui.theme import tokens
from ui.theme.icons import PIcons
from ui.playlist_card import PlaylistCard
from ui.playlist_detail_view import PlaylistDetailView
from ui.song_row import SongRow
from ui.widgets import (
    ContextMenuAction,
    build_context_menu_button,
    build_popup_menu_items,
    show_context_menu,
    wrap_with_context_menu,
)


class _DummyPage:
    def __init__(self, width: float = 1200, height: float = 700) -> None:
        self.width = width
        self.height = height
        self.overlay: list[ft.Control] = []
        self.update_calls = 0

    def update(self) -> None:
        self.update_calls += 1

    def launch_url(self, _url: str):
        return True


def _make_track(idx: int, *, source_path: str = "", source_url: str = "", selected: bool = True) -> Track:
    return Track(
        id=f"tr_{idx}",
        name=f"Canción {idx}",
        artist=f"Artista {idx}",
        album=f"Álbum {idx}",
        duration="3:10",
        img_url="",
        platform="spotify",
        duration_ms=190_000,
        selected=selected,
        source_url=source_url,
        source_path=source_path,
    )


class TestContextMenuHelpers(unittest.TestCase):
    def test_popup_items_filter_dividers_and_invoke(self) -> None:
        called: list[str] = []
        actions = [
            ContextMenuAction("Oculta", visible=False, on_click=lambda: called.append("hidden")),
            ContextMenuAction("Reproducir", icon=PIcons.PLAY, on_click=lambda: called.append("play")),
            ContextMenuAction("Deshabilitada", icon=PIcons.QUEUE, disabled=True, on_click=lambda: called.append("disabled")),
            ContextMenuAction("Eliminar", icon=PIcons.TRASH, danger=True, divider_before=True, on_click=lambda: called.append("del")),
        ]
        items = build_popup_menu_items(actions)
        self.assertEqual(len(items), 4)
        self.assertTrue(items[2].disabled)
        self.assertIsInstance(items[2].content, ft.Divider)

        items[0].on_click(None)
        items[1].on_click(None)
        items[3].on_click(None)
        self.assertEqual(called, ["play", "del"])

        del_text = items[3].content.controls[-1]
        dis_text = items[1].content.controls[-1]
        self.assertEqual(del_text.color, tokens.ERROR_COL)
        self.assertEqual(dis_text.color, tokens.TEXT_DIM)

    def test_build_context_menu_button_dynamic_provider(self) -> None:
        state = {"label": "Seleccionar"}
        page = _DummyPage(width=800, height=500)
        btn = build_context_menu_button(
            lambda: [ContextMenuAction(state["label"], icon=PIcons.CHECK_CIRCLE)],
            get_page=lambda: page,
        )
        self.assertIsInstance(btn, ft.GestureDetector)
        ov1 = btn.open_menu()
        self.assertIsNotNone(ov1)
        self.assertEqual(ov1.menu_card.content.controls[0].content.controls[-1].value, "Seleccionar")

        state["label"] = "Deseleccionar"
        ov2 = btn.open_menu()
        self.assertIsNotNone(ov2)
        self.assertEqual(ov2.menu_card.content.controls[0].content.controls[-1].value, "Deseleccionar")

    def test_show_context_menu_clamps_replaces_and_dismisses(self) -> None:
        page = _DummyPage(width=800, height=500)
        self.assertIsNone(show_context_menu(page, [ContextMenuAction("X", visible=False)]))
        self.assertEqual(len(page.overlay), 0)

        hits: list[str] = []
        ov1 = show_context_menu(
            page,
            [ContextMenuAction("Uno", icon=PIcons.PLAY, on_click=lambda: hits.append("uno"))],
            title="Menú",
            x=790,
            y=490,
            width=220,
        )
        self.assertIsNotNone(ov1)
        self.assertEqual(len(page.overlay), 1)
        positioned = ov1.controls[1]
        self.assertLessEqual(positioned.left, 800 - 220 - 8)
        self.assertLessEqual(positioned.top, 500 - 8)

        ov2 = show_context_menu(
            page,
            [
                ContextMenuAction("Dos", icon=PIcons.QUEUE, on_click=lambda: hits.append("dos")),
                ContextMenuAction("Bloqueada", disabled=True, on_click=lambda: hits.append("no")),
            ],
        )
        self.assertIsNot(page._context_menu_host.content, ov1)
        self.assertIs(page._context_menu_host.content, ov2)
        self.assertTrue(page._context_menu_host.visible)

        menu_col = ov2.menu_card.content
        item_dos = menu_col.controls[0]
        item_dos.on_click(None)
        self.assertEqual(hits, ["dos"])
        self.assertFalse(page._context_menu_host.visible)
        self.assertIsNone(page._context_menu_host.content)

    def test_wrap_with_context_menu_secondary_tap(self) -> None:
        page = _DummyPage()
        inner = ft.Container(width=100, height=40)
        gd = wrap_with_context_menu(
            inner,
            [ContextMenuAction("Acción", icon=PIcons.PLAY)],
            title="Tarjeta",
            get_page=lambda: page,
        )
        self.assertIsInstance(gd, ft.GestureDetector)
        evt = SimpleNamespace(global_position=SimpleNamespace(x=120.0, y=80.0))
        gd.on_secondary_tap_down(evt)
        self.assertEqual(len(page.overlay), 1)
        ov = page.overlay[0].content
        self.assertEqual(ov.controls[1].left, 120.0)
        self.assertEqual(ov.controls[1].top, 80.0)


class TestCardAndSongRowContextMenus(unittest.TestCase):
    def test_playlist_card_context_actions_and_secondary_tap(self) -> None:
        summary = PlaylistSummary(
            id="pl_1",
            name="Favoritas",
            platform="Spotify",
            track_count=12,
            external_url="https://open.spotify.com/playlist/pl_1",
        )
        events: list[tuple[str, str]] = []
        card = PlaylistCard(
            summary,
            state=LibraryLoadState.READY,
            on_tap=lambda s: events.append(("tap", s.id)),
            on_play=lambda s: events.append(("play", s.id)),
            on_shuffle_play=lambda s: events.append(("shuffle", s.id)),
            on_add_to_queue=lambda s: events.append(("queue", s.id)),
            on_download=lambda s: events.append(("download", s.id)),
            on_open_external=lambda s: events.append(("external", s.id)),
            on_remove=lambda s: events.append(("remove", s.id)),
        )
        self.assertIsInstance(card.content, ft.GestureDetector)
        self.assertIsInstance(card._menu_btn, ft.GestureDetector)

        actions = card.context_actions()
        labels = [a.label for a in actions]
        self.assertEqual(
            labels,
            [
                "Abrir playlist",
                "Reproducir",
                "Aleatorio",
                "Añadir a la cola",
                "Descargar playlist",
                "Ver en plataforma",
                "Quitar de la Biblioteca",
            ],
        )
        for act in actions:
            act.on_click()
        self.assertEqual(
            events,
            [
                ("tap", "pl_1"),
                ("play", "pl_1"),
                ("shuffle", "pl_1"),
                ("queue", "pl_1"),
                ("download", "pl_1"),
                ("external", "pl_1"),
                ("remove", "pl_1"),
            ],
        )

    def test_song_row_context_actions_and_refresh(self) -> None:
        tr = _make_track(1, source_url="https://open.spotify.com/track/tr_1", selected=False)
        events: list[tuple[str, str]] = []
        row = SongRow(
            tr,
            1,
            on_toggle=lambda tid: events.append(("toggle", tid)),
            on_download=lambda tid: events.append(("download", tid)),
            on_play=lambda t: events.append(("play", t.id)),
            on_play_next=lambda t: events.append(("next", t.id)),
            on_add_to_queue=lambda t: events.append(("queue", t.id)),
            on_open_external=lambda t: events.append(("external", t.id)),
        )
        self.assertIsInstance(row.content, ft.GestureDetector)
        self.assertIsInstance(row._menu_btn, ft.GestureDetector)

        actions = row.context_actions()
        labels = [a.label for a in actions]
        self.assertEqual(
            labels,
            [
                "Reproducir",
                "Reproducir siguiente",
                "Añadir a la cola",
                "Descargar canción",
                "Abrir en plataforma",
                "Seleccionar",
            ],
        )
        for act in actions:
            act.on_click()
        self.assertEqual(
            events,
            [
                ("play", "tr_1"),
                ("next", "tr_1"),
                ("queue", "tr_1"),
                ("download", "tr_1"),
                ("external", "tr_1"),
                ("toggle", "tr_1"),
            ],
        )

        tr_done = _make_track(1, source_url="https://open.spotify.com/track/tr_1", selected=True)
        row.refresh(tr_done, notify=False, download_done=True)
        refreshed = row.context_actions()
        dl_act = next(a for a in refreshed if "descargada" in a.label.lower())
        sel_act = refreshed[-1]
        self.assertTrue(dl_act.disabled)
        self.assertEqual(sel_act.label, "Deseleccionar")


class TestPlaylistDetailViewFeatures(unittest.TestCase):
    def _build_detail_with_tracks(self, tracks: list[Track], *, local: bool = True):
        page = _DummyPage()
        summary = PlaylistSummary(
            id="/tmp/local" if local else "remote_1",
            name="Mi Playlist",
            platform="Local" if local else "Spotify",
            track_count=len(tracks),
            external_url="" if local else "https://open.spotify.com/playlist/remote_1",
        )
        queue = PlayQueue()
        player_bar = MagicMock()
        player_bar.queue = queue

        def _play_files(items, start_index=0):
            playables = [build_playable_track(p, t) for p, t in items]
            playables = [pt for pt in playables if pt is not None]
            queue.set_tracks(playables, start_index=start_index)
            return len(playables)

        def _enqueue_file(path, track=None, play_now=False):
            pt = build_playable_track(path, track)
            if pt is None:
                return False
            queue.enqueue(pt, play_now=play_now or queue.current is None)
            return True

        def _move_queue_item(old_pos, new_pos):
            queue.move(old_pos, new_pos)

        player_bar.play_files.side_effect = _play_files
        player_bar.enqueue_file.side_effect = _enqueue_file
        player_bar.move_queue_item.side_effect = _move_queue_item

        app_state = MagicMock()
        app_state.download_state = TransferState.IDLE

        async def _loader():
            return None, list(tracks)

        service = MagicMock()
        view = PlaylistDetailView(
            page,
            summary,
            service,
            on_back=lambda: None,
            app_state=app_state,
            loader=_loader if local else None,
            player_bar=player_bar,
        )
        return view, page, player_bar, queue, app_state

    def test_shuffle_button_enables_shuffle_and_plays_random_track(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tracks = []
                for i in range(4):
                    p = Path(tmp) / f"song_{i}.mp3"
                    p.write_bytes(b"ID3")
                    tracks.append(_make_track(i, source_path=str(p)))

                view, _page, player_bar, queue, _app_state = self._build_detail_with_tracks(tracks)
                try:
                    await view.reload()
                    self.assertFalse(view._shuffle_btn.disabled)
                    self.assertTrue(view._shuffle_btn.visible)

                    with patch.object(queue._rng, "randrange", return_value=2), \\
                         patch("ui.playlist_detail_view.settings_store.set_shuffle") as set_shuf:
                        view._on_shuffle_play()

                    self.assertTrue(queue.shuffle)
                    set_shuf.assert_called_once_with(True)
                    player_bar.play_files.assert_called_once()
                    _items, kwargs = player_bar.play_files.call_args
                    self.assertEqual(kwargs["start_index"], 2)
                    self.assertEqual(queue.current.track, tracks[2])
                finally:
                    view.will_unmount()

        asyncio.run(_run())

    def test_shuffle_button_falls_back_when_queue_is_mock(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                tracks = []
                for i in range(3):
                    p = Path(tmp) / f"mock_song_{i}.mp3"
                    p.write_bytes(b"ID3")
                    tracks.append(_make_track(i, source_path=str(p)))

                view, _page, player_bar, _queue, _app_state = self._build_detail_with_tracks(tracks)
                player_bar.queue = MagicMock()
                try:
                    await view.reload()
                    with patch("ui.playlist_detail_view.random.randrange", return_value=1), \\
                         patch("ui.playlist_detail_view.settings_store.set_shuffle") as set_shuf:
                        view._on_shuffle_play()

                    player_bar.queue.set_shuffle.assert_called_once_with(True)
                    set_shuf.assert_called_once_with(True)
                    player_bar.play_files.assert_called_once()
                    _items, kwargs = player_bar.play_files.call_args
                    self.assertEqual(kwargs["start_index"], 1)
                finally:
                    view.will_unmount()

        asyncio.run(_run())

    def test_multi_selection_and_batch_actions(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                p0 = Path(tmp) / "song_0.mp3"
                p1 = Path(tmp) / "song_1.mp3"
                p0.write_bytes(b"ID3")
                p1.write_bytes(b"ID3")
                tracks = [
                    _make_track(0, source_path=str(p0)),
                    _make_track(1, source_path=str(p1)),
                    _make_track(2, source_url="https://open.spotify.com/track/tr_2"),
                ]
                view, _page, player_bar, queue, app_state = self._build_detail_with_tracks(tracks)
                try:
                    await view.reload()
                    self.assertFalse(view._selection_mode)
                    self.assertFalse(view._batch_bar.visible)
                    self.assertFalse(view._select_mode_btn.disabled)

                    view.set_selection_mode(True)
                    self.assertTrue(view._selection_mode)
                    self.assertTrue(view._batch_bar.visible)
                    self.assertTrue(all(chk.visible for chk in view._row_checkboxes.values()))
                    self.assertTrue(view._batch_play_btn.disabled)

                    view.toggle_track_selection(tracks[1])
                    view.toggle_track_selection(tracks[2])
                    self.assertEqual(view.selected_tracks(), [tracks[1], tracks[2]])
                    self.assertEqual(view._selected_count_label.value, "2 seleccionadas")
                    self.assertFalse(view._batch_play_btn.disabled)
                    self.assertFalse(view._batch_queue_btn.disabled)
                    self.assertFalse(view._batch_download_btn.disabled)

                    view._on_batch_play()
                    played_items = player_bar.play_files.call_args[0][0]
                    self.assertEqual([tr for _p, tr in played_items], [tracks[1]])

                    view.select_all(True)
                    self.assertTrue(view._select_all_chk.value)
                    self.assertEqual(len(view.selected_tracks()), 3)
                    view._on_batch_enqueue()
                    self.assertEqual(len(queue.tracks), 2)

                    view._on_batch_download()
                    app_state.download_tracks.assert_called_once()
                    dl_tracks = app_state.download_tracks.call_args[0][0]
                    self.assertEqual(dl_tracks, tracks)

                    view.set_selection_mode(False)
                    self.assertFalse(view._selection_mode)
                    self.assertFalse(view._batch_bar.visible)
                    self.assertEqual(view.selected_tracks(), [])
                finally:
                    view.will_unmount()

        asyncio.run(_run())

    def test_row_context_menu_play_next_and_selection(self) -> None:
        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                paths = []
                tracks = []
                for i in range(3):
                    p = Path(tmp) / f"s_{i}.mp3"
                    p.write_bytes(b"ID3")
                    paths.append(str(p))
                    tracks.append(_make_track(i, source_path=str(p)))

                view, _page, player_bar, queue, _app_state = self._build_detail_with_tracks(tracks)
                try:
                    await view.reload()
                    player_bar.enqueue_file(paths[0], track=tracks[0], play_now=True)
                    player_bar.enqueue_file(paths[1], track=tracks[1], play_now=False)

                    actions_2 = view._track_context_actions(tracks[2], 3)
                    next_act = next(a for a in actions_2 if a.label == "Reproducir siguiente")
                    next_act.on_click()
                    ordered_tracks = [pt.track for pt in queue.ordered_tracks()]
                    self.assertEqual(ordered_tracks, [tracks[0], tracks[2], tracks[1]])

                    sel_act = next(a for a in actions_2 if "Seleccionar" in a.label)
                    sel_act.on_click()
                    self.assertTrue(view._selection_mode)
                    self.assertEqual(view.selected_tracks(), [tracks[2]])
                finally:
                    view.will_unmount()

        asyncio.run(_run())

    def test_missing_track_context_has_recovery_and_requeues_format(self) -> None:
        async def _run() -> None:
            tracks = [_make_track(1, source_path="")]
            view, page, _pb, _q, app_state = self._build_detail_with_tracks(tracks)
            page.show_dialog = MagicMock()
            page.run_task = lambda fn, *a, **kw: asyncio.create_task(fn(*a, **kw))
            async def _fake_dl(tr_list, **kwargs):
                return None
            app_state.download_tracks = MagicMock(side_effect=_fake_dl)
            try:
                await view.reload()
                actions = view._track_context_actions(tracks[0], 1)
                rec_act = next((a for a in actions if "Buscar alternativa" in a.label), None)
                self.assertIsNotNone(rec_act)
                async def _fake_search(*_a, **_kw):
                    return {
                        "candidates": [{
                            "video_id": "yt_alt_9",
                            "title": "Track 1 (Live)",
                            "artist": "Artist 1",
                            "duration": "3:10",
                            "duration_ms": 190000,
                            "url": "https://www.youtube.com/watch?v=yt_alt_9",
                        }],
                        "error": "",
                    }

                with patch(
                    "ui.playlist_detail_view.search_youtube_alternatives",
                    side_effect=_fake_search,
                ):
                    panel = await view._open_track_recovery(tracks[0])
                    self.assertTrue(page.show_dialog.called)
                    self.assertEqual(len(panel.results_column.controls), 1)
                    view._download_recovered_track(
                        tracks[0],
                        video_id="yt_alt_9",
                        candidate_url="https://www.youtube.com/watch?v=yt_alt_9",
                        audio_format="flac",
                    )
                    await asyncio.sleep(0)
                    app_state.download_tracks.assert_called_once()
                    _, dl_kwargs = app_state.download_tracks.call_args
                    self.assertEqual(dl_kwargs.get("audio_format"), "flac")
            finally:
                view.will_unmount()

        asyncio.run(_run())

    def test_hover_boolean_manifest_actions_and_hidden_updates(self) -> None:
        from ui.downloads_view import DownloadsView
        from ui.main_ui import PlaylistManagerUI

        async def _run() -> None:
            summary = PlaylistSummary(
                id="local_pl",
                name="M.M.",
                platform="Local",
                track_count=2,
                external_url="https://music.apple.com/playlist/pl.u-123",
            )
            card = PlaylistCard(summary, state=LibraryLoadState.READY, on_tap=lambda _s: None)
            card._on_hover(type("E", (), {"data": True})())
            self.assertEqual(card.scale, 1.02)
            card._on_hover(type("E", (), {"data": False})())
            self.assertEqual(card.scale, 1.0)

            page = _DummyPage()
            tracks = [_make_track(1), _make_track(2)]
            detail = PlaylistDetailView(
                page,
                summary,
                MagicMock(),
                on_back=lambda: None,
                is_imported_manifest=True,
            )
            try:
                await asyncio.sleep(0)
                self.assertTrue(detail._download_btn.visible)
                row1 = detail._build_track_row(tracks[0], 1)
                row1.on_hover(type("E", (), {"data": True})())
                self.assertIsNotNone(row1.border)
                detail._selected_ids.add(id(tracks[0]))
                row1.on_hover(type("E", (), {"data": False})())
                self.assertIsNotNone(row1.border)
            finally:
                detail.will_unmount()

            dv = DownloadsView.__new__(DownloadsView)
            ft.Container.__init__(dv)
            dv.visible = False
            dv._downloads_dirty = False
            dv.update = MagicMock()
            dv._refresh()
            self.assertTrue(dv._downloads_dirty)
            dv.update.assert_not_called()

            ui = PlaylistManagerUI.__new__(PlaylistManagerUI)
            ui.state = MagicMock(transfer_state=None)
            ui._current_module = 1
            ui._gestion_dirty = False
            ui.page = MagicMock()
            ui._sync_report_dialog = MagicMock()
            ui._on_state_changed()
            self.assertTrue(ui._gestion_dirty)
            ui.page.update.assert_not_called()

        asyncio.run(_run())

    def test_cross_playlist_recognition_routes_downloads_and_merges_manifest(self) -> None:
        async def _run() -> None:
            import types
            from engine.downloader import read_playlist_manifest, write_playlist_manifest
            from core.state import AppState
            from services import settings_store

            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = Path(tmp)
                dl_dir = tmp_path / "Descargas"
                dl_dir.mkdir()
                other_local = tmp_path / "MiMusica"
                other_local.mkdir()

                # Pista ya descargada en otra playlist y otra en carpeta local:
                # deben reconocerse de forma cruzada sin duplicar descargas.
                other_pl = dl_dir / "Otra Playlist [Spotify]"
                other_pl.mkdir()
                t3_file = other_pl / "Artist 3 - Track 3.mp3"
                t3_file.write_bytes(b"mp3")
                write_playlist_manifest(
                    other_pl,
                    name="Otra Playlist",
                    platform="Spotify",
                    entries=[{"id": "id_3", "name": "Track 3", "artist": "Artist 3", "path": str(t3_file)}],
                )
                t4_file = other_local / "Artist 4 - Track 4.mp3"
                t4_file.write_bytes(b"mp3")

                # Archivo descargado específicamente para la playlist actual:
                pl_sub = dl_dir / "Mi Manifiesto [Apple Music]"
                pl_sub.mkdir()
                t2_file = pl_sub / "Artist 2 - Track 2.mp3"
                t2_file.write_bytes(b"mp3")
                write_playlist_manifest(
                    pl_sub,
                    name="Mi Manifiesto",
                    platform="Apple Music",
                    entries=[{"id": "id_2", "name": "Track 2", "artist": "Artist 2", "path": str(t2_file)}],
                )

                t1 = Track(id="id_1", name="Track 1", artist="Artist 1", album="Alb 1", duration="3:00", img_url="", platform="Apple Music")
                t2 = Track(id="id_2", name="Track 2", artist="Artist 2", album="Alb 2", duration="3:00", img_url="", platform="Apple Music")
                t3 = Track(id="id_3", name="Track 3", artist="Artist 3", album="Alb 3", duration="3:00", img_url="", platform="Apple Music")
                t4 = Track(id="id_4", name="Track 4", artist="Artist 4", album="Alb 4", duration="3:00", img_url="", platform="Apple Music")
                summary = PlaylistSummary(
                    id="apple_music__pl1",
                    name="Mi Manifiesto · Apple Music",
                    platform="Local",
                    track_count=4,
                )
                page = _DummyPage()
                app_state = MagicMock()
                app_state.download_state = TransferState.IDLE

                async def _dl_complete(tracks, **_kwargs):
                    dest = pl_sub / "Artist 1 - Track 1.mp3"
                    dest.write_bytes(b"mp3")
                    for tr in tracks:
                        tr.source_path = str(dest)
                    return None

                app_state.download_tracks.side_effect = (
                    lambda tracks, **kwargs: asyncio.create_task(_dl_complete(tracks, **kwargs))
                )

                with patch.object(settings_store, "get_download_dir", return_value=str(dl_dir)), \
                     patch.object(settings_store, "get_local_folders", return_value=[str(other_local)]):
                    async def _loader():
                        return types.SimpleNamespace(name="Mi Manifiesto"), [t1, t2, t3, t4]

                    detail = PlaylistDetailView(
                        page,
                        summary,
                        MagicMock(),
                        on_back=lambda: None,
                        app_state=app_state,
                        loader=_loader,
                        is_imported_manifest=True,
                    )
                    try:
                        if detail._init_reload_task is not None:
                            await detail._init_reload_task
                        else:
                            await detail.reload()
                        self.assertFalse(detail._has_file_by_id.get(id(t1), False))
                        self.assertTrue(detail._has_file_by_id.get(id(t2), False))
                        self.assertTrue(detail._has_file_by_id.get(id(t3), False))
                        self.assertTrue(detail._has_file_by_id.get(id(t4), False))
                        self.assertEqual(detail._playlist_download_identity(), ("Mi Manifiesto", "Apple Music"))

                        # Lote: solo pide la pista faltante hacia su subcarpeta de playlist
                        detail._on_download()
                        dl_args, dl_kw = app_state.download_tracks.call_args
                        self.assertEqual([t.id for t in dl_args[0]], ["id_1"])
                        self.assertEqual(dl_kw.get("playlist_name"), "Mi Manifiesto")
                        self.assertEqual(dl_kw.get("platform"), "Apple Music")
                        await asyncio.sleep(0.05)
                        self.assertTrue(detail._has_file_by_id.get(id(t1), False))

                        # Descarga individual: también enruta a la carpeta de la playlist
                        app_state.download_tracks.reset_mock()
                        t_single = Track(id="id_5", name="Track 5", artist="Artist 5", album="Alb 5", duration="3:00", img_url="", platform="Apple Music")
                        detail._download_single_track(t_single, ft.Container())
                        await asyncio.sleep(0.05)
                        _, single_kw = app_state.download_tracks.call_args
                        self.assertEqual(single_kw.get("playlist_name"), "Mi Manifiesto")
                        self.assertEqual(single_kw.get("platform"), "Apple Music")

                        # Merge de manifiesto: descargar una pista no borra las previas
                        real_state = AppState(service=None)
                        real_state._write_download_manifest(
                            pl_sub, "Mi Manifiesto", "Apple Music", [t_single],
                            write_playlist_manifest, read_playlist_manifest,
                        )
                        merged = read_playlist_manifest(pl_sub)
                        merged_ids = [e.get("id") for e in merged.get("tracks", [])]
                        self.assertEqual(merged_ids, ["id_2", "id_5"])
                    finally:
                        detail.will_unmount()

        asyncio.run(_run())


suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestContextMenuHelpers)
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestCardAndSongRowContextMenus))
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TestPlaylistDetailViewFeatures))
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
"""


class TestContextMenus(unittest.TestCase):
    def test_context_menus_and_detail_features_in_subprocess(self) -> None:
        _run_in_subprocess(_SUBPROC_TESTS)

    def test_owned_ui_files_follow_token_and_animation_rules(self) -> None:
        root = Path(__file__).resolve().parent.parent
        owned = [
            root / "ui/widgets.py",
            root / "ui/song_row.py",
            root / "ui/playlist_card.py",
            root / "ui/playlist_detail_view.py",
        ]
        hex_pat = re.compile(r"""["']#[0-9A-Fa-f]{6,8}["']""")
        anim_pat = re.compile(r"""ft\.Animation\(\s*\d+""")
        for path in owned:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(hex_pat.search(text), f"Hardcoded hex color in {path}")
            self.assertIsNone(anim_pat.search(text), f"Hardcoded ft.Animation literal in {path}")


if __name__ == "__main__":
    unittest.main()
