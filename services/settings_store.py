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
import threading
from pathlib import Path
from typing import Any, Callable

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
_KEY_SCENE_RIGHT_OPEN = "scene_right_panel_open"
_KEY_MPRIS_ENABLED = "mpris_enabled"
_KEY_REDUCE_MOTION = "reduce_motion"
_KEY_ENHANCED_FOCUS = "enhanced_focus"
_KEY_THEME = "theme"
_KEY_BG_TEXTURE = "bg_texture"
_KEY_PREFERRED_BROWSER = "preferred_browser"
_KEY_PREFERRED_BROWSERS = "preferred_browsers"
_KEY_FADE_SECONDS = "fade_seconds"
_KEY_RESUME_ON_OPEN = "resume_on_open"
_KEY_STREAM_WITHOUT_DOWNLOAD = "stream_without_download"
_KEY_LAST_PLAYBACK = "last_playback"
_KEY_LIBRARY_SORT_ORDER = "library_sort_order"
_KEY_CHECK_UPDATES_ON_START = "check_updates_on_start"

_VALID_REPEAT = ("off", "all", "one")
_VALID_QUEUE_TABS = ("cola", "letra")
_VALID_QUEUE_MODES = ("push", "overlay")
_VALID_LYRICS_PROVIDERS = ("lrclib", "local", "off")
VALID_THEMES = ("sala", "bermellon", "matcha", "valvula", "glicina", "hielo", "washi")
_VALID_BG_TEXTURES = ("grid", "sei", "none")
_VALID_LIBRARY_SORT_ORDERS = ("recent", "az", "za")


_LOCK = threading.RLock()
_CACHE: tuple[Path, int, int, dict[str, Any]] | None = None
_LISTENERS: list[Callable[[], None]] = []


def subscribe(cb: Callable[[], None]) -> None:
    """Registra un callback notificado cuando cambian los ajustes."""
    with _LOCK:
        if cb not in _LISTENERS:
            _LISTENERS.append(cb)


def unsubscribe(cb: Callable[[], None]) -> None:
    """Anula un callback registrado con ``subscribe``."""
    with _LOCK:
        if cb in _LISTENERS:
            _LISTENERS.remove(cb)


def _coerce_bool(raw: Any, default: bool) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        norm = raw.strip().lower()
        if norm in ("1", "true", "yes", "on", "si", "sí"):
            return True
        if norm in ("0", "false", "no", "off", ""):
            return False
        return default
    return bool(raw)


def load_settings() -> dict[str, Any]:
    """Devuelve los ajustes guardados; un archivo ausente o corrupto da ``{}``."""
    global _CACHE
    with _LOCK:
        path = SETTINGS_JSON
        try:
            st = path.stat()
        except OSError:
            _CACHE = None
            return {}
        if (
            _CACHE is not None
            and _CACHE[0] == path
            and _CACHE[1] == st.st_mtime_ns
            and _CACHE[2] == st.st_size
        ):
            return dict(_CACHE[3])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _CACHE = None
            return {}
        if not isinstance(data, dict):
            _CACHE = None
            return {}
        _CACHE = (path, st.st_mtime_ns, st.st_size, dict(data))
        return dict(data)


def save_settings(data: dict[str, Any]) -> None:
    """Persiste los ajustes de forma atómica."""
    global _CACHE
    with _LOCK:
        ensure_config_dir()
        path = SETTINGS_JSON
        tmp = path.with_suffix(".json.tmp")
        payload = dict(data)
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        try:
            st = path.stat()
            _CACHE = (path, st.st_mtime_ns, st.st_size, payload)
        except OSError:
            _CACHE = None
        listeners = list(_LISTENERS)
    for cb in listeners:
        try:
            cb()
        except Exception:
            pass


def _get_bool(key: str, default: bool) -> bool:
    return _coerce_bool(load_settings().get(key), default)


def _set_bool(key: str, value: bool) -> None:
    with _LOCK:
        data = load_settings()
        data[key] = bool(value)
        save_settings(data)


def _get_choice(key: str, default: str, valid: tuple[str, ...]) -> str:
    raw = str(load_settings().get(key) or "").strip().lower()
    return raw if raw in valid else default


def _set_choice(key: str, value: str, default: str, valid: tuple[str, ...]) -> None:
    val = str(value or "").strip().lower()
    with _LOCK:
        data = load_settings()
        data[key] = val if val in valid else default
        save_settings(data)


