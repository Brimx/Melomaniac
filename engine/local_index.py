"""
engine/local_index.py — Índice persistente de audio local
════════════════════════════════════════════════════════════════════
Evita releer Mutagen de toda una carpeta en cada apertura del detalle.

Clave de validez: ``path + mtime + size``. Si el archivo cambió, la entrada
se descarta y se vuelve a leer. El índice no conoce rutas de configuración:
`services/local_index.py` decide dónde vive el JSON y lo inyecta.

Qué se persiste:
    - Metadatos por archivo (los mismos campos que usa ``build_local_tracks``).
    - Portada de carpeta (sidecar ``cover.jpg``/``folder.jpg``/``front.jpg``),
      que es una por carpeta y por eso no infla el archivo.

Qué solo vive en memoria: la portada incrustada de cada archivo, que puede
pesar decenas de KB y multiplicarse por cientos. Se cachea en un dict acotado
para que reabrir la misma playlist en la sesión no vuelva a decodificarla,
pero no se escribe a disco.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

INDEX_VERSION = 1

# Tope del caché en memoria de portadas incrustadas: ~2000 pistas visibles.
_COVER_CACHE_MAX = 2000


class LocalIndex:
    """Metadatos de audio local con validez por ``mtime`` y tamaño.

    Args:
        path: Archivo JSON del índice. Se crea al guardar; no es obligatorio
            que exista al construir.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._files: dict[str, dict[str, Any]] = {}
        self._folder_covers: dict[str, dict[str, Any]] = {}
        self._cover_cache: dict[str, str] = {}
        self._dirty = False
        self.load()

    # ── persistencia ──────────────────────────────────────────────────

    def load(self) -> None:
        """Carga el JSON; cualquier error deja el índice vacío sin romper."""
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict) or raw.get("version") != INDEX_VERSION:
            return
        files = raw.get("files")
        if isinstance(files, dict):
            self._files = {
                str(k): v for k, v in files.items()
                if isinstance(v, dict) and isinstance(v.get("meta"), dict)
            }
        covers = raw.get("folder_covers")
        if isinstance(covers, dict):
            self._folder_covers = {
                str(k): v for k, v in covers.items()
                if isinstance(v, dict) and isinstance(v.get("uri"), str)
            }

    def save(self, *, force: bool = False) -> bool:
        """Escritura atómica. ``force`` guarda aunque no haya cambios."""
        if not self._dirty and not force:
            return False
        payload = {
            "version": INDEX_VERSION,
            "files": self._files,
            "folder_covers": self._folder_covers,
        }
        tmp = self.path.with_suffix(".json.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            return False
        self._dirty = False
        return True

    # ── metadatos ─────────────────────────────────────────────────────

    @staticmethod
    def _stat(path: str) -> Optional[tuple[float, int]]:
        try:
            st = os.stat(path)
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def metadata_for(self, audio_path: str) -> Optional[dict[str, Any]]:
        """Metadatos cacheados, o ``None`` si el archivo cambió o falta."""
        if not audio_path:
            return None
        entry = self._files.get(os.path.abspath(audio_path))
        if entry is None:
            return None
        current = self._stat(audio_path)
        if current is None:
            return None
        if (entry.get("mtime"), entry.get("size")) != current:
            return None
        return dict(entry["meta"])

    def remember(self, audio_path: str, metadata: dict[str, Any]) -> None:
        """Guarda los metadatos de un archivo recién leído."""
        if not audio_path:
            return
        current = self._stat(audio_path)
        if current is None:
            return
        self._files[os.path.abspath(audio_path)] = {
            "mtime": current[0],
            "size": current[1],
            "meta": dict(metadata),
        }
        self._dirty = True

    # ── portadas ──────────────────────────────────────────────────────

    def folder_cover(self, folder: str, sidecar: str, uri_builder) -> str:
        """Portada de carpeta cacheada por ``mtime`` del sidecar.

        ``uri_builder`` recibe la ruta del sidecar y devuelve el data URI; el
        índice solo decide si hace falta volver a construirlo.
        """
        if not sidecar:
            return ""
        key = os.path.abspath(folder)
        current = self._stat(sidecar)
        if current is None:
            return ""
        entry = self._folder_covers.get(key)
        if entry and (entry.get("mtime"), entry.get("size")) == current:
            return str(entry.get("uri") or "")
        uri = uri_builder(sidecar)
        if not uri:
            return ""
        self._folder_covers[key] = {
            "mtime": current[0], "size": current[1], "uri": uri,
        }
        self._dirty = True
        return uri

    def embedded_cover(self, audio_path: str, builder) -> str:
        """Portada incrustada: caché en memoria por sesión, nunca en disco."""
        if not audio_path:
            return ""
        key = os.path.abspath(audio_path)
        cached = self._cover_cache.get(key)
        if cached is not None:
            return cached
        uri = builder(key)
        if len(self._cover_cache) >= _COVER_CACHE_MAX:
            # Descarta la mitad más antigua: dict conserva orden de inserción.
            for old in list(self._cover_cache)[: _COVER_CACHE_MAX // 2]:
                self._cover_cache.pop(old, None)
        self._cover_cache[key] = uri
        return uri

    # ── mantenimiento ─────────────────────────────────────────────────

    def prune_folder(self, folder: str, live_paths: set[str]) -> None:
        """Elimina entradas de ``folder`` que ya no corresponden a un archivo.

        Solo toca las rutas bajo ``folder``: podar todo el índice exigiría un
        ``stat`` por cada archivo conocido en cada escaneo.
        """
        root = os.path.abspath(folder)
        prefix = root.rstrip(os.sep) + os.sep
        dead = [
            p for p in self._files
            if (p == root or p.startswith(prefix)) and p not in live_paths
        ]
        for path in dead:
            self._files.pop(path, None)
            self._cover_cache.pop(path, None)
            self._dirty = True
        if dead:
            self._dirty = True

    def drop(self, audio_path: str) -> None:
        """Olvida un archivo concreto (por ejemplo, borrado o reemplazado)."""
        key = os.path.abspath(audio_path)
        if self._files.pop(key, None) is not None:
            self._dirty = True
        self._cover_cache.pop(key, None)
