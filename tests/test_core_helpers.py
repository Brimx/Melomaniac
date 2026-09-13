"""Focused regression tests for the extracted core helpers."""

from __future__ import annotations

import asyncio
import unittest

from core.availability import apply_availability_status, scan_track_availability
from core.models import SearchResult, Track
from core.transfer import failure_reason_from_exc, search_with_rate_limit_backoff
from services.circuit_breaker import RateLimitError


class _FakeService:
    def __init__(self, result: SearchResult | None = None, error: Exception | None = None):
        self.search_cache: dict = {}
        self.result = result
        self.error = error
        self.calls = 0
        self._cb = {}

    async def search_with_fallback(self, *_args, **_kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


class _FakeBreaker:
    def __init__(self):
        self.retry_after = None

    def trip(self, retry_after: int) -> None:
        self.retry_after = retry_after


class CoreHelperTests(unittest.TestCase):
    def test_failure_reason_is_limited(self):
        reason = failure_reason_from_exc(RuntimeError("x" * 301))
        self.assertEqual(len(reason), 301)
        self.assertTrue(reason.endswith("…"))

    def test_rate_limit_search_trips_platform_breaker(self):
        breaker = _FakeBreaker()
        service = _FakeService(error=RateLimitError("Spotify", 17))
        service._cb["Spotify"] = breaker

        with self.assertRaises(RateLimitError):
            asyncio.run(search_with_rate_limit_backoff(service, "Spotify", "Song", "Artist"))

        self.assertEqual(breaker.retry_after, 17)
        self.assertEqual(service.calls, 1)

    def test_availability_helper_uses_cache_and_updates_status(self):
        service = _FakeService(result=SearchResult("destination-1", False))
        track = Track("local-1", "Song", "Artist", "", "3:00", "", "local")

        result = asyncio.run(scan_track_availability(service, "Spotify", track))
        apply_availability_status(track, result)
        cached = asyncio.run(scan_track_availability(service, "Spotify", track))

        self.assertEqual(result.track_id, "destination-1")
        self.assertEqual(cached.track_id, "destination-1")
        self.assertEqual(track.transfer_status, "found")
        self.assertEqual(service.calls, 1)


if __name__ == "__main__":
    unittest.main()
