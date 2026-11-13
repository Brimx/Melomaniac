"""
Motor de transformación de datos local para la gestión de playlists.
Proporciona funciones para ordenar y segmentar listas de canciones en memoria
sin realizar peticiones de red, optimizando la experiencia de usuario.
Semver 4.6.0: allowlist sin `platform`, split normalizado, multi-key.
"""

from collections import defaultdict
from dataclasses import replace
import re
from typing import Any, Dict, Iterable, List
import unicodedata

from core.models import Track
from engine.normalizer import _normalize_title, clean_metadata, normalize_isrc

# Allowlist de claves con sentido musical (platform excluida: no es metadata)
# Semver 4.4.3: añade genre + title(=name) para División por Título/Género §2,5.
# genre: Apple genreNames independiente; Spotify vía artist.genres; YTM pendiente fuente externa (deshabilitado en UI si vacío).
_ALLOWED_SORT_KEYS: frozenset[str] = frozenset({
    "artist", "album", "name", "duration_ms",
    "release_date", "track_number", "original_position", "genre",
})
_ALLOWED_SPLIT_KEYS: frozenset[str] = frozenset({"artist", "album", "release_date", "genre", "name"})


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
        keys: Lista de nombres de atributos por los cuales ordenar (ej: ['artist', 'album', 'name', 'release_date', 'genre']).
              Solo se aceptan keys en allowlist.
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
    Soporta Artista/Álbum/Género/Título(name) §2,5 — Género YTM pendiente fuente.

    Args:
        tracks: Lista maestra de canciones.
        key: Nombre del atributo por el cual agrupar (artist/album/genre/name).
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


_DEFAULT_DEDUP_TOLERANCE_MS = 3000
_PLACEHOLDER_ARTISTS: frozenset[str] = frozenset({
    "", "artista desconocido", "desconocido", "unknown", "unknown artist",
})
_PLACEHOLDER_ALBUMS: frozenset[str] = frozenset({
    "", "álbum desconocido", "album desconocido", "archivo local",
    "desconocido", "unknown", "unknown album",
})


