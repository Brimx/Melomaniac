"""
Motor de transformación de datos local para la gestión de playlists.
Proporciona funciones para ordenar y segmentar listas de canciones en memoria
sin realizar peticiones de red, optimizando la experiencia de usuario.
Semver 4.1.0: allowlist sin `platform`, split normalizado, multi-key.
"""

from collections import defaultdict
from typing import Any, Dict, List

from core.models import Track

# Allowlist de claves con sentido musical (platform excluida: no es metadata)
_ALLOWED_SORT_KEYS: frozenset[str] = frozenset({"artist", "album", "name", "duration_ms"})
_ALLOWED_SPLIT_KEYS: frozenset[str] = frozenset({"artist", "album"})


def _get_attr_safe(track: Track, key: str) -> Any:
    """Obtiene un atributo del Track de forma segura para ordenamiento."""
    val = getattr(track, key, None)
    if val is None:
        return ""
    if isinstance(val, str):
        return val.lower().strip()
    return val


def _normalize_split_val(val: Any) -> str:
    """Normaliza valor de split: trim, vacío→Desconocido, conserva capitalización base."""
    if val is None:
        return "Desconocido"
    s = str(val).strip()
    # colapsa espacios internos sin cambiar capitalización
    s = " ".join(s.split())
    return s if s else "Desconocido"


def sort_tracks(tracks: List[Track], keys: List[str], reverse: bool = False) -> List[Track]:
    """
    Ordena una lista de Tracks bajo criterios jerárquicos.

    Args:
        tracks: Lista maestra de canciones.
        keys: Lista de nombres de atributos por los cuales ordenar (ej: ['artist', 'album', 'name']).
              Solo se aceptan keys en allowlist; `platform` se ignora.
        reverse: Si es True, ordena de forma descendente.

    Returns:
        Nueva lista ordenada (no muta la original).
    """
    if not tracks or not keys:
        return list(tracks)
    # valida y filtra allowlist
    clean_keys = [k for k in keys if k in _ALLOWED_SORT_KEYS]
    if not clean_keys:
        return list(tracks)

    def sort_key(track: Track):
        return tuple(_get_attr_safe(track, k) for k in clean_keys)

    return sorted(tracks, key=sort_key, reverse=reverse)


def split_tracks(tracks: List[Track], key: str) -> Dict[str, List[Track]]:
    """
    Agrupa la lista maestra basándose en un atributo específico (normalizado).

    Args:
        tracks: Lista maestra de canciones.
        key: Nombre del atributo por el cual agrupar (artist/album).
             Keys fuera de allowlist retornan dict vacío.

    Returns:
        Diccionario donde la clave es el valor normalizado del atributo.
    """
    if key not in _ALLOWED_SPLIT_KEYS:
        return {}
    segments = defaultdict(list)
    for track in tracks:
        # valor crudo + normalizado (trim, vacío→Desconocido)
        raw = getattr(track, key, None)
        val = _normalize_split_val(raw)
        segments[val].append(track)

    return dict(segments)
