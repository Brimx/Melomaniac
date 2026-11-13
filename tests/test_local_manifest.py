"""
tests/test_local_manifest.py — Pruebas de Bloque 2.3 (Manifiestos locales sin audio)
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core import library_store
from core.config import LOCAL_LIBRARY_LABEL
from core.models import PlaylistMeta, Track
from engine import local_manifests
from engine.downloader import write_playlist_manifest
from services import authentication, settings_store
from services.library_state import LibraryState


_UI_SUBPROC_SCRIPT = """
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import flet as ft

from core import library_store
from core.config import LOCAL_LIBRARY_LABEL
from core.models import Track
from services import authentication, settings_store
from services.library_state import LibraryState
from ui.library_view import LibraryView


class _DummyPage:
    def __init__(self) -> None:
        self.services = []
        self.overlay = []
        self.dialogs = []

    def run_task(self, fn, *args, **kwargs):
        raise RuntimeError("headless")

    def show_dialog(self, dlg) -> None:
        self.dialogs.append(dlg)

    def update(self) -> None:
        pass


tc = unittest.TestCase()
with tempfile.TemporaryDirectory() as tmp:
    tmp_path = Path(tmp)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    folder_a = tmp_path / "MusicaLocal"
    folder_a.mkdir()
    downloaded_sub = folder_a / "Mix Descargado [Spotify]"
    downloaded_sub.mkdir()
    (downloaded_sub / "track1.mp3").write_bytes(b"")
    write_target = downloaded_sub / "playlist.json"
    write_target.write_text(
        json.dumps({"name": "Mix Descargado", "platform": "Spotify", "count": 1, "tracks": []}),
        encoding="utf-8",
    )
    # Subcarpeta sin archivos de audio: no debe aparecer como playlist descargada
    empty_sub = folder_a / "Vacia [Spotify]"
    empty_sub.mkdir()
    (empty_sub / "playlist.json").write_text(
        json.dumps({"name": "Vacia", "platform": "Spotify", "count": 1, "tracks": []}),
        encoding="utf-8",
    )

    with (
        mock.patch.object(authentication, "CONFIG_DIR", cfg_dir),
        mock.patch.object(settings_store, "SETTINGS_JSON", cfg_dir / "settings.json"),
        mock.patch.object(settings_store, "ensure_config_dir", lambda: cfg_dir.mkdir(parents=True, exist_ok=True)),
        mock.patch.object(library_store, "LIBRARY_JSON", cfg_dir / "library_playlists.json"),
        mock.patch.object(library_store, "ensure_config_dir", lambda: cfg_dir.mkdir(parents=True, exist_ok=True)),
    ):
        state = LibraryState(service=None)
        state.add_local_folder(str(folder_a))
        saved = state.save_local_manifest(
            platform="Spotify",
            playlist_id="37i9dQZF1DXcBWIGoYBM5M",
            name="Today's Top Hits",
            owner="Spotify",
            img_url="https://example.com/cover.jpg",
            tracks=[
                Track(
                    id="t1",
                    name="Song One",
                    artist="Artist A",
                    album="Album 1",
                    duration="3:15",
                    img_url="https://example.com/t1.jpg",
                    platform="Spotify",
                    duration_ms=195000,
                    isrc="USABC1234567",
                    source_url="https://open.spotify.com/track/t1",
                )
            ],
        )

        page = _DummyPage()
        view = LibraryView(page, state, service=None)

        # Cambiar a la fuente Local
        class _Evt:
            def __init__(self, data):
                self.control = type("Ctrl", (), {"data": data})()

        view._on_platform_click(_Evt(LOCAL_LIBRARY_LABEL))

        tc.assertTrue(view._grid.visible)
        tc.assertFalse(view._empty.visible)
        tc.assertTrue(view._import_manifest_btn.visible)
        tc.assertTrue(view._add_folder_btn.visible)

        # Debe distinguir playlists importadas/descargadas vs carpetas locales
        tc.assertEqual(len(view._playlists_row.controls), 2)
        tc.assertEqual(len(view._grid_row.controls), 1)
        tc.assertIn("importadas (sin audio)", view._status_text.value)
        tc.assertIn("descargadas", view._status_text.value)
        tc.assertIn("carpetas locales", view._status_text.value)

        # Abrir el manifiesto importado debe configurar loader y on_remove en PlaylistDetailView
        imported_summ = state.imported_manifest_summaries()[0]
        view._on_open_playlist(imported_summ)
        tc.assertIsNotNone(view._detail)
        tc.assertIsNotNone(view._detail._loader)
        tc.assertTrue(view._detail._is_manifest)

        # Subcarpeta descargada vacía ignorada; la que tiene audio se puede desvincular sin borrar mp3
        dl_summs = state.downloaded_playlist_summaries()
        tc.assertEqual(len(dl_summs), 1)
        tc.assertTrue(state.remove_downloaded_playlist(str(downloaded_sub)))
        tc.assertFalse(write_target.exists())
        tc.assertTrue((downloaded_sub / "track1.mp3").exists())
        tc.assertEqual(len(state.downloaded_playlist_summaries()), 0)

        # Quitar el manifiesto limpiamente desde LibraryState / callback
        removed = state.remove_local_manifest(saved.manifest_id)
        tc.assertTrue(removed)
        view._on_back_from_detail()
        tc.assertEqual(len(state.imported_manifest_summaries()), 0)
        tc.assertEqual(len(view._playlists_row.controls), 0)
        tc.assertEqual(len(view._grid_row.controls), 1)

        # El diálogo de importación pone Plataforma primero y autodetecta desde URL
        view._open_import_dialog()
        tc.assertEqual(len(page.dialogs), 1)
        dlg = page.dialogs[0]
        tc.assertIsInstance(dlg, ft.AlertDialog)
        body_col = dlg.content.content
        plat_dd = body_col.controls[0]
        url_field = body_col.controls[1]
        url_field.value = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        url_field.on_change(None)
        tc.assertEqual(plat_dd.value, "Spotify")

        # Con playlists sincronizadas por plataforma, el selector filtra según Plataforma
        from core.library_models import LibraryLoadState, LibrarySnapshot, PlaylistSummary
        state._snapshots["Spotify"] = LibrarySnapshot(
            platform="Spotify",
            state=LibraryLoadState.READY,
            items=[PlaylistSummary(platform="Spotify", id="sp_1", name="Mix SP", track_count=5)],
        )
        state._snapshots["YouTube Music"] = LibrarySnapshot(
            platform="YouTube Music",
            state=LibraryLoadState.READY,
            items=[PlaylistSummary(platform="YouTube Music", id="yt_1", name="Mix YT", track_count=3)],
        )
        page.dialogs.clear()
        view._platform = "Spotify"
        view._open_import_dialog()
        body_col2 = page.dialogs[0].content.content
        plat_dd2 = body_col2.controls[0]
        cached_dd2 = body_col2.controls[1]
        url_field2 = body_col2.controls[2]
        tc.assertEqual(plat_dd2.value, "Spotify")
        tc.assertEqual([o.text for o in cached_dd2.options[1:]], ["Mix SP"])
        cached_dd2.value = "Spotify::sp_1"
        cached_dd2.on_select(type("E", (), {"control": cached_dd2})())
        plat_dd2.value = "YouTube Music"
        plat_dd2.on_select(type("E", (), {"control": plat_dd2})())
        tc.assertEqual(cached_dd2.value, "")
        tc.assertEqual([o.text for o in cached_dd2.options[1:]], ["Mix YT"])
        url_field2.value = "https://open.spotify.com/playlist/sp_1"
        url_field2.on_change(None)
        tc.assertEqual(plat_dd2.value, "Spotify")
        tc.assertEqual([o.text for o in cached_dd2.options[1:]], ["Mix SP"])
