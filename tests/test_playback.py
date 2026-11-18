"""
tests/test_playback.py — Pruebas unitarias de PlayQueue (stdlib unittest)
════════════════════════════════════════════════════════════════════════
Sin Flet y sin audio: valida aleatorio por identidad, modos de repetición,
eliminación, reordenamiento e historial de reproducción.
"""

from __future__ import annotations

import asyncio
import random
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.models import Track
from core.playback import PlayableTrack, PlayQueue, RepeatMode


def _make_item(idx: int, name: str | None = None) -> PlayableTrack:
    label = name or f"Track {idx}"
    tr = Track(
        id=f"id_{idx}",
        name=label,
        artist=f"Artist {idx}",
        album="Album",
        duration="3:00",
        img_url="",
        platform="local",
        duration_ms=180_000,
        source_path=f"/tmp/track_{idx}.flac",
    )
    return PlayableTrack(
        track=tr,
        path=tr.source_path,
        container="flac",
        codec="flac",
        duration_ms=180_000,
        bitrate_kbps=900,
        sample_rate=44100,
    )


class TestPlayQueue(unittest.TestCase):
    def setUp(self) -> None:
        self.items = [_make_item(i) for i in range(5)]

    def test_no_flet_imported_by_playback(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-c", "import core.playback, sys; assert 'flet' not in sys.modules"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)

    def test_shuffle_twice_preserves_current_and_restores_original_order_by_identity(self) -> None:
        # Dos instancias distintas con los mismos metadatos para verificar identidad (`is`)
        dup_a = _make_item(1, name="Same Song")
        dup_b = _make_item(1, name="Same Song")
        items = [self.items[0], dup_a, self.items[2], dup_b, self.items[4]]
        q = PlayQueue(items, rng=random.Random(42))

        q.select(3)
        self.assertIs(q.current, dup_b)

        # Primera pasada: activar y desactivar aleatorio
        q.set_shuffle(True)
        self.assertEqual(q.pos, 0)
        self.assertIs(q.current, dup_b)
        q.set_shuffle(False)
        self.assertIs(q.current, dup_b)
        self.assertEqual(q.pos, 3)
        for a, b in zip(q.ordered_tracks(), items):
            self.assertIs(a, b)

        # Segunda pasada: activar y desactivar aleatorio de nuevo
        q.set_shuffle(True)
        self.assertEqual(q.pos, 0)
        self.assertIs(q.current, dup_b)
        q.set_shuffle(False)
        self.assertIs(q.current, dup_b)
        self.assertEqual(q.pos, 3)
        for a, b in zip(q.ordered_tracks(), items):
            self.assertIs(a, b)

    def test_next_and_prev_repeat_off(self) -> None:
        q = PlayQueue(self.items[:3], repeat=RepeatMode.OFF)
        self.assertIs(q.current, self.items[0])
        self.assertIs(q.next(), self.items[1])
        self.assertIs(q.next(), self.items[2])
        # Al llegar al final con OFF termina
        self.assertIsNone(q.next())
        self.assertIsNone(q.current)
        self.assertEqual(q.pos, -1)
        # prev() desde estado detenido recupera la última reproducida del historial
        self.assertIs(q.prev(), self.items[2])

    def test_next_and_prev_repeat_all(self) -> None:
        q = PlayQueue(self.items[:3], repeat=RepeatMode.ALL)
        # prev en pos=0 sin historial envuelve al final con ALL
        self.assertIs(q.prev(), self.items[2])
        # next en el final envuelve a 0 con ALL
        self.assertIs(q.next(), self.items[0])
        self.assertEqual(q.pos, 0)

    def test_next_and_prev_repeat_one(self) -> None:
        q = PlayQueue(self.items[:3], repeat=RepeatMode.ONE)
        q.select(2)
        self.assertIs(q.next(), self.items[2])
        self.assertEqual(q.pos, 2)
        self.assertIs(q.prev(), self.items[2])
        self.assertEqual(q.pos, 2)

    def test_prev_restarts_when_elapsed_over_3s(self) -> None:
        q = PlayQueue(self.items[:3])
        q.next()
        self.assertIs(q.current, self.items[1])
        # Más de 3 s reinicia el tema actual sin retroceder de pista
        self.assertIs(q.prev(elapsed_ms=3500), self.items[1])
        self.assertEqual(q.pos, 1)
        # 3 s o menos retrocede a la pista anterior
        self.assertIs(q.prev(elapsed_ms=1200), self.items[0])
        self.assertEqual(q.pos, 0)

    def test_remove_other_current_and_last_remaining(self) -> None:
        q = PlayQueue(self.items[:4], repeat=RepeatMode.OFF)
        q.select(1)  # actual: items[1]
        self.assertIs(q.current, self.items[1])

        # 1) Eliminar una pista anterior a la actual conserva la identidad de la actual
        res = q.remove(0)
        self.assertIs(res, self.items[1])
        self.assertEqual(q.pos, 0)
        self.assertEqual(len(q.ordered_tracks()), 3)

        # 2) Eliminar una pista posterior a la actual conserva la actual
        res = q.remove(2)
        self.assertIs(res, self.items[1])
        self.assertEqual(q.pos, 0)
        self.assertEqual(len(q.ordered_tracks()), 2)

        # 3) Eliminar la pista actual avanza a la siguiente disponible
        res = q.remove(0)
        self.assertIs(res, self.items[2])
        self.assertEqual(q.pos, 0)
        self.assertEqual(len(q.ordered_tracks()), 1)

        # 4) Eliminar la última que queda detiene la cola sin excepción
        res = q.remove(0)
        self.assertIsNone(res)
        self.assertIsNone(q.current)
        self.assertEqual(q.pos, -1)
        self.assertEqual(q.ordered_tracks(), [])

    def test_move_preserves_current_identity(self) -> None:
        q = PlayQueue(self.items)
        q.select(2)  # actual: items[2]
        cur = q.current
        self.assertIs(cur, self.items[2])

        # Mover una pista de antes hacia después de la actual
        q.move(0, 3)
        self.assertIs(q.current, cur)
        self.assertEqual(q.pos, 1)

        # Mover la pista actual directamente a otra posición
        q.move(q.pos, 4)
        self.assertIs(q.current, cur)
        self.assertEqual(q.pos, 4)

    def test_history_returns_to_actually_played_track_with_shuffle(self) -> None:
        q = PlayQueue(self.items, shuffle=True, rng=random.Random(7))
        played_first = q.current
        self.assertIsNotNone(played_first)

        played_second = q.next()
        self.assertIsNotNone(played_second)
        self.assertIsNot(played_first, played_second)

        played_third = q.next()
        self.assertIsNotNone(played_third)

        # Reordenar o volver a barajar mientras tanto no rompe el historial real
        q.set_shuffle(True)
        self.assertIs(q.current, played_third)

        back_one = q.prev()
        self.assertIs(back_one, played_second)

        back_two = q.prev()
        self.assertIs(back_two, played_first)


class TestPlayerBarEnsureAudio(unittest.TestCase):
    """Valida el ciclo de vida simplificado de ``PlayerBar._ensure_audio`` (Flet 1.1.2)."""

    def setUp(self) -> None:
        import tempfile
        from unittest.mock import patch
        from services import settings_store

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
        from services import settings_store

        self._p2.stop()
        self._p1.stop()
        settings_store._CACHE = None
        self._tmp.cleanup()

    def test_init_creates_single_audio_service_without_src(self) -> None:
        from types import SimpleNamespace
        import flet_audio as fta
        from ui.player.player_bar import PlayerBar

        page = SimpleNamespace(services=[])
        bar = PlayerBar(page)

        self.assertIsNotNone(bar.audio)
        self.assertIsInstance(bar.audio, fta.Audio)
        self.assertIsNone(bar.audio.src)
        self.assertFalse(bar.audio.autoplay)
        self.assertEqual(len(page.services), 1)
        self.assertIs(page.services[0], bar.audio)

        # Llamar de nuevo sin src reutiliza la misma instancia sin duplicados
        same = bar._ensure_audio()
        self.assertIs(same, bar.audio)
        self.assertEqual(len(page.services), 1)

    def test_ensure_audio_updates_src_and_volume_in_place(self) -> None:
        from types import SimpleNamespace
        from ui.player.player_bar import PlayerBar

        page = SimpleNamespace(services=[])
        bar = PlayerBar(page)
        initial_audio = bar.audio
        bar._volume = 0.65
        bar._muted = False

        first = bar._ensure_audio("/tmp/track_a.flac")
        self.assertIs(first, initial_audio)
        self.assertEqual(first.src, "/tmp/track_a.flac")
        self.assertAlmostEqual(float(first.volume), 0.65)
        self.assertEqual(len(page.services), 1)

        bar._muted = True
        second = bar._ensure_audio("/tmp/track_b.flac")
        self.assertIs(second, initial_audio)
        self.assertEqual(second.src, "/tmp/track_b.flac")
        self.assertAlmostEqual(float(second.volume), 0.0)
        self.assertEqual(len(page.services), 1)

    def test_load_and_play_current_uses_same_service_and_plays_from_zero(self) -> None:
        import tempfile
        from types import SimpleNamespace
        from ui.player.player_bar import PlayerBar

        page = SimpleNamespace(services=[])
        bar = PlayerBar(page)
        play_calls: list[tuple[object, tuple]] = []

        def _capture_schedule(fn, *args, **kwargs) -> None:
            play_calls.append((fn, args))

        bar._schedule_audio = _capture_schedule  # type: ignore[method-assign]

        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "one.flac"
            p2 = Path(tmpdir) / "two.flac"
            p1.write_bytes(b"RIFF")
            p2.write_bytes(b"RIFF")

            t1 = _make_item(1)
            t2 = _make_item(2)
            t1 = PlayableTrack(
                track=t1.track,
                path=str(p1),
                container="flac",
                codec="flac",
                duration_ms=180_000,
                bitrate_kbps=900,
                sample_rate=44100,
            )
            t2 = PlayableTrack(
                track=t2.track,
                path=str(p2),
                container="flac",
                codec="flac",
                duration_ms=180_000,
                bitrate_kbps=900,
                sample_rate=44100,
            )

            bar.queue.set_tracks([t1, t2], start_index=0)
            bar._load_and_play_current()
            self.assertEqual(bar.audio.src, str(p1.resolve()))
            self.assertEqual(len(play_calls), 1)
            self.assertEqual(play_calls[0], (bar.audio.play, (0,)))

            bar.queue.next()
            bar._load_and_play_current()
            self.assertEqual(bar.audio.src, str(p2.resolve()))
            self.assertEqual(len(play_calls), 2)
            self.assertEqual(play_calls[1], (bar.audio.play, (0,)))
            self.assertEqual(len(page.services), 1)

    def test_play_stream_and_resume_on_open_persistence(self) -> None:
        import tempfile
        from types import SimpleNamespace
        from unittest.mock import patch
        from core.playback import build_stream_playable_track
        from services import settings_store
        from ui.player.player_bar import PlayerBar

        tr = Track(
            id="yt1",
            name="Remote Song",
            artist="Remote Artist",
            album="Remote Album",
            duration="3:00",
            img_url="",
            platform="YouTube Music",
            duration_ms=180_000,
        )
        stream_pt = build_stream_playable_track(
            "https://example.com/audio.webm",
            tr,
            {"ext": "webm", "acodec": "opus", "abr": 128, "asr": 48000},
        )
        self.assertIsNotNone(stream_pt)
        self.assertTrue(stream_pt.is_stream)

        page = SimpleNamespace(services=[])
        bar = PlayerBar(page)
        play_calls: list[tuple[object, tuple]] = []
        bar._schedule_audio = lambda fn, *args, **kwargs: play_calls.append((fn, args))  # type: ignore[method-assign]

        self.assertTrue(bar.play_stream("https://example.com/audio.webm", tr))
        self.assertEqual(bar.audio.src, "https://example.com/audio.webm")
        self.assertEqual(play_calls[-1], (bar.audio.play, (0,)))

        with tempfile.TemporaryDirectory() as tmpdir:
            audio_file = Path(tmpdir) / "saved.mp3"
            audio_file.write_bytes(b"ID3")
            settings_store.set_fade_seconds(2)
            self.assertEqual(settings_store.get_fade_seconds(), 2)
            settings_store.set_stream_without_download(False)
            self.assertFalse(settings_store.get_stream_without_download())
            settings_store.set_resume_on_open(True)
            self.assertTrue(settings_store.get_resume_on_open())

            bar.play_file(str(audio_file))
            bar._position_ms = 42_000
            bar._persist_playback_state()

            bar2 = PlayerBar(SimpleNamespace(services=[]))
            bar2._schedule_audio = lambda fn, *args, **kwargs: None  # type: ignore[method-assign]
            self.assertTrue(bar2.restore_last_playback())
            self.assertIsNotNone(bar2.queue.current)
            self.assertEqual(bar2.position_ms, 42_000)
            self.assertEqual(bar2._pending_seek_ms, 42_000)

    def test_resolve_stream_info_uses_ytdlp_without_download(self) -> None:
        from unittest.mock import patch
        from core.config import (
            YTDLP_STREAM_FORMAT_SELECTOR,
            YTDLP_STREAM_PLAYER_CLIENTS,
        )
        from engine.download_search import clear_stream_cache, resolve_stream_info

        clear_stream_cache()
        captured: dict = {}

        class _FakeYDL:
            def __init__(self, opts):
                captured["opts"] = opts
                self.cookiejar = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                captured["url"] = url
                captured["download"] = download
                captured["cookiejar"] = self.cookiejar
                return {
                    "url": "https://cdn.example/stream.opus",
                    "ext": "webm",
                    "acodec": "opus",
                    "abr": 160,
                    "asr": 48000,
                    "duration": 185,
                }

        tr = Track(
            id="yt9",
            name="Test Song",
            artist="Test Artist",
            album="",
            duration="3:05",
            img_url="",
            platform="YouTube Music",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        with (
            patch("engine.download_search.yt_dlp.YoutubeDL", _FakeYDL),
            patch("engine.download_search._load_cookie_jar", return_value=object()),
            patch("engine.download_search._verify_stream_url", return_value=True),
        ):
            res = resolve_stream_info(tr)
        self.assertEqual(res["error"], "")
        self.assertEqual(res["url"], "https://cdn.example/stream.opus")
        self.assertFalse(captured["download"])
        self.assertIsNone(captured["cookiejar"])
        self.assertEqual(captured["opts"]["format"], YTDLP_STREAM_FORMAT_SELECTOR)
        self.assertEqual(
            captured["opts"]["extractor_args"]["youtube"]["player_client"],
            list(YTDLP_STREAM_PLAYER_CLIENTS),
        )
        clear_stream_cache()

    def test_resolve_stream_info_fallbacks_and_ui_hides_stream_badge(self) -> None:
        from unittest.mock import patch
        from core.playback import build_pending_stream_track, build_stream_playable_track
        from engine.download_search import (
            clear_stream_cache,
            invalidate_stream_cache,
            resolve_stream_info,
        )
        from ui.player.player_queue import build_queue_row

        clear_stream_cache()
        attempts: list[tuple[list[str], bool]] = []
        fake_jar = object()

        class _FallbackYDL:
            def __init__(self, opts):
                self._clients = opts["extractor_args"]["youtube"]["player_client"]
                self.cookiejar = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                attempts.append((list(self._clients), self.cookiejar is not None))
                # Falla el cliente primario anónimo y el secundario anónimo;
                # solo resuelve al caer al intento con cookies.
                if self.cookiejar is None:
                    raise RuntimeError("Requested format is not available")
                return {
                    "url": "https://cdn.example/auth-audio.m4a",
                    "ext": "m4a",
                    "acodec": "mp4a.40.2",
                    "duration": 180,
                }

        tr = Track(
            id="yt_fb",
            name="Fallback Song",
            artist="Artist",
            album="",
            duration="3:00",
            img_url="",
            platform="YouTube Music",
            source_url="https://www.youtube.com/watch?v=9bZkp7q19f0",
        )
        with (
            patch("engine.download_search.yt_dlp.YoutubeDL", _FallbackYDL),
            patch("engine.download_search._load_cookie_jar", return_value=fake_jar),
            patch("engine.download_search._verify_stream_url", return_value=True),
        ):
            res = resolve_stream_info(tr)
            # Segunda llamada usa caché en memoria sin tocar yt-dlp.
            res_cached = resolve_stream_info(tr)
            self.assertEqual(len(attempts), 3)
            self.assertFalse(attempts[0][1])
            self.assertFalse(attempts[1][1])
            self.assertTrue(attempts[2][1])
            self.assertEqual(res["url"], "https://cdn.example/auth-audio.m4a")
            self.assertEqual(res_cached["url"], res["url"])

            invalidate_stream_cache(tr)
            resolve_stream_info(tr)
            self.assertEqual(len(attempts), 6)

        # La verificación rechaza 403 y prueba el siguiente intento.
        clear_stream_cache()
        verify_calls: list[str] = []

        class _MultiYDL:
            def __init__(self, opts):
                self._clients = opts["extractor_args"]["youtube"]["player_client"]
                self.cookiejar = None

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                if "android_vr" in self._clients:
                    return {"url": "https://cdn.example/forbidden.m4a", "ext": "m4a", "duration": 180}
                return {"url": "https://cdn.example/ok18.mp4", "ext": "mp4", "duration": 180}

        def _fake_verify(url: str) -> bool:
            verify_calls.append(url)
            return "ok18" in url

        with (
            patch("engine.download_search.yt_dlp.YoutubeDL", _MultiYDL),
            patch("engine.download_search._load_cookie_jar", return_value=None),
            patch("engine.download_search._verify_stream_url", side_effect=_fake_verify),
        ):
            res_v = resolve_stream_info(tr)
        self.assertEqual(res_v["url"], "https://cdn.example/ok18.mp4")
        self.assertEqual(verify_calls, ["https://cdn.example/forbidden.m4a", "https://cdn.example/ok18.mp4"])
        clear_stream_cache()

        # UI: el badge de formato se oculta para pistas de streaming (no muestra "STREAM").
        import flet as ft
        from ui.player.player_bar import PlayerBar

        bar = PlayerBar(ft.Page)
        pending = build_pending_stream_track(tr)
        bar.queue.set_tracks([pending], start_index=0)
        bar._sync_ui()
        self.assertFalse(bar._badge_box.visible)
        stream_pt = build_stream_playable_track("https://cdn.example/ok.m4a", tr, {"ext": "m4a", "acodec": "mp4a.40.2"})
        self.assertIsNotNone(stream_pt)
        bar.queue.set_tracks([stream_pt], start_index=0)
        bar._sync_ui()
        self.assertFalse(bar._badge_box.visible)

        qrow_stream = build_queue_row(0, pending, True, bar, reorderable=False)
        self.assertEqual(len(qrow_stream.content.controls), 4)  # lead, thumb, meta, remove (sin chip STREAM)
        bar.release()


class TestPlayerBarLifecycleAndEdgeCases(unittest.IsolatedAsyncioTestCase):
    """Cubre crossfade, ticker, pending seek, pistas ausentes, streaming y errores."""

    def setUp(self) -> None:
        import tempfile
        from unittest.mock import patch
        from services import settings_store

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
        from services import settings_store

        self._p2.stop()
        self._p1.stop()
        settings_store._CACHE = None
        self._tmp.cleanup()

    def _make_bar(self):
        from types import SimpleNamespace
        from ui.player.player_bar import PlayerBar

        page = SimpleNamespace(services=[], overlay=[], update=lambda: None)
        bar = PlayerBar(page)
        calls: list[tuple[object, tuple]] = []
        bar._schedule_audio = lambda fn, *args, **kwargs: calls.append((fn, args))  # type: ignore[method-assign]
        return bar, calls

    def _make_files(self, count: int = 2) -> list[PlayableTrack]:
        items: list[PlayableTrack] = []
        for i in range(count):
            p = Path(self._tmp.name) / f"song_{i}.flac"
            p.write_bytes(b"RIFF")
            tr = Track(
                id=f"f_{i}",
                name=f"Song {i}",
                artist=f"Artist {i}",
                album="Album",
                duration="3:00",
                img_url="",
                platform="local",
                duration_ms=180_000,
                source_path=str(p),
            )
            items.append(
                PlayableTrack(
                    track=tr,
                    path=str(p),
                    container="flac",
                    codec="flac",
                    duration_ms=180_000,
                )
            )
        return items

    async def test_cancel_crossfade_pauses_outgoing_and_stop_releases_both(self) -> None:
        bar, calls = self._make_bar()
        items = self._make_files(2)
        bar.queue.set_tracks(items, start_index=0)
        bar._load_and_play_current()
        first_deck = bar.audio
        calls.clear()

        self.assertTrue(bar._begin_crossfade_to_next(2))
        outgoing = bar._xfade_audio
        self.assertIs(outgoing, first_deck)
        self.assertIsNot(bar.audio, outgoing)

        # Pausar a mitad del fundido debe pausar el deck saliente inmediatamente
        bar._on_play_pause_click()
        paused_fns = [fn for fn, _ in calls]
        self.assertIn(outgoing.pause, paused_fns)
        self.assertFalse(bar._crossfade_triggered)

        # Limpiar cola libera ambos decks
        calls.clear()
        bar.clear_queue()
        released_fns = [fn for fn, _ in calls]
        self.assertIn(bar.audio.release, released_fns)
        self.assertIn(outgoing.release, released_fns)
        bar.release()

    async def test_position_events_do_not_fight_ticker_and_backsteps_are_ignored(self) -> None:
        import flet_audio as fta

        bar, _calls = self._make_bar()
        bar._audio_state = fta.AudioState.PLAYING
        bar._position_ms = 2000
        bar._ticker_task = asyncio.create_task(asyncio.sleep(10))
        try:
            # Con el ticker vivo, un evento atrasado del plugin no mueve el contador
            bar._on_position_change(SimpleNamespace(control=None, position=1000))
            self.assertEqual(bar._position_ms, 2000)
        finally:
            bar._ticker_task.cancel()
            bar._ticker_task = None

        # Sin ticker, el evento sí actualiza
        bar._on_position_change(SimpleNamespace(control=None, position=3000))
        self.assertEqual(bar._position_ms, 3000)

        # Lecturas que retroceden menos de 1.5 s son jitter; un salto grande es un seek
        self.assertFalse(bar._accept_polled_position(2200))
        self.assertTrue(bar._accept_polled_position(3500))
        self.assertTrue(bar._accept_polled_position(500))

    async def test_play_tracks_queues_full_playlist_and_resolves_pending_lazily(self) -> None:
        from unittest.mock import patch

        bar, _calls = self._make_bar()
        files = self._make_files(2)
        t_remote = Track(
            "r1", "Remote", "Artist", "", "3:00", "", "Spotify", duration_ms=180_000,
        )
        resolved: list[str] = []

        def _fake_resolve(tr):
            resolved.append(tr.id)
            return {"url": "https://cdn.example/r1.webm", "ext": "webm", "acodec": "opus", "error": ""}

        with patch("ui.player.player_bar.resolve_stream_info", side_effect=_fake_resolve):
            n = bar.play_tracks(
                [files[0].track, t_remote, files[1].track], start_index=0, include_remote=True,
            )
            self.assertEqual(n, 3)
            self.assertEqual(len(bar.queue.order), 3)
            self.assertTrue(bar.queue.tracks[1].is_pending)
            self.assertEqual(resolved, [])  # nada se resuelve hasta que toca sonar

            bar._on_next_click()
            await asyncio.sleep(0.1)

        self.assertEqual(resolved, ["r1"])
        self.assertFalse(bar.queue.current.is_pending)
        self.assertEqual(bar.audio.src, "https://cdn.example/r1.webm")
        self.assertEqual(len(bar.queue.order), 3)

    async def test_unresolvable_remote_is_skipped_to_next_track(self) -> None:
        from unittest.mock import patch

        bar, _calls = self._make_bar()
        files = self._make_files(1)
        t_remote = Track("r2", "Broken", "Artist", "", "3:00", "", "Spotify")

        with patch(
            "ui.player.player_bar.resolve_stream_info",
            return_value={"url": "", "error": "sin resultados"},
        ):
            bar.play_tracks([t_remote, files[0].track], include_remote=True)
            await asyncio.sleep(0.1)

        self.assertIs(bar.queue.current.track, files[0].track)
        self.assertEqual(bar.audio.src, files[0].path)

    async def test_last_track_not_cut_off_by_crossfade_and_ticker_stays_alive(self) -> None:
        import flet_audio as fta
        from services import settings_store

        settings_store.set_fade_seconds(3)
        bar, _calls = self._make_bar()
        items = self._make_files(2)
        bar.queue.set_tracks(items, start_index=0)

        # Sin repetir en la última pista: has_next() es False y _begin_crossfade_to_next devuelve False
        bar.queue.select(1)
        self.assertFalse(bar.queue.has_next())
        bar._load_and_play_current()
        self.assertFalse(bar._begin_crossfade_to_next(3))
        self.assertIs(bar.queue.current, items[1])

        # Con pista siguiente: el ticker dispara crossfade y NO muere (sigue vivo en el nuevo deck)
        bar.queue.select(0)
        bar._load_and_play_current()
        bar.audio.get_current_position = lambda: asyncio.sleep(0, result=177_500)  # type: ignore[method-assign]
        bar.audio.get_duration = lambda: asyncio.sleep(0, result=180_000)  # type: ignore[method-assign]

        # Asegurar un segundo ciclo después del crossfade
        await asyncio.sleep(0.35)
        self.assertIs(bar.queue.current, items[1])
        self.assertEqual(bar._audio_state, fta.AudioState.PLAYING)
        self.assertIsNotNone(bar._ticker_task)
        self.assertFalse(bar._ticker_task.done())
        bar.release()

    def test_pending_seek_cleared_by_seek_to_and_prev_and_zero_restore(self) -> None:
        from services import settings_store

        settings_store.set_resume_on_open(True)
        items = self._make_files(2)
        settings_store.set_last_playback({
            "path": items[0].path,
            "position_ms": 42_000,
            "queue_paths": [items[0].path, items[1].path],
        })

        bar, calls = self._make_bar()
        self.assertTrue(bar.restore_last_playback())
        self.assertEqual(bar._pending_seek_ms, 42_000)
        bar._duration_ms = 180_000

        # Seek mientras está pausado tras restaurar actualiza pending seek a 10s
        bar.seek_to(10_000)
        self.assertEqual(bar._pending_seek_ms, 10_000)
        bar._on_play_pause_click()  # Reanuda -> play(10_000)
        self.assertEqual(calls[-1], (bar.audio.play, (10_000,)))
        self.assertIsNone(bar._pending_seek_ms)

        # Pausar y reanudar otra vez no vuelve a 42s
        calls.clear()
        bar._on_play_pause_click()
        bar._on_play_pause_click()
        self.assertEqual(calls[-1], (bar.audio.resume, ()))

        # Restaurar en posición 0 usa play(0) en el primer play (no resume())
        settings_store.set_last_playback({
            "path": items[0].path,
            "position_ms": 0,
            "queue_paths": [items[0].path],
        })
        bar2, calls2 = self._make_bar()
        self.assertTrue(bar2.restore_last_playback())
        self.assertEqual(bar2._pending_seek_ms, 0)
        bar2._on_play_pause_click()
        self.assertEqual(calls2[-1], (bar2.audio.play, (0,)))

        # Prev limpia pending seek
        bar3, calls3 = self._make_bar()
        settings_store.set_last_playback({
            "path": items[0].path,
            "position_ms": 42_000,
            "queue_paths": [items[0].path],
        })
        bar3.restore_last_playback()
        bar3._on_prev_click()
        self.assertIsNone(bar3._pending_seek_ms)
        self.assertEqual(calls3[-1], (bar3.audio.play, (0,)))

    def test_restore_with_corrupt_settings_and_fade_zero_resume_volume(self) -> None:
        from services import settings_store

        settings_store.set_resume_on_open(True)
        items = self._make_files(1)
        settings_store.set_last_playback({
            "path": items[0].path,
            "position_ms": "corrupto",
            "queue_paths": "not-a-list",
        })
        bar, _calls = self._make_bar()
        self.assertTrue(bar.restore_last_playback())
        self.assertIs(bar.queue.current.path, bar.queue.tracks[0].path)
        self.assertEqual(bar.position_ms, 0)

        # Cambiar fundido a 0 entre pausa y reanudación restaura volumen completo
        settings_store.set_fade_seconds(2)
        bar._on_play_pause_click()
        bar.audio.volume = 0.25  # simula volumen parcial tras fade-out interrumpido
        bar._on_play_pause_click()
        settings_store.set_fade_seconds(0)
        bar._on_play_pause_click()
        self.assertAlmostEqual(float(bar.audio.volume), bar._volume)

    def test_missing_file_skips_to_next_or_stops_cleanly(self) -> None:
        import flet_audio as fta

        bar, calls = self._make_bar()
        items = self._make_files(2)
        Path(items[0].path).unlink()  # primera pista borrada en disco
        bar.queue.set_tracks(items, start_index=0)
        bar._load_and_play_current()
        # Debe saltar a la segunda pista válida
        self.assertIs(bar.queue.current, items[1])
        self.assertEqual(bar._audio_state, fta.AudioState.PLAYING)

        # Si todas faltan (incluso con RepeatMode.ALL), se detiene sin bucle infinito
        Path(items[1].path).unlink()
        bar.queue.repeat = RepeatMode.ALL
        calls.clear()
        bar._load_and_play_current()
        self.assertEqual(bar._audio_state, fta.AudioState.STOPPED)
        self.assertIn(bar.audio.release, [fn for fn, _ in calls])

    async def test_stream_expiry_and_remote_race_and_search_fallback(self) -> None:
        import time
        from unittest.mock import patch
        from core.playback import build_stream_playable_track
        from engine.download_search import (
            clear_stream_cache,
            resolve_stream_info,
            search_candidates,
        )
        clear_stream_cache()

        tr = Track(
            id="s1",
            name="Test Song",
            artist="Test Artist",
            album="",
            duration="3:00",
            img_url="",
            platform="Spotify",
            duration_ms=180_000,
        )
        expired_pt = build_stream_playable_track(
            f"https://cdn.example/videoplayback?expire={int(time.time()) - 10}",
            tr,
        )
        self.assertIsNotNone(expired_pt)
        self.assertTrue(expired_pt.is_stream_expired())

        fresh_pt = build_stream_playable_track(
            f"https://cdn.example/videoplayback?expire={int(time.time()) + 7200}",
            tr,
        )
        self.assertIsNotNone(fresh_pt)
        self.assertFalse(fresh_pt.is_stream_expired())

        # Dos clics rápidos en play_remote_track: solo el último gana
        bar, _calls = self._make_bar()
        tr2 = Track(id="s2", name="Second", artist="Artist", album="", duration="3:00", img_url="", platform="Spotify")

        def _fake_resolve(track_arg):
            if track_arg.id == "s1":
                time.sleep(0.08)
                return {"url": "https://cdn.example/first.opus", "ext": "webm", "acodec": "opus", "error": ""}
            return {"url": "https://cdn.example/second.opus", "ext": "webm", "acodec": "opus", "error": ""}

        with patch("ui.player.player_bar.resolve_stream_info", side_effect=_fake_resolve):
            t1 = asyncio.create_task(bar.play_remote_track(tr))
            await asyncio.sleep(0.01)
            t2 = asyncio.create_task(bar.play_remote_track(tr2))
            r1, r2 = await asyncio.gather(t1, t2)
        self.assertFalse(r1)
        self.assertTrue(r2)
        self.assertEqual(bar.audio.src, "https://cdn.example/second.opus")

        # resolve_stream_info rechaza directos en source_url y hace fallback al 2do candidato
        live_tr = Track(
            id="yt_live",
            name="Live",
            artist="Artist",
            album="",
            duration="0:00",
            img_url="",
            platform="YouTube Music",
            source_url="https://www.youtube.com/watch?v=abcdefghijk",
        )

        class _LiveYDL:
            def __init__(self, _opts):
                self.cookiejar = None
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def extract_info(self, url, download=True):
                if "abcdefghijk" in url:
                    return {"is_live": True, "url": "https://cdn.example/live.m3u8"}
                return {"url": "https://cdn.example/fallback.opus", "duration": 180, "title": "Test Song"}

        with patch("engine.download_search.yt_dlp.YoutubeDL", _LiveYDL):
            res_live = resolve_stream_info(live_tr)
        self.assertEqual(res_live["url"], "")
        self.assertIn("Directo", res_live["error"])

        with (
            patch(
                "engine.download_search.search_candidates",
                return_value={
                    "query": "Test Artist Test Song",
                    "candidates": [
                        {"url": "https://www.youtube.com/watch?v=abcdefghijk"},
                        {"url": "https://www.youtube.com/watch?v=12345678901"},
                    ],
                    "error": "",
                },
            ),
            patch("engine.download_search.yt_dlp.YoutubeDL", _LiveYDL),
            patch("engine.download_search._verify_stream_url", return_value=True),
        ):
            res_fb = resolve_stream_info(tr)
        clear_stream_cache()
        self.assertEqual(res_fb["error"], "")
        self.assertEqual(res_fb["url"], "https://cdn.example/fallback.opus")

        # search_candidates con metadatos vacíos retorna error limpio
        empty_tr = Track(id="e1", name="", artist="", album="", duration="0:00", img_url="", platform="Spotify")
        err_res = search_candidates(empty_tr)
        self.assertEqual(err_res["candidates"], [])
        self.assertTrue(err_res["error"])

    def test_crossfade_skips_pending_next_without_mutating_queue(self) -> None:
        import flet_audio as fta
        from core.config import LOCAL_LIBRARY_LABEL
        from core.playback import build_pending_stream_track, is_remote_track

        self.assertFalse(
            is_remote_track(Track(id="loc1", name="Local", artist="A", album="", duration="3:00", img_url="", platform=LOCAL_LIBRARY_LABEL))
        )
        self.assertTrue(
            is_remote_track(Track(id="rem1", name="Remote", artist="A", album="", duration="3:00", img_url="", platform="Spotify"))
        )

        bar, _calls = self._make_bar()
        local_item = self._make_files(1)[0]
        pending_tr = Track(
            id="s_pend", name="Pending", artist="Artist", album="", duration="3:00", img_url="", platform="Spotify", duration_ms=180_000,
        )
        pending_item = build_pending_stream_track(pending_tr)
        bar.queue.set_tracks([local_item, pending_item], start_index=0)
        bar._load_and_play_current()
        bar._audio_state = fta.AudioState.PLAYING

        self.assertIs(bar.queue.peek_next(), pending_item)
        self.assertFalse(bar._begin_crossfade_to_next(3))
        self.assertIs(bar.queue.current, local_item)

    async def test_double_next_cancellation_does_not_reset_new_crossfade_flag(self) -> None:
        import flet_audio as fta
        from services import settings_store

        settings_store.set_fade_seconds(2)
        bar, _calls = self._make_bar()
        items = self._make_files(3)
        bar.queue.set_tracks(items, start_index=0)
        bar._load_and_play_current()
        bar._audio_state = fta.AudioState.PLAYING

        self.assertTrue(bar._begin_crossfade_to_next(2))
        first_task = bar._fade_task
        self.assertTrue(bar._crossfade_triggered)
        self.assertIsNotNone(first_task)

        # Segundo Siguiente inmediato reemplaza la tarea anterior antes de que termine su finally
        bar._on_next_click()
        await asyncio.sleep(0.05)
        self.assertIsNot(bar._fade_task, first_task)
        self.assertTrue(bar._crossfade_triggered)
        bar.release()

    async def test_stream_last_playback_persists_restores_and_respects_sequence_order(self) -> None:
        import flet_audio as fta
        from unittest.mock import patch
        from services import settings_store
        from core.playback import build_stream_playable_track

        settings_store.set_resume_on_open(True)
        bar, _calls = self._make_bar()
        local_item = self._make_files(1)[0]
        stream_tr = Track(
            id="st_res", name="Restorable Stream", artist="Artist", album="Alb",
            duration="3:00", img_url="", platform="Spotify", duration_ms=180_000,
        )
        stream_item = build_stream_playable_track("https://cdn.example/old.opus", stream_tr)
        self.assertIsNotNone(stream_item)
        bar.queue.set_tracks([local_item, stream_item], start_index=1)
        bar._position_ms = 55_000
        bar._persist_playback_state(sync=True)

        saved = settings_store.get_last_playback()
        self.assertEqual(saved.get("position_ms"), 55_000)
        self.assertEqual(len(saved.get("queue_items") or []), 2)
        self.assertEqual(saved.get("current_index"), 1)

        # Restaurar en nueva instancia restaura pista remota como pendiente y resuelve al pulsar Play
        bar2, calls2 = self._make_bar()
        self.assertTrue(bar2.restore_last_playback())
        self.assertEqual(bar2._audio_state, fta.AudioState.PAUSED)
        self.assertIsNotNone(bar2.queue.current)
        self.assertTrue(bar2.queue.current.is_stream)
        self.assertTrue(bar2.queue.current.is_pending)
        self.assertEqual(bar2.position_ms, 55_000)

        with patch(
            "ui.player.player_bar.resolve_stream_info",
            return_value={"url": "https://cdn.example/fresh.opus", "ext": "webm", "acodec": "opus", "duration_ms": 180_000, "error": ""},
        ):
            bar2._on_play_pause_click()
            await asyncio.sleep(0.05)
        self.assertEqual(bar2.audio.src, "https://cdn.example/fresh.opus")
        self.assertEqual(calls2[-1], (bar2.audio.play, (55_000,)))

        # Orden monótono: una escritura más nueva no se pisa por secuencia vieja
        bar2._position_ms = 12_000
        bar2._persist_playback_state(sync=True)
        stale_seq = bar2._persist_seq
        bar2._position_ms = 80_000
        bar2._persist_playback_state(sync=True)
        self.assertGreater(bar2._persist_seq, stale_seq)
        self.assertEqual(settings_store.get_last_playback().get("position_ms"), 80_000)
        bar2.release()

    async def test_stream_retry_resets_on_fresh_load_and_recovers_mid_track_drop(self) -> None:
        import flet_audio as fta
        from unittest.mock import patch
        from core.playback import build_stream_playable_track

        bar, calls = self._make_bar()
        tr = Track(
            id="st_mid", name="Mid Drop", artist="Artist", album="",
            duration="3:00", img_url="", platform="YouTube Music", duration_ms=180_000,
        )
        initial = build_stream_playable_track("https://cdn.example/v1.opus", tr)
        self.assertIsNotNone(initial)
        bar.queue.set_tracks([initial], start_index=0)
        bar._load_and_play_current()
        bar._position_ms = 45_000

        with patch(
            "ui.player.player_bar.resolve_stream_info",
            return_value={"url": "https://cdn.example/v2.opus", "ext": "webm", "acodec": "opus", "duration_ms": 180_000, "error": ""},
        ):
            bar._on_state_change(SimpleNamespace(control=bar.audio, state=fta.AudioState.STOPPED))
            await asyncio.sleep(0.05)

        self.assertEqual(bar.audio.src, "https://cdn.example/v2.opus")
        self.assertEqual(calls[-1], (bar.audio.play, (45_000,)))
        self.assertEqual(bar._stream_retry_count, 1)

        # Segundo fallo consecutivo en la misma carga no reintenta en bucle
        calls.clear()
        bar._position_ms = 50_000
        bar._on_state_change(SimpleNamespace(control=bar.audio, state=fta.AudioState.STOPPED))
        await asyncio.sleep(0.02)
        self.assertEqual(bar._audio_state, fta.AudioState.STOPPED)

        # Una carga nueva (_is_retry=False) resetea el contador incluso para el mismo id(track)
        bar._load_and_play_current()
        self.assertEqual(bar._stream_retry_count, 0)
        bar.release()

    def test_pending_stream_promotes_to_local_when_file_appears_on_disk(self) -> None:
        from unittest.mock import patch
        from core.playback import build_pending_stream_track

        bar, _calls = self._make_bar()
        audio_path = self._make_files(1)[0].path
        tr = Track(
            id="prom_1", name="Downloaded Meanwhile", artist="Artist", album="",
            duration="3:00", img_url="", platform="Spotify", duration_ms=180_000, source_path=audio_path,
        )
        pending = build_pending_stream_track(tr)
        bar.queue.set_tracks([pending], start_index=0)

        with patch("ui.player.player_bar.resolve_stream_info") as mock_resolve:
            bar._load_and_play_current()
        mock_resolve.assert_not_called()
        self.assertIsNotNone(bar.queue.current)
        self.assertFalse(bar.queue.current.is_stream)
        self.assertEqual(bar.audio.src, audio_path)


if __name__ == "__main__":
    unittest.main()
