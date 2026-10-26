"""
core/config.py — Melomaniac v4.6.0 — Configuración Centralizada
══════════════════════════════════════════════════════════════════
Fuente única para constantes compartidas (regla 1).
Centraliza el orden de plataformas, concurrencia y parámetros de servicios
sin depender de la capa de autenticación ni de la UI.
"""

from __future__ import annotations

# ── Plataformas (single source para core, services y UI) ────────────
PLATFORM_ORDER: tuple[str, ...] = (
    "YouTube Music",
    "Apple Music",
    "Spotify",
)

PLATFORMS: list[str] = list(PLATFORM_ORDER)
LOCAL_SOURCES: frozenset[str] = frozenset({"Archivo Local", "Pegar Texto"})
SOURCE_OPTIONS: list[str] = [*PLATFORMS, *sorted(LOCAL_SOURCES)]

# Destino de exportación local (espejo de parsers, usa pathlib)
EXPORT_DEST_LABEL: str = "Archivo Local (Exportar)"
EXPORT_FORMATS: tuple[str, ...] = ("txt", "csv", "m3u", "m3u8", "xspf")
EXPORT_ORDERS: tuple[str, ...] = ("artist-title", "title-artist")
DEFAULT_EXPORT_ORDER: str = "artist-title"  # TuneMyMusic-compatible
DEFAULT_EXPORT_FORMAT: str = "txt"

# ── Concurrencia (configurable, no forzado) ─────────────────────────
NETWORK_CONCURRENCY: int = 2
RATE_LIMIT_BACKOFF_STEPS: int = 10

# Persistencia de la caché de búsquedas: agrupa escrituras para no reescribir
# el JSON completo tras cada pista. El cierre de transferencia fuerza el flush.
SEARCH_CACHE_FLUSH_EVERY: int = 25
# Errores de red consecutivos tras agotar reintentos antes de abortar el lote.
TRANSFER_MAX_CONSECUTIVE_ERRORS: int = 8

# Transferencia: Apple más estricto, resto más paralelo
TRANSFER_CONCURRENCY: dict[str, int] = {
    "Apple Music": 2,
    "default": 3,
}

def get_transfer_concurrency(destination: str) -> int:
    return TRANSFER_CONCURRENCY.get(destination, TRANSFER_CONCURRENCY["default"])

# ── Spotify chunks ───────────────────────────────────────────────────
SPOTIFY_ADD_CHUNK: int = 50
SPOTIFY_HTTP_TIMEOUT: int = 10
YOUTUBE_HTTP_TIMEOUT: int = 30

# ── Apple Music web endpoint / protección de ráfagas ────────────────
APPLE_API_BASE: str = "https://amp-api.music.apple.com/v1"
APPLE_ISRC_BATCH: int = 25
APPLE_TRANSFER_BATCH: int = 100
APPLE_REQUEST_BURST: int = 50
APPLE_REQUEST_PAUSE: int = 60
# Páginas de playlist que se piden en paralelo (offset). 5 mantiene la
# ráfaga muy por debajo de APPLE_REQUEST_BURST y reduce ~78% el tiempo
# de lectura en playlists de 1000+ pistas.
APPLE_PAGE_WORKERS: int = 5
# Máximo de pistas por página que acepta Apple.
APPLE_PAGE_SIZE: int = 100

# ── Defaults ─────────────────────────────────────────────────────────
DEFAULT_COOLDOWN: int = 60

# ── Descargas (yt-dlp como librería) ─────────────────────────────────
# Directorio de caché de audio. Se resuelve contra ~/Documentos/Melomaniac
# con fallback a ./downloads si Documentos no existe (misma lógica que Exports).
DOWNLOADS_DIR_NAME: str = "Descargas"
# Formatos de audio ofrecidos. Solo audio: nunca se conserva el contenedor
# de video que entrega YouTube.
AUDIO_FORMATS: tuple[str, ...] = ("mp3", "m4a", "opus", "flac", "wav")
DEFAULT_AUDIO_FORMAT: str = "mp3"
# Calidades por formato. "0" es la mejor disponible en el encoder.
AUDIO_BITRATES: dict[str, str] = {
    "mp3": "192",
    "m4a": "256",
    "opus": "128",
    "flac": "0",
    "wav": "0",
}
# El cliente `android` es el único que hoy entrega URL de audio con la
# sesión SABR activa. Los formatos de audio puro quedan ocultos, así que
# se pide el contenedor con audio (18) y se extrae con FFmpeg.
YOUTUBE_PLAYER_CLIENT: str = "android"
# Selector de formato: primero audio puro si existe; si la sesión SABR lo
# oculta, cae al contenedor 18 (360p con audio) y a `best` como último
# recurso. El video nunca se conserva: FFmpeg extrae solo el audio.
YTDLP_FORMAT_SELECTOR: str = "bestaudio/18/best"
# Tamaño mínimo para considerar válido un archivo descargado (bytes).
DOWNLOAD_MIN_BYTES: int = 64 * 1024
# Tope de descargas simultáneas. yt-dlp ya limita por sí mismo; mantenerlo
# bajo reduce el riesgo de throttling.
DOWNLOAD_CONCURRENCY: int = 2

# ── Re-export umbrales fuzzy para conveniencia ───────────────────────
try:
    from engine.normalizer import (
        FUZZY_IDEAL,
        FUZZY_LOG_BAND_LOW,
        FUZZY_REVISION_THRESHOLD,
        FUZZY_TITLE_IDEAL_WHEN_ARTIST_EXACT,
        ARTIST_EXACT_MIN,
        ARTIST_PERFECT,
    )
except ImportError:
    FUZZY_IDEAL = 85
    FUZZY_LOG_BAND_LOW = 70
    FUZZY_REVISION_THRESHOLD = 40
    FUZZY_TITLE_IDEAL_WHEN_ARTIST_EXACT = 60
    ARTIST_EXACT_MIN = 99
    ARTIST_PERFECT = 100