def reset_settings(*, preserve_onboarding: bool = True) -> None:
    """Restablece las preferencias del usuario sin tocar credenciales ni descargas."""
    with _LOCK:
        data: dict[str, Any] = {}
        if preserve_onboarding and get_onboarding_done():
            data[_KEY_ONBOARDING_DONE] = True
        save_settings(data)


def get_download_dir() -> str:
    """Carpeta de descargas elegida por el usuario; vacío si usa la por defecto."""
    return str(load_settings().get(_KEY_DOWNLOAD_DIR) or "")


def set_download_dir(path: str | Path) -> None:
    """Guarda la carpeta de descargas; una ruta vacía restablece la por defecto.

    Si había una carpeta anterior existente y distinta de la nueva, se conserva
    en ``local_folders`` para seguir detectando las canciones ya descargadas.
    """
    value = str(Path(path).expanduser()) if path else ""
    with _LOCK:
        data = load_settings()
        prev = str(data.get(_KEY_DOWNLOAD_DIR) or "").strip()
        if prev and prev != value and os.path.isdir(prev):
            raw_folders = data.get(_KEY_LOCAL_FOLDERS)
            folders = [str(p) for p in raw_folders if p] if isinstance(raw_folders, list) else []
            if prev not in folders:
                data[_KEY_LOCAL_FOLDERS] = [*folders, prev]
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
    with _LOCK:
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
    with _LOCK:
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
    try:
        val = max(0.0, min(1.0, float(volume)))
    except (TypeError, ValueError):
        val = 1.0
    with _LOCK:
        data = load_settings()
        data[_KEY_VOLUME] = round(val, 4)
        save_settings(data)


def get_mute() -> bool:
    """Estado de silencio del reproductor (por defecto False)."""
    return _get_bool(_KEY_MUTE, False)


def set_mute(muted: bool) -> None:
    """Persiste el estado de silencio del reproductor."""
    _set_bool(_KEY_MUTE, muted)


def _coerce_fade_seconds(raw: Any) -> int:
    if isinstance(raw, bool):
        return 0
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return 0
    return val if val in (0, 1, 2, 3) else 0


def get_fade_seconds() -> int:
    """Duración del fundido de audio en segundos (0, 1, 2 o 3; por defecto 0)."""
    return _coerce_fade_seconds(load_settings().get(_KEY_FADE_SECONDS, 0))


def set_fade_seconds(seconds: int) -> None:
    """Persiste la duración del fundido de audio (0, 1, 2 o 3 segundos)."""
    with _LOCK:
        data = load_settings()
        data[_KEY_FADE_SECONDS] = _coerce_fade_seconds(seconds)
        save_settings(data)


def get_resume_on_open() -> bool:
    """Si debe recordarse la última pista y posición al abrir (por defecto False)."""
    return _get_bool(_KEY_RESUME_ON_OPEN, False)


def set_resume_on_open(enabled: bool) -> None:
    """Persiste si se debe reanudar la última pista al abrir."""
    _set_bool(_KEY_RESUME_ON_OPEN, enabled)


def get_stream_without_download() -> bool:
    """Si las pistas remotas se reproducen por streaming sin descargar (por defecto True)."""
    return _get_bool(_KEY_STREAM_WITHOUT_DOWNLOAD, True)


def set_stream_without_download(enabled: bool) -> None:
    """Persiste la preferencia de reproducción sin descarga."""
    _set_bool(_KEY_STREAM_WITHOUT_DOWNLOAD, enabled)


def get_last_playback() -> dict[str, Any]:
    """Devuelve el último estado de reproducción guardado para reanudar."""
    raw = load_settings().get(_KEY_LAST_PLAYBACK)
    return dict(raw) if isinstance(raw, dict) else {}


def set_last_playback(payload: dict[str, Any] | None) -> None:
    """Guarda o limpia la última pista, cola local y posición en milisegundos."""
    with _LOCK:
        data = load_settings()
        if payload:
            data[_KEY_LAST_PLAYBACK] = dict(payload)
        else:
            data.pop(_KEY_LAST_PLAYBACK, None)
        save_settings(data)


def get_shuffle() -> bool:
    """Estado de reproducción aleatoria (por defecto False)."""
    return _get_bool(_KEY_SHUFFLE, False)


def set_shuffle(enabled: bool) -> None:
    """Persiste el estado de reproducción aleatoria."""
    _set_bool(_KEY_SHUFFLE, enabled)


