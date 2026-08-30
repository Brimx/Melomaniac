"""Pruebas unitarias aisladas para ISRC, Mutagen y Apple Music."""

from __future__ import annotations

import unittest
import tempfile
import asyncio
from unittest.mock import Mock, patch

from engine.normalizer import normalize_isrc
from services.api_service import AppleAuthError, AppleRequestLimiter, MusicApiService
from utils.audio_metadata import read_audio_metadata


class IsrcTests(unittest.TestCase):
    def test_normalize_isrc_accepts_hyphens_and_rejects_invalid_values(self):
        self.assertEqual(normalize_isrc("us-s1z-99-00001"), "USS1Z9900001")
        self.assertIsNone(normalize_isrc("not-an-isrc"))

    def test_mutagen_metadata_reads_id3_tsrc(self):
        fake_audio = Mock()
        fake_audio.tags = {
            "TIT2": "Song",
            "TPE1": "Artist",
            "TSRC": "US-S1Z-99-00001",
        }
        fake_audio.info.length = 185.5
        with tempfile.NamedTemporaryFile(suffix=".mp3") as audio_file:
            with patch("mutagen.File", return_value=fake_audio):
                metadata = read_audio_metadata(audio_file.name)
        self.assertEqual(metadata["title"], "Song")
        self.assertEqual(metadata["artist"], "Artist")
        self.assertEqual(metadata["isrc"], "USS1Z9900001")
        self.assertEqual(metadata["duration_ms"], 185500)

    def test_mutagen_missing_file_is_tolerated(self):
        self.assertEqual(read_audio_metadata("/tmp/does-not-exist.mp3"), {})


class AppleTests(unittest.TestCase):
    def test_limiter_resets_after_burst_without_network_wait(self):
        limiter = AppleRequestLimiter(burst=2, pause=0)
        limiter.wait_turn()
        limiter.wait_turn()
        self.assertEqual(limiter.count, 2)
        limiter.wait_turn()
        self.assertEqual(limiter.count, 1)

    def test_isrc_batch_maps_exact_catalog_results(self):
        service = object.__new__(MusicApiService)
        service._am_storefront = "co"
        service._am_rate_limiter = AppleRequestLimiter(burst=50, pause=0)
        service._http_session = Mock()
        response = Mock(status_code=200, headers={}, content=b"{}")
        response.json.return_value = {
            "data": [{
                "id": "123",
                "attributes": {"isrc": "US-S1Z-99-00001"},
            }]
        }
        service._http_session.get.return_value = response

        result = service._am_search_isrc_batch(["US-S1Z-99-00001"])

        self.assertEqual(result["USS1Z9900001"].track_id, "123")
        service._http_session.get.assert_called_once()
        self.assertEqual(
            service._http_session.get.call_args.kwargs["params"]["filter[isrc]"],
            "USS1Z9900001",
        )

    def test_limiter_pauses_before_request_51(self):
        limiter = AppleRequestLimiter(burst=50, pause=60)
        with patch("services.api_service.time.sleep") as sleep:
            for _ in range(51):
                limiter.wait_turn()
        sleep.assert_called_once_with(60.0)

    def test_isrc_queries_are_split_in_batches_of_25(self):
        service = object.__new__(MusicApiService)
        service._am_search_isrc_batch = Mock(side_effect=[{}, {}])
        isrcs = [f"USABC{index:07d}" for index in range(26)]

        asyncio.run(service.search_by_isrcs(isrcs))

        self.assertEqual(service._am_search_isrc_batch.call_count, 2)
        self.assertEqual(len(service._am_search_isrc_batch.call_args_list[0].args[0]), 25)
        self.assertEqual(len(service._am_search_isrc_batch.call_args_list[1].args[0]), 1)

    def test_apple_create_splits_tracks_at_100(self):
        service = object.__new__(MusicApiService)
        service._am_rate_limiter = AppleRequestLimiter(burst=50, pause=0)
        service._http_session = Mock()
        first = Mock(status_code=201, headers={}, content=b"json")
        first.json.return_value = {"data": [{"id": "playlist-1"}]}
        second = Mock(status_code=200, headers={}, content=b"{}")
        service._http_session.post.side_effect = [first, second]

        ok, playlist_id, confirmed, rejected = service._am_create(
            "Test", [str(index) for index in range(101)]
        )

        self.assertTrue(ok)
        self.assertEqual(playlist_id, "playlist-1")
        self.assertEqual(confirmed, 101)
        self.assertEqual(rejected, [])
        self.assertEqual(service._http_session.post.call_count, 2)
        first_payload = service._http_session.post.call_args_list[0].kwargs["json"]
        second_payload = service._http_session.post.call_args_list[1].kwargs["json"]
        self.assertEqual(len(first_payload["relationships"]["tracks"]["data"]), 100)
        self.assertEqual(len(second_payload["data"]), 1)

    def test_apple_403_is_not_retried(self):
        service = object.__new__(MusicApiService)
        service._am_rate_limiter = AppleRequestLimiter(burst=50, pause=0)
        service._http_session = Mock()
        response = Mock(status_code=403, headers={}, content=b"{}")
        service._http_session.get.return_value = response

        with self.assertRaises(AppleAuthError):
            service._am_request("get", "https://amp-api.music.apple.com/v1/test")
        service._http_session.get.assert_called_once()

    def test_apple_429_gets_one_limited_retry(self):
        service = object.__new__(MusicApiService)
        service._am_rate_limiter = AppleRequestLimiter(burst=50, pause=0)
        service._http_session = Mock()
        limited = Mock(status_code=429, headers={}, content=b"{}")
        ok = Mock(status_code=200, headers={}, content=b"{}")
        service._http_session.get.side_effect = [limited, ok]

        with patch("services.api_service.time.sleep"):
            result = service._am_request("get", "https://amp-api.music.apple.com/v1/test")

        self.assertIs(result, ok)
        self.assertEqual(service._http_session.get.call_count, 2)


if __name__ == "__main__":
    unittest.main()
