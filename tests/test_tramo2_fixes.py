"""tests/test_tramo2_fixes.py — Regresiones para las 6 altas de coincidencia y transferencia (tramo 2)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import core.transfer_flow as transfer_flow
from core.models import LoadState, SearchResult, Track, TransferState
from core.state import AppState
from engine.match import _fuzzy_flags_elastic, _fuzzy_scores_triple, validar_match
from services.circuit_breaker import RateLimitError
from services.platforms.spotify import SpotifyClient
from services.platforms.youtube import YouTubeClient


def _make_track(
    tid: str,
    name: str,
    artist: str,
    *,
    platform: str = "Spotify",
    selected: bool = True,
) -> Track:
    tr = Track(
        id=tid,
        name=name,
        artist=artist,
        album="Album",
        duration="3:20",
        img_url="",
        platform=platform,
    )
    tr.selected = selected
    return tr


class _DummyService:
    def __init__(self, search_results: dict[str, SearchResult | Exception] | None = None) -> None:
        self.search_cache: dict[str, object] = {}
        self.search_results = dict(search_results or {})
        self.create_calls: list[tuple[str, str, list[str], str]] = []
        self.search_calls: list[tuple[str, str, str]] = []
        self._cb = None

    def mark_search_cache_dirty(self) -> None:
        pass

    def save_search_cache(self) -> None:
        pass

    async def init_youtube(self) -> bool:
        return True

    async def init_apple(self) -> bool:
        return True

    async def init_spotify(self) -> bool:
        return True

    async def search_by_isrcs(self, _isrcs: list[str]) -> dict[str, SearchResult]:
        return {}

    async def search_with_fallback(
        self,
        platform: str,
        name: str,
        artist: str,
        **_kwargs,
    ) -> SearchResult:
        self.search_calls.append((platform, name, artist))
        outcome = self.search_results.get(name)
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome is not None:
            return outcome
        return SearchResult(None, False)

    async def create_playlist(
        self,
        platform: str,
        title: str,
        ids: list[str],
        description: str = "",
    ) -> tuple[bool, str, int, list[str]]:
        self.create_calls.append((platform, title, list(ids), description))
        return True, "pl_created_1", len(ids), []


class TestTransferStateGuardsAndImmutability(unittest.IsolatedAsyncioTestCase):
    async def test_state_mutations_blocked_while_transfer_running(self) -> None:
        svc = _DummyService()
        state = AppState(service=svc)
        t1 = _make_track("1", "Track One", "Artist One")
        state.tracks = [t1]
        state.source_tracks = [t1]
        state.playlist_id = "pl_orig"
        state.playlist_name = "Original"
        state.load_state = LoadState.READY
        state.transfer_state = TransferState.RUNNING

        await state.load_playlist("pl_other")
        state.load_local_tracks([_make_track("2", "Local", "Local", platform="local")], "Local")
        merged = state.merge_sources([[_make_track("3", "M", "M")]], name="Merged")
        state.reset_session()

        self.assertEqual(state.transfer_state, TransferState.RUNNING)
        self.assertEqual(state.playlist_id, "pl_orig")
        self.assertEqual(state.playlist_name, "Original")
        self.assertEqual(state.tracks, [t1])
        self.assertEqual(merged, [])

    async def test_transfer_captures_destination_and_ignores_reentry(self) -> None:
        t1 = _make_track("1", "Song A", "Artist A")
        t2 = _make_track("2", "Song B", "Artist B")
        svc = _DummyService()
        state = AppState(service=svc)
        state.source = "Spotify"
        state.destination = "YouTube Music"
        state.playlist_name = "Mi Mix"
        state.tracks = [t1, t2]

        async def _search_side_effect(platform: str, name: str, artist: str, **_kwargs) -> SearchResult:
            svc.search_calls.append((platform, name, artist))
            if name == "Song A":
                # Simula intento de cambiar destino o reentrar durante la transferencia
                state.destination = "Apple Music"
                await state.transfer_playlist()
            return SearchResult(f"dest_{name}", False)

        svc.search_with_fallback = _search_side_effect  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", Path(tmp) / "history.jsonl"):
                await state.transfer_playlist()

        self.assertEqual(state.transfer_state, TransferState.DONE)
        self.assertEqual([call[0] for call in svc.search_calls], ["YouTube Music", "YouTube Music"])
        self.assertEqual(len(svc.create_calls), 1)
        self.assertEqual(svc.create_calls[0][0], "YouTube Music")
        self.assertIsNotNone(state.last_report)
        assert state.last_report is not None
        self.assertEqual(state.last_report.destination, "YouTube Music")


class TestMatchingFlagsAndValidation(unittest.IsolatedAsyncioTestCase):
    def test_low_conf_marks_below_85_without_requiring_ideal(self) -> None:
        # Caso reportado: "Creep"/Radiohead vs "Creep (Acoustic)"/"Radiohead Covers"
        comb, tit, art = _fuzzy_scores_triple(
            "Creep", "Radiohead", "Creep (Acoustic)", "Radiohead Covers"
        )
        needs, low = _fuzzy_flags_elastic(comb, tit, art)
        self.assertFalse(needs)
        self.assertTrue(low)

        # Banda 40-69 sin salvamento también queda marcada como baja confianza
        self.assertEqual(_fuzzy_flags_elastic(55, 60, 50), (False, True))
        # <40 sin salvamento requiere revisión
        self.assertEqual(_fuzzy_flags_elastic(35, 30, 40), (True, False))
        # >=85 limpio no se marca
        self.assertEqual(_fuzzy_flags_elastic(95, 88, 100), (False, False))

    async def test_local_track_rejects_low_confidence_match(self) -> None:
        local_track = _make_track("loc1", "Creep", "Radiohead", platform="local")
        svc = _DummyService(
            {"Creep": SearchResult("yt_creep_cover", needs_review=False, low_confidence=True)}
        )
        state = AppState(service=svc)
        state.source = "Local"
        state.destination = "YouTube Music"
        state.playlist_name = "Local Mix"
        state.tracks = [local_track]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", Path(tmp) / "history.jsonl"):
                await state.transfer_playlist()

        self.assertEqual(local_track.transfer_status, "not_found")
        self.assertEqual(svc.create_calls, [])
        self.assertIn(local_track, state.failed_tracks)

    def test_validar_match_blocks_cjk_bypass_empty_artist_and_lethal_before_l1(self) -> None:
        # L0 eliminado: caracteres CJK/Hangul no dan match incondicional
        self.assertFalse(
            validar_match(
                "Completely Different",
                "Other Artist",
                {"title": "夜に駆ける", "artists": [{"name": "YOASOBI"}]},
            )
        )
        # Artista local vacío no hace match por substring vacío "" in r_artist_str
        self.assertFalse(
            validar_match(
                "Creep",
                "",
                {"title": "Creep", "artists": [{"name": "Totally Unrelated Band"}]},
            )
        )
        # L2 antes de L1: karaoke / tribute / cover rechazados aunque título y artista coincidan
        self.assertFalse(
            validar_match(
                "Creep",
                "Radiohead",
                {"title": "Creep (Karaoke)", "artists": [{"name": "Radiohead"}]},
            )
        )
        self.assertFalse(
            validar_match(
                "Creep",
                "Radiohead",
                {"title": "Creep Tribute", "artists": [{"name": "Radiohead"}]},
            )
        )
        # Match asiático legítimo sigue pasando por L1/L3
        self.assertTrue(
            validar_match(
                "夜に駆ける",
                "YOASOBI",
                {"title": "夜に駆ける", "artists": [{"name": "YOASOBI"}]},
            )
        )


class TestSpotifyPartialChunksAndRateLimits(unittest.TestCase):
    def test_sp_add_tracks_only_rejects_failing_chunk_and_continues(self) -> None:
        ids = [f"id_{i}" for i in range(120)]  # 3 lotes: 50, 50, 20
        calls: list[list[str]] = []

        class _FakeSong:
            def __init__(self) -> None:
                self.chunk_2_failures = 0

            def add_songs_to_playlist(self, chunk: list[str]) -> None:
                calls.append(list(chunk))
                if chunk[0] == "id_50":
                    self.chunk_2_failures += 1
                    raise RuntimeError("transient server error 500")

        with patch("services.platforms.spotify.time.sleep", return_value=None):
            rejected = SpotifyClient._sp_add_tracks(MagicMock(), ids, song=_FakeSong())

        self.assertEqual(rejected, ids[50:100])
        # El lote 3 (id_100..id_119) sí se intentó tras fallar el lote 2
        self.assertTrue(any(c == ids[100:120] for c in calls))

    def test_sp_create_attaches_partial_playlist_context_on_rate_limit(self) -> None:
        client = SpotifyClient()
        client._sp_login = MagicMock()
        ids = [f"id_{i}" for i in range(75)]  # 2 lotes: 50, 25

        fake_pl = MagicMock()
        fake_pl.create_playlist.return_value = "spotify:playlist:pl_sp_123"

        class _FakeSong:
            def __init__(self, _pl) -> None:
                pass

            def add_songs_to_playlist(self, chunk: list[str]) -> None:
                if chunk[0] == "id_50":
                    raise RuntimeError("Status Code: 429 Too Many Requests")

        with (
            patch("services.platforms.spotify.HAS_SPOTIFY", True),
            patch("services.platforms.spotify.PrivatePlaylist", return_value=fake_pl),
            patch("services.platforms.spotify.Song", _FakeSong),
            patch("services.platforms.spotify.time.sleep", return_value=None),
        ):
            with self.assertRaises(RateLimitError) as ctx:
                client._sp_create("Mi Lista", ids)

        exc = ctx.exception
        self.assertEqual(getattr(exc, "playlist_id", None), "pl_sp_123")
        self.assertEqual(getattr(exc, "confirmed_ids", None), ids[:50])
        self.assertEqual(getattr(exc, "pending_ids", None), ids[50:])


class TestHuntersRateLimitAndGatherAbort(unittest.IsolatedAsyncioTestCase):
    async def test_yt_hunter_maps_429_to_rate_limit_error(self) -> None:
        client = YouTubeClient()
        client._ytm = MagicMock()

        def _raise_429(*_args, **_kwargs):
            raise RuntimeError("Server returned HTTP 429: Too Many Requests.")

        with patch.object(client, "_yt_sync_search_round", side_effect=_raise_429):
            with self.assertRaises(RateLimitError) as ctx:
                await client._yt_hunter_async("Song", "Artist", "Song", "Artist", 200)
        self.assertEqual(ctx.exception.platform, "YouTube Music")

    async def test_sp_hunter_maps_429_to_rate_limit_error(self) -> None:
        client = SpotifyClient()
        client._sp_cfg = MagicMock()

        with patch.object(
            client,
            "_sp_search_items",
            side_effect=RuntimeError("Status Code: 429"),
        ):
            with self.assertRaises(RateLimitError) as ctx:
                await client._sp_hunter_async("Song", "Artist", "Song", "Artist", 0, False)
        self.assertEqual(ctx.exception.platform, "Spotify")

    async def test_gather_aborts_without_partial_playlist_on_rate_limit_or_auth(self) -> None:
        # Caso 1: una pista tiene éxito y la segunda sufre RateLimitError
        t1 = _make_track("1", "Found Song", "Artist")
        t2 = _make_track("2", "RateLimited Song", "Artist")
        svc = _DummyService(
            {
                "Found Song": SearchResult("yt_1", False),
                "RateLimited Song": RateLimitError("YouTube Music", 60),
            }
        )
        state = AppState(service=svc)
        state.source = "Spotify"
        state.destination = "YouTube Music"
        state.playlist_name = "Mix"
        state.tracks = [t1, t2]

        with tempfile.TemporaryDirectory() as tmp:
            hist = Path(tmp) / "history.jsonl"
            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", hist):
                await state.transfer_playlist()

            self.assertEqual(svc.create_calls, [])
            self.assertEqual(state.transfer_state, TransferState.ERROR)
            self.assertTrue(state.cb["YouTube Music"].is_open)
            state.cb["YouTube Music"].cancel()

            # Caso 2: 401/403 en gather aborta sin caer en "No se encontraron coincidencias"
            t3 = _make_track("3", "Auth Fail Song", "Artist")
            auth_exc = RuntimeError("Sesion YouTube Music expirada (401).")
            svc2 = _DummyService({"Auth Fail Song": auth_exc})
            state2 = AppState(service=svc2)
            state2.source = "Spotify"
            state2.destination = "YouTube Music"
            state2.playlist_name = "Mix 2"
            state2.tracks = [t3]

            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", hist):
                await state2.transfer_playlist()

            self.assertEqual(svc2.create_calls, [])
            self.assertEqual(state2.transfer_state, TransferState.ERROR)
            self.assertTrue(any("401" in line for line in state2.log_lines))
            self.assertFalse(any("No se encontraron coincidencias" in line for line in state2.log_lines))


class TestOnboardingAndSpotifyDetection(unittest.TestCase):
    def test_spotify_candidates_and_init_accept_sp_dc_without_sp_key(self) -> None:
        from services import browser_session as bs
        from services.platforms import spotify as sp_mod

        fake_prof = bs.BrowserProfile(
            engine="firefox",
            browser="firefox",
            label="Firefox · default-release",
            root=Path("/tmp/fake"),
            profile="default-release",
        )
        with (
            patch.object(bs, "detect", return_value=[fake_prof]),
            patch.object(bs, "_profile_cookies", return_value=[(".spotify.com", "sp_dc", "tok_dc_only")]),
            patch.object(bs, "read_spotify_cookies", return_value={}),
        ):
            cands = list(bs.spotify_candidates())

        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0][0], "Firefox · default-release")
        self.assertEqual(cands[0][1]["cookies"], {"sp_dc": "tok_dc_only"})

        cli = SpotifyClient()
        fake_cfg = MagicMock()
        fake_login = MagicMock(logged_in=True)
        with (
            patch.object(sp_mod, "HAS_SPOTIFY", True),
            patch.object(sp_mod, "Config", return_value=fake_cfg),
            patch.object(sp_mod.Login, "from_cookies", return_value=fake_login) as mock_from,
            patch.object(sp_mod, "Song", return_value=MagicMock()),
            patch.object(cli, "_spotify_library_page", return_value={"data": {}}),
        ):
            ok = cli._sync_init_spotify(cands[0][1])

        self.assertTrue(ok)
        self.assertEqual(mock_from.call_args.args[0]["cookies"], {"sp_dc": "tok_dc_only"})

    def test_onboarding_shows_spotify_status_and_restores_key_handler(self) -> None:
        from ui.setup.onboarding import ONBOARDING_STEPS, OnboardingView

        state = AppState(service=None)
        state.auth_session_ok["Spotify"] = True
        state.auth_session_hint["Spotify"] = "Spotify · Firefox · default-release"

        prev_handler = MagicMock()
        page = MagicMock()
        page.services = []
        page.on_keyboard_event = prev_handler
        page.window = MagicMock(on_event=None)

        async def _run() -> None:
            view = OnboardingView(page, state=state, bg_mode="none")
            view.did_mount()
            self.assertNotEqual(page.on_keyboard_event, prev_handler)

            connect_idx = next(i for i, s in enumerate(ONBOARDING_STEPS) if s.key == "connect")
            view._index = connect_idx
            view._render(initial=True)
            self.assertIsNotNone(view._connect_status_row)
            assert view._connect_status_row is not None
            texts = [
                chip.content.controls[1].value
                for chip in view._connect_status_row.controls
            ]
            self.assertTrue(any("Spotify: Firefox · default-release" in t for t in texts))

            view.will_unmount()
            self.assertIs(page.on_keyboard_event, prev_handler)

        asyncio.run(_run())


class TestPoint2AndMediasTramo2(unittest.IsolatedAsyncioTestCase):
    async def test_load_playlist_progress_marshals_to_loop_thread(self) -> None:
        import threading
        from core.models import PlaylistMeta

        loop_tid = threading.get_ident()
        observed_tids: list[int] = []

        class _Svc:
            async def fetch_playlist(self, _src, _pid, cb):
                def _worker():
                    cb(1, 10, "Cargando…")
                    return PlaylistMeta(name="Final", description=""), [_make_track("1", "A", "B")]

                return await asyncio.to_thread(_worker)

        state = AppState(service=_Svc())
        state.subscribe(lambda: observed_tids.append(threading.get_ident()))
        await state.load_playlist("pl_1")

        self.assertEqual(state.load_state, LoadState.READY)
        self.assertEqual(state.playlist_name, "Final")
        self.assertTrue(all(tid == loop_tid for tid in observed_tids))

    async def test_transfer_flow_medias_cache_local_isrc_dedup_and_non_network_errors(self) -> None:
        from core.cache import make_cache_key

        local_low = _make_track("l1", "Low Local", "Artist", platform="local")
        r1 = _make_track("r1", "Dup One", "Artist")
        r2 = _make_track("r2", "Dup Two", "Artist")
        svc = _DummyService(
            {
                "Dup One": SearchResult("shared_dest", False),
                "Dup Two": SearchResult("shared_dest", False),
            }
        )
        svc.search_cache[make_cache_key("Low Local", "Artist", "YouTube Music")] = SearchResult(
            "yt_low", needs_review=False, low_confidence=True
        )
        state = AppState(service=svc)
        state.source = "Spotify"
        state.destination = "YouTube Music"
        state.playlist_name = "Mix"
        state.tracks = [local_low, r1, r2]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", Path(tmp) / "h.jsonl"):
                await state.transfer_playlist()

        self.assertEqual(local_low.transfer_status, "not_found")
        self.assertEqual(svc.create_calls[0][2], ["shared_dest"])

        t_isrc = _make_track("a1", "ISRC Track", "Artist")
        t_isrc.isrc = "us-abc-12-34567"
        looked_up: list[list[str]] = []

        class _AppleSvc(_DummyService):
            async def search_by_isrcs(self, isrcs: list[str]) -> dict[str, SearchResult]:
                looked_up.append(list(isrcs))
                return {"USABC1234567": SearchResult("am_1", False)}

        svc_am = _AppleSvc()
        st_am = AppState(service=svc_am)
        st_am.source = "Spotify"
        st_am.destination = "Apple Music"
        st_am.playlist_name = "Apple Mix"
        st_am.tracks = [t_isrc]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", Path(tmp) / "h2.jsonl"):
                await st_am.transfer_playlist()

        self.assertEqual(looked_up, [["USABC1234567"]])
        self.assertEqual(svc_am.create_calls[0][2], ["am_1"])

        bad_tracks = [_make_track(f"b{i}", f"Bad {i}", "Art") for i in range(9)]
        good = _make_track("g1", "Good", "Art")
        svc_err = _DummyService(
            {**{f"Bad {i}": ValueError("parse") for i in range(9)}, "Good": SearchResult("yt_g", False)}
        )
        st_err = AppState(service=svc_err)
        st_err.source = "Spotify"
        st_err.destination = "YouTube Music"
        st_err.playlist_name = "Partial Mix"
        st_err.tracks = [*bad_tracks, good]

        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(transfer_flow, "TRANSFER_HISTORY_JSONL", Path(tmp) / "h3.jsonl"):
                await st_err.transfer_playlist()

        self.assertEqual(st_err.transfer_state, TransferState.DONE)
        self.assertEqual(svc_err.create_calls[0][2], ["yt_g"])

    def test_score_spotify_match_neutral_without_duration_and_yt_create_unknown_verify(self) -> None:
        from engine.match import score_spotify_match

        real = score_spotify_match("Song", "Artist", 0, False, "Song", "Artist", 210_000, False)
        clip = score_spotify_match("Song", "Artist", 0, False, "Song", "Artist", 1_000, False)
        self.assertEqual(real, clip)

        yt = YouTubeClient()
        yt._ytm = MagicMock()
        yt._ytm.create_playlist.return_value = "pl_yt_1"
        yt._ytm.get_playlist.side_effect = RuntimeError("verify timeout")
        ok, pl_id, confirmed, rejected = yt._yt_create("Title", ["v1", "v2"])
        self.assertTrue(ok)
        self.assertEqual(pl_id, "pl_yt_1")
        self.assertEqual(confirmed, -1)
        self.assertEqual(rejected, [])

    def test_circuit_breaker_trip_from_worker_thread_and_apple_total_propagates_429(self) -> None:
        import concurrent.futures
        from services.circuit_breaker import CircuitBreaker
        from services.platforms.apple import AppleClient

        cb = CircuitBreaker("covers", default_cooldown=1)
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(cb.trip, 1).result()
        self.assertTrue(cb.is_open)
        cb._until = 0.0001
        self.assertFalse(cb.is_open)

        am = AppleClient()
        am._am_rate_limiter = MagicMock()
        am._http_session = MagicMock()
        resp_429 = SimpleNamespace(status_code=429, headers={"Retry-After": "9"})
        am._http_session.get.return_value = resp_429
        am._http_session.delete.return_value = resp_429
        with self.assertRaises(RateLimitError):
            am._apple_playlist_total("p.1")
        with self.assertRaises(RateLimitError):
            am._am_delete("p.1")

    def test_downloader_stem_lock_and_direct_url_live_filter(self) -> None:
        import concurrent.futures
        from engine import downloader

        tr_live = _make_track("v1", "Live Show", "Band")
        tr_live.source_url = "https://www.youtube.com/watch?v=abcdefghijk"
        with patch.object(
            downloader,
            "_probe_video_info",
            return_value={"is_live": True, "duration": 7200, "title": "Live"},
        ):
            res = downloader.search_video_id(tr_live)
        self.assertFalse(res.ok)

        active = 0
        max_active = 0

        def _fake_attempt(*_args, **_kwargs):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            import time
            time.sleep(0.03)
            active -= 1
            return downloader.DownloadResult(ok=True, path="/tmp/a.mp3")

        with tempfile.TemporaryDirectory() as tmp:
            t_a = _make_track("1", "Same Song?", "Artist")
            t_b = _make_track("2", "Same Song:", "Artist")
            with (
                patch.object(downloader, "ffmpeg_available", return_value=True),
                patch.object(downloader, "search_video_id", return_value=downloader.DownloadResult(ok=True, video_id="vid1")),
                patch.object(downloader, "_attempt_download", side_effect=_fake_attempt),
            ):
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                    f1 = pool.submit(downloader.download_audio, t_a, destination_dir=tmp)
                    f2 = pool.submit(downloader.download_audio, t_b, destination_dir=tmp)
                    f1.result()
                    f2.result()
        self.assertEqual(max_active, 1)


if __name__ == "__main__":
    unittest.main()
