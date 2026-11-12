"""
tests/test_merge_and_shuffle.py — Pruebas unitarias de fusión, deduplicación y shuffle contextual
"""

from __future__ import annotations

import random
import sys
import tempfile
import unittest
from pathlib import Path

from core.models import LoadState, Track, TransferState
from core.playback import PlayableTrack, PlayQueue, RepeatMode
from core.state import AppState
from engine.organizer import deduplicate_tracks, merge_playlists


def _make_track(
    idx: int | str = 1,
    *,
    name: str = "Song",
    artist: str = "Artist",
    album: str = "Album",
    duration: str = "3:00",
    duration_ms: int = 180_000,
    platform: str = "Spotify",
    img_url: str = "",
    isrc: str | None = None,
    source_path: str = "",
    source_url: str = "",
    genre: str = "",
    album_artist: str = "",
    track_number: int = 0,
    release_date: str = "",
    is_explicit: bool = False,
) -> Track:
    return Track(
        id=str(idx),
        name=name,
        artist=artist,
        album=album,
        duration=duration,
        img_url=img_url,
        platform=platform,
        duration_ms=duration_ms,
        isrc=isrc,
        source_path=source_path,
        source_url=source_url,
        genre=genre,
        album_artist=album_artist,
        track_number=track_number,
        release_date=release_date,
        is_explicit=is_explicit,
    )


def _make_playable(idx: int) -> PlayableTrack:
    tr = _make_track(idx, name=f"Track {idx}", artist=f"Artist {idx}", platform="local", source_path=f"/tmp/ctx_{idx}.flac")
    return PlayableTrack(
        track=tr,
        path=tr.source_path,
        container="flac",
        codec="flac",
        duration_ms=180_000,
    )


