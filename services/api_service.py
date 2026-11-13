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
       - Clientes compuestos por plataforma: ``services/platforms/{youtube,apple,spotify}.py``
         (``YouTubeClient``, ``AppleClient``, ``SpotifyClient``); aquí queda la
         fachada unificada, el estado compartido, la caché y el despacho.

Autor: Melomaniac Team
Versión: 4.9.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Callable, Optional

import httpx

from core.cache import SingleFlight, make_cache_key, make_search_flight_key
from core.library_models import PlaylistSummary
from core.models import PlaylistMeta, SearchResult, Track
from core.config import (
    NETWORK_CONCURRENCY as CFG_NETWORK_CONCURRENCY,
    RATE_LIMIT_BACKOFF_STEPS as CFG_RATE_LIMIT_BACKOFF_STEPS,
    SEARCH_CACHE_FLUSH_EVERY,
)
from engine.normalizer import (
    build_search_variants,
    clean_metadata,
    normalize_playlist_id,
)
from services.authentication import (
    SEARCH_CACHE_JSON as AUTH_SEARCH_CACHE_JSON,
    ensure_config_dir,
    load_runtime_env,
)
from services.circuit_breaker import CircuitBreaker
from services.platforms._common import GLOBAL_API_SEMAPHORE
from services.platforms.apple import AppleClient
from services.platforms.spotify import SpotifyClient
from services.platforms.youtube import YouTubeClient

SEARCH_CACHE_JSON = str(AUTH_SEARCH_CACHE_JSON)
load_runtime_env()

# Constantes de red — single source: core/config.py
NETWORK_CONCURRENCY = CFG_NETWORK_CONCURRENCY
RATE_LIMIT_BACKOFF_STEPS = CFG_RATE_LIMIT_BACKOFF_STEPS


def _client_prop(client_attr: str, field_name: str) -> property:
    """Delega propiedades de autenticación/sesión al cliente compuesto."""
    return property(
        lambda self: getattr(getattr(self, client_attr), field_name),
        lambda self, value: setattr(getattr(self, client_attr), field_name, value),
    )


