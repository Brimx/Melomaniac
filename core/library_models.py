"""
core/library_models.py — Melomaniac v4.5.1 — Modelos Biblioteca
Fuente única para metadatos de playlists de biblioteca (solo metadatos, nunca tracks).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class LibraryLoadState(Enum):
    """Estado de sincronización por fuente (independiente de LoadState/TransferState)."""

    IDLE = auto()
    LOADING = auto()
    READY = auto()
    STALE = auto()  # último catálogo válido pero obsoleto (red/auth falló)
    ERROR = auto()


@dataclass
class PlaylistSummary:
    """Metadatos mínimos de playlist para Biblioteca."""

    platform: str
    id: str
    name: str
    description: str = ""
    track_count: int = 0
    # URLs de portada normalizadas (1 = single cover, 2-4 = grilla visual)
    cover_urls: list[str] = field(default_factory=list)
    external_url: str = ""  # abrir en plataforma
    synced_at: str = ""  # ISO8601


@dataclass
class LibrarySnapshot:
    """Catálogo cacheado por plataforma."""

    platform: str
    synced_at: str = ""
    items: list[PlaylistSummary] = field(default_factory=list)
    state: LibraryLoadState = LibraryLoadState.IDLE
    error: str = ""