"""


class TestLocalManifestEngine(unittest.TestCase):
    def test_save_load_list_and_remove_manifest_under_config_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "xdg_melomaniac"
            with mock.patch.object(authentication, "CONFIG_DIR", cfg_dir):
                expected_dir = cfg_dir / "local_playlists"
                self.assertEqual(local_manifests.get_local_manifests_dir(), expected_dir)

                tracks = [
                    Track(
                        id="sp_1",
                        name="Midnight City",
                        artist="M83",
                        album="Hurry Up, We're Dreaming",
                        duration="",
                        img_url="https://img.example/m83.jpg",
                        platform="Spotify",
                        duration_ms=243000,
                        isrc="FR9W11112345",
                        source_url="https://open.spotify.com/track/sp_1",
                    ),
                    Track(
                        id="sp_2",
                        name="Instant Crush",
                        artist="Daft Punk",
                        album="Random Access Memories",
                        duration="5:37",
                        img_url="https://img.example/ram.jpg",
                        platform="Spotify",
                        duration_ms=337000,
                        isrc="USQX91300105",
                        source_url="https://open.spotify.com/track/sp_2",
                    ),
                ]

                saved = local_manifests.save_local_manifest(
                    platform="Spotify",
                    playlist_id="https://open.spotify.com/playlist/37i9dQZF1DX4dyzvuaRJ0n?si=abc",
                    name="Electronic Classics",
                    owner="Curator",
                    img_url="https://img.example/cover.jpg",
                    tracks=tracks,
                    created_at="2026-11-12T10:00:00Z",
                )

                manifest_path = Path(saved.path)
                self.assertTrue(manifest_path.is_file())
                self.assertEqual(manifest_path.parent, expected_dir)
                self.assertEqual(saved.id, "37i9dQZF1DX4dyzvuaRJ0n")

                raw = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(raw["platform"], "Spotify")
                self.assertEqual(raw["id"], "37i9dQZF1DX4dyzvuaRJ0n")
                self.assertEqual(raw["name"], "Electronic Classics")
                self.assertEqual(raw["owner"], "Curator")
                self.assertEqual(raw["img_url"], "https://img.example/cover.jpg")
                self.assertEqual(raw["created_at"], "2026-11-12T10:00:00Z")
                self.assertEqual(len(raw["tracks"]), 2)
                self.assertEqual(raw["tracks"][0]["title"], "Midnight City")
                self.assertEqual(raw["tracks"][0]["artist"], "M83")
                self.assertEqual(raw["tracks"][0]["album"], "Hurry Up, We're Dreaming")
                self.assertEqual(raw["tracks"][0]["duration_ms"], 243000)
                self.assertEqual(raw["tracks"][0]["isrc"], "FR9W11112345")
                self.assertEqual(raw["tracks"][0]["source_url"], "https://open.spotify.com/track/sp_1")
                self.assertNotIn("source_path", raw["tracks"][0])

                loaded = local_manifests.load_local_manifest(saved.manifest_id)
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertEqual(loaded.name, "Electronic Classics")
                self.assertEqual(loaded.owner, "Curator")
                self.assertEqual(loaded.tracks[0].name, "Midnight City")
                self.assertEqual(loaded.tracks[0].duration, "4:03")
                self.assertEqual(loaded.tracks[0].source_path, "")
                self.assertEqual(loaded.tracks[1].isrc, "USQX91300105")

                # Reimportar la misma playlist actualiza sin duplicar archivos
                local_manifests.save_local_manifest(
                    platform="Spotify",
                    playlist_id="37i9dQZF1DX4dyzvuaRJ0n",
                    name="Electronic Classics v2",
                    owner="Curator",
                    tracks=tracks[:1],
                    created_at="2026-11-12T11:00:00Z",
                )
                listed = local_manifests.list_local_manifests()
                self.assertEqual(len(listed), 1)
                self.assertEqual(listed[0].name, "Electronic Classics v2")
                self.assertEqual(len(listed[0].tracks), 1)

                # Archivos corruptos o .tmp se ignoran limpiamente
                (expected_dir / "broken.json").write_text("{bad json", encoding="utf-8")
                (expected_dir / "temp.json.tmp").write_text("{}", encoding="utf-8")
                self.assertEqual(len(local_manifests.list_local_manifests()), 1)

                # Eliminación limpia y protección contra path traversal
                self.assertFalse(local_manifests.remove_local_manifest("../settings.json"))
                self.assertTrue(local_manifests.remove_local_manifest(saved.manifest_id))
                self.assertFalse(manifest_path.exists())
                self.assertFalse(local_manifests.remove_local_manifest(saved.manifest_id))

    def test_update_manifest_track_paths_persists_only_existing_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg_dir = Path(tmp) / "cfg"
            dl_dir = Path(tmp) / "downloads"
            dl_dir.mkdir()
            audio = dl_dir / "Artist A - Song A.mp3"
            audio.write_bytes(b"mp3")
            with mock.patch.object(authentication, "CONFIG_DIR", cfg_dir):
                tr = Track(id="t1", name="Song A", artist="Artist A", album="Alb", duration="3:00", img_url="", platform="Spotify")
                saved = local_manifests.save_local_manifest(
                    platform="Spotify",
                    playlist_id="pl_persist",
                    name="Persisted",
                    tracks=[tr],
                )
                tr.source_path = str(audio)
                self.assertTrue(local_manifests.update_manifest_track_paths(saved.manifest_id, [tr]))
                reloaded = local_manifests.load_local_manifest(saved.manifest_id)
                self.assertIsNotNone(reloaded)
                self.assertEqual(reloaded.tracks[0].source_path, str(audio))
                audio.unlink()
                reloaded_after_delete = local_manifests.load_local_manifest(saved.manifest_id)
                self.assertEqual(reloaded_after_delete.tracks[0].source_path, "")

    def test_detect_platform_from_url(self) -> None:
        self.assertEqual(
            local_manifests.detect_platform_from_url("https://open.spotify.com/playlist/abc"),
            "Spotify",
        )
        self.assertEqual(
            local_manifests.detect_platform_from_url("spotify:playlist:abc"),
            "Spotify",
        )
        self.assertEqual(
            local_manifests.detect_platform_from_url("https://music.youtube.com/playlist?list=PL123"),
            "YouTube Music",
        )
        self.assertEqual(
            local_manifests.detect_platform_from_url("https://music.apple.com/us/playlist/mix/pl.u-123"),
            "Apple Music",
        )
        self.assertEqual(local_manifests.detect_platform_from_url("PL12345"), "")


class TestLibraryStateManifestIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_import_playlist_manifest_without_audio_and_distinguish_local_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg_dir = tmp_path / "config"
            cfg_dir.mkdir()
            music_dir = tmp_path / "Music"
            music_dir.mkdir()
            dl_sub = music_dir / "Descargada [YouTube Music]"
            dl_sub.mkdir()
            (dl_sub / "a.mp3").write_bytes(b"")
            write_playlist_manifest(
                dl_sub,
                name="Descargada",
                platform="YouTube Music",
                entries=[{"name": "Track A", "artist": "Art A", "path": str(dl_sub / "a.mp3")}],
            )

            class _FakeService:
                def __init__(self) -> None:
                    self.calls = []

                async def fetch_playlist(self, platform: str, playlist_id: str, progress_cb=None):
                    self.calls.append((platform, playlist_id))
                    meta = PlaylistMeta(name="Roadtrip Mix", description="Solo metadatos")
                    tracks = [
                        Track(
                            id="yt1",
                            name="Drive",
                            artist="Incubus",
                            album="Make Yourself",
                            duration="3:52",
                            img_url="https://img.example/drive.jpg",
                            platform="YouTube Music",
                            duration_ms=232000,
                            isrc="USSM19918231",
                            source_url="https://music.youtube.com/watch?v=yt1",
                        )
                    ]
                    return meta, tracks

            fake_svc = _FakeService()
            with (
                mock.patch.object(authentication, "CONFIG_DIR", cfg_dir),
                mock.patch.object(settings_store, "SETTINGS_JSON", cfg_dir / "settings.json"),
                mock.patch.object(
                    settings_store,
                    "ensure_config_dir",
                    lambda: cfg_dir.mkdir(parents=True, exist_ok=True),
                ),
                mock.patch.object(library_store, "LIBRARY_JSON", cfg_dir / "library_playlists.json"),
                mock.patch.object(
                    library_store,
                    "ensure_config_dir",
                    lambda: cfg_dir.mkdir(parents=True, exist_ok=True),
                ),
            ):
                state = LibraryState(service=fake_svc)
                notifications = []
                state.subscribe(lambda: notifications.append("notified"))
                state.add_local_folder(str(music_dir))

                manifest = await state.import_playlist_manifest(
                    "YouTube Music",
                    "https://music.youtube.com/playlist?list=PL_ROADTRIP",
                    owner="Santi",
                )

                self.assertEqual(fake_svc.calls, [("YouTube Music", "PL_ROADTRIP")])
                self.assertEqual(manifest.name, "Roadtrip Mix")
                self.assertEqual(manifest.owner, "Santi")
                self.assertEqual(len(manifest.tracks), 1)
                self.assertEqual(manifest.tracks[0].source_path, "")

                folders = state.local_folder_summaries()
                downloaded = state.downloaded_playlist_summaries()
                imported = state.imported_manifest_summaries()

                self.assertEqual(len(folders), 1)
                self.assertEqual(len(downloaded), 1)
                self.assertEqual(len(imported), 1)
                self.assertEqual(imported[0].platform, LOCAL_LIBRARY_LABEL)
                self.assertIn("Roadtrip Mix", imported[0].name)
                self.assertTrue(state.is_imported_manifest(imported[0]))
                self.assertFalse(state.is_imported_manifest(folders[0]))
                self.assertEqual(len(state.local_summaries()), 3)

                self.assertTrue(state.remove_local_manifest(imported[0].id))
                self.assertEqual(len(state.imported_manifest_summaries()), 0)
                self.assertGreaterEqual(len(notifications), 3)


class TestLibraryViewManifestUI(unittest.TestCase):
    def test_library_view_distinguishes_local_sections_and_removes_manifest(self) -> None:
        res = subprocess.run(
            [sys.executable, "-c", _UI_SUBPROC_SCRIPT],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr or res.stdout)

    def test_library_view_follows_agents_token_and_animation_rules(self) -> None:
        src = Path("ui/library_view.py").read_text(encoding="utf-8")
        # Sin colores hex hardcodeados
        self.assertIsNone(re.search(r"#[0-9A-Fa-f]{6,8}", src))
        # Sin ft.Animation(<numero>) literal
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                is_anim = (
                    (isinstance(func, ast.Attribute) and func.attr == "Animation")
                    or (isinstance(func, ast.Name) and func.id == "Animation")
                )
                if is_anim and node.args:
                    self.assertFalse(
                        isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, (int, float)),
                        "ft.Animation no debe usar duraciones numéricas literales",
                    )

    def test_import_manifest_resolves_cover_from_snapshot(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from core.library_models import LibraryLoadState, LibrarySnapshot, PlaylistSummary
        from core.models import PlaylistMeta, Track
        from services.library_state import LibraryState

        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "cfg"
            cfg.mkdir()
            with patch("services.authentication.CONFIG_DIR", cfg), \
                 patch("services.library_state.load_library", return_value={}):
                state = LibraryState()
                state._snapshots["Spotify"] = LibrarySnapshot(
                    platform="Spotify",
                    state=LibraryLoadState.READY,
                    items=[
                        PlaylistSummary(
                            platform="Spotify",
                            id="pl_cov_1",
                            name="Exact Cover Playlist",
                            track_count=1,
                            cover_urls=["https://img.example/exact_playlist_cover.jpg"],
                            external_url="https://open.spotify.com/playlist/pl_cov_1",
                        )
                    ],
                )
                service = MagicMock()
                service.fetch_playlist = AsyncMock(
                    return_value=(
                        PlaylistMeta(name="Exact Cover Playlist", description="desc"),
                        [Track(id="t1", name="Song", artist="Artist", album="Alb", duration="3:00", img_url="https://img.example/track.jpg", platform="Spotify")],
                    )
                )
                state.set_service(service)
                manifest = asyncio.run(
                    state.import_playlist_manifest("Spotify", "https://open.spotify.com/playlist/pl_cov_1")
                )
                self.assertEqual(manifest.cover_urls, ["https://img.example/exact_playlist_cover.jpg"])
                self.assertEqual(manifest.img_url, "https://img.example/exact_playlist_cover.jpg")


if __name__ == "__main__":
    unittest.main()
