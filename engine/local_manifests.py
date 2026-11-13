"""
engine/local_manifests.py — Manifiestos locales de playlists sin descargar audio
══════════════════════════════════════════════════════════════════════════════
Persiste playlists importadas desde plataformas (o listas de pistas) como
archivos JSON bajo ``CONFIG_DIR / "local_playlists"`` (portable / XDG según
``services.authentication.CONFIG_DIR``), conservando metadatos de colección y
pistas sin descargar archivos de audio.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from core.models import Track
from core.transfer import normalize_playlist_id, playlist_url
from services import authentication

_INITIAL_LOCAL_PLAYLISTS_DIR = authentication.CONFIG_DIR / "local_playlists"
LOCAL_PLAYLISTS_DIR = _INITIAL_LOCAL_PLAYLISTS_DIR
MANIFEST_VERSION = 1

_SAFE_KEY_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def get_local_manifests_dir(manifests_dir: Path | str | None = None) -> Path:
    """Resuelve la carpeta de manifiestos respetando overrides y ``CONFIG_DIR``."""
    if manifests_dir is not None:
        return Path(manifests_dir)
    if LOCAL_PLAYLISTS_DIR != _INITIAL_LOCAL_PLAYLISTS_DIR:
        return Path(LOCAL_PLAYLISTS_DIR)
    return Path(authentication.CONFIG_DIR) / "local_playlists"


def _format_duration_ms(duration_ms: int) -> str:
    """Convierte milisegundos a ``m:ss`` (o ``h:mm:ss``) para ``Track.duration``."""
    try:
        total_s = max(0, int(round(int(duration_ms) / 1000)))
    except (TypeError, ValueError):
        return "0:00"
    if total_s <= 0:
        return "0:00"
    hours, rem = divmod(total_s, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _slug_part(value: str, fallback: str = "item") -> str:
    cleaned = _SAFE_KEY_RE.sub("_", (value or "").strip()).strip("_-")
    return cleaned[:48] if cleaned else fallback


def manifest_key(platform: str, playlist_id: str, name: str = "") -> str:
    """Genera un identificador de archivo seguro, determinista y libre de colisiones."""
    norm_id = normalize_playlist_id(platform, playlist_id) if platform else (playlist_id or "").strip()
    raw_seed = f"{platform.strip().lower()}::{norm_id or name.strip().lower()}"
    plat_slug = _slug_part(platform.lower(), "local")
    id_slug = _slug_part(norm_id or name, "playlist")
    digest = hashlib.sha1(raw_seed.encode("utf-8")).hexdigest()[:8]
    if norm_id and id_slug == norm_id and plat_slug:
        return f"{plat_slug}__{id_slug}"
    return f"{plat_slug}__{id_slug}_{digest}"


def detect_platform_from_url(url_or_id: str) -> str:
    """Infiere la plataforma de streaming a partir de una URL o URI de playlist."""
    raw = (url_or_id or "").strip().lower()
    if not raw:
        return ""
    if "open.spotify.com" in raw or raw.startswith("spotify:playlist:"):
        return "Spotify"
    if "music.youtube.com" in raw or "youtube.com" in raw or "youtu.be" in raw:
        return "YouTube Music"
    if "music.apple.com" in raw:
        return "Apple Music"
    return ""


def track_to_manifest_dict(track: Track | dict[str, Any], default_platform: str = "") -> dict[str, Any]:
    """Serializa una pista a diccionario JSON de manifiesto (sin ``source_path``)."""
    if isinstance(track, dict):
        title = str(track.get("title") or track.get("name") or "")
        artist = str(track.get("artist") or "")
        album = str(track.get("album") or "")
        duration_ms = int(track.get("duration_ms") or 0)
        duration = str(track.get("duration") or "") or _format_duration_ms(duration_ms)
        return {
            "id": str(track.get("id") or ""),
            "title": title,
            "artist": artist,
            "album": album,
            "duration": duration,
            "duration_ms": duration_ms,
            "img_url": str(track.get("img_url") or ""),
            "platform": str(track.get("platform") or default_platform or ""),
            "isrc": str(track.get("isrc") or ""),
            "source_url": str(track.get("source_url") or ""),
            "is_explicit": bool(track.get("is_explicit", False)),
            "genre": str(track.get("genre") or ""),
            "album_artist": str(track.get("album_artist") or ""),
            "track_number": int(track.get("track_number") or 0),
            "release_date": str(track.get("release_date") or ""),
        }

    duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    duration = str(getattr(track, "duration", "") or "") or _format_duration_ms(duration_ms)
    return {
        "id": str(getattr(track, "id", "") or ""),
        "title": str(getattr(track, "name", "") or ""),
        "artist": str(getattr(track, "artist", "") or ""),
        "album": str(getattr(track, "album", "") or ""),
        "duration": duration,
        "duration_ms": duration_ms,
        "img_url": str(getattr(track, "img_url", "") or ""),
        "platform": str(getattr(track, "platform", "") or default_platform or ""),
        "isrc": str(getattr(track, "isrc", "") or ""),
        "source_url": str(getattr(track, "source_url", "") or ""),
        "is_explicit": bool(getattr(track, "is_explicit", False)),
        "genre": str(getattr(track, "genre", "") or ""),
        "album_artist": str(getattr(track, "album_artist", "") or ""),
        "track_number": int(getattr(track, "track_number", 0) or 0),
        "release_date": str(getattr(track, "release_date", "") or ""),
    }


def track_from_manifest_dict(data: dict[str, Any], default_platform: str = "") -> Track:
    """Reconstruye un ``Track`` desde una entrada del manifiesto (sin archivo en disco)."""
    duration_ms = 0
    try:
        duration_ms = max(0, int(data.get("duration_ms") or 0))
    except (TypeError, ValueError):
        duration_ms = 0
    duration = str(data.get("duration") or "").strip() or _format_duration_ms(duration_ms)
    isrc_raw = str(data.get("isrc") or "").strip()
    track_number = 0
    try:
        track_number = max(0, int(data.get("track_number") or 0))
    except (TypeError, ValueError):
        track_number = 0
    raw_sp = str(data.get("source_path") or "").strip()
    resolved_sp = raw_sp if (raw_sp and os.path.isfile(raw_sp)) else ""

    return Track(
        id=str(data.get("id") or ""),
        name=str(data.get("title") or data.get("name") or ""),
        artist=str(data.get("artist") or ""),
        album=str(data.get("album") or ""),
        duration=duration,
        img_url=str(data.get("img_url") or ""),
        platform=str(data.get("platform") or default_platform or ""),
        duration_ms=duration_ms,
        is_explicit=bool(data.get("is_explicit", False)),
        isrc=isrc_raw or None,
        source_path=resolved_sp,
        source_url=str(data.get("source_url") or ""),
        genre=str(data.get("genre") or ""),
        album_artist=str(data.get("album_artist") or ""),
        track_number=track_number,
        release_date=str(data.get("release_date") or ""),
    )


@dataclass
class LocalPlaylistManifest:
    """Manifiesto local de una playlist importada sin descargar audio."""

    id: str
    name: str
    platform: str = ""
    owner: str = ""
    description: str = ""
    img_url: str = ""
    cover_urls: list[str] = field(default_factory=list)
    external_url: str = ""
    tracks: list[Track] = field(default_factory=list)
    created_at: str = ""
    manifest_id: str = ""
    path: str = ""

    def to_dict(self) -> dict[str, Any]:
        covers = [str(u) for u in self.cover_urls if u]
        primary_img = self.img_url or (covers[0] if covers else "")
        if primary_img and not covers:
            covers = [primary_img]
        return {
            "version": MANIFEST_VERSION,
            "manifest_id": self.manifest_id or manifest_key(self.platform, self.id, self.name),
            "platform": self.platform,
            "id": self.id,
            "name": self.name,
            "owner": self.owner,
            "description": self.description,
            "img_url": primary_img,
            "cover_urls": covers,
            "external_url": self.external_url,
            "created_at": self.created_at,
            "count": len(self.tracks),
            "tracks": [track_to_manifest_dict(t, self.platform) for t in self.tracks],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, path: str = "") -> "LocalPlaylistManifest":
        platform = str(data.get("platform") or "")
        playlist_id = str(data.get("id") or "")
        name = str(data.get("name") or "Playlist")
        raw_covers = data.get("cover_urls")
        covers = [str(u) for u in raw_covers if u] if isinstance(raw_covers, list) else []
        img_url = str(data.get("img_url") or "") or (covers[0] if covers else "")
        if img_url and not covers:
            covers = [img_url]

        raw_tracks = data.get("tracks")
        tracks: list[Track] = []
        if isinstance(raw_tracks, list):
            for entry in raw_tracks:
                if isinstance(entry, dict):
                    tracks.append(track_from_manifest_dict(entry, default_platform=platform))

        manifest_id = str(data.get("manifest_id") or "") or (
            Path(path).stem if path else manifest_key(platform, playlist_id, name)
        )
        return cls(
            id=playlist_id,
            name=name,
            platform=platform,
            owner=str(data.get("owner") or ""),
            description=str(data.get("description") or ""),
            img_url=img_url,
            cover_urls=covers,
            external_url=str(data.get("external_url") or ""),
            tracks=tracks,
            created_at=str(data.get("created_at") or ""),
            manifest_id=manifest_id,
            path=path,
        )


def _resolve_target_path(
    identifier: str | Path,
    *,
    manifests_dir: Path | str | None = None,
) -> Optional[Path]:
    """Resuelve un ``manifest_id`` o ruta JSON dentro de ``manifests_dir`` de forma segura."""
    base = get_local_manifests_dir(manifests_dir).resolve()
    raw = str(identifier or "").strip()
    if not raw:
        return None

    candidate_path = Path(raw)
    if candidate_path.suffix.lower() == ".json" and (
        candidate_path.is_absolute() or os.sep in raw or (os.altsep and os.altsep in raw)
    ):
        try:
            resolved = candidate_path.resolve()
        except OSError:
            return None
        try:
            resolved.relative_to(base)
        except ValueError:
            return None
        return resolved

    stem = raw[:-5] if raw.lower().endswith(".json") else raw
    if not stem or "/" in stem or "\\" in stem or ".." in stem:
        return None
    safe_stem = _SAFE_KEY_RE.sub("_", stem).strip("._")
    if not safe_stem or safe_stem != stem:
        return None
    return base / f"{safe_stem}.json"


def _write_manifest_json(target: Path, payload: dict[str, Any]) -> None:
    tmp = target.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
        raise


def save_local_manifest(
    manifest: LocalPlaylistManifest | None = None,
    *,
    platform: str = "",
    playlist_id: str = "",
    name: str = "",
    owner: str = "",
    description: str = "",
    img_url: str = "",
    cover_urls: Iterable[str] | None = None,
    external_url: str = "",
    tracks: Iterable[Track | dict[str, Any]] = (),
    created_at: str = "",
    manifest_id: str = "",
    manifests_dir: Path | str | None = None,
) -> LocalPlaylistManifest:
    """Guarda un manifiesto local de forma atómica sin descargar audio."""
    if manifest is None:
        norm_id = normalize_playlist_id(platform, playlist_id) if platform else str(playlist_id or "").strip()
        resolved_tracks = [
            t if isinstance(t, Track) else track_from_manifest_dict(t, default_platform=platform)
            for t in tracks
        ]
        covers = [str(u) for u in (cover_urls or ()) if u]
        if not covers and not img_url:
            seen_covers: set[str] = set()
            for tr in resolved_tracks:
                if tr.img_url and tr.img_url not in seen_covers:
                    seen_covers.add(tr.img_url)
                    covers.append(tr.img_url)
                    if len(covers) >= 4:
                        break
        primary_img = img_url or (covers[0] if covers else "")
        if primary_img and not covers:
            covers = [primary_img]
        ext_url = external_url or (playlist_url(platform, norm_id) if platform and norm_id else "")
        ts = created_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        key = manifest_id or manifest_key(platform, norm_id, name)
        manifest = LocalPlaylistManifest(
            id=norm_id,
            name=name or "Playlist importada",
            platform=platform,
            owner=owner,
            description=description,
            img_url=primary_img,
            cover_urls=covers,
            external_url=ext_url,
            tracks=resolved_tracks,
            created_at=ts,
            manifest_id=key,
        )
    else:
        if not manifest.created_at:
            manifest.created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if not manifest.manifest_id:
            manifest.manifest_id = manifest_key(manifest.platform, manifest.id, manifest.name)
        if not manifest.external_url and manifest.platform and manifest.id:
            manifest.external_url = playlist_url(manifest.platform, manifest.id)
        if not manifest.img_url and manifest.cover_urls:
            manifest.img_url = manifest.cover_urls[0]
        elif manifest.img_url and not manifest.cover_urls:
            manifest.cover_urls = [manifest.img_url]

    base = get_local_manifests_dir(manifests_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = _resolve_target_path(manifest.manifest_id, manifests_dir=base)
    if target is None:
        safe_id = manifest_key(manifest.platform, manifest.id, manifest.name)
        manifest.manifest_id = safe_id
        target = base / f"{safe_id}.json"

    manifest.path = str(target)
    _write_manifest_json(target, manifest.to_dict())
    return manifest


def load_local_manifest(
    identifier: str | Path,
    *,
    manifests_dir: Path | str | None = None,
) -> Optional[LocalPlaylistManifest]:
    """Carga un manifiesto local por ``manifest_id`` o ruta JSON; ``None`` si falta o está corrupto."""
    target = _resolve_target_path(identifier, manifests_dir=manifests_dir)
    if target is None or not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tracks"), list):
        return None
    return LocalPlaylistManifest.from_dict(data, path=str(target))


def list_local_manifests(
    *,
    manifests_dir: Path | str | None = None,
) -> list[LocalPlaylistManifest]:
    """Lista los manifiestos locales válidos ordenados del más reciente al más antiguo."""
    base = get_local_manifests_dir(manifests_dir)
    if not base.is_dir():
        return []
    items: list[LocalPlaylistManifest] = []
    try:
        files = sorted(base.iterdir())
    except OSError:
        return []
    for entry in files:
        if entry.suffix.lower() != ".json" or not entry.is_file():
            continue
        manifest = load_local_manifest(entry, manifests_dir=base)
        if manifest is not None:
            items.append(manifest)
    items.sort(key=lambda m: m.name.lower())
    items.sort(key=lambda m: m.created_at or "", reverse=True)
    return items


def remove_local_manifest(
    identifier: str | Path,
    *,
    manifests_dir: Path | str | None = None,
) -> bool:
    """Elimina el archivo JSON de un manifiesto local. Devuelve False si no existía."""
    target = _resolve_target_path(identifier, manifests_dir=manifests_dir)
    if target is None or not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


def update_manifest_track_paths(
    identifier: str | Path,
    tracks: Iterable[Track],
    *,
    manifests_dir: Path | str | None = None,
) -> bool:
    """Asocia rutas locales descargadas a las pistas correspondientes del manifiesto."""
    target = _resolve_target_path(identifier, manifests_dir=manifests_dir)
    if target is None or not target.is_file():
        return False
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    entries = data.get("tracks") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return False

    by_id: dict[str, str] = {}
    by_pair: dict[tuple[str, str], str] = {}
    for tr in tracks:
        sp = str(getattr(tr, "source_path", "") or "").strip()
        if not sp or not os.path.isfile(sp):
            continue
        tid = str(getattr(tr, "id", "") or "").strip()
        if tid:
            by_id[tid] = sp
        pair = (
            str(getattr(tr, "name", "") or "").strip().lower(),
            str(getattr(tr, "artist", "") or "").strip().lower(),
        )
        if pair[0]:
            by_pair[pair] = sp

    if not by_id and not by_pair:
        return False

    changed = False
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        eid = str(entry.get("id") or "").strip()
        epair = (
            str(entry.get("title") or entry.get("name") or "").strip().lower(),
            str(entry.get("artist") or "").strip().lower(),
        )
        new_sp = by_id.get(eid) or by_pair.get(epair)
        if new_sp and entry.get("source_path") != new_sp:
            entry["source_path"] = new_sp
            changed = True

    if changed:
        _write_manifest_json(target, data)
    return changed
