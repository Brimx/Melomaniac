"""Availability-scan helpers for tracks before a transfer.

The scan workflow still belongs to ``AppState`` because it owns progress and
notifications.  This module owns the per-track lookup and Apple ISRC cache
preload so those details are not embedded in the state coordinator.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from core.cache import make_cache_key, unwrap_search_result
from core.models import SearchResult, Track
from core.transfer import search_with_rate_limit_backoff
from engine.match import _duration_to_seconds


async def preload_apple_isrc_matches(service, tracks: Iterable[Track], destination: str) -> None:
    """Resolve available ISRCs in bulk and place successful results in cache."""
    if destination != "Apple Music":
        return

    tracks = list(tracks)
    exact_matches = await service.search_by_isrcs(
        [track.isrc for track in tracks if track.isrc]
    )
    for track in tracks:
        if not track.isrc:
            continue
        exact = exact_matches.get(track.isrc)
        if exact and exact.track_id:
            service.search_cache[make_cache_key(track.name, track.artist, destination)] = exact
    if exact_matches:
        service.save_search_cache()


async def scan_track_availability(
    service,
    destination: str,
    track: Track,
    *,
    log: Callable[[str], None] | None = None,
) -> SearchResult:
    """Return the cached or freshly searched availability result for a track."""
    cache_key = make_cache_key(track.name, track.artist, destination)
    local_duration_s = (
        track.duration_ms // 1000
        if getattr(track, "duration_ms", 0)
        else _duration_to_seconds(track.duration)
    )

    if cache_key in service.search_cache:
        result = unwrap_search_result(service.search_cache[cache_key])
    else:
        try:
            result = await search_with_rate_limit_backoff(
                service,
                destination,
                track.name,
                track.artist,
                local_duration_s=local_duration_s,
                local_duration_ms=track.duration_ms,
                local_is_explicit=track.is_explicit,
                local_isrc=track.isrc,
                log=log,
            )
        except Exception:
            result = SearchResult(None, False)
        service.search_cache[cache_key] = result

    return result


def apply_availability_status(track: Track, result: SearchResult) -> None:
    """Apply a search result to the track status used by the UI."""
    track.transfer_status = (
        "not_found"
        if not result.track_id
        else "revision_necesaria"
        if result.needs_review
        else "found"
    )
