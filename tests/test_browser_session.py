"""Pruebas offline (stdlib) para services.browser_session."""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from services.browser_session import (
    compute_sapisidhash,
    read_firefox_cookies,
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


if __name__ == "__main__":
    unittest.main()
