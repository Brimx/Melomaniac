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
_KEY_ONBOARDING_DONE = "onboarding_done"
_KEY_QUEUE_OPEN = "queue_panel_open"
_KEY_QUEUE_TAB = "queue_panel_tab"
_KEY_QUEUE_MODE = "queue_mode"
_KEY_LYRICS_ENABLED = "lyrics_enabled"
_KEY_LYRICS_PROVIDER = "lyrics_provider"
_KEY_LYRICS_SAVE_LRC = "lyrics_save_lrc"
_KEY_LYRICS_EMBED_TAGS = "lyrics_embed_tags"
_KEY_SCENE_SHOW_DISC = "scene_show_disc"
_KEY_SCENE_AMBIENT_BG = "scene_ambient_bg"
_KEY_MPRIS_ENABLED = "mpris_enabled"
_KEY_REDUCE_MOTION = "reduce_motion"
_KEY_ENHANCED_FOCUS = "enhanced_focus"
_KEY_THEME = "theme"
_KEY_BG_TEXTURE = "bg_texture"
_KEY_PREFERRED_BROWSER = "preferred_browser"

_VALID_REPEAT = ("off", "all", "one")
_VALID_QUEUE_TABS = ("cola", "letra")
_VALID_QUEUE_MODES = ("push", "overlay")
_VALID_LYRICS_PROVIDERS = ("lrclib", "local", "off")
VALID_THEMES = ("sala", "bermellon", "matcha", "valvula", "glicina", "hielo", "washi")
_VALID_BG_TEXTURES = ("grid", "sei", "none")


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


def _get_bool(key: str, default: bool) -> bool:
    raw = load_settings().get(key)
    return default if raw is None else bool(raw)


def _set_bool(key: str, value: bool) -> None:
    data = load_settings()
    data[key] = bool(value)
    save_settings(data)


def _get_choice(key: str, default: str, valid: tuple[str, ...]) -> str:
    raw = str(load_settings().get(key) or "").strip().lower()
    return raw if raw in valid else default


def _set_choice(key: str, value: str, default: str, valid: tuple[str, ...]) -> None:
    val = str(value or "").strip().lower()
    data = load_settings()
    data[key] = val if val in valid else default
    save_settings(data)


def reset_settings(*, preserve_onboarding: bool = True) -> None:
    """Restablece las preferencias del usuario sin tocar credenciales ni descargas."""
    data: dict[str, Any] = {}
    if preserve_onboarding and get_onboarding_done():
        data[_KEY_ONBOARDING_DONE] = True
    save_settings(data)


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


def get_onboarding_done() -> bool:
    """Si el usuario ya completó u omitió la bienvenida de primera ejecución."""
    return bool(load_settings().get(_KEY_ONBOARDING_DONE, False))


def set_onboarding_done(done: bool = True) -> None:
    """Persiste que la bienvenida ya se mostró."""
    data = load_settings()
    data[_KEY_ONBOARDING_DONE] = bool(done)
    save_settings(data)


def get_repeat() -> str:
    """Modo de repetición ('off', 'all', 'one')."""
    raw = str(load_settings().get(_KEY_REPEAT, "off") or "off").lower()
    return raw if raw in _VALID_REPEAT else "off"


def set_repeat(mode: str) -> None:
    """Persiste el modo de repetición ('off', 'all', 'one')."""
    _set_choice(_KEY_REPEAT, mode, "off", _VALID_REPEAT)


# ── Cola lateral, Letras y Escena Reproduciendo ────────────────────────


def get_queue_open() -> bool:
    """Si el panel lateral de cola/letra inicia abierto."""
    return _get_bool(_KEY_QUEUE_OPEN, False)


def set_queue_open(is_open: bool) -> None:
    """Persiste si el panel lateral de cola/letra está abierto."""
    _set_bool(_KEY_QUEUE_OPEN, is_open)


def get_queue_tab() -> str:
    """Pestaña activa del panel lateral ('cola' o 'letra')."""
    return _get_choice(_KEY_QUEUE_TAB, "cola", _VALID_QUEUE_TABS)


def set_queue_tab(tab: str) -> None:
    """Persiste la pestaña activa del panel lateral ('cola' o 'letra')."""
    _set_choice(_KEY_QUEUE_TAB, tab, "cola", _VALID_QUEUE_TABS)


def get_queue_mode() -> str:
    """Comportamiento del panel de cola ('push' o 'overlay')."""
    raw = str(load_settings().get(_KEY_QUEUE_MODE) or "").strip().lower()
    if raw == "over":
        return "overlay"
    return raw if raw in _VALID_QUEUE_MODES else "push"


def set_queue_mode(mode: str) -> None:
    """Persiste el modo del panel de cola ('push' o 'overlay')."""
    val = str(mode or "").strip().lower()
    if val == "over":
        val = "overlay"
    _set_choice(_KEY_QUEUE_MODE, val, "push", _VALID_QUEUE_MODES)


def get_lyrics_enabled() -> bool:
    """Si se muestran letras sincronizadas en Cola/Escena (por defecto sí)."""
    return _get_bool(_KEY_LYRICS_ENABLED, True)


