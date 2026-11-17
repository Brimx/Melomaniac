"""
services/cover_warmer.py — Descarga en segundo plano de portadas al caché de disco
══════════════════════════════════════════════════════════════════════════════════
El caché de portadas (``services/cover_cache``) ya sabe descargar, pero nadie lo
alimentaba desde la UI: cada arranque volvía a pedir las portadas a la red.
``request(url)`` encola una descarga; un único worker con concurrencia acotada
rellena el disco para que el siguiente repintado use el data URI local
(``cover_cache.cached_data_uri``).

Es perezoso y seguro fuera del loop: no crea worker hasta el primer
``request`` dentro de un loop de eventos, y nunca bloquea el hilo de UI
(``asyncio.to_thread`` + semáforo).
"""

from __future__ import annotations

import asyncio
import threading
from typing import Optional

from services import cover_cache

_MAX_CONCURRENCY = 4

_queue: Optional["asyncio.Queue[str]"] = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_pending: set[str] = set()
_done: set[str] = set()
_started = False
_lock = threading.Lock()


def _ensure_worker() -> None:
    """Arranca el worker una sola vez, dentro del loop de eventos en curso."""
    global _queue, _loop, _started
    if _started:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    with _lock:
        if _started:
            return
        _queue = asyncio.Queue()
        _loop = loop
        _started = True
        loop.create_task(_drain())


def request(url: str) -> None:
    """Encola una portada para descargar; no-op si no aplica, ya está o va en vuelo."""
    if not url or not url.startswith(("http://", "https://")):
        return
    if url in _done or url in _pending:
        return
    if cover_cache.cached_path_for_url(url) is not None:
        _done.add(url)
        return
    _ensure_worker()
    q, loop = _queue, _loop
    if q is None or loop is None:
        return
    _pending.add(url)
    try:
        loop.call_soon_threadsafe(q.put_nowait, url)
    except Exception:
        _pending.discard(url)


async def _drain() -> None:
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)
    assert _queue is not None

    async def _one(url: str) -> None:
        async with sem:
            try:
                await asyncio.to_thread(cover_cache.ensure_cached, url)
            except Exception:
                pass
            finally:
                _pending.discard(url)
                _done.add(url)

    while True:
        url = await _queue.get()
        asyncio.create_task(_one(url))
