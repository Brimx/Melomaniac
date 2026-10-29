"""
tests/test_cover_resolver.py — Resolución de portadas sin red (unittest)
════════════════════════════════════════════════════════════════════════
Inyecta ``fetch``/``get_json`` para validar la clave por álbum, el orden de
la cadena de proveedores, la validación de homónimos y el rechazo de
miniaturas 16:9 como artwork.
"""

from __future__ import annotations

import io
import sys
import unittest
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import Track
from services.covers import CoverResolution, CoverResolver, album_key

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:  # pragma: no cover - Pillow está en requirements.txt
    HAS_PIL = False


def _encode(size: tuple[int, int], fmt: str = "JPEG") -> bytes:
    img = Image.new("RGB", size, (30, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def _track(**over) -> Track:
    base = dict(
        id="t1", name="Song", artist="Artist", album="Album",
        duration="3:00", img_url="", platform="YouTube Music",
        album_artist="Artist", release_date="2001-05-01",
    )
    base.update(over)
    return Track(**base)


@unittest.skipUnless(HAS_PIL, "Pillow no disponible")
class AlbumKeyTest(unittest.TestCase):
    def test_same_album_shares_key(self) -> None:
        a = _track(id="a", name="One")
        b = _track(id="b", name="Two")
        self.assertEqual(album_key(a), album_key(b))

    def test_different_year_differs(self) -> None:
        a = _track(release_date="2001-05-01")
        b = _track(release_date="2011-05-01")
        self.assertNotEqual(album_key(a), album_key(b))

    def test_track_without_album_does_not_group(self) -> None:
        a = _track(album="", name="One")
        b = _track(album="", name="Two")
        self.assertNotEqual(album_key(a), album_key(b))

    def test_empty_track_has_no_key(self) -> None:
        self.assertEqual(album_key(_track(album="", name="", id="")), "")


@unittest.skipUnless(HAS_PIL, "Pillow no disponible")
class ResolveChainTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self._orig_cache = None
        # Aísla el caché de disco del config/ real del usuario.
        from services import cover_cache
        self._cc = cover_cache
        self._orig_cache = (cover_cache.CACHE_DIR, cover_cache.INDEX_JSON)
        cover_cache.CACHE_DIR = Path(self.tmp.name)
        cover_cache.INDEX_JSON = Path(self.tmp.name) / "index.json"

    def tearDown(self) -> None:
        self._cc.CACHE_DIR, self._cc.INDEX_JSON = self._orig_cache
        self.tmp.cleanup()

    def test_platform_square_url_wins(self) -> None:
        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return _encode((800, 800))

        r = CoverResolver(fetch=fetch, get_json=lambda u, p: {})
        res = r.resolve(_track(img_url="https://x/cover.jpg"))
        self.assertTrue(res)
        self.assertEqual(res.source, "platform")
        self.assertEqual(len(calls), 1, "no debe consultar proveedores si ya hay artwork")

    def test_youtube_thumb_169_is_not_platform_artwork(self) -> None:
        """Un thumbnail 16:9 no se acepta como portada de plataforma."""
        r = CoverResolver(fetch=lambda u: _encode((1280, 720)), get_json=lambda u, p: {})
        res = r.resolve(_track(img_url="https://i.ytimg.com/vi/x/hqdefault.jpg"))
        self.assertFalse(res)

    def test_itunes_match_returns_cover(self) -> None:
        def get_json(url: str, params: dict) -> dict:
            if "itunes" in url:
                return {"results": [{
                    "collectionName": "Album",
                    "artistName": "Artist",
                    "artworkUrl100": "https://it/img/100x100bb.jpg",
                }]}
            return {}

        r = CoverResolver(fetch=lambda u: _encode((600, 600)), get_json=get_json)
        res = r.resolve(_track())
        self.assertTrue(res)
        self.assertEqual(res.source, "itunes")

    def test_itunes_homonym_is_rejected(self) -> None:
        def get_json(url: str, params: dict) -> dict:
            if "itunes" in url:
                return {"results": [{
                    "collectionName": "Completely Different Record",
                    "artistName": "Nobody Else",
                    "artworkUrl100": "https://it/img/100x100bb.jpg",
                }]}
            return {}

        r = CoverResolver(fetch=lambda u: _encode((600, 600)), get_json=get_json)
        self.assertFalse(r.resolve(_track()))

    def test_deezer_used_when_itunes_empty(self) -> None:
        def get_json(url: str, params: dict) -> dict:
            if "deezer" in url:
                return {"data": [{
                    "title": "Album",
                    "artist": {"name": "Artist"},
                    "cover_xl": "https://dz/cover.jpg",
                }]}
            return {}

        r = CoverResolver(fetch=lambda u: _encode((1000, 1000)), get_json=get_json)
        res = r.resolve(_track())
        self.assertTrue(res)
        self.assertEqual(res.source, "deezer")

    def test_isrc_is_skipped_without_isrc(self) -> None:
        """MusicBrainz solo se consulta cuando el Track trae ISRC."""
        seen: list[str] = []

        def get_json(url: str, params: dict) -> dict:
            seen.append(url)
            return {}

        r = CoverResolver(fetch=lambda u: b"", get_json=get_json)
        r.resolve(_track())
        self.assertFalse(any("musicbrainz" in u for u in seen))

    def test_album_is_resolved_once_for_its_tracks(self) -> None:
        """La caché por álbum evita repetir la consulta por cada pista."""
        calls: list[str] = []

        def get_json(url: str, params: dict) -> dict:
            calls.append(url)
            return {"results": [{
                "collectionName": "Album",
                "artistName": "Artist",
                "artworkUrl100": "https://it/img/100x100bb.jpg",
            }]}

        r = CoverResolver(fetch=lambda u: _encode((600, 600)), get_json=get_json)
        tracks = [_track(id=str(i), name=f"Song {i}") for i in range(5)]
        resolved = r.resolve_many(tracks)
        self.assertEqual(len(resolved), 1, "las cinco pistas son el mismo álbum")
        self.assertEqual(len(calls), 1, "una sola consulta para todo el álbum")

    def test_cover_resolution_truthiness(self) -> None:
        self.assertFalse(CoverResolution())
        self.assertTrue(CoverResolution(b"\xff\xd8\xff", "itunes"))


if __name__ == "__main__":
    unittest.main()
