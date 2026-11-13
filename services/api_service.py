"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.9.0                               ║
║              Servicio Unificado de APIs Musicales                    ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: services/api_service.py
Descripción: Fachada unificada asíncrona sobre YouTube Music, Apple Music
             y Spotify. Abstrae las diferencias entre plataformas y
             proporciona una interfaz consistente para lectura, búsqueda
             y creación de playlists.

Estrategia de Diseño - Patrón Facade:
    MusicApiService actúa como punto único de acceso a múltiples APIs
    externas, ocultando su complejidad y diferencias:
    
    1. Abstracción de Plataformas:
       - Interfaz unificada para búsqueda, carga y transferencia
       - Normalización de respuestas a modelos comunes (Track, SearchResult)
       - Manejo consistente de errores entre plataformas
    
    2. Gestión de Autenticación:
        - YouTube Music: Headers de sesión (Cookie + Authorization)
        - Apple Music: Bearer token + User token de la sesión web
        - Spotify: cookies web mediante spotapi
    
    3. Resiliencia y Rate Limiting:
       - Circuit breakers por plataforma
       - Reintentos acotados y backoff para búsquedas/inserciones
       - Semáforo global para limitar concurrencia
       - Detección de 401/429 con mensajes específicos
    
    4. Optimizaciones:
       - Caché de búsquedas para evitar peticiones duplicadas
       - Sesiones HTTP reutilizables
       - Búsquedas concurrentes con límite de semáforo
    
    5. Sistema Hunter Recovery:
       - Búsqueda con fallback a queries alternativos
       - Matching fuzzy con umbrales adaptativos
       - Selección inteligente de mejor resultado

   Constantes:
       - NETWORK_CONCURRENCY: Límite global de peticiones concurrentes (2)
       - RATE_LIMIT_BACKOFF_STEPS: Compatibilidad del contrato de backoff (10)

   Estructura:
       - Operaciones por plataforma: services/platforms/{youtube,apple,spotify}.py
         (mixins); aquí queda el estado compartido, la caché y el despacho.

Autor: Melomaniac Team
Versión: 4.9.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from functools import partial
from typing import Callable, Optional

import httpx
import requests

from core.cache import SingleFlight, make_cache_key, make_search_flight_key
from core.library_models import PlaylistSummary
from core.models import PlaylistMeta, SearchResult, Track
from core.transfer import normalize_playlist_id
from core.config import (
    NETWORK_CONCURRENCY as CFG_NETWORK_CONCURRENCY,
    RATE_LIMIT_BACKOFF_STEPS as CFG_RATE_LIMIT_BACKOFF_STEPS,
    SEARCH_CACHE_FLUSH_EVERY,
    YOUTUBE_HTTP_TIMEOUT as CFG_YOUTUBE_HTTP_TIMEOUT,
)
from engine.normalizer import build_search_variants, clean_metadata
from services.authentication import (
    SEARCH_CACHE_JSON as AUTH_SEARCH_CACHE_JSON,
    ensure_config_dir,
    load_runtime_env,
)
from services.circuit_breaker import CircuitBreaker
from services.platforms._common import GLOBAL_API_SEMAPHORE
from services.platforms.apple import AppleMixin, AppleRequestLimiter
from services.platforms.spotify import SpotifyMixin
from services.platforms.youtube import YouTubeMixin

SEARCH_CACHE_JSON = str(AUTH_SEARCH_CACHE_JSON)
load_runtime_env()

# Constantes de red — single source: core/config.py
NETWORK_CONCURRENCY = CFG_NETWORK_CONCURRENCY
RATE_LIMIT_BACKOFF_STEPS = CFG_RATE_LIMIT_BACKOFF_STEPS


