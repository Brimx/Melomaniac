"""
core/library_store.py — Persistencia atómica de metadatos Biblioteca
Solo metadatos de playlists, nunca tracks. Atomic tmp+os.replace, JSON corrupto -> vacío sin crash.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict

from core.config import PLATFORM_ORDER
from core.library_models import LibraryLoadState, LibrarySnapshot, PlaylistSummary
from services.authentication import CONFIG_DIR, ensure_config_dir

LIBRARY_JSON = CONFIG_DIR / "library_playlists.json"
VERSION = 1


def _summary_to_dict(s: PlaylistSummary) -> dict:
    return {
        "platform": s.platform,
        "id": s.id,
        "name": s.name,
        "description": s.description or "",
        "track_count": int(s.track_count or 0),
        "cover_urls": list(s.cover_urls or []),
        "external_url": s.external_url or "",
        "synced_at": s.synced_at or "",
    }


def _summary_from_dict(d: dict, platform_fallback: str = "") -> PlaylistSummary:
    return PlaylistSummary(
        platform=d.get("platform") or platform_fallback,
        id=str(d.get("id") or ""),
        name=str(d.get("name") or "Playlist"),
        description=str(d.get("description") or ""),
        track_count=int(d.get("track_count") or 0),
        cover_urls=list(d.get("cover_urls") or ([d["cover_url"]] if d.get("cover_url") else [])),
        external_url=str(d.get("external_url") or d.get("url") or ""),
        synced_at=str(d.get("synced_at") or ""),
    )


def load_library() -> Dict[str, LibrarySnapshot]:
    """Carga desde disco. JSON inexistente/corrupto -> snapshots vacíos IDLE."""
    snapshots: Dict[str, LibrarySnapshot] = {
        p: LibrarySnapshot(platform=p) for p in PLATFORM_ORDER
    }
    if not LIBRARY_JSON.exists():
        return snapshots
    try:
        raw = json.loads(LIBRARY_JSON.read_text(encoding="utf-8"))
    except Exception:
        return snapshots
    sources = raw.get("sources") if isinstance(raw, dict) else None
    if not isinstance(sources, dict):
        return snapshots
    for platform, entry in sources.items():
        if platform not in snapshots or not isinstance(entry, dict):
            continue
        items_raw = entry.get("items") or []
        items: list[PlaylistSummary] = []
        if isinstance(items_raw, list):
            for it in items_raw:
                if not isinstance(it, dict) or not it.get("id"):
                    continue
                items.append(_summary_from_dict(it, platform))
        snapshots[platform] = LibrarySnapshot(
            platform=platform,
            synced_at=str(entry.get("synced_at") or ""),
            items=items,
            state=LibraryLoadState.READY if items else LibraryLoadState.IDLE,
        )
    return snapshots


def save_library(snapshots: Dict[str, LibrarySnapshot]) -> None:
    """Persistencia atómica versionada."""
    ensure_config_dir()
    payload = {
        "version": VERSION,
        "sources": {
            platform: {
                "synced_at": snap.synced_at or "",
                "items": [_summary_to_dict(s) for s in snap.items],
            }
            for platform, snap in snapshots.items()
            if platform in PLATFORM_ORDER
        },
    }
    # Asegura que todas las plataformas existan aunque estén vacías
    for p in PLATFORM_ORDER:
        if p not in payload["sources"]:
            payload["sources"][p] = {"synced_at": "", "items": []}
    tmp = LIBRARY_JSON.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, LIBRARY_JSON)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
