"""Shared transfer helpers used by :mod:`core.state`.

This module contains transfer concerns that do not need to own ``AppState``:
rate-limit aware searches and compact error descriptions.  The state object
remains the workflow coordinator; these helpers only receive their explicit
service and logging dependencies.
"""

from __future__ import annotations

from typing import Callable, Optional

from core.models import SearchResult
from services.circuit_breaker import RateLimitError


def failure_reason_from_exc(exc: BaseException) -> str:
    """Return a short, user-facing description for a failed operation."""
    msg = str(exc)
    return msg[:300] + ("…" if len(msg) > 300 else "")


async def search_with_rate_limit_backoff(
    service,
    platform: str,
    name: str,
    artist: str,
    *,
    local_duration_s: Optional[int] = None,
    local_duration_ms: int = 0,
    local_is_explicit: bool = False,
    local_isrc: str | None = None,
    log: Optional[Callable[[str], None]] = None,
    backoff_steps: int = 1,
) -> SearchResult:
    """Search through the service and trip the platform breaker on HTTP 429.

    The default strategy is fail-fast: the circuit breaker owns the cooldown,
    so this helper does not issue extra requests after a rate-limit response.
    ``backoff_steps`` remains available for compatibility with the existing
    caller contract.
    """
    rl_backoff: Optional[float] = None
    for _step in range(backoff_steps):
        try:
            return await service.search_with_fallback(
                platform,
                name,
                artist,
                local_duration_s=local_duration_s,
                local_duration_ms=local_duration_ms,
                local_is_explicit=local_is_explicit,
                local_isrc=local_isrc,
            )
        except RateLimitError as exc:
            retry_after = max(1, int(exc.retry_after))
            if rl_backoff is None:
                rl_backoff = float(retry_after)
            breaker = getattr(service, "_cb", {}).get(platform)
            if breaker is not None:
                breaker.trip(retry_after)
            if log:
                log(
                    f"[WARN] 429 {platform}: abriendo breaker ~{retry_after}s · "
                    f"búsqueda abortada para '{name}'"
                )
            raise

    if log:
        log(f"[ERROR] 429: reintentos agotados en {platform}")
    raise RateLimitError(platform, int(rl_backoff or 60))