class MusicApiService(YouTubeMixin, AppleMixin, SpotifyMixin):
    """
    Fachada unificada asíncrona sobre APIs de plataformas de streaming.
    
    Proporciona una interfaz consistente para interactuar con YouTube
    Music, Apple Music y Spotify, ocultando las diferencias de sus APIs y
    manejando autenticación, rate limiting y errores de forma unificada.
    
    Attributes:
        _cb: Diccionario de circuit breakers por plataforma.
        _ytm: Cliente de YouTube Music (YTMusic).
        _am_headers: Headers para peticiones a Apple Music.
        _am_storefront: Código de país para Apple Music (default: "us").
        _search_cache: Caché de resultados de búsqueda.
        auth_manager: Referencia a AuthManager (inyectada externamente).
    
    Methods:
        init_youtube: Inicializa cliente de YouTube Music con headers.
        init_apple: Inicializa headers de Apple Music.
        search_with_fallback: Búsqueda con fallback a queries alternativos.
        init_spotify: Inicializa la sesión de Spotify.
        fetch_playlist: Carga una playlist desde una plataforma.
        add_track: Agrega una canción a una playlist de destino.
        cleanup_sessions: Limpia sesiones HTTP al cerrar.
    
    Example:
        >>> service = MusicApiService(circuit_breakers)
        >>> result = await service.search_with_fallback(
        ...     "YouTube Music", "Bohemian Rhapsody", "Queen"
        ... )
    
    Note:
        Este servicio es stateful: mantiene clientes autenticados y
        sesiones HTTP. Debe llamarse cleanup_sessions() al cerrar la
        aplicación para liberar recursos correctamente.
    """

    def __init__(self, circuit_breakers: dict[str, CircuitBreaker]):
        """
        Inicializa el servicio con circuit breakers para cada plataforma.
        
        Args:
            circuit_breakers: Diccionario {plataforma: CircuitBreaker}.
        
        Note:
            Los clientes de plataformas se inicializan bajo demanda
            cuando se necesitan, no en el constructor.
        """
        ensure_config_dir()
        self._cb  = circuit_breakers
        self._ytm = None
        self._ytm_lock = threading.RLock()
        self._am_headers:    dict = {}
        self._am_storefront: str  = "us"
        self._sp_login = None
        self._sp_cfg   = None
        # Instancia Song reutilizable: SpotAPI re-fetchea sesión, client token
        # y hashes JS (~5-8 requests) cada vez que se construye un BaseClient.
        # Reutilizarla reduce cada búsqueda a 1 solo request.
        self._sp_song  = None
        self._sp_artist = None
        # Serializa inserciones manuales sobre el Song reutilizado; el
        # BaseClient ya trae sesión/token/hashes calientes de la búsqueda.
        self._sp_insert_lock = threading.Lock()
        self._search_cache: dict[str, SearchResult] = self._load_search_cache()
        # Deduplica búsquedas concurrentes de pistas equivalentes.
        self._search_flights = SingleFlight()
        # Escrituras pendientes de search_cache.json (ver mark_search_cache_dirty).
        self._search_cache_dirty: int = 0
        self._am_rate_limiter = AppleRequestLimiter()
        self._shutdown_cleaned: bool = False
        self.youtube_auth_error: str = ""
        self.apple_auth_error: str = ""
        self.spotify_auth_error: str = ""
        self.youtube_session_source: str = ""
        self.apple_session_source: str = ""
        self.spotify_session_source: str = ""
        self.youtube_preferred_source: Optional[str] = None
        self.apple_preferred_source: Optional[str] = None
        self.spotify_preferred_source: Optional[str] = None
        self.auth_manager = None

        # ──────────────────────────────────────────────────────────────
        # SESIONES HTTP REUTILIZABLES
        # ──────────────────────────────────────────────────────────────
        # Mantener sesiones abiertas mejora performance al reutilizar
        # conexiones TCP y evitar handshakes SSL repetidos
        
        _am_ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        )
        self._http_session = httpx.Client(
            headers={"User-Agent": _am_ua},
            timeout=10.0,          # requests usaba timeout por llamada; aquí es defensa por defecto
            follow_redirects=True,  # requests seguía redirecciones por defecto
        )
        # YouTube Music se queda en requests: ytmusicapi exige
        # isinstance(requests_session, requests.Session) (ytmusic.py:226).
        self._yt_http_session = requests.Session()
        self._yt_http_session.request = partial(
            self._yt_http_session.request, timeout=CFG_YOUTUBE_HTTP_TIMEOUT
        )

    # ══════════════════════════════════════════════════════════════════
    # GESTIÓN DE SESIONES
    # ══════════════════════════════════════════════════════════════════

    def _cleanup_sessions(self) -> None:
        if getattr(self, "_shutdown_cleaned", False):
            return
        self._shutdown_cleaned = True
        for sess in (self._http_session, self._yt_http_session):
            try:
                sess.close()
            except OSError:
                pass
        self._ytm = None
        self._am_headers = {}

    def cleanup_sessions(self) -> None:
        self._cleanup_sessions()

    @property
    def search_cache(self) -> dict:
        return self._search_cache

    def _load_search_cache(self) -> dict:
        """
        Carga la caché de búsquedas persistida en disco.

        Permite reanudar una transferencia interrumpida (por 429, cierre
        de la app, etc.) sin re-buscar canciones ya resueltas.
        """
        try:
            with open(SEARCH_CACHE_JSON, encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:  # pylint: disable=broad-exception-caught
            return {}
        cache: dict[str, SearchResult] = {}
        for key, val in raw.items():
            if isinstance(val, dict):
                cache[key] = SearchResult(
                    track_id=val.get("track_id"),
                    needs_review=bool(val.get("needs_review", False)),
                    low_confidence=bool(val.get("low_confidence", False)),
                    isrc=val.get("isrc"),
                )
            elif isinstance(val, str):  # Legacy: solo track_id
                cache[key] = SearchResult(val, False)
        return cache

    def remember_resolution(
        self, name: str, artist: str, destination: str,
        track_id: str, *, isrc: str | None = None,
    ) -> None:
        """Fija una resolución manual como acierto para próximas transferencias.

        Evita que una canción resuelta desde el reporte vuelva a aparecer como
        ``needs_review`` en la siguiente ejecución por culpa de la caché.
        """
        if not track_id:
            return
        self._search_cache[make_cache_key(name, artist, destination)] = SearchResult(
            track_id=track_id,
            needs_review=False,
            low_confidence=False,
            isrc=isrc,
        )
        self.save_search_cache()

    def mark_search_cache_dirty(self) -> None:
        """
        Marca la caché como modificada y persiste cada N entradas.

        Evita reescribir ``search_cache.json`` completo tras cada pista.
        ``transfer_playlist`` fuerza el flush al terminar para no perder
        progreso si la app se cierra antes de alcanzar el umbral.
        """
        self._search_cache_dirty += 1
        if self._search_cache_dirty >= SEARCH_CACHE_FLUSH_EVERY:
            self.save_search_cache()

    def save_search_cache(self) -> None:
        """
        Persiste la caché de búsquedas a disco (config/search_cache.json).

        Se invoca por lotes durante la transferencia y al cerrarla para que
        el progreso sobreviva reinicios y la transferencia sea reanudable.
        """
        try:
            payload = {
                key: {
                    "track_id":         val.track_id,
                    "needs_review":     bool(val.needs_review),
                    "low_confidence":   bool(val.low_confidence),
                    "isrc":             val.isrc,
                }
                for key, val in self._search_cache.items()
                if isinstance(val, SearchResult)
            }
            tmp = f"{SEARCH_CACHE_JSON}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, SEARCH_CACHE_JSON)
            self._search_cache_dirty = 0
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    # ── YouTube Music Auth ─────────────────────────────────────────────

    # ── Apple Music Auth ───────────────────────────────────────────────

    # ── Spotify Auth ───────────────────────────────────────────────────

    # ── Playlist Fetching ──────────────────────────────────────────────

    async def fetch_playlist(
        self,
        platform: str,
        playlist_id: str,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> tuple[PlaylistMeta, list[Track]]:
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id:
            raise ValueError("Playlist sin identificador")
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._sync_fetch_youtube, playlist_id, progress_cb)
        elif platform == "Apple Music":
            return await asyncio.to_thread(self._sync_fetch_apple, playlist_id, progress_cb)
        elif platform == "Spotify":
            return await asyncio.to_thread(self._sync_fetch_spotify, playlist_id, progress_cb)
        raise ValueError(f"Unknown platform: {platform}")

    # ── Biblioteca: listado de playlists (solo metadatos) ───────────────

    # Despacho por plataforma: un dict evita repetir la cadena de comparaciones
    # y deja en un solo lugar qué plataformas saben listar su biblioteca.
    _SYNC_LIST = {
        "YouTube Music": "_sync_list_ytm",
        "Apple Music": "_sync_list_apple",
        "Spotify": "_sync_list_spotify",
    }

    async def list_library_playlists(self, platform: str) -> list[PlaylistSummary]:
        self._cb[platform].check_or_raise()
        method = self._SYNC_LIST.get(platform)
        if method is None:
            raise ValueError(f"Unknown platform: {platform}")
        return await asyncio.to_thread(getattr(self, method))

    # ── Search ─────────────────────────────────────────────────────────

    async def search_track(
        self,
        platform: str,
        name: str,
        artist: str,
        local_duration_s: Optional[int] = None,
        local_duration_ms: int = 0,
        local_is_explicit: bool = False,
        local_isrc: str | None = None,
    ) -> SearchResult:
        ct, ca = clean_metadata(name, artist)
        self._cb[platform].check_or_raise()
        # Pacing ya gestionado en cada hunter (ver _am_hunter sleep 0.8-1.3).
        # No sleep aquí para evitar doble espera (regla 6 SRP).
        if platform == "YouTube Music":
            return await self._yt_hunter_async(ct, ca, name, artist, local_duration_s)
        if platform == "Apple Music":
            return await self._am_hunter_async(
                ct, ca, name, artist, local_duration_s, local_isrc=local_isrc
            )
        if platform == "Spotify":
            return await self._sp_hunter_async(
                ct, ca, name, artist,
                local_duration_ms=local_duration_ms,
                local_is_explicit=local_is_explicit,
            )
        return SearchResult(None, False)

    CATALOG_KINDS = frozenset({"songs", "albums", "artists"})
    _PLATFORM_SEARCH_CATALOG = {
        "YouTube Music": "_yt_search_catalog_sync",
        "Apple Music": "_am_search_catalog_sync",
        "Spotify": "_sp_search_catalog_sync",
    }
    _PLATFORM_FETCH_ALBUM = {
        "YouTube Music": "_yt_fetch_album_sync",
        "Apple Music": "_am_fetch_album_sync",
        "Spotify": "_sp_fetch_album_sync",
    }
    _PLATFORM_FETCH_ARTIST = {
        "YouTube Music": "_yt_fetch_artist_sync",
        "Apple Music": "_am_fetch_artist_sync",
        "Spotify": "_sp_fetch_artist_sync",
    }
    _PLATFORM_ADD_TRACKS = {
        "YouTube Music": "_yt_add_tracks",
        "Apple Music": "_am_add_tracks",
        "Spotify": "_sp_add_tracks_to_playlist",
    }

    @staticmethod
    def _catalog_song_item(track: Track) -> dict:
        subtitle = (
            f"{track.artist} · {track.album}"
            if track.album and track.album != "Unknown"
            else track.artist
        )
        return {
            "id": track.id,
            "name": track.name,
            "subtitle": subtitle,
            "artist": track.artist,
            "img_url": track.img_url,
            "kind": "songs",
            "external_url": track.source_url,
            "platform": track.platform,
            "track": track,
        }

    @staticmethod
    def _catalog_album_item(
        *,
        album_id: str,
        name: str,
        artist: str,
        img_url: str,
        external_url: str,
        platform: str,
        year: str = "",
        item_type: str = "",
    ) -> dict:
        parts = [p for p in (artist, year, item_type) if p]
        return {
            "id": album_id,
            "name": name,
            "subtitle": " · ".join(parts),
            "artist": artist,
            "img_url": img_url,
            "kind": "albums",
            "external_url": external_url,
            "platform": platform,
            "year": year,
        }

    @staticmethod
    def _catalog_artist_item(
        *,
        artist_id: str,
        name: str,
        img_url: str,
        external_url: str,
        platform: str,
        subtitle: str = "",
    ) -> dict:
        return {
            "id": artist_id,
            "name": name,
            "subtitle": subtitle or "Artista",
            "artist": name,
            "img_url": img_url,
            "kind": "artists",
            "external_url": external_url,
            "platform": platform,
        }

    @staticmethod
    def _normalize_catalog_id(platform: str, raw_id: str, entity: str) -> str:
        val = (raw_id or "").strip()
        if not val:
            return ""
        if platform == "Spotify":
            marker = f":{entity}:"
            if marker in val:
                val = val.split(marker, 1)[1]
            slash_marker = f"/{entity}/"
            if slash_marker in val:
                val = val.split(slash_marker, 1)[1]
            return val.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
        if platform == "Apple Music":
            slash_marker = f"/{entity}/"
            if slash_marker in val:
                val = val.split(slash_marker, 1)[1]
                val = val.split("?", 1)[0].split("#", 1)[0].strip("/")
                if "/" in val:
                    val = val.rsplit("/", 1)[-1]
            return val
        if platform == "YouTube Music":
            if entity == "album" and "list=" in val:
                return val.split("list=", 1)[1].split("&", 1)[0].split("#", 1)[0].strip()
            for prefix in ("/browse/", "/channel/"):
                if prefix in val:
                    val = val.split(prefix, 1)[1]
                    break
            return val.split("?", 1)[0].split("#", 1)[0].strip().strip("/")
        return val

    async def search_catalog(
        self,
        platform: str,
        query: str,
        *,
        kind: str = "songs",
        limit: int = 20,
    ) -> list[dict]:
        """Busca canciones, álbumes o artistas en el catálogo de ``platform``."""
        method = self._PLATFORM_SEARCH_CATALOG.get(platform)
        if method is None:
            raise ValueError(f"Unknown platform: {platform}")
        kind_norm = (kind or "").strip().lower()
        if kind_norm not in self.CATALOG_KINDS:
            raise ValueError(f"Unsupported catalog kind: {kind}")
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise ValueError("limit must be a positive integer")
        query = (query or "").strip()
        if not query:
            return []
        self._cb[platform].check_or_raise()
        capped = min(limit, 50)
        if platform == "Spotify" and not self._sp_cfg and self._sp_song is None:
            await asyncio.to_thread(self._sync_init_spotify)
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(getattr(self, method), query, kind_norm, capped)

    async def fetch_album_tracks(
        self,
        platform: str,
        album_id: str,
    ) -> tuple[PlaylistMeta, list[Track]]:
        """Carga los metadatos y pistas de un álbum desde ``platform``."""
        method = self._PLATFORM_FETCH_ALBUM.get(platform)
        if method is None:
            raise ValueError(f"Unknown platform: {platform}")
        self._cb[platform].check_or_raise()
        normalized = self._normalize_catalog_id(platform, album_id, "album")
        if not normalized:
            raise ValueError("Álbum sin identificador")
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(getattr(self, method), normalized)

    async def fetch_artist_overview(self, platform: str, artist_id: str) -> dict:
        """Carga perfil, canciones destacadas y álbumes de un artista."""
        method = self._PLATFORM_FETCH_ARTIST.get(platform)
        if method is None:
            raise ValueError(f"Unknown platform: {platform}")
        self._cb[platform].check_or_raise()
        normalized = self._normalize_catalog_id(platform, artist_id, "artist")
        if not normalized:
            raise ValueError("Artista sin identificador")
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(getattr(self, method), normalized)

    async def search_candidates(self, platform: str, query: str,
                                limit: int = 5) -> list[Track]:
        """Devuelve hasta ``limit`` resultados crudos para elegir a mano.

        A diferencia de ``search_track``, no aplica fuzzy ni elige un único
        ganador: el usuario ve las alternativas y decide. Reutiliza los
        mismos clientes, semáforo global y circuit breaker.
        """
        query = (query or "").strip()
        if not query:
            return []
        self._cb[platform].check_or_raise()
        if platform == "Spotify" and not self._sp_cfg:
            await asyncio.to_thread(self._sync_init_spotify)
        async with GLOBAL_API_SEMAPHORE:
            if platform == "YouTube Music":
                return await asyncio.to_thread(self._yt_search_candidates_sync, query, limit)
            if platform == "Apple Music":
                return await asyncio.to_thread(self._am_search_candidates_sync, query, limit)
            if platform == "Spotify":
                return await asyncio.to_thread(self._sp_search_candidates_sync, query, limit)
        return []

    @staticmethod
    def _search_result_rank(result: SearchResult) -> int:
        """Calidad relativa de un resultado: 0 nada, 1 revisión, 2 baja, 3 alta."""
        if not result.track_id:
            return 0
        if result.needs_review:
            return 1
        if result.low_confidence:
            return 2
        return 3

    async def search_with_fallback(
        self,
        platform: str,
        name: str,
        artist: str,
        local_duration_s: Optional[int] = None,
        local_duration_ms: int = 0,
        local_is_explicit: bool = False,
        local_isrc: str | None = None,
    ) -> SearchResult:
        """Busca una pista probando variantes, sin duplicar búsquedas en vuelo.

        La clave de singleflight incluye plataforma, metadatos limpios,
        duración, explicit e ISRC. Si otra tarea ya está buscando la misma
        identidad, comparte su resultado en lugar de consumir cuota extra.
        """
        flight_key = make_search_flight_key(
            platform,
            name,
            artist,
            duration_s=local_duration_s,
            duration_ms=local_duration_ms,
            is_explicit=local_is_explicit,
            isrc=local_isrc,
        )
        return await self._search_flights.run(
            flight_key,
            lambda: self._search_with_variants(
                platform,
                name,
                artist,
                local_duration_s,
                local_duration_ms,
                local_is_explicit,
                local_isrc,
            ),
        )

    async def _search_with_variants(
        self,
        platform: str,
        name: str,
        artist: str,
        local_duration_s: Optional[int],
        local_duration_ms: int,
        local_is_explicit: bool,
        local_isrc: str | None,
    ) -> SearchResult:
        """Prueba variantes y conserva el mejor resultado disponible.

        Solo sigue probando mientras no haya match o el match requiera
        revisión; un resultado de baja confianza se considera aceptable,
        igual que antes, para no multiplicar búsquedas sin necesidad.
        """
        best = SearchResult(None, False)
        best_rank = 0
        for t_pass, a_pass in build_search_variants(name, artist):
            result = await self.search_track(
                platform, t_pass, a_pass, local_duration_s,
                local_duration_ms=local_duration_ms,
                local_is_explicit=local_is_explicit,
                local_isrc=local_isrc,
            )
            rank = self._search_result_rank(result)
            if rank > best_rank:
                best = result
                best_rank = rank
            if best_rank >= 2:
                break
        return best

    # ── YouTube Music Hunter ───────────────────────────────────────────

    # ── Apple Music Hunter ─────────────────────────────────────────────

    # ── Spotify Hunter ─────────────────────────────────────────────────

    # ── Playlist Creation ──────────────────────────────────────────────

    async def create_playlist(self, platform: str, title: str, track_ids: list[str],
                              description: str = "") -> tuple[bool, str, int, list[str]]:
        self._cb[platform].check_or_raise()
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._yt_create, title, track_ids, description)
        elif platform == "Apple Music":
            return await asyncio.to_thread(self._am_create, title, track_ids, description)
        elif platform == "Spotify":
            return await asyncio.to_thread(self._sp_create, title, track_ids, description)
        return False, "Platform not supported", 0, []

    async def add_track(self, platform: str, playlist_id: str,
                        track_id: str) -> tuple[bool, str]:
        """Inserta una sola canción en una playlist ya creada.

        Se usa desde el reporte de cierre para aplicar una alternativa
        elegida manualmente, respetando el circuit breaker y el limitador
        de cada plataforma.
        """
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id or not track_id:
            return False, "Playlist o canción sin identificador"
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._yt_add_track, playlist_id, track_id)
        if platform == "Apple Music":
            return await asyncio.to_thread(self._am_add_track, playlist_id, track_id)
        if platform == "Spotify":
            return await asyncio.to_thread(self._sp_add_track, playlist_id, track_id)
        return False, "Platform not supported"

    async def add_tracks_to_playlist(
        self,
        platform: str,
        playlist_id: str,
        tracks: list[Track],
    ) -> int:
        """Inserta un lote de pistas en una playlist existente de ``platform``.

        Si alguna pista proviene de otra plataforma o de una fuente local
        (``track.platform != platform`` o ``track.id`` vacío), la resuelve antes
        vía ``search_with_fallback`` respetando la caché y el rate limit
        secuencial de Apple Music.
        """
        method = self._PLATFORM_ADD_TRACKS.get(platform)
        if method is None:
            raise ValueError(f"Unknown platform: {platform}")
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id:
            raise ValueError("Playlist sin identificador")
        if not tracks:
            return 0

        resolved_ids: list[str] = []
        cache_dirty = False
        for track in tracks:
            if track is None:
                continue
            tid = str(getattr(track, "id", "") or "").strip()
            if getattr(track, "platform", "") == platform and tid:
                resolved_ids.append(tid)
                continue
            cache_key = make_cache_key(track.name, track.artist, platform)
            cached = self._search_cache.get(cache_key)
            if cached and cached.track_id and not cached.needs_review:
                resolved_ids.append(cached.track_id)
                continue
            dur_ms = int(getattr(track, "duration_ms", 0) or 0)
            dur_s = (dur_ms // 1000) if dur_ms else None
            res = await self.search_with_fallback(
                platform,
                track.name,
                track.artist,
                local_duration_s=dur_s,
                local_duration_ms=dur_ms,
                local_is_explicit=bool(getattr(track, "is_explicit", False)),
                local_isrc=getattr(track, "isrc", None),
            )
            if res.track_id:
                self._search_cache[cache_key] = res
                self.mark_search_cache_dirty()
                cache_dirty = True
                resolved_ids.append(res.track_id)

        if cache_dirty:
            self.save_search_cache()
        if not resolved_ids:
            return 0
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(getattr(self, method), playlist_id, resolved_ids)

    # ── Playlist Deletion (reemplazar) §11 ───────────────────────────────
    async def delete_playlist(self, platform: str, playlist_id: str) -> tuple[bool, str]:
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id:
            return False, "Playlist sin identificador"
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._yt_delete, playlist_id)
        elif platform == "Apple Music":
            return await asyncio.to_thread(self._am_delete, playlist_id)
        elif platform == "Spotify":
            return await asyncio.to_thread(self._sp_delete, playlist_id)
        return False, "Platform not supported"

