"""Tests unitarios offline para backend de letras (services/lyrics, audio_metadata, downloader)."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.audio_metadata import read_lyrics, write_lyrics
from engine.downloader import _apply_lyrics
from services import lyrics as lyrics_svc
from services.lyrics import (
    LyricLine,
    LyricsResult,
    active_line_index,
    fetch_lyrics,
    get_lyrics,
    load_local_lyrics,
    parse_lrc,
    reset_rate_limit,
    save_lrc_file,
    serialize_lrc,
)

try:
    from mutagen.id3 import ID3
    HAS_MUTAGEN = True
except ImportError:  # pragma: no cover
    HAS_MUTAGEN = False


class TestLrcParserAndSerializer(unittest.TestCase):
    def test_parse_lrc_formats_and_multiple_timestamps(self) -> None:
        raw = (
            "[ar:Daft Punk]\n"
            "[ti:Around the World]\n"
            "[00:20.50][00:05.100]Around the world\n"
            "[01:02]Second line\n"
            "[00:10.5]Half second\n"
        )
        lines = parse_lrc(raw)
        self.assertEqual(
            lines,
            [
                LyricLine(5100, "Around the world"),
                LyricLine(10500, "Half second"),
                LyricLine(20500, "Around the world"),
                LyricLine(62000, "Second line"),
            ],
        )

    def test_serialize_lrc_roundtrip(self) -> None:
        lines = [LyricLine(12340, "Line A"), LyricLine(65000, "Line B")]
        text = serialize_lrc(lines, title="Song", artist="Band", album="LP", duration_ms=180000)
        self.assertIn("[ti:Song]", text)
        self.assertIn("[ar:Band]", text)
        self.assertIn("[length:03:00]", text)
        self.assertEqual(parse_lrc(text), [LyricLine(12340, "Line A"), LyricLine(65000, "Line B")])

    def test_active_line_index(self) -> None:
        lines = [LyricLine(1000, "A"), LyricLine(5000, "B"), LyricLine(9000, "C")]
        self.assertEqual(active_line_index([], 1000), -1)
        self.assertEqual(active_line_index(lines, 500), -1)
        self.assertEqual(active_line_index(lines, 1000), 0)
        self.assertEqual(active_line_index(lines, 4999), 0)
        self.assertEqual(active_line_index(lines, 5000), 1)
        self.assertEqual(active_line_index(lines, 99999), 2)


class TestLocalAndEmbeddedLyrics(unittest.TestCase):
    def test_sidecar_lrc_and_priority_over_embedded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "track.mp3"
            audio.write_bytes(b"")
            if HAS_MUTAGEN:
                ID3().save(str(audio))
                write_lyrics(str(audio), plain_text="Embedded plain")

            res_embed = load_local_lyrics(audio)
            if HAS_MUTAGEN:
                self.assertIsNotNone(res_embed)
                self.assertEqual(res_embed.source, "embedded")
                self.assertEqual(res_embed.plain_text, "Embedded plain")

            saved = save_lrc_file(
                audio,
                LyricsResult(lines=[LyricLine(2000, "Sidecar line")], synced=True),
            )
            self.assertIsNotNone(saved)
            res_lrc = load_local_lyrics(audio)
            self.assertIsNotNone(res_lrc)
            self.assertEqual(res_lrc.source, "local_lrc")
            self.assertTrue(res_lrc.synced)
            self.assertEqual(res_lrc.lines, [LyricLine(2000, "Sidecar line")])

    @unittest.skipUnless(HAS_MUTAGEN, "mutagen requerido")
    def test_id3_uslt_and_sylt_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "song.mp3"
            ID3().save(str(mp3))
            ok = write_lyrics(
                str(mp3),
                plain_text="Hello\nWorld",
                synced_lines=[(1500, "Hello"), (3200, "World")],
            )
            self.assertTrue(ok)
            raw = read_lyrics(str(mp3))
            self.assertEqual(raw["synced"], [(1500, "Hello"), (3200, "World")])
            self.assertEqual(raw["plain"], "Hello\nWorld")

            loaded = load_local_lyrics(mp3)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.source, "embedded")
            self.assertTrue(loaded.synced)
            self.assertEqual(loaded.lines, [LyricLine(1500, "Hello"), LyricLine(3200, "World")])

    def test_vorbis_flac_lyrics_tags_with_mocked_mutagen(self) -> None:
        class FakeVorbisAudio:
            def __init__(self) -> None:
                self.tags: dict[str, list[str]] = {}
                self.saved = False

            def save(self) -> None:
                self.saved = True

        fake = FakeVorbisAudio()
        with tempfile.TemporaryDirectory() as tmp:
            flac = Path(tmp) / "song.flac"
            flac.write_bytes(b"fLaC")
            with patch("mutagen.File", return_value=fake):
                ok = write_lyrics(
                    str(flac),
                    plain_text="Plain text",
                    lrc_text="[00:01.00]Synced line\n",
                )
                self.assertTrue(ok)
                self.assertTrue(fake.saved)
                self.assertEqual(fake.tags["LYRICS"], ["[00:01.00]Synced line\n"])
                self.assertEqual(fake.tags["UNSYNCEDLYRICS"], ["Plain text"])

                read_back = read_lyrics(str(flac))
                self.assertEqual(read_back["lrc"], "[00:01.00]Synced line")
                self.assertEqual(read_back["plain"], "Plain text")


class TestLrclibFetchAndCache(unittest.TestCase):
    def setUp(self) -> None:
        reset_rate_limit()

    def tearDown(self) -> None:
        reset_rate_limit()

    def test_fetch_caches_and_reuses_disk_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyrics_svc, "CACHE_DIR", Path(tmp)):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {
                "syncedLyrics": "[00:04.00]First\n[00:08.50]Second",
                "plainLyrics": "First\nSecond",
                "instrumental": False,
            }
            track = SimpleNamespace(name="Song", artist="Artist", album="Album", duration_ms=120000)

            with patch("services.lyrics.requests.get", return_value=resp) as mock_get:
                res1 = fetch_lyrics(track)
                self.assertIsNotNone(res1)
                self.assertEqual(res1.source, "lrclib")
                self.assertTrue(res1.synced)
                self.assertEqual(len(res1.lines), 2)
                self.assertEqual(mock_get.call_count, 1)

                res2 = fetch_lyrics(track)
                self.assertIsNotNone(res2)
                self.assertEqual(res2.source, "cache")
                self.assertEqual(res2.lines, res1.lines)
                self.assertEqual(mock_get.call_count, 1)

    def test_fetch_respects_retry_after_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyrics_svc, "CACHE_DIR", Path(tmp)):
            resp_429 = MagicMock()
            resp_429.status_code = 429
            resp_429.headers = {"Retry-After": "30"}

            with patch("services.lyrics.requests.get", return_value=resp_429) as mock_get, \
                 patch("services.lyrics.time.monotonic", side_effect=[100.0, 100.0, 110.0, 131.0, 131.0]):
                self.assertIsNone(fetch_lyrics(title="A", artist="B"))
                # Dentro de la ventana Retry-After: no debe llamar a HTTP
                self.assertIsNone(fetch_lyrics(title="C", artist="D"))
                self.assertEqual(mock_get.call_count, 1)
                # Pasados 30s vuelve a intentar
                self.assertIsNone(fetch_lyrics(title="C", artist="D"))
                self.assertEqual(mock_get.call_count, 2)

    def test_fetch_handles_404_and_network_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyrics_svc, "CACHE_DIR", Path(tmp)):
            resp_404 = MagicMock()
            resp_404.status_code = 404
            with patch("services.lyrics.requests.get", return_value=resp_404) as mock_get:
                self.assertIsNone(fetch_lyrics(title="Missing", artist="Artist"))
                # Negative cache evita segunda petición
                self.assertIsNone(fetch_lyrics(title="Missing", artist="Artist"))
                self.assertEqual(mock_get.call_count, 1)

            with patch("services.lyrics.requests.get", side_effect=requests.Timeout("timeout")):
                self.assertIsNone(fetch_lyrics(title="NetErr", artist="Artist", use_cache=False))

    def test_get_lyrics_offline_and_allow_network_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(lyrics_svc, "CACHE_DIR", Path(tmp)):
            with patch("services.lyrics.requests.get") as mock_get:
                self.assertIsNone(get_lyrics(title="Song", artist="Artist", allow_network=False))
                mock_get.assert_not_called()


class TestDownloaderOptInLyrics(unittest.TestCase):
    @unittest.skipUnless(HAS_MUTAGEN, "mutagen requerido")
    def test_apply_lyrics_saves_lrc_and_embeds_tags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "Artist - Song.mp3"
            ID3().save(str(mp3))
            lyrics = LyricsResult(
                lines=[LyricLine(1000, "Line one")],
                plain_text="Line one",
                synced=True,
            )
            _apply_lyrics(
                SimpleNamespace(name="Song", artist="Artist"),
                mp3,
                lambda _t: lyrics,
                save_lrc=True,
                embed_lyrics=True,
            )
            lrc = mp3.with_suffix(".lrc")
            self.assertTrue(lrc.is_file())
            self.assertIn("[00:01.00]Line one", lrc.read_text(encoding="utf-8"))
            self.assertEqual(read_lyrics(str(mp3))["synced"], [(1000, "Line one")])

    def test_apply_lyrics_never_raises_on_missing_or_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / "song.mp3"
            mp3.write_bytes(b"")
            _apply_lyrics(None, mp3, lambda _t: None, save_lrc=True, embed_lyrics=True)
            _apply_lyrics(
                None,
                mp3,
                lambda _t: (_ for _ in ()).throw(RuntimeError("boom")),
                save_lrc=True,
                embed_lyrics=True,
            )
            self.assertFalse(mp3.with_suffix(".lrc").exists())


if __name__ == "__main__":
    unittest.main()
