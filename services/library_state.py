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

    def local_folder_summaries(self) -> list[PlaylistSummary]:
        """Tarjetas de las carpetas locales configuradas por el usuario."""
        return [self._summary_for(folder) for folder in self.local_folders]

    def downloaded_playlist_summaries(self) -> list[PlaylistSummary]:
        """Tarjetas de subcarpetas descargadas con ``playlist.json``."""
        from services.local_index import quick_count

        out: list[PlaylistSummary] = []
        for folder in self.local_folders:
            for child in self._playlist_subfolders(folder):
                if quick_count(child) <= 0:
                    continue
                out.append(self._summary_for(child, playlist_folder=True))
        return out

    def remove_downloaded_playlist(self, folder: str) -> bool:
        """Quita el manifiesto ``playlist.json`` de una subcarpeta descargada sin borrar audios."""
        from engine.downloader import PLAYLIST_MANIFEST_NAME

        target = Path(folder) / PLAYLIST_MANIFEST_NAME
        if not target.is_file():
            return False
        try:
            target.unlink()
        except OSError:
            return False
        try:
            if Path(folder).is_dir() and not any(Path(folder).iterdir()):
                Path(folder).rmdir()
        except OSError:
            pass
        self.notify()
        return True

    def imported_manifest_summaries(self) -> list[PlaylistSummary]:
        """Tarjetas de playlists importadas como manifiesto local (sin audio)."""
        from engine.local_manifests import list_local_manifests

        out: list[PlaylistSummary] = []
        for manifest in list_local_manifests():
            out.append(self._summary_for_manifest(manifest))
        return out

    @staticmethod
    def _summary_for_manifest(manifest) -> PlaylistSummary:
        """Construye la tarjeta de Biblioteca para un manifiesto local importado."""
        display_name = manifest.name or "Playlist importada"
        if manifest.platform:
            display_name = f"{display_name} · {manifest.platform}"
        desc_parts = ["Sin audio descargado"]
        if manifest.owner:
            desc_parts.append(f"Por {manifest.owner}")
        if manifest.description:
            desc_parts.append(manifest.description)
        covers = list(manifest.cover_urls or ([manifest.img_url] if manifest.img_url else []))
        return PlaylistSummary(
            platform=LOCAL_LIBRARY_LABEL,
            id=manifest.path or manifest.manifest_id,
            name=display_name,
            description=" · ".join(desc_parts),
            track_count=len(manifest.tracks),
            cover_urls=covers,
            external_url=manifest.external_url or "",
            synced_at=manifest.created_at or "",
        )

    def local_summaries(self) -> list[PlaylistSummary]:
        """Carpetas locales, playlists descargadas y manifiestos importados.

        Incluye las subcarpetas creadas por Descargas que llevan
        ``playlist.json``: son playlists descargadas, no carpetas sueltas. La
        carpeta raíz sigue apareciendo para no perder las descargas antiguas,
        que se hicieron en plano.

        El recuento sale del manifiesto cuando existe; si no, de un recorrido
        que se detiene en ``SYNC_SCAN_LIMIT``. Antes se listaba la carpeta
        entera en cada refresco, con varios ``stat`` por archivo.
        """
        return [
            *self.local_folder_summaries(),
            *self.downloaded_playlist_summaries(),
            *self.imported_manifest_summaries(),
        ]

    def list_local_manifests(self):
        """Lista los manifiestos locales guardados en ``local_playlists``."""
        from engine.local_manifests import list_local_manifests

        return list_local_manifests()

    def load_local_manifest(self, identifier: str):
        """Carga un manifiesto local por ``manifest_id`` o ruta JSON."""
        from engine.local_manifests import load_local_manifest

        return load_local_manifest(identifier)

    def is_imported_manifest(self, summary_or_id: PlaylistSummary | str) -> bool:
        """Indica si ``summary_or_id`` corresponde a un manifiesto importado sin audio."""
        ident = summary_or_id.id if isinstance(summary_or_id, PlaylistSummary) else str(summary_or_id or "")
        if not ident:
            return False
        return self.load_local_manifest(ident) is not None

    def save_local_manifest(self, **kwargs):
        """Guarda un manifiesto local y notifica a los suscriptores."""
        from engine.local_manifests import save_local_manifest

        manifest = save_local_manifest(**kwargs)
        self.notify()
        return manifest

    def remove_local_manifest(self, identifier: str) -> bool:
        """Elimina un manifiesto local y notifica si existía."""
        from engine.local_manifests import remove_local_manifest

        removed = remove_local_manifest(identifier)
        if removed:
            self.notify()
        return removed

    async def import_playlist_manifest(
        self,
        platform: str,
        playlist_id_or_url: str,
        *,
        owner: str = "",
        name: str = "",
        img_url: str = "",
        cover_urls: Optional[list[str]] = None,
        external_url: str = "",
    ):
        """Importa una playlist remota como manifiesto local sin descargar audio."""
        from core.transfer import normalize_playlist_id
        from engine.local_manifests import save_local_manifest

        if self.service is None:
            raise RuntimeError("service no disponible")
        pid = normalize_playlist_id(platform, playlist_id_or_url)
        if not pid:
            raise ValueError("Playlist sin identificador válido")

        meta, tracks = await self.service.fetch_playlist(platform, pid, None)
        meta_name = str(getattr(meta, "name", "") or "") if meta is not None else ""
        meta_desc = str(getattr(meta, "description", "") or "") if meta is not None else ""
        resolved_name = name or meta_name or f"Playlist {pid}"

        resolved_covers: list[str] = [str(u) for u in (cover_urls or []) if str(u or "").strip()]
        resolved_img = str(img_url or "").strip()
        if resolved_img and not resolved_covers:
            resolved_covers = [resolved_img]

        if not resolved_covers:
            snap = self._snapshots.get(platform)
            if snap is not None:
                for summ in snap.items:
                    summ_ext_id = normalize_playlist_id(platform, summ.external_url or "") if summ.external_url else ""
                    if summ.id == pid or summ_ext_id == pid:
                        resolved_covers = [str(u) for u in (summ.cover_urls or []) if str(u or "").strip()]
                        if not external_url and summ.external_url:
                            external_url = summ.external_url
                        break

        if not resolved_covers and meta is not None:
            meta_covers = getattr(meta, "cover_urls", None) or []
            resolved_covers = [str(u) for u in meta_covers if str(u or "").strip()]
            if not resolved_covers:
                meta_img = str(getattr(meta, "img_url", "") or "").strip()
                if meta_img:
                    resolved_covers = [meta_img]

        if not resolved_covers:
            seen_urls: set[str] = set()
            for tr in tracks:
                u = str(getattr(tr, "img_url", "") or "").strip()
                if u and u not in seen_urls:
                    seen_urls.add(u)
                    resolved_covers.append(u)
                    if len(resolved_covers) >= 4:
                        break

        if resolved_covers and not resolved_img:
            resolved_img = resolved_covers[0]

        manifest = save_local_manifest(
            platform=platform,
            playlist_id=pid,
            name=resolved_name,
            owner=owner,
            description=meta_desc,
            img_url=resolved_img,
            cover_urls=resolved_covers,
            external_url=external_url,
            tracks=tracks,
        )
        self.notify()
        return manifest

    async def import_summary_as_manifest(self, summary: PlaylistSummary):
        """Importa una playlist ya sincronizada en Biblioteca como manifiesto local."""
        return await self.import_playlist_manifest(
            summary.platform,
            summary.id,
            name=summary.name,
            cover_urls=list(summary.cover_urls or []),
            external_url=summary.external_url or "",
        )

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
