"""
tests/test_lyrics_ui_and_scene.py — Pruebas de pestaña Letra (D26) y Escena Reproduciendo
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import flet as ft
from PIL import Image

from core.models import Track
from core.playback import PlayableTrack, PlayQueue
from services import settings_store
from ui.player.now_playing_scene import (
    NowPlayingScene,
    _OLED_MAX_Y,
    _rgb_luminance_y,
    extract_ambient_pair,
)
from ui.player.player_bar import PlayerBar
from ui.player.player_queue import LyricsPane, PlayerQueuePanel


@dataclass(frozen=True)
class _FakeLyricLine:
    time_ms: int
    text: str


@dataclass
class _FakeLyricsResult:
    lines: list[_FakeLyricLine] = field(default_factory=list)
    plain_text: str = ""
    synced: bool = False
    instrumental: bool = False
    source: str = ""


def _make_playable(idx: int = 1, *, img_url: str = "") -> PlayableTrack:
    tr = Track(
        id=f"tr_{idx}",
        name=f"Canción {idx}",
        artist=f"Artista {idx}",
        album="Álbum Demo",
        duration="3:00",
        img_url=img_url,
        platform="local",
        duration_ms=180_000,
        source_path=f"/tmp/cancion_{idx}.flac",
    )
    return PlayableTrack(
        track=tr,
        path=tr.source_path,
        container="flac",
        codec="flac",
        duration_ms=180_000,
        bitrate_kbps=960,
        sample_rate=44100,
    )


class _FakePage:
    def __init__(self) -> None:
        self.services: list = []
        self.overlay: list = []
        self.window = SimpleNamespace(on_event=None)
        self.on_keyboard_event = None

    def update(self) -> None:
        pass

    def run_task(self, fn, *args, **kwargs):
        return asyncio.get_running_loop().create_task(fn(*args, **kwargs))


class TestLyricsPaneAndSync(unittest.TestCase):
    def test_empty_state_when_no_track(self) -> None:
        page = SimpleNamespace(services=[], run_task=lambda *_a, **_k: None)
        bar = SimpleNamespace(
            queue=PlayQueue([]),
            subscribe=lambda _cb: None,
            position_ms=0,
            duration_ms=0,
            _lyrics_cache={},
        )
        pane = LyricsPane(page, bar, initially_visible=True)
        self.assertEqual(pane._status, "empty")
        self.assertFalse(pane._scroll_col.visible)
        self.assertTrue(pane._state_box.visible)

    def test_synced_lyrics_highlight_and_click_to_seek(self) -> None:
        item = _make_playable(1)
        seek_calls: list[int] = []
        bar = SimpleNamespace(
            queue=PlayQueue([item]),
            subscribe=lambda _cb: None,
            subscribe_position=lambda _cb: None,
            unsubscribe_position=lambda _cb: None,
            position_ms=0,
            duration_ms=180_000,
            seek_to=seek_calls.append,
            _lyrics_cache={},
        )
        res = _FakeLyricsResult(
            lines=[
                _FakeLyricLine(1000, "Primera línea"),
                _FakeLyricLine(5000, "Segunda línea"),
                _FakeLyricLine(9000, "Tercera línea"),
                _FakeLyricLine(14000, "Cuarta línea"),
            ],
            synced=True,
            source="local_lrc",
        )
        page = SimpleNamespace(services=[], run_task=lambda *_a, **_k: None)
        pane = LyricsPane(page, bar, initially_visible=True)
        pane._apply_result(res)

        self.assertEqual(pane._status, "synced")
        self.assertEqual(len(pane._line_controls), 4)

        # Posición 5200 ms -> línea índice 1 activa (opacity 1.0)
        pane.sync_position(5200, force=True)
        self.assertEqual(pane._active_idx, 1)
        self.assertAlmostEqual(pane._line_controls[1][0].opacity, 1.0)
        self.assertAlmostEqual(pane._line_controls[0][0].opacity, 0.55)
        self.assertAlmostEqual(pane._line_controls[2][0].opacity, 0.55)
        self.assertAlmostEqual(pane._line_controls[3][0].opacity, 0.34)

        # Clic en la tercera línea busca al timestamp exacto
        pane._line_controls[2][0].on_click(None)
        self.assertEqual(seek_calls, [9000])

    def test_plain_instrumental_and_missing_service_states(self) -> None:
        item = _make_playable(2)
        bar = SimpleNamespace(
            queue=PlayQueue([item]),
            subscribe=lambda _cb: None,
            position_ms=0,
            duration_ms=180_000,
            _lyrics_cache={},
        )
        page = SimpleNamespace(services=[], run_task=lambda *_a, **_k: None)
        pane = LyricsPane(page, bar, initially_visible=True)

        pane._apply_result(_FakeLyricsResult(plain_text="Verso uno\nVerso dos", synced=False, source="embedded"))
        self.assertEqual(pane._status, "plain")
        self.assertTrue(pane._footer.visible)
        self.assertIn("Etiqueta embebida", pane._source_txt.value)

        pane._apply_result(_FakeLyricsResult(instrumental=True))
        self.assertEqual(pane._status, "instrumental")

        with mock.patch("ui.player.player_queue._import_lyrics_service", return_value=None):
            pane.refresh_track(force_reload=True)
            self.assertEqual(pane._status, "none")

    def test_lyrics_disabled_or_provider_off_skips_fetch(self) -> None:
        item = _make_playable(4)
        bar = SimpleNamespace(
            queue=PlayQueue([item]),
            subscribe=lambda _cb: None,
            position_ms=0,
            duration_ms=180_000,
            _lyrics_cache={},
        )
        fake_svc = SimpleNamespace(get_lyrics=mock.Mock())
        page = SimpleNamespace(services=[], run_task=lambda *_a, **_k: None)
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with (
                mock.patch.object(settings_store, "SETTINGS_JSON", settings_path),
                mock.patch.object(settings_store, "ensure_config_dir", lambda: None),
                mock.patch("ui.player.player_queue._import_lyrics_service", return_value=fake_svc),
            ):
                pane = LyricsPane(page, bar, initially_visible=True)
                settings_store.save_settings({"lyrics_enabled": False})
                pane.refresh_track(force_reload=True)
                self.assertEqual(pane._status, "disabled")
                fake_svc.get_lyrics.assert_not_called()

                settings_store.save_settings({"lyrics_enabled": True, "lyrics_provider": "off"})
                pane.refresh_track(force_reload=True)
                self.assertEqual(pane._status, "disabled")
                fake_svc.get_lyrics.assert_not_called()

    def test_async_fetch_connects_to_lyrics_service_contract(self) -> None:
        async def _run() -> None:
            item = _make_playable(3)
            bar = SimpleNamespace(
                queue=PlayQueue([item]),
                subscribe=lambda _cb: None,
                position_ms=2500,
                duration_ms=180_000,
                _lyrics_cache={},
            )
            fake_svc = SimpleNamespace(
                get_lyrics=mock.Mock(
                    return_value=_FakeLyricsResult(
                        lines=[
                            _FakeLyricLine(1000, "Inicio"),
                            _FakeLyricLine(2000, "Medio"),
                        ],
                        synced=True,
                        source="lrclib",
                    )
                ),
                active_line_index=lambda lines, pos_ms: 1 if pos_ms >= 2000 else 0,
            )
            page = _FakePage()
            with mock.patch("ui.player.player_queue._import_lyrics_service", return_value=fake_svc):
                pane = LyricsPane(page, bar, initially_visible=True)
                pane.refresh_track()
                await asyncio.sleep(0.05)

            self.assertEqual(pane._status, "synced")
            self.assertEqual(pane._active_idx, 1)
            fake_svc.get_lyrics.assert_called_once()

        asyncio.run(_run())


class TestAmbientExtractionAndNowPlayingScene(unittest.TestCase):
    def test_extract_ambient_pair_oled_cap_and_grayscale_off(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            # Portada bicolor brillante -> extrae 2 colores con tope OLED Y <= 0.0136
            color_img = Path(tmp) / "color.png"
            im = Image.new("RGB", (64, 64), (255, 40, 40))
            for x in range(32, 64):
                for y in range(64):
                    im.putpixel((x, y), (40, 140, 255))
            im.save(color_img)

            pair = extract_ambient_pair(color_img)
            self.assertIsNotNone(pair)
            for hex_col in pair:
                self.assertTrue(hex_col.startswith("#FF"))
                r = int(hex_col[3:5], 16)
                g = int(hex_col[5:7], 16)
                b = int(hex_col[7:9], 16)
                self.assertLessEqual(_rgb_luminance_y((r, g, b)), _OLED_MAX_Y + 1e-6)

            # Portada B/N -> sin 2 colores cromáticos -> ambiente apagado (None)
            bw_img = Path(tmp) / "bw.png"
            bw = Image.new("RGB", (64, 64), (30, 30, 30))
            for x in range(32, 64):
                for y in range(64):
                    bw.putpixel((x, y), (200, 200, 200))
            bw.save(bw_img)
            self.assertIsNone(extract_ambient_pair(bw_img))

    def test_scene_open_close_focus_reduce_motion_and_thumb_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with (
                mock.patch.object(settings_store, "SETTINGS_JSON", settings_path),
                mock.patch.object(settings_store, "ensure_config_dir", lambda: None),
            ):
                page = _FakePage()
                bar = PlayerBar(page)
                item = _make_playable(7)
                bar.queue.set_tracks([item], start_index=0)

                # Pulsar miniatura abre la escena en page.overlay
                bar._thumb_box.on_click(None)
                scene = bar._now_playing_scene
                self.assertIsInstance(scene, NowPlayingScene)
                self.assertTrue(scene.is_open)
                self.assertIn(scene, page.overlay)

                # Con audio en reproducción y foco, el disco debe girar
                bar._audio_state = mock.MagicMock()
                from flet_audio import AudioState
                bar._audio_state = AudioState.PLAYING
                scene._apply_ambient_pair(("#FF100404", "#FF040814"))
                self.assertTrue(scene._should_spin_disc())
                self.assertTrue(scene._should_animate_ambient())

                # Ventana sin foco pausa disco y ambiente
                scene._on_window_event(SimpleNamespace(type="blur"))
                self.assertFalse(scene._should_spin_disc())
                self.assertFalse(scene._should_animate_ambient())

                scene._on_window_event(SimpleNamespace(type="focus"))
                self.assertTrue(scene._should_spin_disc())
                self.assertTrue(scene._should_animate_ambient())

                # reduce_motion congela ambiente a 0.5/0.5 y frena el disco
                settings_store.save_settings({"reduce_motion": True})
                scene._sync_motion_state()
                self.assertFalse(scene._should_spin_disc())
                self.assertFalse(scene._should_animate_ambient())
                self.assertAlmostEqual(scene._amb_a.opacity, 0.5)
                self.assertAlmostEqual(scene._amb_b.opacity, 0.5)
                self.assertIsNone(scene._amb_a.animate_opacity)

                # scene_show_disc=False oculta el disco; scene_ambient_bg=False apaga ambiente
                settings_store.save_settings({
                    "reduce_motion": False,
                    "scene_show_disc": False,
                    "scene_ambient_bg": False,
                })
                scene._sync_motion_state()
                self.assertFalse(scene._disc_holder.visible)
                self.assertFalse(scene._amb_host.visible)

                # Escape cierra la escena
                scene._on_keyboard_event(SimpleNamespace(key="Escape"))
                self.assertFalse(scene.is_open)

                bar.release()


if __name__ == "__main__":
    unittest.main()
