"""
services/library_state.py — Coordinador Biblioteca (inyección service)
Independiente de AppState, no contamina transfer. Observer simple.
"""

from __future__ import annotations

import time
from typing import Callable, Dict, Optional

from core.config import PLATFORM_ORDER
from core.library_models import LibraryLoadState, LibrarySnapshot, PlaylistSummary
from core.library_store import load_library, save_library
from services.circuit_breaker import RateLimitError


class LibraryState:
    """Coordina sync_source / get_playlists / refresh. Persistencia atómica."""

    def __init__(self, service=None):
        self.service = service
        self._snapshots: Dict[str, LibrarySnapshot] = load_library()
        self._listeners: list[Callable[[], None]] = []
        self._selected_platform: Optional[str] = None

    # Observer
    def subscribe(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def notify(self) -> None:
        for cb in self._listeners:
            try:
                cb()
            except Exception:
                pass

    # Accessors
    @property
    def selected_platform(self) -> Optional[str]:
        return self._selected_platform

    def get_snapshot(self, platform: str) -> LibrarySnapshot:
        return self._snapshots.get(platform) or LibrarySnapshot(platform=platform)

    def get_playlists(self, platform: str) -> list[PlaylistSummary]:
        return list(self.get_snapshot(platform).items)

    def load_state(self, platform: str) -> LibraryLoadState:
        return self.get_snapshot(platform).state

    def set_service(self, service) -> None:
        self.service = service

    def select_platform(self, platform: str) -> None:
        if platform in PLATFORM_ORDER:
            self._selected_platform = platform
            self.notify()

    async def sync_source(self, platform: str) -> LibrarySnapshot:
        snap = self._snapshots.get(platform) or LibrarySnapshot(platform=platform)
        snap.state = LibraryLoadState.LOADING
        snap.error = ""
        self.notify()
        if self.service is None:
            snap.state = LibraryLoadState.ERROR
            snap.error = "service no disponible"
            # conserva último válido si existe
            if snap.items:
                snap.state = LibraryLoadState.STALE
            self.notify()
            return snap
        try:
            items = await self.service.list_library_playlists(platform)
            snap.items = items
            snap.synced_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            snap.state = LibraryLoadState.READY
            snap.error = ""
            self._snapshots[platform] = snap
            save_library(self._snapshots)
        except RateLimitError as e:
            # conserva catálogo válido y marca STALE
            snap.error = f"Rate limit {e.retry_after}s"
            snap.state = LibraryLoadState.STALE if snap.items else LibraryLoadState.ERROR
            try:
                e_platform = getattr(e, "platform", platform)
                if self.service and hasattr(self.service, "_cb"):
                    self.service._cb.get(e_platform or platform, self.service._cb[platform]).trip(e.retry_after)  # type: ignore
            except Exception:
                pass
        except Exception as exc:
            msg = str(exc)[:300]
            # auth/network falla -> STALE si había caché
            if snap.items:
                snap.state = LibraryLoadState.STALE
                snap.error = msg
            else:
                snap.state = LibraryLoadState.ERROR
                snap.error = msg
        self.notify()
        return snap

    async def refresh_source(self, platform: str) -> LibrarySnapshot:
        return await self.sync_source(platform)

    def open_playlist_detail(self, summary: PlaylistSummary) -> PlaylistSummary:
        # tracks se cargan bajo demanda vía service.fetch_playlist en detail view
        return summary
