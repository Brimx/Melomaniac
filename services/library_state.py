"""
services/library_state.py — Coordinador Biblioteca (inyección service)
Independiente de AppState, no contamina transfer. Observer simple.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Dict, Optional

from core.config import LOCAL_LIBRARY_LABEL, PLATFORM_ORDER
from core.library_models import LibraryLoadState, LibrarySnapshot, PlaylistSummary
from core.library_store import load_library, save_library
from engine.parsers import _folder_cover, _sidecar_data_uri
from services import settings_store
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

    # Fuente local: carpetas con audio agregadas por el usuario
    @property
    def local_folders(self) -> list[str]:
        return settings_store.get_local_folders()

    def local_summaries(self) -> list[PlaylistSummary]:
        """Una ``PlaylistSummary`` por carpeta local (id = ruta absoluta).

        Incluye las subcarpetas creadas por Descargas que llevan
        ``playlist.json``: son playlists descargadas, no carpetas sueltas. La
        carpeta raíz sigue apareciendo para no perder las descargas antiguas,
        que se hicieron en plano.

        El recuento sale del manifiesto cuando existe; si no, de un recorrido
        que se detiene en ``SYNC_SCAN_LIMIT``. Antes se listaba la carpeta
        entera en cada refresco, con varios ``stat`` por archivo.
        """
        out: list[PlaylistSummary] = []
        for folder in self.local_folders:
            out.append(self._summary_for(folder))
            for child in self._playlist_subfolders(folder):
                out.append(self._summary_for(child, playlist_folder=True))
        return out

    def forget_local_index(self) -> None:
        """Persiste el índice local; se llama al cerrar la app."""
        from services.local_index import save_shared_index

        save_shared_index()

    @staticmethod
    def _playlist_subfolders(folder: str) -> list[str]:
        """Subcarpetas de ``folder`` que contienen un manifiesto de playlist."""
        from engine.downloader import PLAYLIST_MANIFEST_NAME

        try:
            return sorted(
                str(child) for child in Path(folder).iterdir()
                if child.is_dir() and (child / PLAYLIST_MANIFEST_NAME).is_file()
            )
        except OSError:
            return []

    @staticmethod
    def _summary_for(folder: str, *, playlist_folder: bool = False) -> PlaylistSummary:
        """Tarjeta de una carpeta; con manifiesto usa su nombre y su orden."""
        from engine.downloader import read_playlist_manifest
        from services.local_index import quick_count, shared_index

        manifest = read_playlist_manifest(folder) if playlist_folder else {}
        name = str(manifest.get("name") or "") or Path(folder).name or folder
        if manifest.get("platform"):
            name = f"{name} · {manifest['platform']}"
        sidecar = _folder_cover(folder)
        index = shared_index()
        # El builder recibe la ruta del sidecar, no la carpeta.
        cover = (
            index.folder_cover(folder, sidecar, _sidecar_data_uri) if sidecar else ""
        )
        count = manifest.get("count") if manifest else None
        return PlaylistSummary(
            platform=LOCAL_LIBRARY_LABEL, id=folder,
            name=name, description=folder,
            track_count=int(count) if isinstance(count, int) and count
            else quick_count(folder),
            cover_urls=[cover] if cover else [],
        )

    def add_local_folder(self, path: str) -> bool:
        added = settings_store.add_local_folder(path)
        if added:
            self.notify()
        return added

    def remove_local_folder(self, path: str) -> bool:
        removed = settings_store.remove_local_folder(path)
        if removed:
            self.notify()
        return removed

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
            auth_manager = getattr(self.service, "auth_manager", None)
            if auth_manager is not None:
                auth_manager.record_operation_failure(platform, exc)
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
