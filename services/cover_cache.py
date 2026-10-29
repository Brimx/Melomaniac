"""
services/cover_cache.py — Caché común portadas playlists/álbumes
Archivos hash en config/cover_cache/ + índice JSON atómico. Deduplica por URL, límite tamaño, fallback remoto.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import time
from pathlib import Path
from typing import Optional

import requests

from core.config import COVER_HTTP_TIMEOUT as TIMEOUT
from core.config import COVER_MAX_BYTES as MAX_BYTES
from services.authentication import CONFIG_DIR, ensure_config_dir

CACHE_DIR = CONFIG_DIR / "cover_cache"
INDEX_JSON = CACHE_DIR / "index.json"

# Ext por MIME
MIME_EXT = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _index_load() -> dict:
    if not INDEX_JSON.exists():
        return {}
    try:
        raw = json.loads(INDEX_JSON.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _index_save(idx: dict) -> None:
    ensure_config_dir()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = INDEX_JSON.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, INDEX_JSON)
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _hash_url(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]


def cached_path_for_url(url: str) -> Optional[Path]:
    """Ruta local si existe en índice y archivo presente, si no None (caller usa URL remota)."""
    if not url:
        return None
    h = _hash_url(url)
    idx = _index_load()
    entry = idx.get(h) or idx.get(url)
    if not isinstance(entry, dict):
        return None
    p = entry.get("local_path") or entry.get("path") or ""
    if not p:
        return None
    path = Path(p) if Path(p).is_absolute() else CACHE_DIR / Path(p).name
    # índice legacy guarda solo filename
    if not path.exists():
        # busca por hash prefijo
        for cand in CACHE_DIR.glob(f"{h}.*"):
            if cand.exists():
                return cand
        return None
    return path


def _album_index_key(album_key: str) -> str:
    """Clave de índice para un álbum ya normalizado por el llamador.

    Se guarda con prefijo para no colisionar con los hashes de URL.
    """
    return f"album:{album_key}"


def get_album_cover(album_key: str) -> Optional[Path]:
    """Portada cacheada para ``album_key``, o ``None`` si no hay o el archivo faltó."""
    if not album_key:
        return None
    entry = _index_load().get(_album_index_key(album_key))
    if not isinstance(entry, dict):
        return None
    p = entry.get("local_path") or ""
    if not p:
        return None
    path = Path(p) if Path(p).is_absolute() else CACHE_DIR / Path(p).name
    return path if path.exists() else None


def set_album_cover(album_key: str, local_path: Path | str, *, source: str = "") -> None:
    """Asocia ``album_key`` a una portada ya presente en el caché.

    El archivo debe existir ya; esta función solo escribe el puntero, no copia.
    """
    if not album_key:
        return
    path = Path(local_path)
    if not path.exists():
        return
    idx = _index_load()
    idx[_album_index_key(album_key)] = {
        "local_path": str(path),
        "source": source,
        "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _index_save(idx)


def ensure_cached(url: str, session: Optional[requests.Session] = None) -> Optional[Path]:
    """Descarga si no existe, retorna path local o None si falla/límite (fallback remoto)."""
    if not url or not url.startswith("http"):
        return None
    h = _hash_url(url)
    # dedup: ya cacheado
    existing = cached_path_for_url(url)
    if existing and existing.exists():
        return existing
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    # descarga temporal
    sess = session or requests.Session()
    try:
        resp = sess.get(url, timeout=TIMEOUT, stream=True)
        if resp.status_code != 200:
            return None
        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = MIME_EXT.get(ctype) or mimetypes.guess_extension(ctype) or ".jpg"
        # tamaño límite vía header
        clen = resp.headers.get("Content-Length")
        if clen and clen.isdigit() and int(clen) > MAX_BYTES:
            return None
        tmp_path = CACHE_DIR / f".tmp_{h}{ext}"
        final_path = CACHE_DIR / f"{h}{ext}"
        total = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_BYTES:
                    f.close()
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                    return None
                f.write(chunk)
        # tamaño real
        try:
            sz = tmp_path.stat().st_size
            if sz == 0 or sz > MAX_BYTES:
                tmp_path.unlink(missing_ok=True)
                return None
        except Exception:
            pass
        os.replace(tmp_path, final_path)
        # actualiza índice. Solo se escribe la clave hash: `cached_path_for_url`
        # sigue aceptando la clave-URL de índices legacy, pero duplicar cada
        # entrada al escribir duplicaba el tamaño del índice sin ganar lookup.
        idx = _index_load()
        idx[h] = {
            "url": url,
            "local_path": str(final_path),
            "mime": ctype or "image/jpeg",
            "size": total,
            "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _index_save(idx)
        return final_path
    except Exception:
        return None
    finally:
        if session is None:
            try:
                sess.close()
            except Exception:
                pass