def get_download_format() -> str:
    """Formato de audio por defecto para Descargas."""
    from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT

    raw = str(load_settings().get(_KEY_DOWNLOAD_FORMAT) or "").lower()
    return raw if raw in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT


def set_download_format(fmt: str) -> None:
    """Persiste el formato de audio por defecto; un valor inválido cae al default."""
    from core.config import AUDIO_FORMATS, DEFAULT_AUDIO_FORMAT

    value = str(fmt or "").lower()
    with _LOCK:
        data = load_settings()
        data[_KEY_DOWNLOAD_FORMAT] = value if value in AUDIO_FORMATS else DEFAULT_AUDIO_FORMAT
        save_settings(data)


def get_covers_online() -> bool:
    """Si se permite consultar proveedores de portada en red (por defecto sí)."""
    return _get_bool(_KEY_COVERS_ONLINE, True)


def set_covers_online(enabled: bool) -> None:
    """Persiste el permiso para resolver portadas online."""
    _set_bool(_KEY_COVERS_ONLINE, enabled)


def get_onboarding_done() -> bool:
    """Si el usuario ya completó u omitió la bienvenida de primera ejecución."""
    return _get_bool(_KEY_ONBOARDING_DONE, False)


def set_onboarding_done(done: bool = True) -> None:
    """Persiste que la bienvenida ya se mostró."""
    _set_bool(_KEY_ONBOARDING_DONE, done)


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


def get_scene_right_open() -> bool:
    """Si la escena Reproduciendo muestra el panel derecho (Letra/Cola)."""
    return _get_bool(_KEY_SCENE_RIGHT_OPEN, True)


def set_scene_right_open(enabled: bool) -> None:
    """Persiste la visibilidad del panel derecho en la escena Reproduciendo."""
    _set_bool(_KEY_SCENE_RIGHT_OPEN, enabled)


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


def get_preferred_browser(platform: str | None = None) -> str:
    """Etiqueta del navegador/perfil preferido global o override por plataforma."""
    data = load_settings()
    if platform:
        raw_map = data.get(_KEY_PREFERRED_BROWSERS)
        if isinstance(raw_map, dict):
            return str(raw_map.get(platform) or "").strip()
        return ""
    return str(data.get(_KEY_PREFERRED_BROWSER) or "").strip()


def get_effective_preferred_browser(platform: str | None = None) -> str:
    """Resuelve el navegador preferido efectivo (override por plataforma → global)."""
    if platform:
        override = get_preferred_browser(platform)
        if override == "__auto__":
            return ""
        if override and override != "__global__":
            return override
    return get_preferred_browser()


def set_preferred_browser(browser_label: str, platform: str | None = None) -> None:
    """Persiste el navegador preferido global o el override/excepción por plataforma."""
    val = str(browser_label or "").strip()
    with _LOCK:
        data = load_settings()
        if platform:
            raw_map = data.get(_KEY_PREFERRED_BROWSERS)
            overrides = dict(raw_map) if isinstance(raw_map, dict) else {}
            if val and val != "__global__":
                overrides[platform] = val
            else:
                overrides.pop(platform, None)
            if overrides:
                data[_KEY_PREFERRED_BROWSERS] = overrides
            else:
                data.pop(_KEY_PREFERRED_BROWSERS, None)
        else:
            if val and val not in ("__auto__", "__global__"):
                data[_KEY_PREFERRED_BROWSER] = val
            else:
                data.pop(_KEY_PREFERRED_BROWSER, None)
        save_settings(data)


def get_library_sort_order() -> str:
    """Orden por defecto de estantes en Biblioteca ('recent', 'az' o 'za')."""
    return _get_choice(_KEY_LIBRARY_SORT_ORDER, "recent", _VALID_LIBRARY_SORT_ORDERS)


def set_library_sort_order(order: str) -> None:
    """Persiste el orden por defecto de estantes en Biblioteca."""
    _set_choice(_KEY_LIBRARY_SORT_ORDER, order, "recent", _VALID_LIBRARY_SORT_ORDERS)


def get_check_updates_on_start() -> bool:
    """Si debe buscar actualizaciones automáticamente al abrir (por defecto False)."""
    return _get_bool(_KEY_CHECK_UPDATES_ON_START, False)


def set_check_updates_on_start(enabled: bool) -> None:
    """Persiste si se comprueban actualizaciones al abrir."""
    _set_bool(_KEY_CHECK_UPDATES_ON_START, enabled)