class MusicApiService:
    """
    Fachada unificada asíncrona sobre APIs de plataformas de streaming.

    Compone ``YouTubeClient``, ``AppleClient`` y ``SpotifyClient``, ocultando las
    diferencias de sus APIs y manejando circuit breakers, caché y límites de
    red de forma unificada.
    """

    youtube_auth_error = _client_prop("_youtube", "auth_error")
    apple_auth_error = _client_prop("_apple", "auth_error")
    spotify_auth_error = _client_prop("_spotify", "auth_error")
    youtube_session_source = _client_prop("_youtube", "session_source")
    apple_session_source = _client_prop("_apple", "session_source")
    spotify_session_source = _client_prop("_spotify", "session_source")
    youtube_preferred_source = _client_prop("_youtube", "preferred_source")
    apple_preferred_source = _client_prop("_apple", "preferred_source")
    spotify_preferred_source = _client_prop("_spotify", "preferred_source")

    def __init__(self, circuit_breakers: dict[str, CircuitBreaker]):
        """Inicializa la fachada con circuit breakers y clientes por plataforma."""
        ensure_config_dir()
        self._cb = circuit_breakers
        self._search_cache: dict[str, SearchResult] = self._load_search_cache()
        self._search_flights = SingleFlight()
        self._search_cache_dirty: int = 0
        self._shutdown_cleaned: bool = False
        self.auth_manager = None

        _am_ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"
        )
        self._http_session = httpx.Client(
            headers={"User-Agent": _am_ua},
            timeout=10.0,
            follow_redirects=True,
        )
        self._youtube = YouTubeClient()
        self._apple = AppleClient(self._http_session)
        self._spotify = SpotifyClient(self._http_session)
        self._clients = {
            "YouTube Music": self._youtube,
            "Apple Music": self._apple,
            "Spotify": self._spotify,
        }

    def _require_client(self, platform: str):
        client = self._clients.get(platform)
        if client is None:
            raise ValueError(f"Unknown platform: {platform}")
        return client

    def _cleanup_sessions(self) -> None:
        if getattr(self, "_shutdown_cleaned", False):
            return
        self._shutdown_cleaned = True
        try:
            self._http_session.close()
        except OSError:
            pass
        for client in self._clients.values():
            client.cleanup()

    def cleanup_sessions(self) -> None:
        self._cleanup_sessions()

    async def init_youtube(self, creds: Optional[dict] = None) -> bool:
        """Inicializa y valida el cliente de YouTube Music."""
        return await self._youtube.init(creds)

    async def init_apple(self, creds: Optional[dict] = None) -> bool:
        """Inicializa y valida el cliente de Apple Music."""
        return await self._apple.init(creds)

    async def init_spotify(self, creds: Optional[dict] = None) -> bool:
        """Inicializa y valida el cliente de Spotify."""
        return await self._spotify.init(creds)

    async def search_by_isrcs(self, isrcs: list[str]) -> dict[str, SearchResult]:
        """Resuelve lotes de ISRCs en Apple Music."""
        return await self._apple.search_by_isrcs(isrcs)

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
        client = self._require_client(platform)
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id:
            raise ValueError("Playlist sin identificador")
        return await asyncio.to_thread(client.fetch_playlist, playlist_id, progress_cb)

    # ── Biblioteca: listado de playlists (solo metadatos) ───────────────

    async def list_library_playlists(self, platform: str) -> list[PlaylistSummary]:
        client = self._require_client(platform)
        self._cb[platform].check_or_raise()
        return await asyncio.to_thread(client.list_library)

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
        client = self._clients.get(platform)
        if client is None:
            return SearchResult(None, False)
        ct, ca = clean_metadata(name, artist)
        self._cb[platform].check_or_raise()
        return await client.search_track(
            ct,
            ca,
            name,
            artist,
            local_duration_s=local_duration_s,
            local_duration_ms=local_duration_ms,
            local_is_explicit=local_is_explicit,
            local_isrc=local_isrc,
        )

    CATALOG_KINDS = frozenset({"songs", "albums", "artists"})

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
        client = self._require_client(platform)
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
        await asyncio.to_thread(client.ensure_ready)
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(client.search_catalog, query, kind_norm, capped)

    async def fetch_album_tracks(
        self,
        platform: str,
        album_id: str,
    ) -> tuple[PlaylistMeta, list[Track]]:
        """Carga los metadatos y pistas de un álbum desde ``platform``."""
        client = self._require_client(platform)
        self._cb[platform].check_or_raise()
        normalized = self._normalize_catalog_id(platform, album_id, "album")
        if not normalized:
            raise ValueError("Álbum sin identificador")
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(client.fetch_album, normalized)

    async def fetch_artist_overview(self, platform: str, artist_id: str) -> dict:
        """Carga perfil, canciones destacadas y álbumes de un artista."""
        client = self._require_client(platform)
        self._cb[platform].check_or_raise()
        normalized = self._normalize_catalog_id(platform, artist_id, "artist")
        if not normalized:
            raise ValueError("Artista sin identificador")
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(client.fetch_artist, normalized)

    async def search_candidates(self, platform: str, query: str,
                                limit: int = 5) -> list[Track]:
        """Devuelve hasta ``limit`` resultados crudos para elegir a mano."""
        query = (query or "").strip()
        if not query:
            return []
        client = self._clients.get(platform)
        if client is None:
            return []
        self._cb[platform].check_or_raise()
        await asyncio.to_thread(client.ensure_ready)
        async with GLOBAL_API_SEMAPHORE:
            return await asyncio.to_thread(client.search_candidates, query, limit)

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
        client = self._clients.get(platform)
        if client is None:
            return False, "Platform not supported", 0, []
        self._cb[platform].check_or_raise()
        return await asyncio.to_thread(client.create_playlist, title, track_ids, description)

    async def add_track(self, platform: str, playlist_id: str,
                        track_id: str) -> tuple[bool, str]:
        """Inserta una sola canción en una playlist ya creada."""
        client = self._clients.get(platform)
        if client is None:
            return False, "Platform not supported"
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id or not track_id:
            return False, "Playlist o canción sin identificador"
        return await asyncio.to_thread(client.add_track, playlist_id, track_id)

    async def add_tracks_to_playlist(
        self,
        platform: str,
        playlist_id: str,
        tracks: list[Track],
    ) -> int:
        """Inserta un lote de pistas en una playlist existente de ``platform``."""
        client = self._require_client(platform)
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
            return await asyncio.to_thread(client.add_tracks, playlist_id, resolved_ids)

    # ── Playlist Deletion (reemplazar) §11 ───────────────────────────────
    async def delete_playlist(self, platform: str, playlist_id: str) -> tuple[bool, str]:
        client = self._clients.get(platform)
        if client is None:
            return False, "Platform not supported"
        self._cb[platform].check_or_raise()
        playlist_id = normalize_playlist_id(platform, playlist_id)
        if not playlist_id:
            return False, "Playlist sin identificador"
        return await asyncio.to_thread(client.delete_playlist, playlist_id)

