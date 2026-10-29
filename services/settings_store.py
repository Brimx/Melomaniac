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