def set_lyrics_enabled(enabled: bool) -> None:
    """Persiste si se muestran letras sincronizadas."""
    _set_bool(_KEY_LYRICS_ENABLED, enabled)


def get_lyrics_provider() -> str:
    """Proveedor de letras ('lrclib', 'local' o 'off')."""
    return _get_choice(_KEY_LYRICS_PROVIDER, "lrclib", _VALID_LYRICS_PROVIDERS)


def set_lyrics_provider(provider: str) -> None:
    """Persiste el proveedor de letras ('lrclib', 'local' o 'off')."""
    _set_choice(_KEY_LYRICS_PROVIDER, provider, "lrclib", _VALID_LYRICS_PROVIDERS)


def get_lyrics_save_lrc() -> bool:
    """Si las descargas guardan un archivo .lrc junto al audio (por defecto no)."""
    return _get_bool(_KEY_LYRICS_SAVE_LRC, False)


def set_lyrics_save_lrc(enabled: bool) -> None:
    """Persiste si las descargas guardan archivo .lrc junto al audio."""
    _set_bool(_KEY_LYRICS_SAVE_LRC, enabled)


def get_lyrics_embed_tags() -> bool:
    """Si las descargas incrustan la letra en metadatos del audio (por defecto no)."""
    return _get_bool(_KEY_LYRICS_EMBED_TAGS, False)


def set_lyrics_embed_tags(enabled: bool) -> None:
    """Persiste si las descargas incrustan la letra en tags del audio."""
    _set_bool(_KEY_LYRICS_EMBED_TAGS, enabled)


def get_scene_show_disc() -> bool:
    """Si la escena Reproduciendo muestra el vinilo girando junto a la portada."""
    return _get_bool(_KEY_SCENE_SHOW_DISC, True)


def set_scene_show_disc(enabled: bool) -> None:
    """Persiste la visibilidad del disco en la escena Reproduciendo."""
    _set_bool(_KEY_SCENE_SHOW_DISC, enabled)


def get_scene_ambient_bg() -> bool:
    """Si la escena Reproduciendo usa tinte OLED ambiental extraído de la portada."""
    return _get_bool(_KEY_SCENE_AMBIENT_BG, True)


def set_scene_ambient_bg(enabled: bool) -> None:
    """Persiste el fondo ambiental en la escena Reproduciendo."""
    _set_bool(_KEY_SCENE_AMBIENT_BG, enabled)


# ── Reproducción, Accesibilidad, Apariencia y Conexiones ───────────────


def get_mpris_enabled() -> bool:
    """Si se expone control multimedia MPRIS en D-Bus (Linux)."""
    return _get_bool(_KEY_MPRIS_ENABLED, True)


def set_mpris_enabled(enabled: bool) -> None:
    """Persiste si se habilita MPRIS en el escritorio."""
    _set_bool(_KEY_MPRIS_ENABLED, enabled)


def get_reduce_motion() -> bool:
    """Si el usuario activó Reducir movimiento (detiene animaciones decorativas)."""
    return _get_bool(_KEY_REDUCE_MOTION, False)


def set_reduce_motion(enabled: bool) -> None:
    """Persiste la preferencia de reducir movimiento."""
    _set_bool(_KEY_REDUCE_MOTION, enabled)


def get_enhanced_focus() -> bool:
    """Preferencia de foco reforzado (2 px de acento)."""
    return _get_bool(_KEY_ENHANCED_FOCUS, False)


def set_enhanced_focus(enabled: bool) -> None:
    """Persiste la preferencia de foco reforzado."""
    _set_bool(_KEY_ENHANCED_FOCUS, enabled)


def get_theme() -> str:
    """Identificador del tema visual elegido ('sala', 'bermellon', etc.)."""
    return _get_choice(_KEY_THEME, "sala", VALID_THEMES)


def set_theme(theme_id: str) -> None:
    """Persiste el tema visual elegido."""
    _set_choice(_KEY_THEME, theme_id, "sala", VALID_THEMES)


def get_bg_texture() -> str:
    """Textura de fondo elegida ('grid', 'sei' o 'none')."""
    return _get_choice(_KEY_BG_TEXTURE, "none", _VALID_BG_TEXTURES)


def set_bg_texture(texture: str) -> None:
    """Persiste la textura de fondo elegida."""
    _set_choice(_KEY_BG_TEXTURE, texture, "none", _VALID_BG_TEXTURES)


def get_preferred_browser() -> str:
    """Etiqueta del navegador/perfil preferido para leer sesiones, o vacío si es automático."""
    return str(load_settings().get(_KEY_PREFERRED_BROWSER) or "").strip()


def set_preferred_browser(browser_label: str) -> None:
    """Persiste el navegador/perfil preferido (vacío restablece a automático)."""
    data = load_settings()
    val = str(browser_label or "").strip()
    if val and val != "__auto__":
        data[_KEY_PREFERRED_BROWSER] = val
    else:
        data.pop(_KEY_PREFERRED_BROWSER, None)
    save_settings(data)
