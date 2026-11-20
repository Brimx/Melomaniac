"""tests/test_audit_fixes.py — Regresiones para hallazgos de UI, post-mortem, filtros y ajustes."""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import flet as ft

from core.models import LoadState, Track, TransferState
from core.state import AppState
from core.transfer import TransferIssue
from services import authentication, settings_store


def _mock_page() -> MagicMock:
    page = MagicMock()
    page.services = []
    page.overlay = []
    page.width = 1280
    page.height = 720
    return page


class TestPostmortemAndTelemetry(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._reports_dir = Path(self._tmp.name) / "transfer_reports"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_postmortem_preserves_issue_reason_exports_json_and_shows_container(self) -> None:
        from ui.shell.main_layout import _RegistroDrawer

        page = _mock_page()
        drawer = _RegistroDrawer(page)
        drawer.container.visible = False

        tr = Track(
            id="t1",
            name="Song One",
            artist="Artist One",
            album="Album",
            duration="3:10",
            img_url="",
            platform="Spotify",
        )
        issue = TransferIssue(track=tr, kind="not_found", reason="Sin coincidencias exactas")
        drawer.update_postmortem([issue], destination="Apple Music", confirmed=0, detected=1)

        self.assertIn("Sin coincidencias exactas", drawer._pm_text.value)
        self.assertNotIn("sin detalle", drawer._pm_text.value)

        with patch.object(authentication, "TRANSFER_REPORTS_DIR", self._reports_dir):
            drawer._export_postmortem()

        files = list(self._reports_dir.glob("postmortem_*.json"))
        self.assertEqual(len(files), 1)
        payload = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(payload["failed"][0]["reason"], "Sin coincidencias exactas")
        self.assertEqual(payload["failed"][0]["name"], "Song One")

        drawer.show_postmortem()
        self.assertTrue(drawer.container.visible)
        self.assertTrue(drawer._body_wrap.visible)

    def test_telemetry_manual_open_resets_on_new_transfer_and_respects_catalog(self) -> None:
        from ui.shell.main_layout import LayoutController, _RegistroDrawer

        page = _mock_page()
        tel = _RegistroDrawer(page)
        tel.container.visible = False
        ui = SimpleNamespace(
            page=page,
            state=SimpleNamespace(
                tracks=[Track("1", "A", "B", "", "1:00", "", "Spotify")],
                load_state=LoadState.READY,
                transfer_state=TransferState.IDLE,
                last_report=None,
                destination="Apple Music",
                destination_confirmed=True,
                source_tracks=[],
                segments={},
                show_dual=False,
                dual_mode="lista",
            ),
            _catalog_open=False,
            _telemetry=tel,
        )
        ctrl = LayoutController(ui)
        ctrl._toggle_telemetry_manual()
        self.assertTrue(ctrl._telemetry_manual_open)
        ctrl._toggle_telemetry_manual()
        self.assertFalse(ctrl._telemetry_manual_open)

        # Mientras catálogo está abierto, el toggle no abre ni parpadea
        ui._catalog_open = True
        ctrl._toggle_telemetry_manual()
        self.assertFalse(ctrl._telemetry_manual_open)
        ui._catalog_open = False

        # Al iniciar nueva transferencia, se limpia la preferencia manual previa
        ui.state.transfer_state = TransferState.RUNNING
        ctrl._sync_workflow_ui()
        self.assertIsNone(ctrl._telemetry_manual_open)
        self.assertTrue(tel.container.visible)


class TestCatalogSidebarAndStateGuards(unittest.TestCase):
    def test_set_destination_blocked_while_transfer_running(self) -> None:
        st = AppState(service=None)
        st.destination = "Spotify"
        st.transfer_state = TransferState.RUNNING
        st.set_destination("Apple Music")
        self.assertEqual(st.destination, "Spotify")

        st.transfer_state = TransferState.IDLE
        st.set_destination("Apple Music")
        self.assertEqual(st.destination, "Apple Music")

    def test_catalog_panel_hides_sidebar_actions(self) -> None:
        from ui.shell.catalog_panel import CatalogController
        from ui.shell.main_layout import LayoutController

        page = _mock_page()
        st = AppState(service=None)
        st.load_state = LoadState.READY
        st.tracks = [Track("1", "Song", "Artist", "", "3:00", "", "Spotify")]

        sb_sec = ft.Column(visible=True)
        sb_div = ft.Divider(visible=True)
        ctx_bar = ft.Container(visible=True)
        cat_panel = ft.Container(visible=False)
        col_hdrs = ft.Container(visible=True)
        list_area = ft.Container(visible=True)

        ui = SimpleNamespace(
            page=page,
            state=st,
            _catalog_open=False,
            _ctx_action_buttons={"transfer": ft.Button("Transferir", visible=True)},
            _contextual_bar=ctx_bar,
            _sidebar_actions_section=sb_sec,
            _sidebar_actions_divider=sb_div,
            _catalog_panel=cat_panel,
            _col_headers=col_hdrs,
            _list_area=list_area,
            _content=ft.Container(),
            _panel_gestion=ft.Row(),
        )
        ui._layout = LayoutController(ui)
        cat = CatalogController(ui)
        cat._toggle_catalog_panel(True)
        self.assertTrue(ui._catalog_open)
        self.assertFalse(sb_sec.visible)
        self.assertFalse(sb_div.visible)
        self.assertFalse(ctx_bar.visible)

        cat._toggle_catalog_panel(False)
        self.assertFalse(ui._catalog_open)
        self.assertTrue(sb_sec.visible)
        self.assertTrue(sb_div.visible)


class TestContextMenuLibraryAndSettingsCoercion(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._settings_path = Path(self._tmp.name) / "settings.json"
        settings_store._CACHE = None
        self._p1 = patch.object(settings_store, "SETTINGS_JSON", self._settings_path)
        self._p2 = patch.object(
            settings_store,
            "ensure_config_dir",
            lambda: self._settings_path.parent.mkdir(parents=True, exist_ok=True),
        )
        self._p1.start()
        self._p2.start()

    def tearDown(self) -> None:
        self._p2.stop()
        self._p1.stop()
        settings_store._CACHE = None
        self._tmp.cleanup()

    def test_stream_option_hidden_on_local_and_imported_tracks(self) -> None:
        from ui.views.playlist_detail_rows import build_track_context_actions

        tr_platform = Track("1", "Remote", "Artist", "", "3:00", "", "Spotify")
        tr_local = Track("2", "Local", "Artist", "", "3:00", "", "local")

        view_platform = SimpleNamespace(
            _has_file_by_id={id(tr_platform): False},
            _is_local=False,
            _is_manifest=False,
            player_bar=object(),
            _stream_single_track=lambda _t: None,
            app_state=object(),
            _selection_mode=False,
            _page_ref=_mock_page(),
        )
        acts_platform = build_track_context_actions(view_platform, tr_platform, 1)
        self.assertTrue(any(a.label == "Reproducir sin descargar" for a in acts_platform))

        # Manifiesto importado en Local sí permite streaming cuando no hay archivo en disco
        for is_local, is_manifest, track in (
            (True, True, tr_local),
            (False, True, tr_platform),
        ):
            view_manifest = SimpleNamespace(
                _has_file_by_id={id(track): False},
                _is_local=is_local,
                _is_manifest=is_manifest,
                player_bar=object(),
                _stream_single_track=lambda _t: None,
                app_state=object(),
                _selection_mode=False,
                _page_ref=_mock_page(),
            )
            acts = build_track_context_actions(view_manifest, track, 1)
            self.assertTrue(any(a.label == "Reproducir sin descargar" for a in acts))

        # Carpeta local pura con archivo borrado (source_path ausente) oculta streaming;
        # playlist descargada parcialmente (sin source_path) sí permite streaming.
        tr_deleted = Track(
            id="l_del", name="Local Borrada", artist="Artista", album="",
            duration="3:00", img_url="", platform=tr_local.platform,
            source_path="/tmp/archivo_que_ya_no_existe_12345.mp3",
        )
        view_pure_local = SimpleNamespace(
            _has_file_by_id={id(tr_deleted): False, id(tr_local): False},
            _is_local=True,
            _is_manifest=False,
            player_bar=object(),
            _stream_single_track=lambda _t: None,
            app_state=object(),
            _selection_mode=False,
            _page_ref=_mock_page(),
        )
        acts_deleted = build_track_context_actions(view_pure_local, tr_deleted, 1)
        self.assertFalse(any(a.label == "Reproducir sin descargar" for a in acts_deleted))
        acts_partial = build_track_context_actions(view_pure_local, tr_local, 1)
        self.assertTrue(any(a.label == "Reproducir sin descargar" for a in acts_partial))

    def test_settings_store_type_coercion_and_library_filter_reset(self) -> None:
        self._settings_path.write_text(
            json.dumps({
                "resume_on_open": "false",
                "stream_without_download": "false",
                "fade_seconds": True,
            }),
            encoding="utf-8",
        )
        self.assertFalse(settings_store.get_resume_on_open())
        self.assertFalse(settings_store.get_stream_without_download())
        self.assertEqual(settings_store.get_fade_seconds(), 0)

        from core.config import LOCAL_LIBRARY_LABEL
        from services.library_state import LibraryState
        from ui.views.library_view import LibraryView

        lib_state = LibraryState(service=None)
        lv = LibraryView(_mock_page(), lib_state, service=None)
        lv._toggle_filter("today")
        lv._search_query = "rock"
        self.assertIn("today", lv._active_filters)
        lv._on_platform_select(LOCAL_LIBRARY_LABEL)
        self.assertEqual(lv._active_filters, set())
        self.assertEqual(lv._search_query, "")

    def test_library_sort_order_persists_between_config_and_library_view(self) -> None:
        from services.library_state import LibraryState
        from ui.views.library_view import LibraryView

        settings_store.set_library_sort_order("az")
        self.assertEqual(settings_store.get_library_sort_order(), "az")

        lv = LibraryView(_mock_page(), LibraryState(service=None), service=None)
        self.assertEqual(lv._sort_mode, 1)  # "az"

        lv._cycle_sort_mode()
        self.assertEqual(lv._sort_mode, 2)  # "za"
        self.assertEqual(settings_store.get_library_sort_order(), "za")

        settings_store.set_library_sort_order("recent")
        lv._refresh()
        self.assertEqual(lv._sort_mode, 0)

    def test_startup_update_check_and_gestion_idle_merge_and_row_play(self) -> None:
        import app
        from services.update_checker import UpdateInfo
        from ui.shell.main_layout import LayoutController

        page = _mock_page()
        # Con preferencia apagada (default) no consulta red
        with patch("services.update_checker.check_for_updates") as chk:
            res_off = asyncio.run(app.run_startup_update_check(page))
        self.assertFalse(res_off)
        chk.assert_not_called()

        # Con preferencia activa y actualización disponible, notifica
        settings_store.set_check_updates_on_start(True)
        fake_info = UpdateInfo(
            checked=True,
            has_update=True,
            current_version="4.10.0",
            latest_version="4.11.0",
            release_url="https://github.com/Brimx/Melomaniac/releases/latest",
            release_title="v4.11.0",
            release_notes="",
            download_url=None,
            asset_name=None,
            instructions="",
            error_message=None,
        )
        with patch("services.update_checker.check_for_updates", return_value=fake_info):
            res_on = asyncio.run(app.run_startup_update_check(page))
        self.assertTrue(res_on)
        page.show_dialog.assert_called_once()

        # Empty state de Gestión incluye acceso a Fusionar playlists
        merge_calls: list[int] = []
        ui_dummy = SimpleNamespace(
            page=page,
            state=AppState(service=None),
            SKELETON_COUNT=6,
            _open_merge_dialog=lambda: merge_calls.append(1),
            _on_toggle_catalog_search=lambda: None,
            _on_mode_change=lambda _e: None,
            _on_order_change=lambda _e: None,
        )
        ctrl = LayoutController(ui_dummy)
        ctrl._build_mode_bar()
        ctrl._build_list_and_preview_panels()
        buttons_row = ui_dummy._empty_state.content.controls[3]
        self.assertIsInstance(buttons_row, ft.Row)
        self.assertEqual(len(buttons_row.controls), 2)
        buttons_row.controls[1].on_click(None)
        self.assertEqual(merge_calls, [1])

        # _on_row_play reproduce local o streaming remoto según disponibilidad
        from ui.shell.main_ui import PlaylistManagerUI

        played_local: list[str] = []
        played_remote: list[str] = []

        async def _fake_remote(tr):
            played_remote.append(tr.id)
            return True

        fake_pb = SimpleNamespace(
            play_file=lambda p, _t=None: played_local.append(p),
            play_remote_track=_fake_remote,
        )
        fake_ui = SimpleNamespace(page=page, player_bar=fake_pb)

        audio_path = Path(self._tmp.name) / "local_song.mp3"
        audio_path.write_bytes(b"ID3")
        tr_local = Track("1", "Local", "Artist", "", "3:00", "", "local", source_path=str(audio_path))
        tr_remote = Track("2", "Remote", "Artist", "", "3:00", "", "Spotify")

        PlaylistManagerUI._on_row_play(fake_ui, tr_local)
        self.assertEqual(played_local, [str(audio_path)])

        page.run_task = lambda fn: asyncio.run(fn())
        PlaylistManagerUI._on_row_play(fake_ui, tr_remote)
        self.assertEqual(played_remote, ["2"])

    def test_playlist_detail_search_filters_by_title_artist_album_and_focuses(self) -> None:
        from core.config import LOCAL_LIBRARY_LABEL
        from core.library_models import PlaylistSummary
        from core.models import filter_tracks
        from ui.views.playlist_detail_view import PlaylistDetailView

        tracks = [
            Track("1", "Bohemian Rhapsody", "Queen", "A Night at the Opera", "5:55", "", "Spotify"),
            Track("2", "Time", "Pink Floyd", "The Dark Side of the Moon", "6:53", "", "Spotify"),
            Track("3", "Stairway to Heaven", "Led Zeppelin", "Led Zeppelin IV", "8:02", "", "Spotify"),
        ]
        self.assertEqual([t.id for t in filter_tracks(tracks, "queen")], ["1"])
        self.assertEqual([t.id for t in filter_tracks(tracks, "dark side")], ["2"])
        self.assertEqual(len(filter_tracks(tracks, "   ")), 3)

        page = _mock_page()
        summary = PlaylistSummary(
            platform=LOCAL_LIBRARY_LABEL,
            id="/tmp/fake",
            name="Mi carpeta",
            track_count=3,
        )

        async def _loader():
            return tracks

        async def _run() -> None:
            view = PlaylistDetailView(page, summary, service=None, on_back=lambda: None, loader=_loader)
            await view.reload()
            self.assertEqual(len(view._list_view.controls), 3)

            view._on_search_change(SimpleNamespace(control=SimpleNamespace(value="pink floyd")))
            self.assertEqual(len(view._list_view.controls), 1)
            self.assertEqual(view._search_count.value, "1 de 3")
            self.assertFalse(view._filter_empty_box.visible)

            view._on_search_change(SimpleNamespace(control=SimpleNamespace(value="inexistente")))
            self.assertEqual(len(view._list_view.controls), 0)
            self.assertFalse(view._list_view.visible)
            self.assertTrue(view._filter_empty_box.visible)

            view._clear_search()
            self.assertEqual(len(view._list_view.controls), 3)
            self.assertTrue(view._list_view.visible)
            self.assertFalse(view._filter_empty_box.visible)
            self.assertEqual(view._search_count.value, "")

        asyncio.run(_run())

    def test_play_all_queues_whole_playlist_with_remote_tracks(self) -> None:
        from core.library_models import PlaylistSummary
        from ui.views.playlist_detail_view import PlaylistDetailView

        audio_path = Path(self._tmp.name) / "downloaded.mp3"
        audio_path.write_bytes(b"ID3")
        t_dl = Track("1", "Descargada", "A", "", "3:00", "", "Spotify", source_path=str(audio_path))
        t_remote = Track("2", "Remota", "B", "", "3:00", "", "Spotify")
        summary = PlaylistSummary(platform="Spotify", id="pl1", name="Mix", track_count=2)
        player_bar = MagicMock()
        player_bar.play_tracks.return_value = 2

        async def _loader():
            return None, [t_dl, t_remote]

        async def _run() -> None:
            with patch.object(settings_store, "get_download_dir", return_value=self._tmp.name), \
                 patch.object(settings_store, "get_local_folders", return_value=[]), \
                 patch.object(settings_store, "get_stream_without_download", return_value=True):
                view = PlaylistDetailView(
                    _mock_page(), summary, MagicMock(), on_back=lambda: None,
                    loader=_loader, player_bar=player_bar,
                )
                try:
                    await view.reload()
                    self.assertTrue(view._play_all_btn.visible)
                    view._on_play_all()
                    player_bar.play_tracks.assert_called_once()
                    args, kwargs = player_bar.play_tracks.call_args
                    self.assertEqual([t.id for t in args[0]], ["1", "2"])
                    self.assertTrue(kwargs["include_remote"])
                    player_bar.play_files.assert_not_called()

                    # Con streaming apagado solo se encolan los archivos en disco
                    player_bar.reset_mock()
                    with patch.object(settings_store, "get_stream_without_download", return_value=False):
                        view._on_play_all()
                    player_bar.play_tracks.assert_not_called()
                    items = player_bar.play_files.call_args[0][0]
                    self.assertEqual([t.id for _p, t in items], ["1"])
                finally:
                    view.will_unmount()

        asyncio.run(_run())

    def test_row_click_streams_when_enabled_and_respects_local_and_manifest(self) -> None:
        from ui.views.playlist_detail_rows import build_detail_track_row

        async def _run() -> None:
            tr_remote = Track(
                id="r1", name="Remote", artist="Artist", album="Alb",
                duration="3:00", img_url="", platform="Spotify",
            )
            tr_missing_local = Track(
                id="l1", name="Missing", artist="Artist", album="Alb",
                duration="3:00", img_url="", platform="Local", source_path="/nonexistent/missing.mp3",
            )

            def _make_view(*, is_local: bool, is_manifest: bool) -> MagicMock:
                view = MagicMock()
                view._is_local = is_local
                view._is_manifest = is_manifest
                view._selection_mode = False
                view._selected_ids = set()
                view._has_file_by_id = {}
                view._row_containers = {}
                view._press_tasks = set()
                view._page_ref = _mock_page()
                view.player_bar = MagicMock()
                view.app_state = MagicMock()
                view.app_state.download_done = []
                view.app_state.download_state = TransferState.IDLE
                view.app_state.download_active_ids = set()
                view.app_state.download_paused = False
                view.app_state.download_items = []
                return view

            # Plataforma con stream activo -> reproduce sin descargar
            v_stream = _make_view(is_local=False, is_manifest=False)
            with patch.object(settings_store, "get_stream_without_download", return_value=True):
                row = build_detail_track_row(v_stream, tr_remote, 1)
                row.on_click(None)
                await asyncio.sleep(0.12)
            v_stream._stream_single_track.assert_called_once_with(tr_remote)
            v_stream._download_single_track.assert_not_called()

            # Streaming desactivado -> descarga
            v_dl = _make_view(is_local=False, is_manifest=False)
            with patch.object(settings_store, "get_stream_without_download", return_value=False):
                row = build_detail_track_row(v_dl, tr_remote, 1)
                row.on_click(None)
                await asyncio.sleep(0.12)
            v_dl._download_single_track.assert_called_once()
            v_dl._stream_single_track.assert_not_called()

            # Manifiesto local con streaming activo -> reproduce sin descargar
            v_man = _make_view(is_local=True, is_manifest=True)
            with patch.object(settings_store, "get_stream_without_download", return_value=True):
                row = build_detail_track_row(v_man, tr_remote, 1)
                row.on_click(None)
                await asyncio.sleep(0.12)
            v_man._stream_single_track.assert_called_once_with(tr_remote)
            v_man._download_single_track.assert_not_called()

            # Carpeta local con archivo ausente -> notifica en vez de descargar de YouTube
            v_loc = _make_view(is_local=True, is_manifest=False)
            row = build_detail_track_row(v_loc, tr_missing_local, 1)
            row.on_click(None)
            await asyncio.sleep(0.12)
            v_loc._play_single_track.assert_called_once_with(tr_missing_local, 1)
            v_loc._download_single_track.assert_not_called()

        asyncio.run(_run())

    def test_library_state_notified_on_download_completion(self) -> None:
        from ui.shell.main_ui import PlaylistManagerUI
        from ui.views.library_view import LibraryView

        self.assertTrue(callable(getattr(LibraryView, "refresh", None)))
        lib_state = MagicMock()
        state = SimpleNamespace(download_state=TransferState.DONE, transfer_state=TransferState.IDLE)
        fake_ui = SimpleNamespace(
            state=state,
            library_state=lib_state,
            _last_download_state=TransferState.RUNNING,
            _current_module=1,
            _sync_report_dialog=MagicMock(),
            page=MagicMock(),
        )
        PlaylistManagerUI._on_state_changed(fake_ui)
        lib_state.notify.assert_called_once()
        self.assertEqual(fake_ui._last_download_state, TransferState.DONE)

    def test_recent_playlists_persistence_and_home_ordering(self) -> None:
        from core.config import LOCAL_LIBRARY_LABEL
        from core.library_models import PlaylistSummary
        from ui.views.home_view import HomeView

        settings_store.record_recent_playlist("Spotify", "sp_2")
        settings_store.record_recent_playlist(LOCAL_LIBRARY_LABEL, "loc_1")
        settings_store.record_recent_playlist("Spotify", "sp_2")  # sube al tope sin duplicar
        persisted = settings_store.get_recent_playlists()
        self.assertEqual([(p["platform"], p["id"]) for p in persisted], [("Spotify", "sp_2"), (LOCAL_LIBRARY_LABEL, "loc_1")])

        by_plat = {
            "YouTube Music": [
                PlaylistSummary(id=f"yt_{i}", name=f"YT {i}", platform="YouTube Music", track_count=5)
                for i in range(1, 8)
            ],
            "Spotify": [
                PlaylistSummary(id="sp_1", name="SP 1", platform="Spotify", track_count=5),
                PlaylistSummary(id="sp_2", name="SP 2", platform="Spotify", track_count=5),
            ],
            LOCAL_LIBRARY_LABEL: [
                PlaylistSummary(id="loc_1", name="Local 1", platform=LOCAL_LIBRARY_LABEL, track_count=5),
            ],
        }
        fake_home = SimpleNamespace(
            library_state=SimpleNamespace(get_playlists=lambda p: by_plat.get(p, [])),
        )
        recents = HomeView._collect_recent_summaries(fake_home)
        self.assertEqual(recents[0].id, "sp_2")
        self.assertEqual(recents[1].id, "loc_1")
        # El resto se completa balanceado entre fuentes (no solo YouTube Music)
        self.assertIn("sp_1", [r.id for r in recents])
        self.assertIn("yt_1", [r.id for r in recents])

    def test_library_visible_sources_filters_by_session_and_defaults_to_local(self) -> None:
        from core.config import LOCAL_LIBRARY_LABEL
        from core.library_models import LibrarySnapshot, PlaylistSummary
        from services.library_state import LibraryState, visible_library_sources
        from ui.views.library_view import LibraryView

        # Sin sesiones ni caché -> solo Local y abre directo en Local con "+ Conectar cuenta"
        empty_lib = LibraryState(service=None)
        empty_lib._snapshots = {}
        app_no_auth = SimpleNamespace(
            auth_session_ok={"YouTube Music": False, "Apple Music": False, "Spotify": False},
            subscribe=lambda _cb: None,
        )
        self.assertEqual(visible_library_sources(app_no_auth, empty_lib), [LOCAL_LIBRARY_LABEL])
        opened_wizard: list[str] = []
        lv = LibraryView(
            _mock_page(),
            empty_lib,
            service=None,
            app_state=app_no_auth,
            on_open_wizard=lambda p: opened_wizard.append(p),
        )
        self.assertEqual(lv._platform, LOCAL_LIBRARY_LABEL)
        self.assertEqual(list(lv._platform_btns.keys()), [LOCAL_LIBRARY_LABEL])
        # Último control de _platform_row es "+ Conectar cuenta"
        self.assertEqual(len(lv._platform_row.controls), 2)
        lv._platform_row.controls[-1].on_click(None)
        self.assertEqual(opened_wizard, ["YouTube Music"])

        # Con sesión Spotify + caché YouTube Music -> oculta Apple Music
        cached_lib = LibraryState(service=None)
        cached_lib._snapshots = {
            "YouTube Music": LibrarySnapshot(
                platform="YouTube Music",
                items=[PlaylistSummary(id="yt1", name="YT", platform="YouTube Music", track_count=3)],
            )
        }
        app_sp = SimpleNamespace(
            auth_session_ok={"YouTube Music": False, "Apple Music": False, "Spotify": True},
            subscribe=lambda _cb: None,
        )
        self.assertEqual(
            visible_library_sources(app_sp, cached_lib),
            ["YouTube Music", "Spotify", LOCAL_LIBRARY_LABEL],
        )


if __name__ == "__main__":
    unittest.main()
