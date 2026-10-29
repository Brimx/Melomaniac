"""
services/settings_store.py — Preferencias persistentes del usuario
══════════════════════════════════════════════════════════════════
Lee y escribe ``config/settings.json`` (ruta en ``services.authentication``)
con escritura atómica ``.tmp`` + ``os.replace``. Hoy guarda la carpeta de
descargas y las carpetas locales de Biblioteca; Config reutilizará este
módulo para el resto de ajustes. Nunca guarda credenciales.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from services.authentication import SETTINGS_JSON, ensure_config_dir

_KEY_DOWNLOAD_DIR = "download_dir"
_KEY_LOCAL_FOLDERS = "local_folders"
_KEY_VOLUME = "volume"
_KEY_MUTE = "mute"
_KEY_SHUFFLE = "shuffle"
_KEY_REPEAT = "repeat"
_KEY_DOWNLOAD_FORMAT = "download_format"
_KEY_COVERS_ONLINE = "covers_online"
_VALID_REPEAT = ("off", "all", "one")


def load_settings() -> dict[str, Any]:
    """Devuelve los ajustes guardados; un archivo ausente o corrupto da ``{}``."""
    try:
        data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(data: dict[str, Any]) -> None:
    """Persiste los ajustes de forma atómica."""
    ensure_config_dir()
    tmp = SETTINGS_JSON.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, SETTINGS_JSON)


def get_download_dir() -> str:
    """Carpeta de descargas elegida por el usuario; vacío si usa la por defecto."""
    return str(load_settings().get(_KEY_DOWNLOAD_DIR) or "")


def set_download_dir(path: str | Path) -> None:
    """Guarda la carpeta de descargas; una ruta vacía restablece la por defecto."""
    data = load_settings()
    value = str(Path(path).expanduser()) if path else ""
    if value:
        data[_KEY_DOWNLOAD_DIR] = value
    else:
        data.pop(_KEY_DOWNLOAD_DIR, None)
    save_settings(data)


def get_local_folders() -> list[str]:
    """Carpetas locales agregadas a la Biblioteca, en orden de inserción."""
    raw = load_settings().get(_KEY_LOCAL_FOLDERS)
    return [str(p) for p in raw if p] if isinstance(raw, list) else []


def add_local_folder(path: str | Path) -> bool:
    """Agrega una carpeta a la Biblioteca. Devuelve False si ya estaba."""
    value = str(Path(path).expanduser())
    folders = get_local_folders()
    if value in folders:
        return False
    data = load_settings()
    data[_KEY_LOCAL_FOLDERS] = [*folders, value]
    save_settings(data)
    return True


def remove_local_folder(path: str | Path) -> bool:
    """Quita una carpeta de la Biblioteca. Devuelve False si no estaba."""
    value = str(Path(path).expanduser())
    folders = get_local_folders()
    if value not in folders:
        return False
    data = load_settings()
    data[_KEY_LOCAL_FOLDERS] = [f for f in folders if f != value]
    save_settings(data)
    return True


def get_volume() -> float:
    """Volumen del reproductor entre 0.0 y 1.0 (por defecto 1.0)."""
    raw = load_settings().get(_KEY_VOLUME, 1.0)
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 1.0


def set_volume(volume: float) -> None:
    """Persiste el volumen del reproductor entre 0.0 y 1.0."""
    data = load_settings()
    try:
        val = max(0.0, min(1.0, float(volume)))
    except (TypeError, ValueError):
        val = 1.0
    data[_KEY_VOLUME] = round(val, 4)
    save_settings(data)


def get_mute() -> bool:
    """Estado de silencio del reproductor (por defecto False)."""
    return bool(load_settings().get(_KEY_MUTE, False))


def set_mute(muted: bool) -> None:
    """Persiste el estado de silencio del reproductor."""
    data = load_settings()
    data[_KEY_MUTE] = bool(muted)
    save_settings(data)


def get_shuffle() -> bool:
    """Estado de reproducción aleatoria (por defecto False)."""
    return bool(load_settings().get(_KEY_SHUFFLE, False))


def set_shuffle(enabled: bool) -> None:
    """Persiste el estado de reproducción aleatoria."""
    data = load_settings()
    data[_KEY_SHUFFLE] = bool(enabled)
    save_settings(data)


def get_download_format() -> str:
    """Formato de audio por defecto para Descargas."""
    from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT

    raw = str(load_settings().get(_KEY_DOWNLOAD_FORMAT) or "").lower()
    return raw if raw in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT


def set_download_format(fmt: str) -> None:
    """Persiste el formato de audio por defecto; un valor inválido cae al default."""
    from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT

    value = str(fmt or "").lower()
    data = load_settings()
    data[_KEY_DOWNLOAD_FORMAT] = value if value in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT
    save_settings(data)


def get_covers_online() -> bool:
    """Si se permite consultar proveedores de portada en red (por defecto sí)."""
    return bool(load_settings().get(_KEY_COVERS_ONLINE, True))


def set_covers_online(enabled: bool) -> None:
    """Persiste el permiso para resolver portadas online."""
    data = load_settings()
    data[_KEY_COVERS_ONLINE] = bool(enabled)
    save_settings(data)


def get_repeat() -> str:
    """Modo de repetición ('off', 'all', 'one')."""
    raw = str(load_settings().get(_KEY_REPEAT, "off") or "off").lower()
    return raw if raw in _VALID_REPEAT else "off"


def set_repeat(mode: str) -> None:
    """Persiste el modo de repetición ('off', 'all', 'one')."""
    val = str(mode or "off").lower()
    if val not in _VALID_REPEAT:
        val = "off"
    data = load_settings()
    data[_KEY_REPEAT] = val
    save_settings(data)
