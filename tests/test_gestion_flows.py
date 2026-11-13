"""Pruebas unitarias de los flujos de Gestión (Bloques 2.1, 2.2, 2.4/2.5, 2.9, 2.10, 2.11)."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import unittest
from pathlib import Path


_SUBPROC_TESTS = r"""
import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import flet as ft

from core.library_models import PlaylistSummary
from core.models import LoadState, PlaylistMeta, SearchResult, Track, TransferState
from core.state import AppState
from ui.main_layout import compute_contextual_actions_state
from ui.main_ui import PlaylistManagerUI


class _DummyPage:
    def __init__(self) -> None:
        self.services = []
        self.overlay = []
        self.width = 1280
        self.height = 860
        self.window = types.SimpleNamespace(width=1280, height=860)
        self.dialogs = []
        self.on_resize = None
        self.updated = 0

    def update(self) -> None:
        self.updated += 1

    def show_dialog(self, dlg) -> None:
        dlg.open = True
        self.dialogs.append(dlg)

    def run_task(self, coro_or_fn, *args, **kwargs):
        coro = coro_or_fn(*args, **kwargs) if callable(coro_or_fn) else coro_or_fn
        try:
            loop = asyncio.get_running_loop()
            return loop.create_task(coro)
        except RuntimeError:
            return None


def _make_track(
    tid: str,
    name: str,
    artist: str = "Artist",
    *,
    platform: str = "YouTube Music",
    source_path: str = "",
    selected: bool = True,
) -> Track:
    return Track(
        id=tid,
        name=name,
        artist=artist,
        album="Album",
        duration="3:00",
        img_url="",
        platform=platform,
        duration_ms=180000,
        source_path=source_path,
        selected=selected,
    )


def _walk_controls(root):
    out = [root]
    for attr in ("content", "title", "leading", "trailing"):
        sub = getattr(root, attr, None)
        if sub is not None:
            out.extend(_walk_controls(sub))
    for attr in ("controls", "actions"):
        seq = getattr(root, attr, None)
        if isinstance(seq, list):
            for child in seq:
                if child is not None:
                    out.extend(_walk_controls(child))
    return out


