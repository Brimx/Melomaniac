"""
services/playlist_cache.py — Caché en disco de playlists de plataforma
══════════════════════════════════════════════════════════════════════════════
Guarda el resultado de ``fetch_playlist`` bajo ``config/playlist_cache/`` para
reabrir una playlist sin volver a paginar la API. Una entrada dentro del TTL se
sirve tal cual; una vencida se devuelve al instante y el servicio la refresca
en segundo plano (stale-while-revalidate).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from core.config import PLAYLIST_CACHE_MAX_FILES, PLAYLIST_CACHE_TTL_S
from core.models import PlaylistMeta, Track
from engine.local_manifests import (
    manifest_key,
    track_from_manifest_dict,
    track_to_manifest_dict,
)
from services.authentication import CONFIG_DIR, ensure_config_dir

CACHE_DIR = CONFIG_DIR / "playlist_cache"
CACHE_VERSION = 1


@dataclass
class CachedPlaylist:
    """Entrada de caché reconstruida desde disco."""

    meta: PlaylistMeta
    tracks: list[Track]
    fetched_at: float

    @property
    def age_s(self) -> float:
        return max(0.0, time.time() - self.fetched_at)

    def is_fresh(self, ttl: int = PLAYLIST_CACHE_TTL_S) -> bool:
        return self.age_s <= max(0, int(ttl))


def _cache_path(platform: str, playlist_id: str) -> Path:
    return CACHE_DIR / f"{manifest_key(platform, playlist_id)}.json"


def load(platform: str, playlist_id: str) -> CachedPlaylist | None:
    """Lee la entrada de disco; ``None`` si falta, está corrupta o cambió de versión."""
    try:
        raw = json.loads(_cache_path(platform, playlist_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != CACHE_VERSION:
        return None
    tracks_raw = raw.get("tracks")
    if not isinstance(tracks_raw, list):
        return None
    meta_raw = raw.get("meta") if isinstance(raw.get("meta"), dict) else {}
    tracks = [
        track_from_manifest_dict(item, platform)
        for item in tracks_raw
        if isinstance(item, dict)
    ]
    try:
        fetched_at = float(raw.get("fetched_at") or 0.0)
    except (TypeError, ValueError):
        fetched_at = 0.0
    return CachedPlaylist(
        meta=PlaylistMeta(
            name=str(meta_raw.get("name") or ""),
            description=str(meta_raw.get("description") or ""),
        ),
        tracks=tracks,
        fetched_at=fetched_at,
    )


def save(platform: str, playlist_id: str, meta, tracks) -> None:
    """Escribe la entrada de forma atómica y poda el caché si supera el tope."""
    if meta is None:
        return
    payload = {
        "version": CACHE_VERSION,
        "platform": platform,
        "playlist_id": playlist_id,
        "fetched_at": time.time(),
        "meta": {
            "name": str(getattr(meta, "name", "") or ""),
            "description": str(getattr(meta, "description", "") or ""),
        },
        "tracks": [track_to_manifest_dict(t, platform) for t in (tracks or [])],
    }
    path = _cache_path(platform, playlist_id)
    tmp = path.with_suffix(".tmp")
    try:
        ensure_config_dir()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # pylint: disable=broad-exception-caught
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        return
    prune()


def prune(max_files: int = PLAYLIST_CACHE_MAX_FILES) -> None:
    """Borra las entradas más antiguas cuando se supera el tope de archivos."""
    if max_files <= 0:
        return
    try:
        files = [p for p in CACHE_DIR.glob("*.json") if p.is_file()]
    except OSError:
        return
    if len(files) <= max_files:
        return

    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    files.sort(key=_mtime)
    for stale in files[: len(files) - max_files]:
        try:
            stale.unlink()
        except OSError:
            pass
