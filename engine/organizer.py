"""
Motor de transformación de datos local para la gestión de playlists.
Proporciona funciones para ordenar y segmentar listas de canciones en memoria
sin realizar peticiones de red, optimizando la experiencia de usuario.
Semver 4.5.0: allowlist sin `platform`, split normalizado, multi-key.
"""

from collections import defaultdict
from typing import Any, Dict, List

from core.models import Track

# Allowlist de claves con sentido musical (platform excluida: no es metadata)
# Semver 4.2: añade release_date, track_number, original_position.
# genre queda pendiente para biblioteca (no external enrichment en esta fase, §10).
_ALLOWED_SORT_KEYS: frozenset[str] = frozenset({
    "artist", "album", "name", "duration_ms",
    "release_date", "track_number", "original_position",
})
_ALLOWED_SPLIT_KEYS: frozenset[str] = frozenset({"artist", "album", "release_date"})


def _get_attr_safe(track: Track, key: str) -> Any:
    """Obtiene un atributo del Track de forma segura para ordenamiento."""
    # original_position no es campo de Track; se deriva del orden fuente si el caller lo inyecta como attr
    if key == "original_position":
        # si el Track tiene _orig_pos inyectado (AppState), úsalo; si no, fallback a 0 preservando estable
        val = getattr(track, "_orig_pos", None)
        if val is None:
            val = getattr(track, "original_position", None)
        if val is not None:
            try:
                return int(val)
            except Exception:
                return 0
        return 0
    val = getattr(track, key, None)
    if val is None:
        return ""
    if isinstance(val, str):
        # release_date: orden cronológico como string lower (YYYY-MM-DD) funciona lexicográficamente
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
    Ordena una lista de Tracks bajo criterios jerárquicos (genérico, no por criterio específico).

    Args:
        tracks: Lista maestra de canciones.
        keys: Lista de nombres de atributos por los cuales ordenar (ej: ['artist', 'album', 'name', 'release_date']).
              Solo se aceptan keys en allowlist; `platform`/`genre` se ignoran (genre pendiente).
        reverse: Si es True, ordena de forma descendente.

    Returns:
        Nueva lista ordenada (no muta la original). ORIGINAL = retorna copia sin ordenar si keys vacía.
    """
    if not tracks or not keys:
        return list(tracks)
    # ORIGINAL: si todas las keys son original_position sin reverse, preserva orden fuente
    if len(keys) == 1 and keys[0] == "original_position" and not reverse:
        return list(tracks)
    clean_keys = [k for k in keys if k in _ALLOWED_SORT_KEYS]
    if not clean_keys:
        return list(tracks)

    def sort_key(track: Track):
        return tuple(_get_attr_safe(track, k) for k in clean_keys)

    return sorted(tracks, key=sort_key, reverse=reverse)


def group_tracks_stable(tracks: List[Track], key: str) -> List[Track]:
    """
    Agrupa estable O(n): coloca elementos relacionados juntos conservando orden relativo
    y posición del grupo = 1ª aparición (§12, §15). Retorna lista aplanada, no dict.
    """
    if key not in _ALLOWED_SPLIT_KEYS:
        return list(tracks)
    seen: dict[str, list[Track]] = {}
    order: list[str] = []
    for t in tracks:
        val = _normalize_split_val(getattr(t, key, None))
        if val not in seen:
            seen[val] = []
            order.append(val)
        seen[val].append(t)
    out: List[Track] = []
    for k in order:
        out.extend(seen[k])
    return out


def organize_with_grouping(
    tracks: List[Track],
    group_by: str | None,
    sort_keys: List[str] | None,
    reverse: bool = False,
) -> List[Track]:
    """
    §11-14: GROUP → SORT per-grupo. Si group_by es None, solo sort plano.
    Si sort_keys es None/ORIGINAL, solo agrupa estable.
    """
    if not tracks:
        return []
    if group_by and group_by in _ALLOWED_SPLIT_KEYS:
        # agrupa estable primero
        grouped = group_tracks_stable(tracks, group_by)
        if sort_keys and sort_keys != ["original_position"]:
            # sort dentro de cada grupo (§14)
            clean_sort = [k for k in sort_keys if k in _ALLOWED_SORT_KEYS]
            if not clean_sort:
                return grouped
            # reagrupa para ordenar intra-grupo
            buckets: dict[str, list[Track]] = {}
            order: list[str] = []
            for t in grouped:
                k = _normalize_split_val(getattr(t, group_by, None))
                if k not in buckets:
                    buckets[k] = []
                    order.append(k)
                buckets[k].append(t)
            out: List[Track] = []
            for k in order:
                bucket = buckets[k]
                out.extend(sorted(bucket, key=lambda tr: tuple(_get_attr_safe(tr, kk) for kk in clean_sort), reverse=reverse))
            return out
        return grouped
    # sin agrupar: sort plano
    if sort_keys:
        return sort_tracks(tracks, sort_keys, reverse)
    return list(tracks)


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