class TestGestionFlows(unittest.TestCase):
    def _build_ui(self, service=None):
        page = _DummyPage()
        state = AppState(service=service)
        with patch("ui.main_layout.LayoutMixin._build_modules_and_root") as _:
            pass
        ui = PlaylistManagerUI(page, state)
        return page, state, ui

    def test_contextual_bar_visibility_and_no_permanent_dest_lockin(self) -> None:
        page, state, ui = self._build_ui()
        # Sin bloqueo permanente de destino en el sidebar
        self.assertFalse(ui._dst_dd.visible)
        self.assertFalse(ui._dest_session_warn.visible)

        # Idle: solo Fusionar visible
        idle_states = compute_contextual_actions_state(state)
        self.assertTrue(idle_states["merge"]["visible"])
        self.assertFalse(idle_states["merge"]["disabled"])
        for key in ("transfer", "organize", "split", "download", "export"):
            self.assertFalse(idle_states[key]["visible"])

        # Playlist remota cargada: todas las 6 acciones visibles y habilitadas
        state.tracks = [_make_track("yt1", "Song 1", platform="YouTube Music")]
        state.source_tracks = list(state.tracks)
        state.load_state = LoadState.READY
        remote_states = compute_contextual_actions_state(state)
        for key in ("transfer", "organize", "split", "merge", "download", "export"):
            self.assertTrue(remote_states[key]["visible"], key)
            self.assertFalse(remote_states[key]["disabled"], key)

        # Carpeta local con audio en disco: Descargar oculto
        state.load_local_tracks(
            [_make_track("loc1", "Local Audio", platform="Local", source_path="/tmp/a.mp3")],
            playlist_name="Mi Carpeta",
        )
        local_audio_states = compute_contextual_actions_state(state)
        self.assertFalse(local_audio_states["download"]["visible"])
        self.assertTrue(local_audio_states["download"]["disabled"])
        self.assertTrue(local_audio_states["transfer"]["visible"])
        self.assertTrue(local_audio_states["export"]["visible"])

        # Posición de la barra contextual encima de los headers de la tabla
        content_col = ui._content.content
        self.assertIsInstance(content_col, ft.Column)
        idx_ctx = content_col.controls.index(ui._contextual_bar)
        idx_hdr = content_col.controls.index(ui._col_headers)
        self.assertLess(idx_ctx, idx_hdr)

    def test_local_load_does_not_require_destination_first(self) -> None:
        async def _run():
            _page, state, ui = self._build_ui()
            state.set_source("Archivo Local")
            self.assertFalse(state.destination_confirmed)
            called = {"pick": 0}

            async def _fake_pick():
                called["pick"] += 1

            ui._do_local_pick = _fake_pick
            ui._on_load(None)
            await asyncio.sleep(0)
            self.assertEqual(called["pick"], 1)

        asyncio.run(_run())

    def test_transfer_dialog_with_live_preflight_and_synced_destination(self) -> None:
        async def _run():
            service = MagicMock()
            service.init_spotify = AsyncMock(return_value=True)
            service.add_tracks_to_playlist = AsyncMock(return_value=1)
            page, state, ui = self._build_ui(service=service)

            state.tracks = [_make_track("sp1", "Track One", platform="Spotify")]
            state.source_tracks = list(state.tracks)
            state.source = "Spotify"
            state.destination = "Spotify"
            state.playlist_name = "Origen"
            state.load_state = LoadState.READY

            ui.library_state = MagicMock()
            ui.library_state.get_playlists.side_effect = lambda plat: (
                [PlaylistSummary(platform="Spotify", id="sp_dest_1", name="Favoritas", track_count=12)]
                if plat == "Spotify"
                else []
            )

            am = MagicMock()
            am.check_sessions = AsyncMock(
                return_value=[types.SimpleNamespace(platform="Spotify", ok=False, error="Token expirado", expired=True)]
            )
            am.ingest_preflight_results = MagicMock()
            ui.auth_manager = am

            # Preflight fallido bloquea
            ok, err, expired = await ui._run_dialog_preflight("Spotify")
            self.assertFalse(ok)
            self.assertEqual(err, "Token expirado")
            self.assertTrue(expired)

            # Preflight OK en diálogo permite transferir a playlist existente
            am.check_sessions = AsyncMock(
                return_value=[types.SimpleNamespace(platform="Spotify", ok=True, error="", expired=False)]
            )
            state.destination = "Spotify"
            await ui._open_transfer_dialog(has_divisions=False)
            dlg = page.dialogs[-1]
            controls = _walk_controls(dlg)
            # Verifica que existe el selector de plataforma destino y el de playlist sincronizada
            dds = [c for c in controls if isinstance(c, ft.Dropdown)]
            self.assertGreaterEqual(len(dds), 2)
            await asyncio.sleep(0.02)

            # Inserción directa a playlist existente usa service.add_tracks_to_playlist
            added = await ui._add_tracks_to_existing_destination(
                "Spotify", "sp_dest_1", "Favoritas", list(state.tracks)
            )
            self.assertEqual(added, 1)
            service.add_tracks_to_playlist.assert_awaited_once()

        asyncio.run(_run())

    def test_add_tracks_to_playlist_resolves_local_and_falls_back(self) -> None:
        async def _run():
            service = MagicMock()
            # Sin add_tracks_to_playlist -> fallback a add_track
            del service.add_tracks_to_playlist
            service.init_youtube = AsyncMock(return_value=True)
            service.search_with_fallback = AsyncMock(
                return_value=SearchResult(track_id="yt_resolved_1")
            )
            service.add_track = AsyncMock(return_value=(True, "OK"))

            _page, state, ui = self._build_ui(service=service)
            local_tr = _make_track("loc_1", "Song A", "Art A", platform="Local", source_path="/tmp/a.mp3")
            added = await ui._add_tracks_to_existing_destination(
                "YouTube Music", "PL123", "Mi Lista YT", [local_tr]
            )
            self.assertEqual(added, 1)
            service.search_with_fallback.assert_awaited_once()
            service.add_track.assert_awaited_once_with("YouTube Music", "PL123", "yt_resolved_1")

        asyncio.run(_run())

    def test_merge_dialog_calls_app_state_merge_sources(self) -> None:
        async def _run():
            service = MagicMock()
            t1 = _make_track("t1", "Alpha", "One")
            t2 = _make_track("t2", "Beta", "Two")
            t1_dup = _make_track("t1", "Alpha", "One")
            service.fetch_playlist = AsyncMock(
                return_value=(PlaylistMeta(name="Lista 2", description=""), [t1_dup, t2])
            )

            page, state, ui = self._build_ui(service=service)
            state.tracks = [t1]
            state.source_tracks = [t1]
            state.playlist_name = "Lista 1"
            state.load_state = LoadState.READY

            ui.library_state = MagicMock()
            ui.library_state.get_playlists.side_effect = lambda plat: (
                [PlaylistSummary(platform="YouTube Music", id="p2", name="Lista 2", track_count=2)]
                if plat == "YouTube Music"
                else []
            )
            ui.library_state.local_summaries.return_value = []

            ui._open_merge_dialog()
            dlg = page.dialogs[-1]
            controls = _walk_controls(dlg)
            checkboxes = [c for c in controls if isinstance(c, ft.Checkbox)]
            self.assertEqual(len(checkboxes), 3)
            self.assertIn("duplicadas", checkboxes[-1].label.lower())
            self.assertTrue(checkboxes[-1].value)
            # Marca la segunda fuente (Lista 2)
            checkboxes[1].value = True
            checkboxes[1].on_change(types.SimpleNamespace(control=checkboxes[1]))

            # Dispara acción Fusionar
            merge_btn = dlg.actions[-1]
            merge_btn.on_click(None)
            await asyncio.sleep(0.05)

            self.assertEqual(state.playlist_name, "Playlist Fusionada")
            self.assertEqual([t.name for t in state.tracks], ["Alpha", "Beta"])

        asyncio.run(_run())

    def test_catalog_search_tabs_album_and_artist_drilldown(self) -> None:
        async def _run():
            song_tr = _make_track("s1", "Catalog Song", "Catalog Artist")
            album_tr = _make_track("a1", "Album Track", "Album Artist")
            artist_top = _make_track("top1", "Artist Hit", "Artist X")

            service = MagicMock()

            async def _search_catalog(platform, query, *, kind="songs", limit=20):
                if kind == "songs":
                    return [{"id": "s1", "name": "Catalog Song", "subtitle": "Catalog Artist", "img_url": "", "kind": "songs", "track": song_tr}]
                if kind == "albums":
                    return [{"id": "alb1", "name": "Great Album", "subtitle": "Album Artist", "img_url": "", "kind": "albums"}]
                return [{"id": "art1", "name": "Artist X", "subtitle": "Artista", "img_url": "", "kind": "artists"}]

            service.search_catalog = AsyncMock(side_effect=_search_catalog)
            service.fetch_album_tracks = AsyncMock(
                return_value=(PlaylistMeta(name="Great Album", description=""), [album_tr])
            )
            service.fetch_artist_overview = AsyncMock(
                return_value={
                    "artist": {"id": "art1", "name": "Artist X"},
                    "top_tracks": [artist_top],
                    "albums": [{"id": "alb1", "name": "Great Album", "subtitle": "2024"}],
                }
            )

            _page, state, ui = self._build_ui(service=service)
            ui._on_toggle_catalog_search()
            self.assertTrue(ui._catalog_panel.visible)
            self.assertFalse(ui._col_headers.visible)

            # Tab Canciones
            await ui._run_catalog_search("catalog", kind="songs", platform="YouTube Music")
            self.assertEqual(len(ui._catalog_results_lv.controls), 1)

            # Tab Álbumes + abrir álbum + cargar en Gestión
            ui._catalog_tab = "albums"
            await ui._run_catalog_search("great", kind="albums", platform="YouTube Music")
            self.assertEqual(len(ui._catalog_results_lv.controls), 1)
            await ui._open_catalog_album("YouTube Music", "alb1", "Great Album")
            self.assertTrue(ui._catalog_detail_bar.visible)
            self.assertEqual(len(ui._catalog_detail_tracks), 1)

            ui._on_catalog_detail_load()
            self.assertFalse(ui._catalog_panel.visible)
            self.assertEqual(state.playlist_name, "Great Album")
            self.assertEqual([t.name for t in state.tracks], ["Album Track"])

            # Tab Artistas + abrir artista
            ui._toggle_catalog_panel(True)
            ui._catalog_tab = "artists"
            await ui._run_catalog_search("artist", kind="artists", platform="YouTube Music")
            await ui._open_catalog_artist("YouTube Music", "art1", "Artist X")
            self.assertTrue(ui._catalog_detail_bar.visible)
            self.assertEqual([t.name for t in ui._catalog_detail_tracks], ["Artist Hit"])

        asyncio.run(_run())

    def test_transfer_dialog_confirm_new_respects_preflight(self) -> None:
        async def _run():
            service = MagicMock()
            page, state, ui = self._build_ui(service=service)
            state.tracks = [_make_track("yt1", "Song 1", platform="YouTube Music")]
            state.source_tracks = list(state.tracks)
            state.source = "YouTube Music"
            state.destination = "Spotify"
            state.playlist_name = "Mi Origen"
            state.load_state = LoadState.READY

            state.transfer_playlist = MagicMock()
            ui.library_state = MagicMock()
            ui.library_state.get_playlists.return_value = []

            # Caso 1: preflight falla -> bloquea confirmar y no llama transfer_playlist
            am_fail = MagicMock()
            am_fail.check_sessions = AsyncMock(
                return_value=[types.SimpleNamespace(platform="Spotify", ok=False, error="Sin sesión", expired=True)]
            )
            am_fail.ingest_preflight_results = MagicMock()
            ui.auth_manager = am_fail

            await ui._open_transfer_dialog(has_divisions=False)
            await asyncio.sleep(0.02)
            dlg_fail = page.dialogs[-1]
            confirm_fail = dlg_fail.actions[-1]
            self.assertTrue(confirm_fail.disabled)
            confirm_fail.on_click(None)
            await asyncio.sleep(0.02)
            state.transfer_playlist.assert_not_called()

            # Caso 2: preflight OK -> confirmar en modo "new" llama transfer_playlist
            am_ok = MagicMock()
            am_ok.check_sessions = AsyncMock(
                return_value=[types.SimpleNamespace(platform="Spotify", ok=True, error="", expired=False)]
            )
            am_ok.ingest_preflight_results = MagicMock()
            ui.auth_manager = am_ok

            await ui._open_transfer_dialog(has_divisions=False)
            await asyncio.sleep(0.02)
            dlg_ok = page.dialogs[-1]
            confirm_ok = dlg_ok.actions[-1]
            self.assertFalse(confirm_ok.disabled)
            confirm_ok.on_click(None)
            await asyncio.sleep(0.02)
            state.transfer_playlist.assert_called_once()
            self.assertEqual(state.destination, "Spotify")

        asyncio.run(_run())

    def test_export_dialog_file_and_playlist_modes(self) -> None:
        async def _run():
            import tempfile
            from pathlib import Path

            service = MagicMock()
            service.init_spotify = AsyncMock(return_value=True)
            service.add_tracks_to_playlist = AsyncMock(return_value=1)

            page, state, ui = self._build_ui(service=service)
            tr = _make_track("sp1", "Export Song", "Export Artist", platform="Spotify")
            state.tracks = [tr]
            state.source_tracks = [tr]
            state.playlist_name = "Mi Export"
            state.destination = "Spotify"
            state.load_state = LoadState.READY

            ui.library_state = MagicMock()
            ui.library_state.get_playlists.side_effect = lambda plat: (
                [PlaylistSummary(platform="Spotify", id="sp_list_9", name="Dest Sync", track_count=3)]
                if plat == "Spotify"
                else []
            )
            am = MagicMock()
            am.check_sessions = AsyncMock(
                return_value=[types.SimpleNamespace(platform="Spotify", ok=True, error="", expired=False)]
            )
            am.ingest_preflight_results = MagicMock()
            ui.auth_manager = am

            # Modo Archivo local
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = Path(tmpdir) / "salida.txt"
                picked_path = Path(tmpdir) / "elegida.txt"
                ui._save_picker = types.SimpleNamespace(
                    save_file=AsyncMock(return_value=str(picked_path))
                )
                orig_run_task = page.run_task
                seen_handlers = []

                def _checked_run_task(fn, *args, **kwargs):
                    import inspect
                    self.assertTrue(
                        inspect.iscoroutinefunction(fn),
                        f"run_task requiere corrutina sin invocar, recibido: {fn!r}",
                    )
                    seen_handlers.append(fn)
                    return orig_run_task(fn, *args, **kwargs)

                page.run_task = _checked_run_task
                await ui._open_export_dialog(initial_mode="file")
                dlg_file = page.dialogs[-1]
                tfs = [c for c in _walk_controls(dlg_file) if isinstance(c, ft.TextField)]
                self.assertTrue(tfs)
                pick_btns = [
                    c for c in _walk_controls(dlg_file)
                    if callable(getattr(c, "on_click", None))
                    and any(getattr(t, "value", "") == "Elegir…" for t in _walk_controls(c))
                ]
                self.assertEqual(len(pick_btns), 1)
                pick_btns[0].on_click(None)
                await asyncio.sleep(0.02)
                self.assertEqual(len(seen_handlers), 1)
                self.assertEqual(tfs[0].value, str(picked_path))
                tfs[0].value = str(out_path)
                dlg_file.actions[-1].on_click(None)
                await asyncio.sleep(0.02)
                self.assertTrue(out_path.exists())
                self.assertIn("Export Song", out_path.read_text(encoding="utf-8"))

            # Modo Añadir a playlist
            await ui._open_export_dialog(initial_mode="playlist")
            await asyncio.sleep(0.02)
            dlg_pl = page.dialogs[-1]
            dlg_pl.actions[-1].on_click(None)
            await asyncio.sleep(0.03)
            service.add_tracks_to_playlist.assert_awaited_once()

        asyncio.run(_run())

    def test_batch_download_excludes_local_files(self) -> None:
        _page, state, ui = self._build_ui()
        remote_tr = _make_track("yt1", "Remote Song", platform="YouTube Music", source_path="", selected=True)
        local_tr = _make_track("loc1", "Local Song", platform="Local", source_path="/tmp/local.mp3", selected=True)
        unselected_tr = _make_track("yt2", "Unselected", platform="YouTube Music", source_path="", selected=False)
        state.tracks = [remote_tr, local_tr, unselected_tr]
        state.playlist_name = "Mix"
        state.download_tracks = MagicMock()

        ui._on_batch_download()
        state.download_tracks.assert_called_once()
        passed_tracks = state.download_tracks.call_args.args[0]
        self.assertEqual([t.id for t in passed_tracks], ["yt1"])

    def test_catalog_search_fallback_without_search_catalog(self) -> None:
        async def _run():
            cand = _make_track("c1", "Fallback Song", "Fallback Artist", platform="Spotify")
            service = MagicMock()
            del service.search_catalog
            del service.fetch_album_tracks
            del service.fetch_artist_overview
            service.search_candidates = AsyncMock(return_value=[cand])

            _page, _state, ui = self._build_ui(service=service)
            ui._toggle_catalog_panel(True)

            # Fallback de Canciones a search_candidates
            await ui._run_catalog_search("fallback", kind="songs", platform="Spotify")
            service.search_candidates.assert_awaited_once()
            self.assertEqual(len(ui._catalog_results_lv.controls), 1)

            # Álbumes / Artistas sin search_catalog ni fetch_* no lanzan excepción
            await ui._run_catalog_search("x", kind="albums", platform="Spotify")
            self.assertEqual(len(ui._catalog_results_lv.controls), 0)
            await ui._open_catalog_album("Spotify", "alb_missing", "Missing Album")
            await ui._open_catalog_artist("Spotify", "art_missing", "Missing Artist")
            self.assertFalse(ui._catalog_detail_bar.visible)

        asyncio.run(_run())

    def test_schedule_state_render_from_worker_thread_marshals_to_ui_loop(self) -> None:
        import threading
        import warnings
        from ui.components.skeletons import (
            SkeletonRow,
            start_skeleton_pulse_tasks,
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", RuntimeWarning)
            tasks = []
            start_skeleton_pulse_tasks([SkeletonRow(0)], tasks)
            self.assertEqual(tasks, [])
        self.assertFalse(
            any("was never awaited" in str(w.message) for w in caught),
            f"Unexpected unawaited coroutine warning: {[str(w.message) for w in caught]}",
        )

        async def _run():
            _, _, ui = self._build_ui()
            rendered_threads: list[int] = []
            ui_tid = threading.get_ident()
            ui._on_state_changed = lambda: rendered_threads.append(threading.get_ident())

            worker_errors: list[Exception] = []

            def _worker() -> None:
                try:
                    ui._schedule_state_render()
                except Exception as exc:  # pragma: no cover
                    worker_errors.append(exc)

            t = threading.Thread(target=_worker)
            t.start()
            t.join()
            await asyncio.sleep(0.02)
            self.assertEqual(worker_errors, [])
            self.assertEqual(rendered_threads, [ui_tid])

        asyncio.run(_run())


suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestGestionFlows)
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
"""


class TestGestionFlowsSuite(unittest.TestCase):
    def test_gestion_flows_in_subprocess(self) -> None:
        res = subprocess.run(
            [sys.executable, "-c", _SUBPROC_TESTS],
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            res.returncode,
            0,
            msg=f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}",
        )

    def test_agents_ui_rules_in_gestion_files(self) -> None:
        hex_re = re.compile(r"#[0-9a-fA-F]{6,8}")
        anim_re = re.compile(r"ft\.Animation\(\s*[0-9]+|animation_duration\s*=\s*[0-9]+")
        for rel in ("ui/main_layout.py", "ui/main_actions.py", "ui/main_ui.py"):
            src = Path(rel).read_text(encoding="utf-8")
            self.assertIsNone(hex_re.search(src), f"Hex hardcodeado en {rel}")
            self.assertIsNone(anim_re.search(src), f"Duración literal en {rel}")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    is_dd = (
                        (isinstance(fn, ast.Attribute) and fn.attr == "Dropdown")
                        or (isinstance(fn, ast.Name) and fn.id == "Dropdown")
                    )
                    if is_dd:
                        kw_names = {kw.arg for kw in node.keywords}
                        self.assertNotIn("on_change", kw_names, f"Dropdown.on_change prohibido en {rel}")


if __name__ == "__main__":
    unittest.main()