class TestDeduplicateAndMerge(unittest.TestCase):
    def test_empty_inputs(self) -> None:
        self.assertEqual(deduplicate_tracks([]), [])
        self.assertEqual(merge_playlists([]), [])
        self.assertEqual(merge_playlists([[], []]), [])

    def test_cascade_level1_isrc_ignores_formatting_and_title_differences(self) -> None:
        t1 = _make_track("sp1", name="Song A", artist="Artist", platform="Spotify", isrc="us-rc1-76-07839")
        t2 = _make_track("am9", name="Song A (Deluxe Edition)", artist="Artist", platform="Apple Music", isrc="USRC17607839", duration_ms=190_000)

        out = deduplicate_tracks([t1, t2])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].id, "sp1")
        self.assertEqual(out[0].platform, "Spotify")

    def test_cascade_level2_platform_and_id(self) -> None:
        t1 = _make_track("abc", name="Original Title", artist="Art", platform="Spotify", isrc=None)
        t2 = _make_track("abc", name="Different Title", artist="Art", platform="spotify", isrc=None, duration_ms=210_000)
        t3 = _make_track("abc", name="Other Song", artist="Other Art", platform="Apple Music", isrc=None)

        out = deduplicate_tracks([t1, t2, t3])
        self.assertEqual(len(out), 2)
        self.assertEqual([(t.platform, t.id) for t in out], [("Spotify", "abc"), ("Apple Music", "abc")])

    def test_cascade_level3_normalized_metadata_and_duration_tolerance(self) -> None:
        t1 = _make_track(
            "am1",
            name="Bohemian Rhapsody (Remastered 2011)",
            artist="Queen feat. Guest",
            platform="Apple Music",
            duration_ms=354_000,
        )
        # Dentro de ±3s (357_000 = +3000ms) con ruido en título
        t2 = _make_track(
            "yt1",
            name="Bohemian Rhapsody [Official Video]",
            artist="Queen",
            platform="YouTube Music",
            duration_ms=357_000,
        )
        # Fuera de ±3s (357_001 = +3001ms) -> se conserva aparte
        t3 = _make_track(
            "yt2",
            name="Bohemian Rhapsody",
            artist="Queen",
            platform="YouTube Music",
            duration_ms=357_001,
        )

        out = deduplicate_tracks([t1, t2, t3])
        self.assertEqual([t.id for t in out], ["am1", "yt2"])

    def test_distinct_valid_isrcs_are_not_merged_by_title_match(self) -> None:
        studio = _make_track("1", name="Song", artist="Band", duration_ms=200_000, isrc="USAAA0000001")
        live = _make_track("2", name="Song (Live)", artist="Band", duration_ms=200_000, isrc="USBBB0000002")

        out = deduplicate_tracks([studio, live])
        self.assertEqual([t.id for t in out], ["1", "2"])

    def test_preserves_first_occurrence_order_and_enriches_metadata_without_mutating_inputs(self) -> None:
        first_sparse = _make_track(
            "loc_1",
            name="Midnight City",
            artist="M83",
            album="Archivo Local",
            duration="",
            duration_ms=0,
            platform="local",
            img_url="",
            isrc=None,
            source_path="/music/midnight.flac",
        )
        other = _make_track("sp_2", name="Intro", artist="M83", platform="Spotify", duration_ms=120_000)
        dup_rich = _make_track(
            "sp_1",
            name="Midnight City (Remastered)",
            artist="M83",
            album="Hurry Up, We're Dreaming",
            duration="4:03",
            duration_ms=243_000,
            platform="Spotify",
            img_url="https://img.example/cover.jpg",
            isrc="FR9W11101234",
            source_url="https://open.spotify.com/track/sp_1",
            genre="Synthpop",
            album_artist="M83",
            track_number=2,
            release_date="2011-10-18",
            is_explicit=True,
        )

        merged = merge_playlists([[first_sparse, other], [dup_rich]])
        self.assertEqual([t.id for t in merged], ["loc_1", "sp_2"])

        enriched = merged[0]
        self.assertEqual(enriched.platform, "local")
        self.assertEqual(enriched.source_path, "/music/midnight.flac")
        self.assertEqual(enriched.album, "Hurry Up, We're Dreaming")
        self.assertEqual(enriched.duration_ms, 243_000)
        self.assertEqual(enriched.duration, "4:03")
        self.assertEqual(enriched.img_url, "https://img.example/cover.jpg")
        self.assertEqual(enriched.isrc, "FR9W11101234")
        self.assertEqual(enriched.source_url, "https://open.spotify.com/track/sp_1")
        self.assertEqual(enriched.genre, "Synthpop")
        self.assertEqual(enriched.album_artist, "M83")
        self.assertEqual(enriched.track_number, 2)
        self.assertEqual(enriched.release_date, "2011-10-18")
        self.assertTrue(enriched.is_explicit)

        # No muta el objeto original
        self.assertIsNone(first_sparse.isrc)
        self.assertEqual(first_sparse.album, "Archivo Local")

    def test_transitive_isrc_enrichment_deduplicates_later_track(self) -> None:
        t1 = _make_track("sp1", name="Song X", artist="Artist", platform="Spotify", isrc=None)
        t2 = _make_track("sp1", name="Song X", artist="Artist", platform="Spotify", isrc="USXYZ1234567")
        t3 = _make_track("am1", name="Different Variant", artist="Other", platform="Apple Music", isrc="USXYZ1234567", duration_ms=250_000)

        out = deduplicate_tracks([t1, t2, t3])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].id, "sp1")
        self.assertEqual(out[0].isrc, "USXYZ1234567")