def _effective_duration_ms(track: Track) -> int:
    """Obtiene la duración en ms desde ``duration_ms`` o parseando ``duration``."""
    raw_ms = int(getattr(track, "duration_ms", 0) or 0)
    if raw_ms > 0:
        return raw_ms
    dur_str = str(getattr(track, "duration", "") or "").strip()
    if not dur_str:
        return 0
    try:
        parts = [int(p) for p in dur_str.split(":")]
    except ValueError:
        return 0
    if len(parts) == 2:
        return max(0, (parts[0] * 60 + parts[1]) * 1000)
    if len(parts) == 3:
        return max(0, (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000)
    return 0


def _normalize_meta_text(text: str) -> str:
    """Normaliza título o artista para deduplicación conservando núcleo semántico."""
    raw = unicodedata.normalize("NFKC", str(text or "")).strip()
    if not raw:
        return ""
    cleaned = _normalize_title(raw)
    cleaned, _ = clean_metadata(cleaned, "")
    cleaned = re.sub(r"\s*[-–]\s*$", "", cleaned)
    cleaned = " ".join(cleaned.split()).strip().casefold()
    if cleaned:
        return cleaned
    return " ".join(raw.split()).strip().casefold()


def _format_duration_str(duration_ms: int) -> str:
    if duration_ms <= 0:
        return ""
    total_s = duration_ms // 1000
    return f"{total_s // 60}:{total_s % 60:02d}"


def _merge_track_metadata(existing: Track, candidate: Track) -> tuple[Track, bool]:
    """Enriquece campos vacíos o placeholder de ``existing`` con ``candidate``."""
    updates: dict[str, Any] = {}

    if not str(existing.id or "").strip() and str(candidate.id or "").strip():
        updates["id"] = candidate.id
    if not str(existing.platform or "").strip() and str(candidate.platform or "").strip():
        updates["platform"] = candidate.platform
    if not str(existing.name or "").strip() and str(candidate.name or "").strip():
        updates["name"] = candidate.name

    ex_artist = str(existing.artist or "").strip()
    ca_artist = str(candidate.artist or "").strip()
    if ca_artist and (
        not ex_artist
        or (ex_artist.casefold() in _PLACEHOLDER_ARTISTS and ca_artist.casefold() not in _PLACEHOLDER_ARTISTS)
    ):
        updates["artist"] = candidate.artist

    ex_album = str(existing.album or "").strip()
    ca_album = str(candidate.album or "").strip()
    if ca_album and (
        not ex_album
        or (ex_album.casefold() in _PLACEHOLDER_ALBUMS and ca_album.casefold() not in _PLACEHOLDER_ALBUMS)
    ):
        updates["album"] = candidate.album

    ex_dur_ms = int(existing.duration_ms or 0)
    ca_dur_ms = _effective_duration_ms(candidate)
    if ex_dur_ms <= 0 and ca_dur_ms > 0:
        updates["duration_ms"] = ca_dur_ms

    if not str(existing.duration or "").strip():
        ca_dur_str = str(candidate.duration or "").strip()
        if ca_dur_str:
            updates["duration"] = ca_dur_str
        elif updates.get("duration_ms", ex_dur_ms) > 0:
            updates["duration"] = _format_duration_str(updates.get("duration_ms", ex_dur_ms))

    if not normalize_isrc(existing.isrc):
        ca_isrc = normalize_isrc(candidate.isrc)
        if ca_isrc:
            updates["isrc"] = ca_isrc

    for field in ("img_url", "source_path", "source_url", "genre", "album_artist", "release_date", "failure_reason"):
        if not str(getattr(existing, field, "") or "").strip():
            ca_val = str(getattr(candidate, field, "") or "").strip()
            if ca_val:
                updates[field] = getattr(candidate, field)

    if int(existing.track_number or 0) <= 0 and int(candidate.track_number or 0) > 0:
        updates["track_number"] = int(candidate.track_number)

    if not existing.is_explicit and candidate.is_explicit:
        updates["is_explicit"] = True
    if not existing.selected and candidate.selected:
        updates["selected"] = True

    if not updates:
        return existing, False
    return replace(existing, **updates), True


def deduplicate_tracks(
    tracks: Iterable[Track],
    *,
    duration_tolerance_ms: int = _DEFAULT_DEDUP_TOLERANCE_MS,
) -> List[Track]:
    """
    Deduplica pistas en cascada preservando orden de primera aparición y riqueza
    de metadatos:
        1. ``ISRC`` normalizado.
        2. ``(platform, id)``.
        3. ``(title, artist, duration_ms)`` normalizados con tolerancia ``±3s``.
    """
    result: List[Track] = []
    by_isrc: dict[str, int] = {}
    by_plat_id: dict[tuple[str, str], int] = {}
    by_norm_meta: dict[tuple[str, str], list[int]] = defaultdict(list)

    def _index_track(idx: int, tr: Track) -> None:
        isrc = normalize_isrc(tr.isrc)
        if isrc and isrc not in by_isrc:
            by_isrc[isrc] = idx
        plat = str(tr.platform or "").strip().casefold()
        tid = str(tr.id or "").strip()
        if plat and tid and (plat, tid) not in by_plat_id:
            by_plat_id[(plat, tid)] = idx
        nt = _normalize_meta_text(tr.name)
        na = _normalize_meta_text(tr.artist)
        if nt and na and idx not in by_norm_meta[(nt, na)]:
            by_norm_meta[(nt, na)].append(idx)

    for track in tracks:
        cand_isrc = normalize_isrc(track.isrc)
        plat = str(track.platform or "").strip().casefold()
        tid = str(track.id or "").strip()
        plat_key = (plat, tid) if (plat and tid) else None
        norm_t = _normalize_meta_text(track.name)
        norm_a = _normalize_meta_text(track.artist)
        norm_key = (norm_t, norm_a) if (norm_t and norm_a) else None
        cand_dur = _effective_duration_ms(track)

        match_idx: int | None = None
        if cand_isrc and cand_isrc in by_isrc:
            match_idx = by_isrc[cand_isrc]
        elif plat_key and plat_key in by_plat_id:
            match_idx = by_plat_id[plat_key]
        elif norm_key and norm_key in by_norm_meta:
            for idx in by_norm_meta[norm_key]:
                existing = result[idx]
                ex_isrc = normalize_isrc(existing.isrc)
                if cand_isrc and ex_isrc and cand_isrc != ex_isrc:
                    continue
                ex_dur = _effective_duration_ms(existing)
                if cand_dur > 0 and ex_dur > 0 and abs(cand_dur - ex_dur) > duration_tolerance_ms:
                    continue
                match_idx = idx
                break

        if match_idx is not None:
            updated, changed = _merge_track_metadata(result[match_idx], track)
            if changed:
                result[match_idx] = updated
                _index_track(match_idx, updated)
            continue

        idx = len(result)
        kept = replace(track)
        result.append(kept)
        _index_track(idx, kept)

    return result


def merge_playlists(
    sources: Iterable[Iterable[Track]],
    *,
    deduplicate: bool = True,
    duration_tolerance_ms: int = _DEFAULT_DEDUP_TOLERANCE_MS,
) -> List[Track]:
    """Funde múltiples listas de pistas en una sola lista (deduplicada opcionalmente)."""
    combined: List[Track] = []
    for source in sources:
        if source:
            combined.extend(source)
    if not deduplicate:
        return combined
    return deduplicate_tracks(combined, duration_tolerance_ms=duration_tolerance_ms)
