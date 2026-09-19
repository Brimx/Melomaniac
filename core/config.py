"""
core/config.py — Melomaniac v3.3.8 — Configuración Centralizada
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

# ── Concurrencia (configurable, no forzado) ─────────────────────────
NETWORK_CONCURRENCY: int = 2
RATE_LIMIT_BACKOFF_STEPS: int = 10

# Transferencia: Apple más estricto, resto más paralelo
TRANSFER_CONCURRENCY: dict[str, int] = {
    "Apple Music": 2,
    "default": 3,
}

def get_transfer_concurrency(destination: str) -> int:
    return TRANSFER_CONCURRENCY.get(destination, TRANSFER_CONCURRENCY["default"])

# ── Spotify chunks ───────────────────────────────────────────────────
SPOTIFY_ADD_CHUNK: int = 50

# ── Apple Music web endpoint / protección de ráfagas ────────────────
APPLE_API_BASE: str = "https://amp-api.music.apple.com/v1"
APPLE_ISRC_BATCH: int = 25
APPLE_TRANSFER_BATCH: int = 100
APPLE_REQUEST_BURST: int = 50
APPLE_REQUEST_PAUSE: int = 60

# ── Defaults ─────────────────────────────────────────────────────────
DEFAULT_COOLDOWN: int = 60

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
