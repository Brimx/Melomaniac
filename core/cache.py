"""
core/cache.py — MelomaniacPass v5.1 — Helpers de Caché
════════════════════════════════════════════════════════
Funciones comunes para caché de búsquedas (regla 3).
Evita duplicar `cache_key` y `unwrap` en core/state.py.
"""

from __future__ import annotations

from typing import Union

from core.models import SearchResult
from engine.normalizer import clean_metadata


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