class TestAppStateMergeSources(unittest.TestCase):
    def test_merge_sources_loads_deduped_tracks_and_resets_view_state(self) -> None:
        state = AppState(service=None)
        notifications = 0

        def _on_notify() -> None:
            nonlocal notifications
            notifications += 1

        state.subscribe(_on_notify)
        state.search_query = "stale"
        state.filtered = [_make_track("old")]
        state.segments = {"Old": [_make_track("old")]}
        state.active_segment_key = "Old"
        state.active_segment_keys = {"Old"}
        state.show_dual = True
        state.dual_mode = "doble"
        state.transfer_state = TransferState.DONE
        state.transfer_progress = 10
        state.transfer_total = 10

        src_remote = [
            _make_track("sp1", name="Track One", artist="Artist A", platform="Spotify", isrc="US1111111111"),
            _make_track("sp2", name="Track Two", artist="Artist B", platform="Spotify", duration_ms=200_000),
        ]
        src_local = [
            _make_track("loc1", name="Track One", artist="Artist A", platform="local", isrc="US1111111111", source_path="/tmp/one.mp3"),
            _make_track("loc2", name="Track Three", artist="Artist C", platform="local", duration_ms=210_000),
        ]

        merged = state.merge_sources([src_remote, src_local], name="Mix Total")

        self.assertEqual(len(merged), 3)
        self.assertEqual([t.id for t in state.tracks], ["sp1", "sp2", "loc2"])
        self.assertEqual(state.tracks[0].source_path, "/tmp/one.mp3")
        self.assertEqual([t.id for t in state.source_tracks], ["sp1", "sp2", "loc2"])
        self.assertEqual(state.playlist_name, "Mix Total")
        self.assertTrue(state.playlist_id.startswith("merged_"))
        self.assertEqual(state.load_state, LoadState.READY)
        self.assertEqual(state.search_query, "")
        self.assertEqual(state.filtered, [])
        self.assertEqual(state.segments, {})
        self.assertIsNone(state.active_segment_key)
        self.assertIsNone(state.active_segment_keys)
        self.assertFalse(state.show_dual)
        self.assertEqual(state.dual_mode, "lista")
        self.assertEqual(state.transfer_state, TransferState.IDLE)
        self.assertEqual(state.transfer_progress, 0)
        self.assertEqual(state.transfer_total, 0)
        self.assertEqual(notifications, 1)


class TestShuffleFromContext(unittest.TestCase):
    def test_no_flet_imported(self) -> None:
        self.assertNotIn("flet", sys.modules)

    def test_shuffle_from_context_picks_random_start_and_enables_shuffle(self) -> None:
        items = [_make_playable(i) for i in range(6)]
        starts: set[str] = set()

        for seed in range(12):
            q = PlayQueue(shuffle=False, repeat=RepeatMode.ALL, rng=random.Random(seed))
            q.history.append(_make_playable(99))
            first = q.shuffle_from_context(items)
            self.assertIsNotNone(first)
            self.assertTrue(q.shuffle)
            self.assertEqual(q.pos, 0)
            self.assertIs(q.current, first)
            self.assertEqual(q.history, [])
            self.assertEqual(len(q.ordered_tracks()), len(items))
            self.assertEqual({pt.track.id for pt in q.ordered_tracks()}, {pt.track.id for pt in items})
            starts.add(first.track.id)

        # Con múltiples semillas no arranca siempre por items[0]
        self.assertGreater(len(starts), 1)

        # Desactivar shuffle restaura el orden contextual conservando la pista actual
        q = PlayQueue(rng=random.Random(3))
        picked = q.shuffle_from_context(items)
        q.set_shuffle(False)
        self.assertIs(q.current, picked)
        self.assertEqual(q.ordered_tracks(), items)

    def test_shuffle_from_context_accepts_tracks_with_local_files_and_empty_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            p1 = Path(tmpdir) / "a.mp3"
            p2 = Path(tmpdir) / "b.mp3"
            p1.write_bytes(b"ID3")
            p2.write_bytes(b"ID3")

            tracks = [
                _make_track("1", name="A", platform="local", source_path=str(p1)),
                _make_track("2", name="Missing", platform="Spotify", source_path=""),
                _make_track("3", name="B", platform="local", source_path=str(p2)),
            ]

            q = PlayQueue(rng=random.Random(5))
            first = q.shuffle_from_context(tracks)
            self.assertIsNotNone(first)
            self.assertTrue(q.shuffle)
            self.assertEqual({pt.track.id for pt in q.ordered_tracks()}, {"1", "3"})

            empty_res = q.shuffle_from_context([])
            self.assertIsNone(empty_res)
            self.assertTrue(q.shuffle)
            self.assertIsNone(q.current)
            self.assertEqual(q.ordered_tracks(), [])


if __name__ == "__main__":
    unittest.main()
