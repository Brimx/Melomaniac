"""Pruebas offline (stdlib) para services.browser_session."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services import browser_session, settings_store
from services.browser_session import (
    BrowserProfile,
    compute_sapisidhash,
    read_firefox_cookies,
    resolve_browser_launch_command,
    select_apple_jwt,
)


def _make_jwt(header: dict, payload: dict, sig: str = "c2lnbmF0dXJl") -> str:
    def _b64(obj: dict) -> str:
        raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    return f"{_b64(header)}.{_b64(payload)}.{sig}"


class TestBrowserSessionOffline(unittest.TestCase):
    def test_sapisidhash_fixed_values(self) -> None:
        ts = 1700000000
        sapisid = "AbCdEfGhIjKlMnOpQrStUvWxYz12345678"
        expected_sha = hashlib.sha1(
            f"{ts} {sapisid} https://music.youtube.com".encode("utf-8")
        ).hexdigest()
        result = compute_sapisidhash(sapisid, ts=ts)
        self.assertEqual(result, f"SAPISIDHASH {ts}_{expected_sha}")

    def test_select_apple_jwt_matches_only_webplaykid_and_ampwebplay(self) -> None:
        jwt_wrong_kid = _make_jwt(
            {"alg": "ES256", "kid": "LT2ZDZSXNQ"},
            {"iss": "AMPWebPlay", "iat": 1700000000},
        )
        jwt_wrong_iss = _make_jwt(
            {"alg": "ES256", "kid": "WebPlayKid"},
            {"iss": "5IKPP2IECQ", "iat": 1700000000},
        )
        jwt_valid = _make_jwt(
            {"alg": "ES256", "kid": "WebPlayKid"},
            {"iss": "AMPWebPlay", "iat": 1700000000},
        )

        bundle_with_valid = f'const a="{jwt_wrong_kid}"; const b="{jwt_valid}"; const c="{jwt_wrong_iss}";'
        self.assertEqual(select_apple_jwt(bundle_with_valid), jwt_valid)

        bundle_without_valid = f'const a="{jwt_wrong_kid}"; const c="{jwt_wrong_iss}";'
        self.assertIsNone(select_apple_jwt(bundle_without_valid))

    def test_firefox_reader_filters_wal_and_cleans_temp_files(self) -> None:
        shm_root = Path("/dev/shm") if Path("/dev/shm").is_dir() else Path(tempfile.gettempdir())
        before_temp = set(shm_root.glob("melo_ff_*"))

        with tempfile.TemporaryDirectory() as prof_dir_str:
            prof_dir = Path(prof_dir_str)
            db_path = prof_dir / "cookies.sqlite"

            conn = sqlite3.connect(str(db_path))
            try:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute(
                    """
                    CREATE TABLE moz_cookies (
                        id INTEGER PRIMARY KEY,
                        originAttributes TEXT NOT NULL,
                        name TEXT NOT NULL,
                        value TEXT NOT NULL,
                        host TEXT NOT NULL
                    )
                    """
                )
                # 1. Cookie permitida en contenedor por defecto
                conn.execute(
                    "INSERT INTO moz_cookies (originAttributes, name, value, host) VALUES ('', 'SAPISID', 'sapisid_main', '.youtube.com')"
                )
                # 2. Cookie en otro contenedor (originAttributes != '') -> debe filtrarse
                conn.execute(
                    "INSERT INTO moz_cookies (originAttributes, name, value, host) VALUES ('^userContextId=1', 'SAPISID', 'sapisid_container', '.youtube.com')"
                )
                # 3. Cookie con nombre NO permitido -> debe filtrarse
                conn.execute(
                    "INSERT INTO moz_cookies (originAttributes, name, value, host) VALUES ('', 'PREF', 'pref_ignored', '.youtube.com')"
                )
                conn.commit()
                # Forzar checkpoint inicial y luego insertar una fila que quede SOLO en el WAL
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                conn.execute("PRAGMA wal_autocheckpoint=0;")
                conn.execute(
                    "INSERT INTO moz_cookies (originAttributes, name, value, host) VALUES ('', 'sp_dc', 'wal_only_sp_dc', '.spotify.com')"
                )
                conn.commit()

                wal_file = prof_dir / "cookies.sqlite-wal"
                self.assertTrue(wal_file.exists() and wal_file.stat().st_size > 0)

                # Leer mientras la conexión original sigue abierta con la fila en el WAL
                rows = read_firefox_cookies(prof_dir)
            finally:
                conn.close()

        after_temp = set(shm_root.glob("melo_ff_*"))
        self.assertEqual(before_temp, after_temp, "No deben quedar directorios temporales melo_ff_*")

        by_name = {name: (host, val) for host, name, val in rows}
        self.assertIn("SAPISID", by_name)
        self.assertEqual(by_name["SAPISID"], (".youtube.com", "sapisid_main"))
        self.assertIn("sp_dc", by_name)
        self.assertEqual(by_name["sp_dc"], (".spotify.com", "wal_only_sp_dc"))
        self.assertNotIn("PREF", by_name)
        self.assertEqual(len(rows), 2)


class TestPreferredBrowserAndLaunch(unittest.TestCase):
    def _with_temp_settings(self, fn) -> None:
        from unittest import mock

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            with (
                mock.patch.object(settings_store, "SETTINGS_JSON", settings_path),
                mock.patch.object(settings_store, "ensure_config_dir", lambda: None),
                mock.patch.object(settings_store, "_CACHE", None),
            ):
                fn()

    def test_settings_per_platform_override_and_auto_exception(self) -> None:
        def _run() -> None:
            settings_store.set_preferred_browser("Firefox · default-release")
            self.assertEqual(
                settings_store.get_effective_preferred_browser("YouTube Music"),
                "Firefox · default-release",
            )
            settings_store.set_preferred_browser("Brave · Default", platform="YouTube Music")
            settings_store.set_preferred_browser("__auto__", platform="Spotify")

            self.assertEqual(
                settings_store.get_effective_preferred_browser("YouTube Music"),
                "Brave · Default",
            )
            self.assertEqual(
                settings_store.get_effective_preferred_browser("Apple Music"),
                "Firefox · default-release",
            )
            self.assertEqual(
                settings_store.get_effective_preferred_browser("Spotify"),
                "",
            )

            settings_store.set_preferred_browser("__global__", platform="YouTube Music")
            self.assertEqual(
                settings_store.get_effective_preferred_browser("YouTube Music"),
                "Firefox · default-release",
            )

        self._with_temp_settings(_run)

    def test_order_candidates_strict_when_installed_fallback_when_missing(self) -> None:
        from unittest import mock

        brave = BrowserProfile("chromium", "brave", "Brave · Default", Path("/tmp/brave"), "Default")
        firefox = BrowserProfile("firefox", "firefox", "Firefox · default-release", Path("/tmp/ff"), "default")
        items = [("Firefox · default-release", {"k": "ff"})]

        def _run() -> None:
            # Brave instalado, pero sin sesión para esta plataforma → estricto (no cae a Firefox)
            with mock.patch.object(browser_session, "detect", return_value=[brave, firefox]):
                res = list(browser_session._order_candidates(items, "Brave · Default", platform="YouTube Music"))
                self.assertEqual(res, [])

            # Brave guardado ya no está instalado/detectado → fallback al resto
            with mock.patch.object(browser_session, "detect", return_value=[firefox]):
                res = list(browser_session._order_candidates(items, "Brave · Default", platform="YouTube Music"))
                self.assertEqual(res, [("Firefox · default-release", {"k": "ff"})])

            # Excepción __auto__ ignora global y devuelve todos
            settings_store.set_preferred_browser("Brave · Default")
            with mock.patch.object(browser_session, "detect", return_value=[brave, firefox]):
                res = list(browser_session._order_candidates(items, "__auto__", platform="YouTube Music"))
                self.assertEqual(res, [("Firefox · default-release", {"k": "ff"})])

        self._with_temp_settings(_run)

    def test_apple_candidates_honors_saved_preferred_browser(self) -> None:
        from unittest import mock

        brave = BrowserProfile("chromium", "brave", "Brave · Default", Path("/tmp/brave"), "Default")
        firefox = BrowserProfile("firefox", "firefox", "Firefox · default-release", Path("/tmp/ff"), "default")

        def _fake_cookies(prof: BrowserProfile):
            if prof.browser == "firefox":
                return [(".music.apple.com", "media-user-token", "ff_tok")]
            return [(".music.apple.com", "media-user-token", "brave_tok")]

        def _run() -> None:
            settings_store.set_preferred_browser("Brave · Default")
            with (
                mock.patch.object(browser_session, "detect", return_value=[firefox, brave]),
                mock.patch.object(browser_session, "_profile_cookies", side_effect=_fake_cookies),
                mock.patch.object(browser_session, "_derive_apple_bearer", return_value="jwt_bearer"),
                mock.patch.object(browser_session, "read_env_values", return_value={}),
            ):
                cands = list(browser_session.apple_candidates(None))
                self.assertEqual(len(cands), 1)
                self.assertEqual(cands[0][0], "Brave · Default")
                self.assertEqual(cands[0][1]["user_token"], "brave_tok")

        self._with_temp_settings(_run)

    def test_resolve_browser_launch_command_and_fallback(self) -> None:
        from unittest import mock

        brave = BrowserProfile("chromium", "brave", "Brave · Default", Path("/tmp/brave"), "Default")

        def _run() -> None:
            with (
                mock.patch.object(browser_session, "detect", return_value=[brave]),
                mock.patch.object(browser_session, "_find_browser_executable", return_value="/usr/bin/brave-browser"),
                mock.patch.object(browser_session.sys, "platform", "linux"),
            ):
                cmd, label = resolve_browser_launch_command("https://music.youtube.com", "Brave · Default")
                self.assertEqual(label, "Brave · Default")
                self.assertEqual(
                    cmd,
                    ["/usr/bin/brave-browser", "--profile-directory=Default", "https://music.youtube.com"],
                )

            # Navegador no instalado → devuelve (None, etiqueta) para activar fallback
            with (
                mock.patch.object(browser_session, "detect", return_value=[brave]),
                mock.patch.object(browser_session, "_find_browser_executable", return_value=None),
            ):
                cmd, label = resolve_browser_launch_command("https://music.youtube.com", "Brave · Default")
                self.assertIsNone(cmd)
                self.assertEqual(label, "Brave · Default")

            # Modo automático → (None, None)
            cmd, label = resolve_browser_launch_command("https://music.youtube.com", "__auto__")
            self.assertEqual((cmd, label), (None, None))

        self._with_temp_settings(_run)

    def test_config_wizard_apply_save_persists_per_platform_sources(self) -> None:
        from types import SimpleNamespace
        from unittest import mock
        from ui.setup.config_wizard import ConfigWizard

        def _run() -> None:
            wiz = ConfigWizard.__new__(ConfigWizard)
            wiz._yt_fields = {}
            wiz._am_fields = {}
            wiz._sp_fields = {}
            wiz._source_dropdowns = {
                "YouTube Music": SimpleNamespace(value="Brave · Default"),
                "Apple Music": SimpleNamespace(value="__global__"),
                "Spotify": SimpleNamespace(value="__auto__"),
            }
            settings_store.set_preferred_browser("Firefox · default-release")
            vals = wiz._collect_form_values()
            with mock.patch("ui.setup.config_wizard.write_browser_json"), \
                 mock.patch("ui.setup.config_wizard.write_env_values"), \
                 mock.patch("ui.setup.config_wizard.write_spotify_cookies"):
                wiz._apply_save(vals)

            self.assertEqual(settings_store.get_effective_preferred_browser("YouTube Music"), "Brave · Default")
            self.assertEqual(settings_store.get_effective_preferred_browser("Apple Music"), "Firefox · default-release")
            self.assertEqual(settings_store.get_effective_preferred_browser("Spotify"), "")

        self._with_temp_settings(_run)


if __name__ == "__main__":
    unittest.main()
