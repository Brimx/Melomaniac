"""
services/local_index.py — Índice local compartido y escaneo en segundo plano
═══════════════════════════════════════════════════════════════════════
Dos piezas que la Biblioteca necesita y que no caben en ``engine`` (que no
conoce rutas de configuración) ni en la UI (que no debe bloquear el evento):
el ``LocalIndex`` compartido sobre ``config/local_index.json`` y el escaneo
de carpetas grandes en un hilo aparte.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from engine.local_index import LocalIndex
from engine.parsers import scan_audio_folder
from services.authentication import CONFIG_DIR

LOCAL_INDEX_JSON = CONFIG_DIR / "local_index.json"

# Una carpeta con más archivos que esto se escanea en segundo plano: leer
# Mutagen de todo el árbol antes de pintar la primera fila se siente roto.
SYNC_SCAN_LIMIT = 150

_index: Optional[LocalIndex] = None


def shared_index() -> LocalIndex:
    """``LocalIndex`` perezoso y único por proceso."""
    global _index
    if _index is None:
        _index = LocalIndex(LOCAL_INDEX_JSON)
    return _index


def save_shared_index() -> bool:
    """Persiste el índice compartido; ``False`` si no había nada nuevo."""
    return shared_index().save()


def quick_count(folder: str, *, limit: int = SYNC_SCAN_LIMIT) -> int:
    """Cuenta archivos de audio de ``folder`` sin leer etiquetas.

    Se detiene en ``limit``: el número exacto solo importa cuando es pequeño.
    """
    from engine.parsers import list_audio_files

    return len(list_audio_files(folder, limit=limit))


async def scan_folder_async(folder: str, *, limit: int = 5000):
    """Escanea ``folder`` fuera del evento y deja el índice listo para guardar.

    Returns:
        ``(tracks, index)`` donde ``tracks`` es la lista de ``Track``.
    """
    index = shared_index()
    tracks = await asyncio.to_thread(scan_audio_folder, folder, limit=limit, index=index)
    return tracks, index
