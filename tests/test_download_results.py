"""
tests/test_download_results.py — Panel de resultados de Descargas (unittest)
════════════════════════════════════════════════════════════════════════
Sin red, sin Flet renderizado y sin yt-dlp real: valida cómo se agrupan las
pistas de la última cola (exitosas, omitidas, fallidas y faltantes) y la
reconstrucción de ``Track`` que hace el reintento desde una entrada del panel.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.models import Track
from core.state import AppState
from engine import download_search, downloader


def _state() -> AppState:
    """``AppState`` sin servicios: el panel y los reintentos no los tocan."""
    return AppState(service=None)


class DownloadMissingTest(unittest.TestCase):
    def test_missing_lists_only_unfinished_tracks(self) -> None:
        st = _state()
        a = Track(id="a", name="A", artist="X", album="", duration="", img_url="", platform="Local")
        b = Track(id="b", name="B", artist="X", album="", duration="", img_url="", platform="Local")
        c = Track(id="c", name="C", artist="X", album="", duration="", img_url="", platform="Local")
        st.download_done = [{"id": "a", "path": "/tmp/a.mp3"}]
        st.download_skipped = []
        st._download_pending_tracks = [b]

        st._collect_download_missing([a, b, c])

        ids = [m["id"] for m in st.download_missing]
        self.assertEqual(ids, ["b", "c"])
        reasons = {m["id"]: m["error"] for m in st.download_missing}
        self.assertEqual(reasons["b"], "Pausada")
        self.assertEqual(reasons["c"], "No se intentó")

    def test_skipped_tracks_do_not_count_as_missing(self) -> None:
        st = _state()
        a = Track(id="a", name="A", artist="", album="", duration="", img_url="", platform="")
        st.download_skipped = [{"id": "a", "path": "/tmp/a.mp3"}]

        st._collect_download_missing([a])

        self.assertEqual(st.download_missing, [])

    def test_empty_queue_clears_missing(self) -> None:
        st = _state()
        st.download_missing = [{"id": "old"}]

        st._collect_download_missing([])

        self.assertEqual(st.download_missing, [])


class RetryDownloadsTest(unittest.TestCase):
    def test_retry_rebuilds_track_from_entry(self) -> None:
        st = _state()
        st.audio_format = "flac"
        created: list[list[Track]] = []

        def _fake_download(tracks, **kwargs):
            created.append(list(tracks))
            return mock.MagicMock()

        with mock.patch.object(st, "download_tracks", side_effect=_fake_download):
            st.retry_downloads([{
                "id": "7", "name": "Song", "artist": "Band", "album": "LP",
                "platform": "Local", "duration_ms": 185000, "isrc": "USABC1234567",
                "error": "boom", "format": "mp3",
            }])

        self.assertEqual(len(created), 1)
        track = created[0][0]
        self.assertEqual((track.id, track.name, track.artist), ("7", "Song", "Band"))
        self.assertEqual(track.duration_ms, 185000)
        self.assertEqual(track.isrc, "USABC1234567")

    def test_retry_uses_attached_track_when_present(self) -> None:
        st = _state()
        track = Track(id="z", name="Z", artist="Y", album="", duration="", img_url="", platform="")
        created: list[list[Track]] = []

        with mock.patch.object(
            st, "download_tracks",
            side_effect=lambda tracks, **kw: created.append(list(tracks)) or mock.MagicMock(),
        ):
            st.retry_downloads([{"_track": track}])

        self.assertIs(created[0][0], track)

    def test_retry_without_entries_returns_none(self) -> None:
        st = _state()
        self.assertIsNone(st.retry_downloads([]))

    def test_retry_honours_alternate_format(self) -> None:
        st = _state()
        st.audio_format = "mp3"
        captured: dict = {}

        def _fake_download(tracks, **kwargs):
            captured.update(kwargs)
            return mock.MagicMock()

        with mock.patch.object(st, "download_tracks", side_effect=_fake_download):
            st.retry_downloads([{"id": "1", "name": "N", "artist": "A"}],
                               audio_format="opus")

        self.assertEqual(captured["audio_format"], "opus")


class SearchCandidatesTest(unittest.TestCase):
    def _entry(self, video_id: str, title: str, duration: int) -> dict:
        return {
            "id": video_id, "title": title, "channel": "Canal",
            "duration": duration, "is_live": False,
        }

    def test_candidates_are_ranked_and_described(self) -> None:
        track = Track(id="t", name="Song", artist="Band", album="", duration="",
                      img_url="", platform="", duration_ms=200000)
        entries = [
            self._entry("aaa", "Song (Official Video)", 200),
            self._entry("bbb", "Song", 199),
            self._entry("ccc", "Song live", 9000),
        ]
        with mock.patch.object(download_search, "_search_entries", return_value=(entries, "")):
            found = downloader.search_candidates(track)

        self.assertEqual(found["error"], "")
        ids = [c["video_id"] for c in found["candidates"]]
        self.assertNotIn("ccc", ids, "un vídeo de 2.5 h debe descartarse")
        self.assertEqual(len(ids), 2)
        self.assertIn("url", found["candidates"][0])
        scores = [c["score"] for c in found["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_no_candidates_reports_reason(self) -> None:
        track = Track(id="t", name="Song", artist="Band", album="", duration="",
                      img_url="", platform="")
        with mock.patch.object(
            download_search, "_search_entries", return_value=([], "Sin resultados en YouTube")
        ):
            found = downloader.search_candidates(track)

        self.assertEqual(found["candidates"], [])
        self.assertIn("Sin resultados", found["error"])

    def test_search_video_id_reuses_candidate_ranking(self) -> None:
        track = Track(id="t", name="Song", artist="Band", album="", duration="",
                      img_url="", platform="", duration_ms=200000)
        entries = [self._entry("aaa", "Otra cosa", 30), self._entry("bbb", "Song", 200)]
        with mock.patch.object(download_search, "_search_entries", return_value=(entries, "")):
            result = downloader.search_video_id(track)

        self.assertTrue(result.ok)
        self.assertEqual(result.video_id, "bbb")
        self.assertEqual(result.duration_s, 200)


class CrossDownloadManifestTest(unittest.TestCase):
    def test_write_download_manifest_clears_external_source_path(self) -> None:
        import tempfile
        from pathlib import Path
        from engine.downloader import read_playlist_manifest, write_playlist_manifest

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_dir = root / "FM [Spotify]"
            other_dir = root / "MM [Apple Music]"
            target_dir.mkdir()
            other_dir.mkdir()

            ext_file = other_dir / "Artist A - External.mp3"
            ext_file.write_bytes(b"mp3")
            own_file = target_dir / "Artist B - Local.mp3"
            own_file.write_bytes(b"mp3")

            ext_track = Track(
                id="ext_1", name="External", artist="Artist A", album="Alb A",
                duration="3:10", img_url="", platform="Spotify",
                isrc="USABC2400001", duration_ms=190000, source_path=str(ext_file),
            )
            own_track = Track(
                id="own_1", name="Local", artist="Artist B", album="Alb B",
                duration="2:50", img_url="", platform="Spotify",
                isrc="USABC2400002", duration_ms=170000, source_path=str(own_file),
            )

            state = AppState(service=None)
            state._download_runner._write_download_manifest(
                target_dir,
                "FM",
                "Spotify",
                [ext_track, own_track],
                write_playlist_manifest,
                read_playlist_manifest,
            )

            manifest = read_playlist_manifest(target_dir)
            self.assertIsNotNone(manifest)
            by_id = {e["id"]: e for e in manifest["tracks"]}
            self.assertEqual(by_id["ext_1"]["path"], "")
            self.assertEqual(by_id["ext_1"]["isrc"], "USABC2400001")
            self.assertEqual(by_id["ext_1"]["duration_ms"], 190000)
            self.assertEqual(by_id["own_1"]["path"], str(own_file))


class DynamicDownloadQueueTest(unittest.TestCase):
    def test_enqueues_tracks_while_running_and_completes_per_batch(self) -> None:
        import asyncio
        import tempfile
        import time
        from types import SimpleNamespace
        from core.models import TransferState

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                st = _state()
                t1 = Track(id="t1", name="Song 1", artist="Art 1", album="", duration="3:00", img_url="", platform="Spotify")
                t2 = Track(id="t2", name="Song 2", artist="Art 2", album="", duration="3:00", img_url="", platform="Spotify")
                t3 = Track(id="t3", name="Song 3", artist="Art 3", album="", duration="3:00", img_url="", platform="Spotify")

                active_now = 0
                max_parallel = 0

                def _fake_download_audio(track, destination_dir=None, audio_format="mp3", **_kw):
                    nonlocal active_now, max_parallel
                    active_now += 1
                    max_parallel = max(max_parallel, active_now)
                    try:
                        # t1 termina rápido, t2 y t3 tardan un poco más
                        time.sleep(0.03 if track.id == "t1" else 0.08)
                        out = Path(destination_dir) / f"{track.id}.{audio_format}"
                        out.write_bytes(b"audio")
                        return SimpleNamespace(ok=True, path=str(out), video_id=f"vid_{track.id}", skipped=False, code="", error="")
                    finally:
                        active_now -= 1

                with mock.patch("engine.downloader.ffmpeg_available", return_value=True), \
                     mock.patch("engine.downloader.download_audio", side_effect=_fake_download_audio), \
                     mock.patch("services.settings_store.get_covers_online", return_value=False):
                    task1 = st.download_tracks([t1], destination_dir=tmp, playlist_name="Lista", platform="Spotify")
                    await asyncio.sleep(0.005)
                    # Mientras t1 está corriendo, encolamos t2 y t3 individualmente
                    self.assertEqual(st.download_state, TransferState.RUNNING)
                    task2 = st.download_tracks([t2], destination_dir=tmp, playlist_name="Lista", platform="Spotify")
                    task3 = st.download_tracks([t3], destination_dir=tmp, playlist_name="Lista", platform="Spotify")
                    # Duplicado en caliente reutiliza el mismo future sin inflar download_total
                    task2_dup = st.download_tracks([t2], destination_dir=tmp, playlist_name="Lista", platform="Spotify")
                    self.assertEqual(st.download_total, 3)

                    await task1
                    # Cuando task1 retorna, t1 ya tiene archivo en disco aunque t3 siga en curso
                    self.assertTrue(bool(t1.source_path and Path(t1.source_path).is_file()))

                    await asyncio.gather(task2, task3, task2_dup)
                    self.assertEqual(st.download_state, TransferState.DONE)
                    self.assertEqual(len(st.download_done), 3)
                    self.assertGreaterEqual(max_parallel, 2)
                    self.assertLessEqual(max_parallel, 2)

        asyncio.run(_run())


class DownloadQueueLifecycleTest(unittest.TestCase):
    """Regresiones del encolado en caliente: pausa, cancelación, mezcla y dedup."""

    def _patches(self, fake):
        return (
            mock.patch("engine.downloader.ffmpeg_available", return_value=True),
            mock.patch("engine.downloader.download_audio", side_effect=fake),
            mock.patch("services.settings_store.get_covers_online", return_value=False),
            mock.patch("services.local_index.match_tracks_against_library", return_value=[]),
        )

    @staticmethod
    def _track(tid: str, name: str = "", artist: str = "") -> Track:
        return Track(
            id=tid, name=name or f"Song {tid}", artist=artist or f"Art {tid}",
            album="", duration="3:00", img_url="", platform="Spotify",
        )

    def test_enqueue_while_paused_does_not_clobber_the_new_queue(self) -> None:
        """Encolar en pausa abre sesión nueva sin que la vieja pise su estado."""
        import asyncio
        import tempfile
        import time
        from types import SimpleNamespace
        from engine.downloader import DOWNLOAD_PAUSED

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                st = _state()
                t1 = self._track("t1")
                t2 = self._track("t2")

                def _fake(track, destination_dir=None, audio_format="mp3", **kw):
                    pause = kw.get("pause_event")
                    for _ in range(30):
                        if pause is not None and pause.is_set():
                            return SimpleNamespace(
                                ok=False, path="", video_id="", skipped=False,
                                code=DOWNLOAD_PAUSED, error="Pausada",
                            )
                        time.sleep(0.01)
                    out = Path(destination_dir) / f"{track.id}.{audio_format}"
                    out.write_bytes(b"audio")
                    return SimpleNamespace(
                        ok=True, path=str(out), video_id=f"v_{track.id}",
                        skipped=False, code="", error="",
                    )

                p1, p2, p3, p4 = self._patches(_fake)
                with p1, p2, p3, p4:
                    task1 = st.download_tracks(
                        [t1], destination_dir=tmp, playlist_name="A", platform="Spotify"
                    )
                    await asyncio.sleep(0.02)
                    st.pause_downloads()

                    task2 = st.download_tracks(
                        [t2], destination_dir=tmp, playlist_name="A", platform="Spotify"
                    )
                    await asyncio.gather(task1, task2)

                # La cola nueva nace pausada y queda pendiente, no fallida.
                self.assertTrue(st.download_paused)
                self.assertIn(t2, st._download_pending_tracks)
                self.assertFalse(any(e.get("id") == "t2" for e in st.download_failed))
                self.assertEqual(
                    [m["id"] for m in st.download_missing if m["id"] == "t2"], ["t2"]
                )

        asyncio.run(_run())

    def test_enqueue_after_cancel_is_not_discarded(self) -> None:
        """Lo encolado justo después de cancelar no sale como «Cancelada»."""
        import asyncio
        import tempfile
        import time
        from types import SimpleNamespace

        async def _run() -> None:
            with tempfile.TemporaryDirectory() as tmp:
                st = _state()
                t1 = self._track("t1")
                t2 = self._track("t2")

                def _fake(track, destination_dir=None, audio_format="mp3", **_kw):
                    time.sleep(0.06)
                    out = Path(destination_dir) / f"{track.id}.{audio_format}"
                    out.write_bytes(b"audio")
                    return SimpleNamespace(
                        ok=True, path=str(out), video_id=f"v_{track.id}",
                        skipped=False, code="", error="",
                    )

                p1, p2, p3, p4 = self._patches(_fake)
                with p1, p2, p3, p4:
                    task1 = st.download_tracks(
                        [t1], destination_dir=tmp, playlist_name="A", platform="Spotify"
                    )
                    await asyncio.sleep(0.02)
                    st.cancel_downloads()
                    task2 = st.download_tracks(
                        [t2], destination_dir=tmp, playlist_name="A", platform="Spotify"
                    )
                    await asyncio.gather(task1, task2)

                self.assertTrue(bool(t2.source_path and Path(t2.source_path).is_file()))
                self.assertFalse(any(e.get("id") == "t2" for e in st.download_failed))
                self.assertIn("t2", [e["id"] for e in st.download_done])

        asyncio.run(_run())


if __name__ == "__main__":
    unittest.main()
