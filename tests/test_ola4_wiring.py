"""tests/test_ola4_wiring.py — Regresión de cableado Ola 4 (Config, MPRIS, onboarding y letras)."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

import requests

from services import lyrics as lyrics_svc
from services import settings_store
from ui.components.skeletons import is_reduced_motion
from ui.player.player_mpris import MprisController
from ui.setup.onboarding import OnboardingView
from ui.setup.onboarding_bg import OnboardingBackground, OnboardingDiscoBackground


class TestReducedMotionWiring(unittest.TestCase):
    def test_is_reduced_motion_reads_settings_store(self) -> None:
        env_clean = {
            k: v
            for k, v in os.environ.items()
            if k not in (
                "MELOMANIAC_REDUCED_MOTION",
                "REDUCED_MOTION",
                "PREFERS_REDUCED_MOTION",
                "GTK_ENABLE_ANIMATIONS",
            )
        }
        with patch.dict(os.environ, env_clean, clear=True):
            with patch.object(settings_store, "get_reduce_motion", return_value=True):
                self.assertTrue(is_reduced_motion())
            with patch.object(settings_store, "get_reduce_motion", return_value=False):
                self.assertFalse(is_reduced_motion())

    def test_is_reduced_motion_handles_settings_error_safely(self) -> None:
        with patch.object(settings_store, "get_reduce_motion", side_effect=RuntimeError("boom")):
            self.assertFalse(is_reduced_motion(SimpleNamespace(reduced_motion=False, media=None)))


class TestMprisWiring(unittest.TestCase):
    def test_mpris_controller_skips_when_disabled(self) -> None:
        fake_page = SimpleNamespace(run_task=MagicMock())
        fake_player = SimpleNamespace(
            _page_ref=fake_page,
            _dbus_mpris="sentinel",
            _on_play_pause_click=lambda: None,
            _stop_playback=lambda: None,
            _on_next_click=lambda: None,
            _on_prev_click=lambda: None,
            _log=lambda _m: None,
            queue=SimpleNamespace(current=None),
        )
        ctrl = MprisController(fake_player)
        fake_svc = MagicMock()

        with patch.object(settings_store, "get_mpris_enabled", return_value=False):
            ctrl.attach(fake_svc)

        self.assertIsNone(fake_player._dbus_mpris)
        fake_svc.set_handlers.assert_not_called()
        fake_page.run_task.assert_not_called()

    def test_mpris_controller_attaches_when_enabled(self) -> None:
        fake_page = SimpleNamespace(run_task=MagicMock())
        fake_player = SimpleNamespace(
            _page_ref=fake_page,
            _dbus_mpris=None,
            _on_play_pause_click=lambda: None,
            _stop_playback=lambda: None,
            _on_next_click=lambda: None,
            _on_prev_click=lambda: None,
            _log=lambda _m: None,
            queue=SimpleNamespace(current=None),
        )
        ctrl = MprisController(fake_player)
        fake_svc = MagicMock()

        with patch.object(settings_store, "get_mpris_enabled", return_value=True):
            ctrl.attach(fake_svc)

        self.assertIs(fake_player._dbus_mpris, fake_svc)
        fake_svc.set_handlers.assert_called_once()
        fake_page.run_task.assert_called_once_with(fake_svc.start)


class TestOnboardingBgModeWiring(unittest.TestCase):
    def test_onboarding_defaults_to_disco_and_honors_settings_store(self) -> None:
        fake_page = SimpleNamespace(services=[], on_keyboard_event=None, reduced_motion=False)
        view_default = OnboardingView(fake_page)
        self.assertEqual(view_default.bg_mode, "disco")
        self.assertIsInstance(view_default._bg, OnboardingDiscoBackground)

        with patch.object(settings_store, "get_bg_mode", return_value="constellation", create=True):
            view_from_cfg = OnboardingView(fake_page)
            self.assertEqual(view_from_cfg.bg_mode, "constellation")
            self.assertIsInstance(view_from_cfg._bg, OnboardingBackground)


class TestLyricsWiringAndErrors(unittest.TestCase):
    def setUp(self) -> None:
        lyrics_svc.reset_rate_limit()

    def tearDown(self) -> None:
        lyrics_svc.reset_rate_limit()

    def test_get_lyrics_use_cache_false_bypasses_negative_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyrics_svc, "CACHE_DIR", Path(tmp)):
            resp_404 = MagicMock(status_code=404)
            resp_ok = MagicMock(
                status_code=200,
                json=lambda: {"plainLyrics": "Line one\nLine two", "syncedLyrics": "", "instrumental": False},
            )
            with patch("services.lyrics.requests.get", side_effect=[resp_404, resp_ok]) as mock_get:
                self.assertIsNone(lyrics_svc.get_lyrics(title="Song", artist="Artist"))
                # Caché negativa vigente: no debe consultar la red con use_cache=True
                self.assertIsNone(lyrics_svc.get_lyrics(title="Song", artist="Artist"))
                self.assertEqual(mock_get.call_count, 1)

                # Botón Reintentar (use_cache=False) sí salta la caché negativa
                res = lyrics_svc.get_lyrics(title="Song", artist="Artist", use_cache=False)
                self.assertIsNotNone(res)
                self.assertEqual(res.plain_text, "Line one\nLine two")
                self.assertEqual(mock_get.call_count, 2)

    def test_distinguishes_network_and_5xx_errors_from_404(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyrics_svc, "CACHE_DIR", Path(tmp)):
            resp_404 = MagicMock(status_code=404)
            with patch("services.lyrics.requests.get", return_value=resp_404):
                self.assertIsNone(
                    lyrics_svc.get_lyrics(title="Missing", artist="Artist", raise_on_error=True)
                )
                self.assertEqual(lyrics_svc.get_last_error(), "")

            resp_500 = MagicMock(status_code=500)
            with patch("services.lyrics.requests.get", return_value=resp_500):
                with self.assertRaises(lyrics_svc.LyricsFetchError):
                    lyrics_svc.get_lyrics(
                        title="Err500", artist="Artist", use_cache=False, raise_on_error=True
                    )
                self.assertIn("500", lyrics_svc.get_last_error())

            with patch("services.lyrics.requests.get", side_effect=requests.Timeout("timed out")):
                with self.assertRaises(lyrics_svc.LyricsFetchError):
                    lyrics_svc.get_lyrics(
                        title="Timeout", artist="Artist", use_cache=False, raise_on_error=True
                    )
                self.assertTrue(lyrics_svc.get_last_error())

            # Compatibilidad: sin raise_on_error devuelve None pero conserva last_error
            with patch("services.lyrics.requests.get", side_effect=requests.ConnectionError("offline")):
                self.assertIsNone(
                    lyrics_svc.get_lyrics(title="Offline", artist="Artist", use_cache=False)
                )
                self.assertTrue(lyrics_svc.get_last_error())


if __name__ == "__main__":
    unittest.main()
