"""
tests/test_playback.py — Pruebas unitarias de PlayQueue (stdlib unittest)
════════════════════════════════════════════════════════════════════════
Sin Flet y sin audio: valida aleatorio por identidad, modos de repetición,
eliminación, reordenamiento e historial de reproducción.
"""

from __future__ import annotations

import random
import subprocess
import sys
import unittest
from pathlib import Path

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

    def test_init_creates_single_audio_service_without_src(self) -> None:
        from types import SimpleNamespace
        import flet_audio as fta
        from ui.player_bar import PlayerBar

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
        from ui.player_bar import PlayerBar

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
        from ui.player_bar import PlayerBar

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


if __name__ == "__main__":
    unittest.main()
