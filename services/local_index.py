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
import os
from pathlib import Path
from typing import Any, Iterable, Optional

from engine.local_index import LocalIndex
from engine.normalizer import _normalize_title, clean_metadata, normalize_isrc
from engine.parsers import list_audio_files, scan_audio_folder
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


async def scan_folder_async(folder: str, *, limit: int = 5000) -> list:
    """Escanea ``folder`` fuera del evento y deja el índice listo para guardar.

    Returns:
        ``list[Track]`` con las canciones encontradas.
    """
    index = shared_index()
    tracks = await asyncio.to_thread(scan_audio_folder, folder, limit=limit, index=index)
    return tracks


def _norm_pair(title: str, artist: str) -> tuple[str, str]:
    c_title, c_artist = clean_metadata(str(title or ""), str(artist or ""))
    return (_normalize_title(c_title or title or ""), _normalize_title(c_artist or artist or ""))


def _durations_compatible(left_ms: int, right_ms: int, tolerance_ms: int = 10_000) -> bool:
    if not left_ms or not right_ms:
        return True
    return abs(int(left_ms) - int(right_ms)) <= tolerance_ms


def match_tracks_against_library(
    tracks: Iterable[Any],
    *,
    download_dir: str | Path | None = "",
    local_folders: Iterable[str | Path] = (),
    playlist_name: str = "",
    platform: str = "",
    include_shared_index: bool = True,
) -> int:
    """Cruza pistas contra su carpeta, otras playlists descargadas y carpetas locales.

    Preserva el orden propio de ``tracks`` y rellena ``track.source_path`` cuando
    encuentra el archivo ya existente en disco para evitar descargas duplicadas.
    """
    from engine.downloader import (
        _target_stem,
        read_playlist_manifest,
        resolve_download_dir,
        resolve_playlist_dir,
    )

    track_list = list(tracks)
    if not track_list:
        return 0

    dl_root = resolve_download_dir(download_dir or None, create=False)
    manifest_dirs: list[Path] = []
    audio_roots: list[Path] = []
    seen_dirs: set[str] = set()

    def _add_manifest_dir(path: Path) -> None:
        key = os.path.normpath(str(path))
        if key not in seen_dirs and path.is_dir():
            seen_dirs.add(key)
            manifest_dirs.append(path)

    # Prioridad 1: carpeta de la playlist actual (y variante sin plataforma).
    if playlist_name:
        pl_dir = resolve_playlist_dir(dl_root, playlist_name, platform, create=False)
        _add_manifest_dir(pl_dir)
        if platform:
            plain_dir = resolve_playlist_dir(dl_root, playlist_name, "", create=False)
            if plain_dir != pl_dir:
                _add_manifest_dir(plain_dir)

    # Prioridad 2: raíz de Descargas y playlists hermanas descargadas.
    if dl_root.is_dir():
        _add_manifest_dir(dl_root)
        audio_roots.append(dl_root)
        try:
            for sub in sorted(dl_root.iterdir(), key=lambda p: p.name.lower()):
                if sub.is_dir():
                    _add_manifest_dir(sub)
        except OSError:
            pass

    # Prioridad 3: carpetas locales configuradas y sus subcarpetas de primer nivel.
    for extra in local_folders:
        if not extra:
            continue
        extra_path = Path(extra).expanduser()
        if not extra_path.is_dir():
            continue
        audio_roots.append(extra_path)
        if playlist_name:
            sub = resolve_playlist_dir(extra_path, playlist_name, platform, create=False)
            _add_manifest_dir(sub)
        _add_manifest_dir(extra_path)
        try:
            for sub in sorted(extra_path.iterdir(), key=lambda p: p.name.lower()):
                if sub.is_dir():
                    _add_manifest_dir(sub)
        except OSError:
            pass

    by_id: dict[str, tuple[str, int]] = {}
    by_isrc: dict[str, tuple[str, int]] = {}
    by_exact_pair: dict[tuple[str, str], tuple[str, int]] = {}
    by_clean_pair: dict[tuple[str, str], tuple[str, int]] = {}
    by_filename: dict[str, str] = {}

    def _register_entry(
        path: str,
        *,
        eid: str = "",
        isrc: str = "",
        title: str = "",
        artist: str = "",
        duration_ms: int = 0,
    ) -> None:
        if not path:
            return
        if eid and eid not in by_id:
            by_id[eid] = (path, duration_ms)
        norm_isrc = normalize_isrc(isrc) if isrc else None
        if norm_isrc and norm_isrc not in by_isrc:
            by_isrc[norm_isrc] = (path, duration_ms)
        t_low = str(title or "").strip().lower()
        a_low = str(artist or "").strip().lower()
        if t_low and (t_low, a_low) not in by_exact_pair:
            by_exact_pair[(t_low, a_low)] = (path, duration_ms)
        c_pair = _norm_pair(title, artist)
        if c_pair[0] and c_pair not in by_clean_pair:
            by_clean_pair[c_pair] = (path, duration_ms)

    for d in manifest_dirs:
        mdata = read_playlist_manifest(d)
        for entry in mdata.get("tracks") or []:
            if not isinstance(entry, dict):
                continue
            epath = str(entry.get("path") or "").strip()
            if not epath:
                continue
            _register_entry(
                epath,
                eid=str(entry.get("id") or "").strip(),
                isrc=str(entry.get("isrc") or "").strip(),
                title=str(entry.get("name") or entry.get("title") or ""),
                artist=str(entry.get("artist") or ""),
                duration_ms=int(entry.get("duration_ms") or 0),
            )

    for root in audio_roots:
        for fpath in list_audio_files(root, limit=10_000):
            fname = os.path.basename(fpath).lower()
            if fname and fname not in by_filename:
                by_filename[fname] = fpath

    # Prioridad 4: metadatos ya escaneados en LocalIndex dentro de las raíces activas.
    if include_shared_index and audio_roots:
        resolved_roots = [os.path.abspath(str(r)) for r in audio_roots]
        idx = shared_index()
        for abs_path, record in getattr(idx, "_files", {}).items():
            if not any(
                abs_path == r or abs_path.startswith(r + os.sep)
                for r in resolved_roots
            ):
                continue
            meta = record.get("meta") if isinstance(record, dict) else None
            if not isinstance(meta, dict):
                continue
            _register_entry(
                abs_path,
                isrc=str(meta.get("isrc") or ""),
                title=str(meta.get("title") or meta.get("name") or ""),
                artist=str(meta.get("artist") or ""),
                duration_ms=int(meta.get("duration_ms") or 0),
            )

    from engine.parsers import _LOCAL_AUDIO_EXTENSIONS

    matched = 0
    for tr in track_list:
        cur = str(getattr(tr, "source_path", "") or "")
        if cur and os.path.isfile(cur):
            matched += 1
            continue

        tr_dur = int(getattr(tr, "duration_ms", 0) or 0)
        candidates: list[tuple[str, int]] = []

        tid = str(getattr(tr, "id", "") or "").strip()
        if tid and tid in by_id:
            candidates.append(by_id[tid])
        tr_isrc = normalize_isrc(getattr(tr, "isrc", None))
        if tr_isrc and tr_isrc in by_isrc:
            candidates.append(by_isrc[tr_isrc])

        t_name = str(getattr(tr, "name", "") or "")
        t_artist = str(getattr(tr, "artist", "") or "")
        exact_k = (t_name.strip().lower(), t_artist.strip().lower())
        if exact_k[0] and exact_k in by_exact_pair:
            candidates.append(by_exact_pair[exact_k])
        clean_k = _norm_pair(t_name, t_artist)
        if clean_k[0] and clean_k in by_clean_pair:
            candidates.append(by_clean_pair[clean_k])

        found_path = ""
        for cand_path, cand_dur in candidates:
            if _durations_compatible(tr_dur, cand_dur) and os.path.isfile(cand_path):
                found_path = cand_path
                break

        if not found_path:
            stem = _target_stem(tr).lower()
            for ext in _LOCAL_AUDIO_EXTENSIONS:
                cand_path = by_filename.get(f"{stem}{ext}".lower(), "")
                if cand_path and os.path.isfile(cand_path):
                    found_path = cand_path
                    break

        if found_path:
            tr.source_path = found_path
            matched += 1

    return matched
