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

from services.authentication import CONFIG_DIR, ensure_config_dir

CACHE_DIR = CONFIG_DIR / "cover_cache"
INDEX_JSON = CACHE_DIR / "index.json"
MAX_BYTES = 2_000_000  # 2 MB por portada
TIMEOUT = 8

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
        # actualiza índice
        idx = _index_load()
        idx[h] = {
            "url": url,
            "local_path": str(final_path),
            "mime": ctype or "image/jpeg",
            "size": total,
            "cached_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        # también indexa por URL para lookup rápido
        idx[url] = idx[h]
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
