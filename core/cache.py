"""
core/cache.py — Melomaniac v4.6.0 — Helpers de Caché
════════════════════════════════════════════════════════
Funciones comunes para caché de búsquedas (regla 3).
Evita duplicar `cache_key` y `unwrap` en core/state.py.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Hashable, TypeVar, Union

from core.models import SearchResult
from engine.normalizer import clean_metadata

T = TypeVar("T")


class SingleFlight:
    """
    Deduplica operaciones asíncronas concurrentes con la misma clave.

    Varias pistas de una playlist pueden compartir título y artista. Sin
    deduplicación, cada una lanza su propia búsqueda y consume cuota de API
    por el mismo resultado. La primera llamada ejecuta la operación; las
    demás esperan su resultado. Los errores no se cachean: cada llamada
    recibe la excepción y el vuelo se libera para un nuevo intento.
    """

    def __init__(self) -> None:
        self._inflight: dict[Hashable, asyncio.Future] = {}

    async def run(self, key: Hashable, operation: Callable[[], Awaitable[T]]) -> T:
        """Ejecuta ``operation`` una sola vez por ``key`` mientras esté en vuelo."""
        current = self._inflight.get(key)
        if current is not None:
            return await asyncio.shield(current)

        loop = asyncio.get_running_loop()
        flight: asyncio.Future = loop.create_future()
        self._inflight[key] = flight
        try:
            result = await operation()
        except BaseException as exc:
            if not flight.done():
                flight.set_exception(exc)
                # Marca la excepción como leída: los seguidores pueden haber
                # sido cancelados y asyncio no debe reportarla como huérfana.
                flight.exception()
            raise
        else:
            if not flight.done():
                flight.set_result(result)
            return result
        finally:
            self._inflight.pop(key, None)


def make_cache_key(name: str, artist: str, destination: str) -> str:
    """
    Clave de caché normalizada: `cn|||ca|||dest`.

    Usa `clean_metadata` para que "Bohemian Rhapsody (Remastered)"
    comparta clave con "Bohemian Rhapsody".
    Pasa datos simples, no controles UI (regla 10).
    """
    cn, ca = clean_metadata(name, artist)
    return f"{cn.lower()}|||{ca.lower()}|||{destination}"


def unwrap_search_result(raw: Union[SearchResult, str, dict, None]) -> SearchResult:
    """
    Normaliza valores legacy de `search_cache`.

    `raw` puede ser:
    - `SearchResult` (actual)
    - `str` track_id legacy
    - `dict` serializado con `track_id/needs_review/...`
    - `None`
    """
    if isinstance(raw, SearchResult):
        return raw
    if isinstance(raw, str):
        return SearchResult(raw, False) if raw else SearchResult(None, False)
    if isinstance(raw, dict):
        return SearchResult(
            track_id=raw.get("track_id"),
            needs_review=bool(raw.get("needs_review", False)),
            low_confidence=bool(raw.get("low_confidence", False)),
            isrc=raw.get("isrc"),
        )
    return SearchResult(None, False)


def make_search_flight_key(
    platform: str,
    name: str,
    artist: str,
    *,
    duration_s: int | None = None,
    duration_ms: int = 0,
    is_explicit: bool = False,
    isrc: str | None = None,
) -> str:
    """
    Clave de deduplicación para búsquedas concurrentes.

    Incluye la plataforma y todos los datos que afectan al hunter: título y
    artista limpios, duración en milisegundos (normaliza el fallback en
    segundos), flag explicit e ISRC. Dos pistas con la misma identidad
    comparten una sola búsqueda.
    """
    cn, ca = clean_metadata(name, artist)
    ms = int(duration_ms or (duration_s * 1000 if duration_s else 0))
    return "|||".join((
        platform,
        cn.lower(),
        ca.lower(),
        str(ms),
        "1" if is_explicit else "0",
        str(isrc or "").strip().upper(),
    ))
