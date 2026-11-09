"""services/platforms/_common.py — Piezas compartidas por los mixins de plataforma."""

from __future__ import annotations

import asyncio

from core.config import NETWORK_CONCURRENCY


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


GLOBAL_API_SEMAPHORE = asyncio.Semaphore(NETWORK_CONCURRENCY)
