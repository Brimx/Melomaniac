"""services/platforms/_common.py — Piezas compartidas por los clientes de plataforma."""

from __future__ import annotations

import asyncio

from core.config import NETWORK_CONCURRENCY
from core.models import Track


def _as_text(value) -> str:
    """Normaliza la descripción de playlist según la forma de cada API.

    YouTube la expone como str, Apple como {"standard": ...} o str,
    Spotify como str. Ausente/None → "".
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("standard", "short", "text"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def catalog_song_item(track: Track) -> dict:
    """Normaliza un resultado de canción de catálogo para la UI."""
    subtitle = (
        f"{track.artist} · {track.album}"
        if track.album and track.album != "Unknown"
        else track.artist
    )
    return {
        "id": track.id,
        "name": track.name,
        "subtitle": subtitle,
        "artist": track.artist,
        "img_url": track.img_url,
        "kind": "songs",
        "external_url": track.source_url,
        "platform": track.platform,
        "track": track,
    }


def catalog_album_item(
    *,
    album_id: str,
    name: str,
    artist: str,
    img_url: str,
    external_url: str,
    platform: str,
    year: str = "",
    item_type: str = "",
) -> dict:
    """Normaliza un resultado de álbum de catálogo para la UI."""
    parts = [p for p in (artist, year, item_type) if p]
    return {
        "id": album_id,
        "name": name,
        "subtitle": " · ".join(parts),
        "artist": artist,
        "img_url": img_url,
        "kind": "albums",
        "external_url": external_url,
        "platform": platform,
        "year": year,
    }


def catalog_artist_item(
    *,
    artist_id: str,
    name: str,
    img_url: str,
    external_url: str,
    platform: str,
    subtitle: str = "",
) -> dict:
    """Normaliza un resultado de artista de catálogo para la UI."""
    return {
        "id": artist_id,
        "name": name,
        "subtitle": subtitle or "Artista",
        "artist": name,
        "img_url": img_url,
        "kind": "artists",
        "external_url": external_url,
        "platform": platform,
    }


GLOBAL_API_SEMAPHORE = asyncio.Semaphore(NETWORK_CONCURRENCY)
