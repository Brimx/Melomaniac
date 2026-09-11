"""Regression tests for the runtime layout and package exports."""

from __future__ import annotations

import unittest
from pathlib import Path

from core import AppState, PlaylistMeta, Track
from engine import build_local_tracks, clean_metadata, read_audio_metadata
from services import CircuitBreaker, MusicApiService, RateLimitError
from services.authentication import (
    BROWSER_JSON,
    CONFIG_DIR,
    ENV_FILE,
    SEARCH_CACHE_JSON,
    SPOTIFY_COOKIES_JSON,
)
from ui import AuthManager, ConfigWizard, PlaylistManagerUI


class RuntimeLayoutTests(unittest.TestCase):
    def test_runtime_files_share_the_config_directory(self):
        self.assertTrue(CONFIG_DIR.is_absolute())
        for path in (ENV_FILE, BROWSER_JSON, SPOTIFY_COOKIES_JSON, SEARCH_CACHE_JSON):
            self.assertEqual(Path(path).parent, CONFIG_DIR)

    def test_public_package_exports_resolve(self):
        self.assertIsNotNone(AppState)
        self.assertIsNotNone(PlaylistMeta)
        self.assertIsNotNone(Track)
        self.assertIsNotNone(build_local_tracks)
        self.assertIsNotNone(clean_metadata)
        self.assertIsNotNone(read_audio_metadata)
        self.assertIsNotNone(CircuitBreaker)
        self.assertIsNotNone(MusicApiService)
        self.assertIsNotNone(RateLimitError)
        self.assertIsNotNone(AuthManager)
        self.assertIsNotNone(ConfigWizard)
        self.assertIsNotNone(PlaylistManagerUI)

    def test_authentication_components_have_single_owners(self):
        self.assertEqual(AuthManager.__module__, "ui.auth_manager")
        self.assertEqual(ConfigWizard.__module__, "ui.config_wizard")


if __name__ == "__main__":
    unittest.main()
