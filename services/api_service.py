"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.6.0                               ║
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

   Funciones Auxiliares:
       - _is_ytm_unauthorized: Detecta HTTP 401 de YouTube Music

Autor: Melomaniac Team
Versión: 4.6.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional

import httpx
import requests
from services.authentication import (
    BROWSER_JSON,
    SEARCH_CACHE_JSON as AUTH_SEARCH_CACHE_JSON,
    SPOTIFY_COOKIES_JSON as AUTH_SPOTIFY_COOKIES_JSON,
    ensure_config_dir,
    load_runtime_env,
    youtube_http_status,
)
from core.cache import SingleFlight, make_cache_key, make_search_flight_key
from core.library_models import PlaylistSummary
from core.models import Track, SearchResult, PlaylistMeta
from core.transfer import normalize_playlist_id
from core.config import (
    NETWORK_CONCURRENCY as CFG_NETWORK_CONCURRENCY,
    RATE_LIMIT_BACKOFF_STEPS as CFG_RATE_LIMIT_BACKOFF_STEPS,
    SEARCH_CACHE_FLUSH_EVERY,
    SPOTIFY_ADD_CHUNK as CFG_SPOTIFY_ADD_CHUNK,
    SPOTIFY_HTTP_TIMEOUT as CFG_SPOTIFY_HTTP_TIMEOUT,
    YOUTUBE_HTTP_TIMEOUT as CFG_YOUTUBE_HTTP_TIMEOUT,
    APPLE_API_BASE,
    APPLE_ISRC_BATCH,
    APPLE_TRANSFER_BATCH,
    APPLE_REQUEST_BURST,
    APPLE_REQUEST_PAUSE,
    APPLE_PAGE_WORKERS,
    APPLE_PAGE_SIZE,
)
from services.circuit_breaker import CircuitBreaker, RateLimitError
from engine.normalizer import (
    clean_metadata, build_search_query, build_search_variants,
    _normalize_title, normalize_isrc, FUZZY_IDEAL,
)
from engine.match import (
    _fuzzy_scores_triple, _fuzzy_flags_elastic, _ideal_pass_hunter,
    _joji_trikeyword_query, _duration_to_seconds,
    _yt_select_best, score_spotify_match,
)

SEARCH_CACHE_JSON = str(AUTH_SEARCH_CACHE_JSON)
SPOTIFY_COOKIES_JSON = str(AUTH_SPOTIFY_COOKIES_JSON)
load_runtime_env()

# ══════════════════════════════════════════════════════════════════════
# DETECCIÓN DE LIBRERÍAS OPCIONALES
# ══════════════════════════════════════════════════════════════════════

try:
    from ytmusicapi import YTMusic
    HAS_YTMUSIC = True
except ImportError:
    HAS_YTMUSIC = False

try:
    from spotapi import Song, Login, PublicPlaylist, PrivatePlaylist, Config
    from spotapi.utils.logger import NoopLogger
    HAS_SPOTIFY = True
except ImportError:
    HAS_SPOTIFY = False

# ══════════════════════════════════════════════════════════════════════
# CONSTANTES DE CONFIGURACIÓN — single source: core/config.py
# ══════════════════════════════════════════════════════════════════════

NETWORK_CONCURRENCY = CFG_NETWORK_CONCURRENCY
RATE_LIMIT_BACKOFF_STEPS = CFG_RATE_LIMIT_BACKOFF_STEPS
GLOBAL_API_SEMAPHORE = asyncio.Semaphore(NETWORK_CONCURRENCY)


def _as_text(value) -> str:
    """Normaliza la descripción de playlist según la forma de cada API.

    YouTube la expone como str, Apple como {"standard": ...} o str,
    Spotify como str. Ausente/None → "".
    """
    if value is None:
        return ""
    if isinstance(value, dict):
        for key in ("standard", "short", "text"):
            text = value.get(key)
            if isinstance(text, str) and text.strip():
                return text.strip()
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


class AppleAuthError(RuntimeError):
    """Error no reintentable de la sesión web de Apple Music."""

    def __init__(self, status_code: int, message: str = ""):
        self.status_code = status_code
        super().__init__(message or f"Apple Music HTTP {status_code}")


class AppleRequestLimiter:
    """Limitador compartido entre búsquedas, lectura y escritura Apple.

    El contador se aplica a peticiones salientes, no a canciones encontradas.
    Es síncrono porque las peticiones HTTP se ejecutan en threads; así
    también protege llamadas que no pasan por un hunter async.
    """

    def __init__(self, burst: int = APPLE_REQUEST_BURST, pause: int = APPLE_REQUEST_PAUSE):
        self.burst = burst
        self.pause = pause
        self.count = 0
        self._next_allowed = 0.0
        self._lock = threading.Lock()

    def wait_turn(self) -> None:
        with self._lock:
            delay = max(0.0, self._next_allowed - time.monotonic())
            if self.count >= self.burst:
                delay = max(delay, float(self.pause))
            if delay:
                time.sleep(delay)
            if self.count >= self.burst:
                self.count = 0
            self.count += 1

    def penalize(self, seconds: int) -> None:
        with self._lock:
            self._next_allowed = max(self._next_allowed, time.monotonic() + max(1, seconds))


# Mensaje de error para sesión expirada de YouTube Music
_YTM_401_MSG = (
    "[ERROR] YouTube Music: la sesión de config/browser.json fue rechazada. "
    "Renueva Cookie + Authorization desde el navegador."
)

# Re-exports para compatibilidad (single source: services.authentication)
SPOTIFY_ADD_CHUNK = CFG_SPOTIFY_ADD_CHUNK


def _is_ytm_unauthorized(exc: BaseException) -> bool:
    """
    Detecta si una excepción es un error de autenticación (HTTP 401) de YouTube Music.
    
    Verifica múltiples indicadores de 401:
    - String "401" en el mensaje de error
    - String "unauthorized" en el mensaje
    - Atributo response.status_code == 401
    
    Args:
        exc: Excepción capturada durante llamada a API de YouTube Music.
    
    Returns:
        True si es un HTTP 401, False en caso contrario.
    
    Note:
        Un 401 indica que los headers de config/browser.json han expirado y
        necesitan ser renovados desde el navegador.
    """
    status = youtube_http_status(exc)
    return status == 401 if status is not None else any(
        marker in str(exc).lower() for marker in ("unauthorized", "sign in", "login")
    )


def _ytm_sign_in_required(response: dict) -> bool:
    """Recognize the successful HTTP response YTMusic returns when logged out."""
    pending = [response]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            renderer = node.get("messageRenderer")
            if isinstance(renderer, dict):
                text_nodes = [renderer]
                parts = []
                while text_nodes:
                    current = text_nodes.pop()
                    if isinstance(current, dict):
                        for key, value in current.items():
                            if key in ("text", "simpleText") and isinstance(value, str):
                                parts.append(value)
                            else:
                                text_nodes.append(value)
                    elif isinstance(current, list):
                        text_nodes.extend(current)
                message = " ".join(parts).lower()
                if any(marker in message for marker in ("sign in", "sign-in", "log in", "login")):
                    return True
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
    return False


class MusicApiService:
    """
    Fachada unificada asíncrona sobre APIs de plataformas de streaming.
    
    Proporciona una interfaz consistente para interactuar con YouTube
    Music y Apple Music, ocultando las diferencias de sus APIs y
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
        load_playlist: Carga playlist desde una plataforma.
        add_to_playlist: Agrega canción a playlist destino.
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
        self.spotify_auth_error: str = ""
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

    def _ytm_library_playlists(self, limit: int | None):
        """Read playlists and reject YTMusic's successful logged-out message."""
        client = self._ytm
        send_request = client._send_request

        def _checked_request(endpoint, body, additionalParams=""):
            response = send_request(endpoint, body, additionalParams)
            if _ytm_sign_in_required(response):
                raise RuntimeError("Sign in required: YouTube Music rejected the session.")
            return response

        client._send_request = _checked_request
        try:
            return client.get_library_playlists(limit=limit)
        finally:
            client._send_request = send_request

    async def init_youtube(self) -> bool:
        return await asyncio.to_thread(self._sync_init_youtube)

    def _sync_init_youtube(self) -> bool:
        with self._ytm_lock:
            return self._sync_init_youtube_locked()

    def _sync_init_youtube_locked(self) -> bool:
        if not HAS_YTMUSIC:
            return False
        if not BROWSER_JSON.exists():
            self.youtube_auth_error = "missing config/browser.json"
            return False
        self.youtube_auth_error = ""
        try:
            self._ytm = YTMusic(auth=str(BROWSER_JSON), requests_session=self._yt_http_session)
            self._ytm_library_playlists(limit=1)
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.youtube_auth_error = str(exc)
            if _is_ytm_unauthorized(exc):
                print(_YTM_401_MSG)
            else:
                print(f"[YouTube Music] init failed: {exc}")
            self._ytm = None
            return False

    # ── Apple Music Auth ───────────────────────────────────────────────

    def _am_request(self, method: str, url: str, **kwargs):
        """Ejecuta una petición Apple con pausa preventiva y retry limitado."""
        request = getattr(self._http_session, method.lower())
        for attempt in range(2):
            self._am_rate_limiter.wait_turn()
            response = request(url, **kwargs)
            if response.status_code == 429 and attempt == 0:
                retry = self._retry_after_seconds(response)
                self._am_rate_limiter.penalize(retry)
                continue
            self._am_check_status(response)
            return response
        raise RateLimitError("Apple Music", APPLE_REQUEST_PAUSE)

    @staticmethod
    def _retry_after_seconds(response) -> int:
        try:
            return max(1, int(response.headers.get("Retry-After", APPLE_REQUEST_PAUSE)))
        except (TypeError, ValueError, AttributeError):
            return APPLE_REQUEST_PAUSE

    async def init_apple(self) -> bool:
        return await asyncio.to_thread(self._sync_init_apple)

    def _sync_init_apple(self) -> bool:
        raw  = os.getenv("APPLE_AUTH_BEARER", "").strip()
        utok = os.getenv("APPLE_MUSIC_USER_TOKEN", "").strip()
        if not raw or not utok:
            return False
        bearer = raw if raw.startswith("Bearer ") else f"Bearer {raw}"
        am_headers = {
            "Authorization":            bearer,
            "media-user-token":         utok,
            "x-apple-music-user-token": utok,
            "Origin":  "https://music.apple.com",
            "Referer": "https://music.apple.com/",
            "Accept":  "application/json",
        }
        try:
            resp = self._am_request(
                "get",
                f"{APPLE_API_BASE}/me/storefront",
                headers=am_headers, timeout=10,
            )
            if resp.status_code == 200:
                self._am_headers    = am_headers
                self._http_session.headers.update(am_headers)
                self._am_storefront = resp.json().get("data", [{}])[0].get("id", "us")
                return True
            return False
        except (AppleAuthError, RateLimitError) as exc:
            print(f"[Apple Music] init failed: {exc}")
            return False
        except Exception as exc:  # pylint: disable=broad-exception-caught
            msg = str(exc)
            # httpx.ConnectError no comparte base con requests: se reconoce por
            # mensaje y por nombre de clase para conservar el arranque offline.
            if (
                isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout))
                or "Name or service not known" in msg
                or "Failed to resolve" in msg
                or "amp-api.music.apple.com" in msg
            ):
                print(f"[Apple Music] init offline (storefront no resuelto) — fallback us")
                self._am_headers    = am_headers
                self._http_session.headers.update(am_headers)
                self._am_storefront = "us"
                return True
            print(f"[Apple Music] init failed: {exc}")
            return False


    # ── Spotify Auth ───────────────────────────────────────────────────

    async def init_spotify(self) -> bool:
        return await asyncio.to_thread(self._sync_init_spotify)

    def _sync_init_spotify(self) -> bool:
        if not HAS_SPOTIFY:
            self.spotify_auth_error = "spotapi no instalado"
            return False
        path = os.path.normpath(SPOTIFY_COOKIES_JSON)
        if not os.path.exists(path):
            self.spotify_auth_error = "missing config/spotify_cookies.json"
            return False
        try:
            import json  # pylint: disable=import-outside-toplevel
            dump = json.loads(open(path, encoding="utf-8").read())
            if not dump.get("identifier") or not dump.get("cookies"):
                self.spotify_auth_error = "config/spotify_cookies.json: falta identifier o cookies"
                return False
            cfg = Config(logger=NoopLogger())
            login = Login.from_cookies(dump, cfg)
            if not login.logged_in:
                self.spotify_auth_error = "cookies inválidas (login falló)"
                return False
            self._sp_login = login
            self._sp_cfg   = cfg
            self._sp_cfg.client.timeout = CFG_SPOTIFY_HTTP_TIMEOUT
            # Song cacheado sobre el MISMO TLSClient del login: el setup de
            # sesión/token/hashes se paga una sola vez, no por búsqueda.
            self._sp_song  = Song(client=cfg.client)
            self.spotify_auth_error = ""
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            print(f"[Spotify] init failed: {exc}")
            self._sp_login = None
            self._sp_song  = None
            return False


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

    def _sync_fetch_youtube(self, pid: str, cb) -> tuple[PlaylistMeta, list[Track]]:
        if not self._ytm:
            self._sync_init_youtube()
        if not self._ytm:
            raise RuntimeError("YouTube Music no disponible. Comprueba config/browser.json.")
        try:
            pl = self._ytm.get_playlist(pid, limit=None)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.youtube_auth_error = str(exc)
            if _is_ytm_unauthorized(exc):
                raise RuntimeError("Sesion YouTube Music expirada (401). Renueva config/browser.json.") from exc
            raise
        name  = pl.get("title", "YouTube Playlist")
        description = _as_text(pl.get("description"))
        raw   = pl.get("tracks", [])
        total = len(raw)
        tracks = []
        for i, t in enumerate(raw, 1):
            thumbs = t.get("thumbnails", [])
            vid = t.get("videoId") or ""
            tracks.append(Track(
                id=vid, name=t["title"],
                artist=", ".join(a["name"] for a in t.get("artists", [])),
                album=(t.get("album") or {}).get("name", "Single"),
                duration=t.get("duration", "0:00"),
                img_url=thumbs[-1]["url"] if thumbs else "",
                platform="YouTube Music",
                isrc=normalize_isrc(t.get("isrc")),
                source_url=f"https://music.youtube.com/watch?v={vid}" if vid else "",
                genre="",  # §10 pendiente fuente externa para YTM
            ))
            if cb and i % 50 == 0:
                cb(i, total, name)
        return PlaylistMeta(name=name, description=description), tracks

    def _sync_fetch_apple(self, pid: str, cb) -> tuple[PlaylistMeta, list[Track]]:
        base     = APPLE_API_BASE
        is_lib   = pid.startswith("p.")
        info_url = (
            f"{base}/me/library/playlists/{pid}" if is_lib
            else f"{base}/catalog/{self._am_storefront}/playlists/{pid}"
        )
        name = "Apple Music Playlist"
        description = ""
        total = 0
        first_page: list[dict] = []
        try:
            # include=tracks entrega relationships.tracks.meta.total, el único
            # total fiable de Apple (ver _apple_playlist_total), y la primera
            # página ya resuelta (idéntica a ?offset=0, verificado).
            r = self._am_request("get", info_url, params={"include": "tracks"}, timeout=10)
            self._am_check_status(r)
            if r.is_success:
                item = (r.json().get("data") or [{}])[0]
                attrs = item.get("attributes") or {}
                name = attrs.get("name", name)
                description = _as_text(attrs.get("description"))
                total = self._apple_track_count(item)
                rel = item.get("relationships") or {}
                first_page = (rel.get("tracks") or {}).get("data") or []
        except RateLimitError:
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        tracks_url = f"{info_url}/tracks"
        try:
            if total > APPLE_PAGE_SIZE:
                tracks = self._apple_collect_parallel(tracks_url, total, first_page, name, cb)
            elif total >= APPLE_PAGE_SIZE and len(first_page) >= APPLE_PAGE_SIZE:
                # Catálogo: include=tracks no expone meta.total y el primer
                # bloque viene lleno. Se piden olas por offset hasta que una
                # página llegue incompleta; así también se paraleliza.
                tracks = self._apple_collect_waves(tracks_url, first_page, name, cb)
            else:
                tracks = self._apple_collect_sequential(tracks_url, name, cb)
        except (RateLimitError, AppleAuthError):
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            # Cualquier rareza del paginado por offset vuelve al camino
            # secuencial por `next`, que es el comportamiento histórico.
            tracks = self._apple_collect_sequential(tracks_url, name, cb)
        return PlaylistMeta(name=name, description=description), tracks

    @staticmethod
    def _apple_track_from_item(item: dict) -> Track:
        attrs  = item.get("attributes", {})
        ms     = attrs.get("durationInMillis", 0)
        arturl = attrs.get("artwork", {}).get("url", "")
        if arturl:
            arturl = arturl.replace("{w}", "400").replace("{h}", "400")
        # releaseDate puede venir como "2023-05-12" o ISO; se guarda como string para criterio release_date
        rel = (attrs.get("releaseDate") or attrs.get("release_date") or "").strip() if isinstance(attrs.get("releaseDate") or attrs.get("release_date"), str) else str(attrs.get("releaseDate") or attrs.get("release_date") or "").strip()
        # género independiente por canción/álbum/artista §2 — Apple más completo
        genre = ""
        try:
            gnames = attrs.get("genreNames") or []
            if isinstance(gnames, list) and gnames:
                genre = str(gnames[0]).strip()
            if not genre:
                genre = str(attrs.get("genre") or "").strip()
        except Exception:
            genre = ""
        return Track(
            id=item["id"], name=attrs.get("name", "Unknown"),
            artist=attrs.get("artistName", "Unknown"),
            album=attrs.get("albumName", "Unknown"),
            duration=f"{int(ms/60000)}:{int((ms/1000)%60):02d}" if ms else "0:00",
            img_url=arturl, platform="Apple Music",
            duration_ms=int(ms) if ms else 0,
            is_explicit=bool(attrs.get("contentRating") == "explicit"),
            isrc=normalize_isrc(attrs.get("isrc")),
            release_date=rel,
            source_url=attrs.get("url") or f"https://music.apple.com/song/{item['id']}",
            genre=genre,
        )

    def _apple_track_page(self, url: str, offset: int) -> list[dict]:
        r = self._am_request(
            "get", url,
            params={"limit": APPLE_PAGE_SIZE, "offset": offset},
            timeout=15,
        )
        self._am_check_status(r)
        if r.status_code == 404 and offset > 0:
            # Apple responde 404 —no página vacía— al pasarse del final.
            # Solo se tolera en páginas posteriores: un 404 en la primera
            # significa playlist inexistente y debe propagarse.
            return []
        r.raise_for_status()
        return r.json().get("data") or []

    def _apple_collect_parallel(
        self, url: str, total: int, first_page: list[dict], name: str, cb
    ) -> list[Track]:
        """Pide las páginas por offset en paralelo respetando el limitador Apple.

        Apple acepta `offset` tanto en biblioteca como en catálogo (verificado)
        y devuelve las páginas en orden independiente del orden de llegada;
        `ThreadPoolExecutor.map` conserva el índice, así que el orden de la
        playlist se mantiene intacto. `first_page` son las pistas que ya trajo
        `include=tracks` (idénticas a offset=0), así no se piden dos veces.
        """
        tracks = [self._apple_track_from_item(i) for i in (first_page or [])]
        if cb and tracks:
            cb(len(tracks), total, name)
        offsets = range(len(tracks), total, APPLE_PAGE_SIZE)
        with ThreadPoolExecutor(max_workers=APPLE_PAGE_WORKERS) as pool:
            for page in pool.map(lambda off: self._apple_track_page(url, off), offsets):
                tracks.extend(self._apple_track_from_item(i) for i in page)
                if cb:
                    cb(len(tracks), total, name)
        return tracks

    def _apple_collect_waves(
        self, url: str, first_page: list[dict], name: str, cb
    ) -> list[Track]:
        """Pagina por offset sin conocer el total (playlists de catálogo).

        Se piden ``APPLE_PAGE_WORKERS`` páginas por ola y se conserva el
        orden de ``pool.map``. La primera página incompleta (o vacía por el
        404 de fin de lista) cierra la paginación.
        """
        tracks = [self._apple_track_from_item(i) for i in (first_page or [])]
        if cb and tracks:
            cb(len(tracks), 0, name)
        offset = len(tracks)
        wave = max(1, APPLE_PAGE_WORKERS)
        while True:
            offsets = [offset + i * APPLE_PAGE_SIZE for i in range(wave)]
            with ThreadPoolExecutor(max_workers=wave) as pool:
                pages = list(pool.map(lambda off: self._apple_track_page(url, off), offsets))
            stop = False
            for page in pages:
                if not page:
                    stop = True
                    break
                tracks.extend(self._apple_track_from_item(i) for i in page)
                if len(page) < APPLE_PAGE_SIZE:
                    stop = True
                    break
            if cb:
                cb(len(tracks), 0, name)
            if stop:
                break
            offset += wave * APPLE_PAGE_SIZE
        return tracks

    def _apple_collect_sequential(self, url: str, name: str, cb) -> list[Track]:
        tracks: list[Track] = []
        while url:
            full = url if url.startswith("http") else f"https://amp-api.music.apple.com{url}"
            r    = self._am_request("get", full, timeout=10)
            self._am_check_status(r)
            r.raise_for_status()
            data = r.json()
            tracks.extend(self._apple_track_from_item(i) for i in data.get("data", []))
            url = data.get("next")
            if cb:
                cb(len(tracks), 0, name)
        return tracks

    def _sync_fetch_spotify(self, pid: str, cb) -> tuple[PlaylistMeta, list[Track]]:
        if not HAS_SPOTIFY:
            raise RuntimeError("SpotAPI no disponible. Instala spotapi.")
        if not self._sp_cfg:
            self._sync_init_spotify()
        if not self._sp_cfg:
            self._sp_cfg = Config(logger=NoopLogger())
        pl = PublicPlaylist(pid, client=self._sp_cfg.client)
        r  = pl.get_playlist_info(limit=100)
        d  = r["data"]["playlistV2"]
        name = d.get("name", "Spotify Playlist")
        description = _as_text(d.get("description"))
        total = d.get("content", {}).get("totalCount", 0)
        tracks: list[Track] = []
        offset = 0
        while True:
            items = d.get("content", {}).get("items", [])
            for it in items:
                t = it.get("itemV2", {}).get("data", {})
                if not t or t.get("__typename") != "Track":
                    continue
                uri = t.get("uri", "")
                tid = uri.split(":")[-1] if uri else ""
                if not tid:
                    continue
                ms = (t.get("trackDuration") or {}).get("totalMilliseconds", 0)
                artists = ", ".join(
                    a["profile"]["name"] for a in (t.get("artists") or {}).get("items", [])
                    if a.get("profile", {}).get("name")
                )
                art_sources = ((t.get("albumOfTrack") or {}).get("coverArt") or {}).get("sources", [])
                img_url = ""
                if art_sources:
                    img_url = max(art_sources, key=lambda s: s.get("width", 0)).get("url", "")
                # Spotify: albumOfTrack puede traer date con precision day/month/year
                alb = t.get("albumOfTrack") or {}
                rel_sp = ""
                try:
                    date_obj = alb.get("date") or {}
                    rel_sp = (date_obj.get("isoString") or date_obj.get("year") or "").strip() if isinstance(date_obj, dict) else str(date_obj or "").strip()
                    if not rel_sp and isinstance(alb.get("releaseDate"), str):
                        rel_sp = alb.get("releaseDate", "").strip()
                except Exception:
                    rel_sp = ""
                # Spotify género a nivel artista (todas sus canciones heredan) §10
                genre_sp = ""
                try:
                    # si el payload trae primaryGenre no oficial, úsalo; si no, vacío pendiente enrichment por artist
                    g = t.get("primaryGenre") or (t.get("genres") or [None])[0] if isinstance(t.get("genres"), list) else None
                    if isinstance(g, str) and g.strip():
                        genre_sp = g.strip()
                except Exception:
                    genre_sp = ""
                tracks.append(Track(
                    id=tid, name=t.get("name", "Unknown"),
                    artist=artists or "Unknown",
                    album=alb.get("name", "Unknown"),
                    duration=f"{int(ms/60000)}:{int((ms/1000)%60):02d}" if ms else "0:00",
                    img_url=img_url, platform="Spotify",
                    isrc=self._extract_spotify_isrc(t),
                    release_date=rel_sp,
                    source_url=f"https://open.spotify.com/track/{tid}" if tid else "",
                    genre=genre_sp,
                ))
            offset += len(items)
            if offset >= total or not items:
                break
            r = pl.get_playlist_info(limit=100, offset=offset)
            d = r["data"]["playlistV2"]
            if cb:
                cb(len(tracks), total, name)
        return PlaylistMeta(name=name, description=description), tracks

    # ── Biblioteca: listado de playlists (solo metadatos) ───────────────

    @staticmethod
    def _ytm_cover_best(thumbnails) -> str:
        if not thumbnails:
            return ""
        try:
            # ytmusicapi ordena ascendente; mayor resolución al final
            return sorted(thumbnails, key=lambda t: t.get("width", 0))[-1].get("url", "")
        except Exception:
            return thumbnails[-1].get("url", "") if thumbnails else ""

    @staticmethod
    def _apple_cover_normalized(url: str, size: int = 400) -> str:
        if not url:
            return ""
        return url.replace("{w}", str(size)).replace("{h}", str(size))

    @staticmethod
    def _spotify_cover_best(sources) -> str:
        if not sources:
            return ""
        try:
            return max(sources, key=lambda s: s.get("width", 0) or 0).get("url", "")
        except Exception:
            return sources[0].get("url", "") if sources else ""

    def _sync_list_ytm(self) -> list[PlaylistSummary]:
        try:
            with self._ytm_lock:
                if not self._ytm:
                    self._sync_init_youtube_locked()
                if not self._ytm:
                    raise RuntimeError(
                        "YouTube Music no disponible. Comprueba config/browser.json."
                    )
                raw = self._ytm_library_playlists(limit=None)
        except Exception as exc:
            if _is_ytm_unauthorized(exc):
                raise RuntimeError("Sesion YouTube Music expirada (401). Renueva config/browser.json.") from exc
            raise
        out: list[PlaylistSummary] = []
        for pl in raw or []:
            pid = str(pl.get("playlistId") or pl.get("browseId") or "").strip()
            if not pid:
                continue
            name = str(pl.get("title") or "Playlist").strip()
            desc = _as_text(pl.get("description"))
            count = 0
            try:
                count = int(str(pl.get("count") or pl.get("trackCount") or 0).strip() or 0)
            except Exception:
                count = 0
            thumbs = pl.get("thumbnails") or pl.get("thumbnail") or []
            if isinstance(thumbs, dict):
                thumbs = [thumbs]
            cover = self._ytm_cover_best(thumbs if isinstance(thumbs, list) else [])
            out.append(PlaylistSummary(
                platform="YouTube Music",
                id=pid,
                name=name,
                description=desc,
                track_count=count,
                cover_urls=[cover] if cover else [],
                external_url=f"https://music.youtube.com/playlist?list={pid}",
            ))
        return out

    @staticmethod
    def _apple_track_count(item: dict) -> int:
        """Cuenta canciones: attributes.trackCount o relationships.tracks.

        La API de biblioteca no expone trackCount en attributes; el total
        real vive en relationships.tracks.meta.total (con include=tracks)
        o se deriva de len(relationships.tracks.data).
        """
        attrs = item.get("attributes") or {}
        try:
            direct = int(attrs.get("trackCount") or attrs.get("track_count") or 0)
            if direct:
                return direct
        except Exception:
            pass
        rels = item.get("relationships") or {}
        tracks_rel = rels.get("tracks") or {}
        try:
            meta_total = int((tracks_rel.get("meta") or {}).get("total") or 0)
            if meta_total:
                return meta_total
        except Exception:
            pass
        try:
            data = tracks_rel.get("data")
            if isinstance(data, list) and data:
                return len(data)
        except Exception:
            pass
        return 0

    def _apple_playlist_total(self, pid: str) -> int:
        """Total via single-fetch ?include=tracks (meta.total).

        El listado no admite include=tracks (400 salvo recurso único);
        el endpoint .../tracks?limit=1 tampoco devuelve meta.total.
        El único total fiable es relationships.tracks.meta.total del
        fetch individual.
        """
        try:
            r = self._am_request(
                "get",
                f"{APPLE_API_BASE}/me/library/playlists/{pid}",
                params={"include": "tracks"}, timeout=10,
            )
            self._am_check_status(r)
            data = r.json().get("data") or []
            if not data:
                return 0
            return self._apple_track_count(data[0])
        except Exception:
            return 0

    def _sync_list_apple(self) -> list[PlaylistSummary]:
        url = f"{APPLE_API_BASE}/me/library/playlists"
        params = {"limit": 100}
        out: list[PlaylistSummary] = []
        next_url: Optional[str] = url
        while next_url:
            full = next_url if next_url.startswith("http") else f"https://amp-api.music.apple.com{next_url}"
            # primera página con params, siguientes ya traen query
            if next_url == url:
                r = self._am_request("get", full, params=params, timeout=10)
            else:
                r = self._am_request("get", full, timeout=10)
            self._am_check_status(r)
            r.raise_for_status()
            data = r.json()
            for item in data.get("data", []):
                attrs = item.get("attributes") or {}
                pid = str(item.get("id") or "").strip()
                if not pid:
                    continue
                name = str(attrs.get("name") or "Playlist").strip()
                desc = _as_text(attrs.get("description"))
                count = self._apple_track_count(item)
                if not count:
                    # listado sin relationships: single-fetch ?include=tracks
                    count = self._apple_playlist_total(pid)
                art = attrs.get("artwork") or {}
                art_url = self._apple_cover_normalized(str(art.get("url") or ""), 400)
                # fallback: si no hay artwork en listado, dejar vacío (detail puede enriquecer con 4 covers)
                cover_urls = [art_url] if art_url else []
                # external url
                ext = str(attrs.get("url") or f"https://music.apple.com/library/playlist/{pid}")
                out.append(PlaylistSummary(
                    platform="Apple Music",
                    id=pid,
                    name=name,
                    description=desc,
                    track_count=count,
                    cover_urls=cover_urls,
                    external_url=ext,
                ))
            next_url = data.get("next")
        return out

    def _spotify_library_page(self, limit: int, offset: int) -> dict:
        """Una página libraryV3 con offset (get_library fija offset=0)."""
        import json as _json
        base = PrivatePlaylist(self._sp_login).base
        url = "https://api-partner.spotify.com/pathfinder/v1/query"
        params = {
            "operationName": "libraryV3",
            "variables": _json.dumps(
                {
                    "filters": [],
                    "order": None,
                    "textFilter": "",
                    "features": ["LIKED_SONGS", "YOUR_EPISODES", "PRERELEASES"],
                    "limit": limit,
                    "offset": offset,
                    "flatten": False,
                    "expandedFolders": [],
                    "folderUri": None,
                    "includeFoldersWhenFlattening": True,
                }
            ),
            "extensions": _json.dumps(
                {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": base.part_hash("libraryV3"),
                    }
                }
            ),
        }
        resp = self._sp_login.client.post(
            url, params=params, authenticate=True, timeout=CFG_SPOTIFY_HTTP_TIMEOUT
        )
        if resp.fail:
            raise RuntimeError(f"Spotify libraryV3 offset={offset} falló: {resp.error}")
        return resp.response if isinstance(resp.response, dict) else {}

    @staticmethod
    def _spotify_image_best(data: dict) -> str:
        """Cover de libraryV3: data.images.items[0].sources o data.image.sources (pseudo)."""
        try:
            imgs = (data.get("images") or {}).get("items") or []
            if imgs and isinstance(imgs[0], dict):
                sources = imgs[0].get("sources") or []
                if sources and isinstance(sources[0], dict):
                    best = max(sources, key=lambda s: (s.get("width") or 0) if isinstance(s.get("width"), int) else 0)
                    if best.get("url"):
                        return str(best["url"])
                    return str(sources[0].get("url") or "")
            img = data.get("image") or {}
            sources = img.get("sources") or []
            if sources and isinstance(sources[0], dict):
                best = max(sources, key=lambda s: (s.get("width") or 0) if isinstance(s.get("width"), int) else 0)
                return str(best.get("url") or sources[0].get("url") or "")
        except Exception:
            pass
        return ""

    def _sync_list_spotify(self) -> list[PlaylistSummary]:
        if not HAS_SPOTIFY:
            raise RuntimeError("SpotAPI no disponible. Instala spotapi.")
        if not self._sp_login:
            self._sync_init_spotify()
        if not self._sp_login:
            raise RuntimeError("Spotify no autenticado. Revisa config/spotify_cookies.json.")
        # libraryV3 mezcla artistas/álbumes/playlists: paginar y filtrar solo playlists
        entries: list[dict] = []
        try:
            offset, total = 0, None
            while True:
                page = self._spotify_library_page(100, offset)
                lib = ((page.get("data") or {}).get("me") or {}).get("libraryV3") or {}
                if total is None:
                    try:
                        total = int(lib.get("totalCount") or 0)
                    except Exception:
                        total = 0
                batch = lib.get("items") or []
                entries.extend([b for b in batch if isinstance(b, dict)])
                offset += len(batch)
                if not batch or len(batch) < 100:
                    break
                if total and offset >= total:
                    break
                if offset >= 600:  # tope seguridad
                    break
        except Exception as exc:
            raise RuntimeError(f"Spotify get_library falló: {exc}") from exc
        out: list[PlaylistSummary] = []
        for it in entries:
            inner = it.get("item") if isinstance(it.get("item"), dict) else {}
            data = inner.get("data") if isinstance(inner.get("data"), dict) else {}
            if not data:
                continue
            inner_tn = str(inner.get("__typename") or "")
            data_tn = str(data.get("__typename") or "")
            if inner_tn == "LibraryPseudoPlaylistResponseWrapper" and data_tn == "PseudoPlaylist":
                # Liked Songs / episodios: solo Liked Songs como playlist
                uri = str(data.get("uri") or inner.get("_uri") or "")
                if "collection:tracks" not in uri and "Liked" not in str(data.get("name") or ""):
                    continue
                name = str(data.get("name") or "Liked Songs").strip()
                try:
                    count = int(data.get("count") or 0)
                except Exception:
                    count = 0
                cover = self._spotify_image_best(data)
                out.append(PlaylistSummary(
                    platform="Spotify",
                    id="collection:tracks",
                    name=name,
                    description="",
                    track_count=count,
                    cover_urls=[cover] if cover else [],
                    external_url="https://open.spotify.com/collection/tracks",
                ))
                continue
            if inner_tn != "PlaylistResponseWrapper" or data_tn not in ("Playlist", "PrivatePlaylist"):
                continue  # artistas, álbumes, NotFound, carpetas
            uri = str(data.get("uri") or "")
            pid = uri.split(":")[-1] if ":" in uri else ""
            if not pid:
                continue
            name = str(data.get("name") or "Playlist").strip()
            desc = _as_text(data.get("description"))
            cover = self._spotify_image_best(data)
            content = data.get("content") or {}
            try:
                count = int(content.get("totalCount")) if content.get("totalCount") is not None else None
            except (TypeError, ValueError):
                count = None
            out.append(PlaylistSummary(
                platform="Spotify",
                id=pid,
                name=name,
                description=desc,
                track_count=count,
                cover_urls=[cover] if cover else [],
                external_url=f"https://open.spotify.com/playlist/{pid}",
            ))
        return out

    async def list_library_playlists(self, platform: str) -> list[PlaylistSummary]:
        self._cb[platform].check_or_raise()
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._sync_list_ytm)
        if platform == "Apple Music":
            return await asyncio.to_thread(self._sync_list_apple)
        if platform == "Spotify":
            return await asyncio.to_thread(self._sync_list_spotify)
        raise ValueError(f"Unknown platform: {platform}")

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

    def _yt_pack_result(self, chosen: dict, orig_name: str, orig_artist: str) -> SearchResult:
        from engine.match import pack_search_result

        found_title = chosen.get("title", "")
        farts = ", ".join(
            a.get("name", "") for a in (chosen.get("artists") or []) if isinstance(a, dict)
        )
        return pack_search_result(
            chosen.get("videoId"), found_title, farts, orig_name, orig_artist,
            isrc=normalize_isrc(chosen.get("isrc")),
        )

    def _yt_sync_search_round(self, query, orig_name, orig_artist, local_duration_s, cached_results=None):
        results = cached_results if cached_results is not None else self._yt_search_songs_sync(query)
        if not results:
            return False, None
        chosen = _yt_select_best(orig_name, orig_artist, results, local_duration_s)
        if not chosen:
            return True, None
        found_title = chosen.get("title", "")
        farts = ", ".join(a.get("name", "") for a in (chosen.get("artists") or []) if isinstance(a, dict))
        comb, tit, art = _fuzzy_scores_triple(orig_name, orig_artist, found_title, farts)
        return True, (chosen, comb, tit, art)

    def _yt_search_songs_sync(self, query: str) -> list:
        if not self._ytm:
            return []
        r = self._ytm.search(query, filter="songs", limit=5)
        return list(r) if r else []

    def _yt_search_candidates_sync(self, query: str, limit: int = 5) -> list[Track]:
        if not self._ytm:
            return []
        try:
            raw = self._ytm.search(query, filter="songs", limit=max(1, min(limit, 10)))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.youtube_auth_error = str(exc)
            if _is_ytm_unauthorized(exc):
                raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
            raise
        out: list[Track] = []
        for item in raw or []:
            vid = item.get("videoId") or ""
            if not vid:
                continue
            artists = ", ".join(
                a.get("name", "") for a in (item.get("artists") or [])
                if isinstance(a, dict) and a.get("name")
            )
            album = item.get("album") or {}
            dur_s = item.get("duration_seconds")
            if dur_s is None:
                dur_s = _duration_to_seconds(item.get("duration", "0:00"))
            out.append(Track(
                id=vid,
                name=item.get("title", "Unknown"),
                artist=artists or "Unknown",
                album=album.get("name", "") if isinstance(album, dict) else "",
                duration=item.get("duration", "0:00"),
                img_url=self._ytm_cover_best(item.get("thumbnails") or []),
                platform="YouTube Music",
                duration_ms=int(dur_s * 1000) if dur_s else 0,
                is_explicit=bool(item.get("isExplicit")),
                source_url=f"https://music.youtube.com/watch?v={vid}",
            ))
        return out

    async def _yt_hunter_async(self, ct, ca, orig_name, orig_artist, local_duration_s) -> SearchResult:
        if not self._ytm:
            return SearchResult(None, False)
        nt = _normalize_title(orig_name)
        na = _normalize_title(orig_artist)
        strict_q: list[str] = []
        for q in (build_search_query(ct, ca), build_search_query(nt, na), nt or ct):
            q = (q or "").strip()
            if q and q not in strict_q:
                strict_q.append(q)
        raw_q: list[str] = []
        for q in (build_search_query(orig_name.strip(), orig_artist.strip()), _joji_trikeyword_query(orig_name, orig_artist)):
            q = (q or "").strip()
            if q and q not in raw_q and q not in strict_q:
                raw_q.append(q)

        def _process_pack(pack):
            if not pack:
                return None
            chosen, comb, tit, art = pack
            if _ideal_pass_hunter(comb, tit, art):
                return self._yt_pack_result(chosen, orig_name, orig_artist)
            return None

        strict_empty_api = True
        best: Optional[dict] = None
        best_comb = -1

        for query in strict_q:
            async with GLOBAL_API_SEMAPHORE:
                try:
                    had_results, pack = await asyncio.to_thread(
                        self._yt_sync_search_round, query, orig_name, orig_artist, local_duration_s
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.youtube_auth_error = str(exc)
                    if _is_ytm_unauthorized(exc):
                        raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
                    raise
            if had_results:
                strict_empty_api = False
            if not pack:
                continue
            ideal = _process_pack(pack)
            if ideal is not None:
                return ideal
            chosen, comb, _, _ = pack
            if comb > best_comb:
                best_comb, best = comb, chosen

        if strict_empty_api and raw_q:
            for query in raw_q:
                async with GLOBAL_API_SEMAPHORE:
                    try:
                        had_results, pack = await asyncio.to_thread(
                            self._yt_sync_search_round, query, orig_name, orig_artist, local_duration_s
                        )
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        self.youtube_auth_error = str(exc)
                        if _is_ytm_unauthorized(exc):
                            raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
                        raise
                if not pack:
                    continue
                ideal = _process_pack(pack)
                if ideal is not None:
                    return ideal
                chosen, comb, _, _ = pack
                if comb > best_comb:
                    best_comb, best = comb, chosen

        if best is None:
            return SearchResult(None, False)
        return self._yt_pack_result(best, orig_name, orig_artist)


    # ── Apple Music Hunter ─────────────────────────────────────────────

    @staticmethod
    def _am_check_status(r) -> None:
        """
        Centraliza la detección de rate limiting / bloqueo de Apple Music.

        HTTP 429 (Too Many Requests) y 423 (Locked) se traducen a
        RateLimitError para que el circuit breaker los gestione. Un 423
        conlleva un cooldown mínimo de 120s por ser típicamente un
        bloqueo temporal del token de sesión web.
        """
        if r.status_code in (401, 403):
            raise AppleAuthError(r.status_code, "Sesión web de Apple Music rechazada")
        if r.status_code in (429, 423):
            try:
                retry = int(r.headers.get("Retry-After", APPLE_REQUEST_PAUSE))
            except (TypeError, ValueError, AttributeError):
                retry = APPLE_REQUEST_PAUSE
            if r.status_code == 423:
                retry = max(retry, 120)
            raise RateLimitError("Apple Music", retry)

    def _am_search_items(self, term: str, limit: int = 5) -> list[dict]:
        """Una llamada de catálogo; devuelve los items crudos de canciones."""
        r = self._am_request(
            "get",
            f"{APPLE_API_BASE}/catalog/{self._am_storefront}/search",
            params={"term": term, "types": "songs", "limit": max(1, min(limit, 10))},
            timeout=10,
        )
        self._am_check_status(r)
        r.raise_for_status()
        return r.json().get("results", {}).get("songs", {}).get("data", []) or []

    def _am_search_candidates_sync(self, term: str, limit: int = 5) -> list[Track]:
        out: list[Track] = []
        for item in self._am_search_items(term, limit=limit):
            try:
                out.append(self._apple_track_from_item(item))
            except Exception:  # pylint: disable=broad-exception-caught
                continue
        return out

    def _am_candidates_for_term(self, term: str) -> list[tuple[str, str, tuple[str, str, int, str]]]:
        """
        Busca canciones en el catálogo web de Apple Music vía
        amp-api.music.apple.com, usando la sesión web
        (JWS + media-user-token), sin developer token.

        A diferencia de iTunes Search API, no tiene el límite de ~20 llamadas/min
        y devuelve el ISRC, que se incluye en la meta para poder cachear
        búsquedas exactas por ISRC en el futuro. El trackId devuelto es el mismo
        song ID del catálogo que usa la API para crear playlists.
        """
        songs = self._am_search_items(term, limit=5)
        return [
            (
                f"{a.get('name', '')} - {a.get('artistName', '')}",
                str(s["id"]),
                (
                    a.get('name', ''),
                    a.get('artistName', ''),
                    int(a.get('durationInMillis') or 0),
                    a.get('isrc', ''),
                ),
            )
            for s in songs
            if (a := s.get("attributes", {})) and s.get("id")
        ]

    def _am_search_isrc_batch(self, isrcs: list[str]) -> dict[str, SearchResult]:
        """Resuelve hasta 25 ISRC con una llamada de catálogo Apple."""
        values = [normalize_isrc(value) for value in isrcs]
        values = [value for value in dict.fromkeys(values) if value]
        if not values:
            return {}
        response = self._am_request(
            "get",
            f"{APPLE_API_BASE}/catalog/{self._am_storefront}/songs",
            params={"filter[isrc]": ",".join(values), "limit": len(values)},
            timeout=10,
        )
        found: dict[str, SearchResult] = {}
        for item in response.json().get("data", []):
            attrs = item.get("attributes", {})
            isrc = normalize_isrc(attrs.get("isrc"))
            if isrc and item.get("id"):
                found[isrc] = SearchResult(
                    track_id=str(item["id"]),
                    needs_review=False,
                    isrc=isrc,
                )
        return found

    async def search_by_isrcs(self, isrcs: list[str]) -> dict[str, SearchResult]:
        """Resuelve ISRC en lotes de 25, siempre de forma secuencial."""
        normalized = [normalize_isrc(value) for value in isrcs]
        normalized = [value for value in dict.fromkeys(normalized) if value]
        resolved: dict[str, SearchResult] = {}
        for start in range(0, len(normalized), APPLE_ISRC_BATCH):
            batch = normalized[start:start + APPLE_ISRC_BATCH]
            async with GLOBAL_API_SEMAPHORE:
                resolved.update(await asyncio.to_thread(self._am_search_isrc_batch, batch))
        return resolved

    def _am_select_best(self, song_title, artist_name, candidates, local_duration_s=None):
        """Devuelve (tid, meta) del mejor candidato. Tie-break: duración más cercana."""
        if not candidates:
            return None
        try:
            from rapidfuzz import fuzz as _fuzz  # pylint: disable=import-outside-toplevel
            ct, ca = clean_metadata(song_title, artist_name)
            ref = f"{ct} {ca}".lower()
            scored = [
                (int(_fuzz.token_sort_ratio(ref, cand_str.lower())), tid, meta)
                for cand_str, tid, meta in candidates
            ]
            best_score = max(s[0] for s in scored)
            pool = [s for s in scored if s[0] >= best_score - 5]  # margen como _yt_select_best
            if local_duration_s is not None and len(pool) > 1:
                local_ms = local_duration_s * 1000
                def _delta(s):
                    ms = s[2][2] if len(s[2]) >= 3 else 0
                    return abs(ms - local_ms) if ms else float("inf")
                pool = [min(pool, key=_delta)]
            _, best_id, best_meta = pool[0]
        except ImportError:
            best_id   = candidates[0][1]
            best_meta = candidates[0][2]
        if best_id is None:
            return None
        return best_id, best_meta

    def _am_pack_result(self, tid, meta, orig_name, orig_artist) -> SearchResult:
        from engine.match import pack_search_result

        found_t, fa = meta[0], meta[1]
        return pack_search_result(
            tid, found_t, fa, orig_name, orig_artist,
            isrc=normalize_isrc(meta[3] if len(meta) > 3 else None),
        )

    def _am_pick_catalog_best(self, song_title, artist_name, candidates, local_duration_s=None) -> SearchResult:
        sel = self._am_select_best(song_title, artist_name, candidates, local_duration_s)
        if not sel:
            return SearchResult(None, False)
        return self._am_pack_result(sel[0], sel[1], song_title, artist_name)

    async def _am_hunter_async(
        self, ct, ca, orig_name, orig_artist, local_duration_s=None,
        local_isrc: str | None = None,
    ) -> SearchResult:
        exact_isrc = normalize_isrc(local_isrc)
        if exact_isrc:
            exact = await self.search_by_isrcs([exact_isrc])
            if exact_isrc in exact and exact[exact_isrc].track_id:
                return exact[exact_isrc]
        terms: list[str] = []
        for t in (
            build_search_query(ct, ca),
            build_search_query(_normalize_title(orig_name), _normalize_title(orig_artist)),
            _normalize_title(orig_name),
        ):
            t = t.strip()
            if t and t not in terms:
                terms.append(t)

        best: Optional[tuple] = None
        best_comb = -1
        for i, term in enumerate(terms):
            if i > 0:
                await asyncio.sleep(random.uniform(0.8, 1.3))
            async with GLOBAL_API_SEMAPHORE:
                chunk = await asyncio.to_thread(self._am_candidates_for_term, term)
            if not chunk:
                continue
            sel = self._am_select_best(orig_name, orig_artist, chunk, local_duration_s)
            if not sel:
                continue
            tid, meta = sel
            comb, tit, art = _fuzzy_scores_triple(orig_name, orig_artist, meta[0], meta[1])
            if _ideal_pass_hunter(comb, tit, art):
                return self._am_pack_result(tid, meta, orig_name, orig_artist)
            if comb > best_comb:
                best_comb, best = comb, (tid, meta)
        if best is None:
            return SearchResult(None, False)
        return self._am_pack_result(best[0], best[1], orig_name, orig_artist)

    # ── Spotify Hunter ─────────────────────────────────────────────────

    def _sp_search_items(self, q: str, limit: int = 5) -> list:
        if not HAS_SPOTIFY or not self._sp_cfg:
            return []
        if self._sp_song is None:
            # Fallback anónimo (login falló pero el catálogo público sirve)
            self._sp_song = Song(client=self._sp_cfg.client)
        r = self._sp_song.query_songs(q, limit=limit)
        try:
            return r["data"]["searchV2"]["tracksV2"]["items"]
        except (KeyError, TypeError):
            return []

    def _sp_track_from_item(self, data: dict) -> Optional[Track]:
        """Convierte un item de búsqueda de Spotify en Track para la UI."""
        tid = str(data.get("id") or "")
        if not tid:
            return None
        artists = ", ".join(
            a["profile"]["name"] for a in (data.get("artists") or {}).get("items", [])
            if a.get("profile", {}).get("name")
        )
        album = data.get("albumOfTrack") or {}
        ms = int((data.get("duration") or {}).get("totalMilliseconds", 0) or 0)
        covers = ((album.get("coverArt") or {}).get("sources") or [])
        rel = ""
        date_obj = album.get("date") or {}
        if isinstance(date_obj, dict):
            rel = str(date_obj.get("isoString") or date_obj.get("year") or "").strip()
        return Track(
            id=tid,
            name=data.get("name", "Unknown"),
            artist=artists or "Unknown",
            album=album.get("name", "Unknown"),
            duration=f"{ms // 60000}:{(ms // 1000) % 60:02d}" if ms else "0:00",
            img_url=self._spotify_cover_best(covers),
            platform="Spotify",
            duration_ms=ms,
            is_explicit=bool(data.get("explicit")),
            isrc=self._extract_spotify_isrc(data),
            release_date=rel,
            source_url=f"https://open.spotify.com/track/{tid}",
        )

    def _sp_search_candidates_sync(self, query: str, limit: int = 5) -> list[Track]:
        out: list[Track] = []
        for item in self._sp_search_items(query, limit=limit):
            data = (item.get("item") or {}).get("data") or {}
            track = self._sp_track_from_item(data)
            if track is not None:
                out.append(track)
        return out

    @staticmethod
    def _extract_spotify_isrc(data: dict) -> str | None:
        """Lee ISRC solo del payload ya recibido de Spotify/SpotAPI."""
        for key in ("external_ids", "externalIds"):
            external = data.get(key) or {}
            if isinstance(external, dict):
                value = external.get("isrc")
                result = normalize_isrc(value)
                if result:
                    return result
        return normalize_isrc(data.get("isrc"))

    def _sp_pick_best_item(self, items, orig_name, orig_artist,
                           local_duration_ms=0, local_is_explicit=False):
        """Devuelve (item, comb, tit, art) del mejor candidato por score."""
        if not items:
            return None, 0, 0, 0
        scored = []
        for it in items:
            d = it.get("item", {}).get("data", {})
            if not d or not d.get("id"):
                continue
            found_title = d.get("name", "")
            fa = ", ".join(
                a["profile"]["name"] for a in (d.get("artists") or {}).get("items", [])
                if a.get("profile", {}).get("name")
            )
            sp_dur_ms   = (d.get("duration") or {}).get("totalMilliseconds", 0) or 0
            sp_explicit = bool(d.get("explicit")) if d.get("explicit") is not None else False
            sc = score_spotify_match(
                orig_name, orig_artist, local_duration_ms, local_is_explicit,
                found_title, fa, sp_dur_ms, sp_explicit,
            )
            comb, tit, art = _fuzzy_scores_triple(orig_name, orig_artist, found_title, fa)
            scored.append((d, sc, comb, tit, art))
        if not scored:
            return None, 0, 0, 0
        scored.sort(key=lambda x: -x[1])
        best_d, _sc, comb, tit, art = scored[0]
        return best_d, comb, tit, art

    def _sp_build_result(self, d, comb, tit, art) -> SearchResult:
        # Spotify ya trae comb/tit/art de _sp_pick_best_item (scoring con
        # duración/explicit). Fuente única de flags es _fuzzy_flags_elastic.
        needs, low = _fuzzy_flags_elastic(comb, tit, art)
        return SearchResult(
            d.get("id"), needs, low_confidence=low,
            isrc=self._extract_spotify_isrc(d),
        )

    async def _sp_hunter_async(self, ct, ca, orig_name, orig_artist,
                               local_duration_ms=0, local_is_explicit=False) -> SearchResult:
        """
        Hunter de Spotify con 1-2 queries por canción.

        Con "Título Artista" la canción suele aparecer en los primeros
        resultados, así que basta la query primaria; solo si no hay match
        ideal se prueba 1 fallback con el título normalizado.
        """
        if not HAS_SPOTIFY:
            return SearchResult(None, False)
        if not self._sp_cfg:
            await asyncio.to_thread(self._sync_init_spotify)
        if not self._sp_cfg:
            # No recrear Config (coste 5-8 req de setup TLS/hashes). Si falla
            # el login, el fallback anónimo en _sp_search_items ya cubre
            # búsquedas públicas sin sesión. Evita gasto extra (regla 7).
            return SearchResult(None, False)

        nt = _normalize_title(orig_name)
        na = _normalize_title(orig_artist)
        queries: list[str] = []
        seen: set[str] = set()
        for q in (build_search_query(ct, ca), build_search_query(nt, na)):
            q = (q or "").strip()
            # Dedup insensible a mayúsculas: para Spotify ambas variantes
            # son la misma búsqueda; evita gastar un request extra.
            if q and q.lower() not in seen:
                seen.add(q.lower())
                queries.append(q)

        best: Optional[tuple] = None
        best_comb = -1

        for q in queries:
            async with GLOBAL_API_SEMAPHORE:
                try:
                    items = await asyncio.to_thread(self._sp_search_items, q)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.spotify_auth_error = str(exc)
                    raise
            if not items:
                continue
            picked, comb, tit, art = self._sp_pick_best_item(
                items, orig_name, orig_artist, local_duration_ms, local_is_explicit
            )
            if picked is None:
                continue
            if comb >= FUZZY_IDEAL or _ideal_pass_hunter(comb, tit, art):
                return self._sp_build_result(picked, comb, tit, art)
            if comb > best_comb:
                best_comb, best = comb, (picked, comb, tit, art)

        if best is None:
            return SearchResult(None, False)
        return self._sp_build_result(*best)

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

    def _yt_add_track(self, playlist_id: str, track_id: str) -> tuple[bool, str]:
        if not self._ytm:
            self._sync_init_youtube()
        if not self._ytm:
            return False, "YouTube Music no disponible"
        try:
            status = self._ytm.add_playlist_items(playlist_id, [track_id])
            if status == "STATUS_SUCCEEDED":
                return True, playlist_id
            return False, str(status or "YouTube Music no confirmó la inserción")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.youtube_auth_error = str(exc)
            if _is_ytm_unauthorized(exc):
                raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
            raise

    def _am_add_track(self, playlist_id: str, track_id: str) -> tuple[bool, str]:
        response = self._am_request(
            "post", f"{APPLE_API_BASE}/me/library/playlists/{playlist_id}/tracks",
            json={"data": [{"id": track_id, "type": "songs"}]}, timeout=15,
        )
        response.raise_for_status()
        try:
            present = self._am_playlist_track_ids(playlist_id)
        except (RateLimitError, AppleAuthError):
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            return True, "Insertada (sin verificación posterior)"
        if track_id in present:
            return True, playlist_id
        return False, "Apple Music no confirmó la inserción (canción no disponible)"

    def _sp_add_track(self, playlist_id: str, track_id: str) -> tuple[bool, str]:
        playlist_id = normalize_playlist_id("Spotify", playlist_id)
        if not playlist_id:
            return False, "Playlist de Spotify sin identificador"
        if not HAS_SPOTIFY:
            return False, "SpotAPI no disponible"
        if not self._sp_login:
            self._sync_init_spotify()
        if not self._sp_login:
            return False, "Spotify no disponible. Comprueba config/spotify_cookies.json."
        try:
            pl = PrivatePlaylist(self._sp_login, playlist_id)
            # Reutiliza el Song de búsqueda: SpotAPI vuelve a pedir sesión,
            # client token y hashes JS (~5-8 requests) por cada BaseClient
            # nuevo; eso era el retraso de ~10 s al insertar una alternativa.
            song = self._sp_song
            if song is None:
                song = Song(client=self._sp_cfg.client)
                self._sp_song = song
            with self._sp_insert_lock:
                song.playlist = pl
                rejected = self._sp_add_tracks(pl, [track_id], song=song)
            if rejected:
                return False, "Spotify rechazó la inserción"
            return True, playlist_id
        except RateLimitError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            if self._sp_is_rate_limited(exc):
                raise RateLimitError("Spotify", 60) from exc
            raise

    def _yt_create(self, title: str, ids: list[str], description: str = "") -> tuple[bool, str, int, list[str]]:
        try:
            pl_id = self._ytm.create_playlist(title, description or "", video_ids=ids)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.youtube_auth_error = str(exc)
            if _is_ytm_unauthorized(exc):
                raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
            raise
        try:
            items = self._ytm.get_playlist(pl_id, limit=len(ids) + 10)
            confirmed_ids = {t.get("videoId") for t in items.get("tracks", []) if t.get("videoId")}
            rejected      = [vid for vid in ids if vid not in confirmed_ids]
            return True, pl_id, len(confirmed_ids), rejected
        except Exception:  # pylint: disable=broad-exception-caught
            return True, pl_id, len(ids), []

    def _am_create(self, title: str, ids: list[str], description: str = "") -> tuple[bool, str, int, list[str]]:
        if not ids:
            return False, "No hay canciones para insertar", 0, []
        chunks = [ids[start:start + APPLE_TRANSFER_BATCH]
                  for start in range(0, len(ids), APPLE_TRANSFER_BATCH)]
        first = chunks[0]
        payload = {
            "attributes": {"name": title, "description": description or ""},
            "relationships": {"tracks": {"data": [{"id": i, "type": "songs"} for i in first]}},
        }
        response = self._am_request(
            "post", f"{APPLE_API_BASE}/me/library/playlists",
            json=payload, timeout=15,
        )
        response.raise_for_status()
        body = response.json() if response.content else {}
        playlist_id = ((body.get("data") or [{}])[0]).get("id", "")
        if len(chunks) > 1 and not playlist_id:
            raise RuntimeError("Apple Music no devolvió el ID de la playlist para continuar los lotes")

        confirmed = len(first)
        added = list(first)
        try:
            for chunk in chunks[1:]:
                response = self._am_request(
                    "post", f"{APPLE_API_BASE}/me/library/playlists/{playlist_id}/tracks",
                    json={"data": [{"id": i, "type": "songs"} for i in chunk]},
                    timeout=15,
                )
                response.raise_for_status()
                confirmed += len(chunk)
                added.extend(chunk)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # La playlist ya existe aunque un lote posterior falle. Se adjunta
            # el contexto para que el reporte pueda enlazarla y listar lo
            # que quedó pendiente.
            try:
                added_set = set(added)
                setattr(exc, "playlist_id", playlist_id)
                setattr(exc, "confirmed_ids", list(added))
                setattr(exc, "pending_ids", [i for i in ids if i not in added_set])
            except Exception:
                pass
            raise
        # Apple acepta el POST aunque una canción no esté disponible en el
        # storefront y la descarta en silencio. Solo una lectura posterior
        # permite saber cuáles entraron de verdad.
        if playlist_id:
            try:
                present = self._am_playlist_track_ids(playlist_id, expected=len(ids))
                unique_ids = set(ids)
                if sum(1 for tid in unique_ids if tid in present) < len(unique_ids):
                    # Consistencia eventual de la biblioteca: una lectura
                    # inmediata puede no ver aún las últimas inserciones.
                    time.sleep(1.5)
                    present = self._am_playlist_track_ids(playlist_id, expected=len(ids))
                rejected = [tid for tid in ids if tid not in present]
                confirmed = len(ids) - len(rejected)
                return True, playlist_id, confirmed, rejected
            except (RateLimitError, AppleAuthError):
                raise
            except Exception:  # pylint: disable=broad-exception-caught
                # La playlist ya existe: no se invalida la transferencia por
                # un fallo de verificación. Se conservan los contadores
                # optimistas y el estado queda registrado en el log.
                pass
        return True, playlist_id or "Playlist creada", confirmed, []

    @staticmethod
    def _apple_identity_ids(item: dict) -> set[str]:
        """IDs útiles de una pista Apple: biblioteca y catálogo.

        Las pistas de una playlist de biblioteca suelen venir como
        ``library-songs`` con ``id`` tipo ``i.xxx``; el ID de catálogo que
        usa la API de creación vive en ``attributes.playParams.id``. La
        verificación debe aceptar ambos para no marcar falsos rechazos.
        """
        ids: set[str] = set()
        raw = str(item.get("id") or "").strip()
        if raw:
            ids.add(raw)
        attrs = item.get("attributes") or {}
        play_params = attrs.get("playParams") or {}
        if isinstance(play_params, dict):
            for key in ("id", "catalogId"):
                value = play_params.get(key)
                if value:
                    ids.add(str(value))
        return ids

    def _am_playlist_track_ids(self, playlist_id: str, *, expected: int = 0) -> set[str]:
        """Lee los IDs reales de una playlist de biblioteca paginando por offset."""
        url = f"{APPLE_API_BASE}/me/library/playlists/{playlist_id}/tracks"
        found: set[str] = set()
        offset = 0
        while True:
            page = self._apple_track_page(url, offset)
            if not page:
                break
            for item in page:
                found.update(self._apple_identity_ids(item))
            if len(page) < APPLE_PAGE_SIZE:
                break
            offset += APPLE_PAGE_SIZE
            if expected and offset >= expected + APPLE_PAGE_SIZE:
                break
        return found

    def _sp_create(self, title: str, ids: list[str], description: str = "") -> tuple[bool, str, int, list[str]]:
        # SpotAPI (PrivatePlaylist.create_playlist) solo acepta título:
        # la descripción se ignora y se documenta con un warn en el log
        # del caller. Requeriría migrar a la Web API oficial con OAuth
        # (PUT /playlists/{id} + scope playlist-modify-*) para soportarla.
        if not HAS_SPOTIFY:
            return False, "SpotAPI no disponible", 0, []
        if not self._sp_login:
            self._sync_init_spotify()
        if not self._sp_login:
            return False, "Spotify no disponible. Comprueba config/spotify_cookies.json.", 0, []
        pl = PrivatePlaylist(self._sp_login)
        try:
            # create_playlist() devuelve el URI completo
            # (spotify:playlist:<id>); el resto de la cadena espera el ID
            # desnudo. Normalizarlo aquí evita el doble prefijo al insertar
            # alternativas y la URL inválida del reporte.
            pl_id = normalize_playlist_id("Spotify", pl.create_playlist(title))
            if not pl_id:
                return False, "Spotify no devolvió un ID de playlist válido", 0, []
            # SpotAPI no asigna playlist_id al crear; sin set_playlist, un
            # Song(pl).add_songs_to_playlist() revienta con
            # ValueError("Playlist not set") y mata la transferencia entera.
            pl.set_playlist(pl_id)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            if self._sp_is_rate_limited(exc):
                raise RateLimitError("Spotify", 60) from exc
            raise
        rejected = self._sp_add_tracks(pl, ids)
        return True, pl_id, len(ids) - len(rejected), rejected

    @staticmethod
    def _sp_is_rate_limited(exc: BaseException) -> bool:
        """
        Detección de rate-limiting (HTTP 429/423) en errores de SpotAPI
        para que el circuit breaker los tramite como el resto de plataformas.
        """
        msg = str(exc)
        if "Status Code: 429" in msg or "Status Code: 423" in msg:
            return True
        payload = getattr(exc, "error", None)
        return bool(payload and ("429" in str(payload) or "423" in str(payload)))

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

    def _yt_delete(self, pid: str) -> tuple[bool, str]:
        if not self._ytm:
            self._sync_init_youtube()
        if not self._ytm:
            return False, "YouTube Music no disponible"
        try:
            # ytmusicapi delete_playlist returns status
            self._ytm.delete_playlist(pid)
            return True, pid
        except Exception as exc:
            return False, str(exc)

    def _am_delete(self, pid: str) -> tuple[bool, str]:
        try:
            r = self._am_request("delete", f"{APPLE_API_BASE}/me/library/playlists/{pid}", timeout=10)
            if r.status_code in (200, 204):
                return True, pid
            return False, f"Apple {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    def _sp_delete(self, pid: str) -> tuple[bool, str]:
        # Spotify: unfollow playlist = delete para usuario; SpotAPI no expone, usar Web API con cookies
        pid = normalize_playlist_id("Spotify", pid)
        if not pid:
            return False, "Playlist de Spotify sin identificador"
        try:
            if not self._sp_login:
                self._sync_init_spotify()
            # SpotAPI elimina la playlist de la biblioteca. El método no
            # recibe argumentos: usa el playlist_id del constructor.
            try:
                if HAS_SPOTIFY and self._sp_login:
                    pl = PrivatePlaylist(self._sp_login, pid)
                    if hasattr(pl, "delete_playlist"):
                        pl.delete_playlist()
                        return True, pid
            except Exception:
                pass
            # Web API unfollow
            # Usa session de _http_session si tiene auth cookies (best effort)
            r = self._http_session.delete(f"https://api.spotify.com/v1/playlists/{pid}/followers", timeout=10)
            if r.status_code in (200, 204):
                return True, pid
            return False, f"Spotify {r.status_code}"
        except Exception as exc:
            return False, str(exc)

    @staticmethod
    def _sp_add_tracks(pl, ids: list[str], song=None) -> list[str]:
        """
        Añade las canciones a la playlist por lotes de SPOTIFY_ADD_CHUNK.

        Fraccionar imita al cliente web: evita que una ráfaga de cientos de
        tracks sea rechazada o rate-limiteada, y aísla un ID problemático
        para que no tumbe la transferencia completa.

        Args:
            pl: PrivatePlaylist con playlist_id ya asignado.
            ids: Track IDs de Spotify a insertar.
            song: Song reutilizable con BaseClient caliente. Si se omite,
                se construye uno nuevo para mantener el flujo de creación.

        Returns:
            Lista de track_ids que NO se pudieron insertar.
        """
        if not ids:
            return []
        if song is None:
            song = Song(pl)
        rejected: list[str] = []
        starts = list(range(0, len(ids), SPOTIFY_ADD_CHUNK))
        for index, start in enumerate(starts):
            chunk = ids[start:start + SPOTIFY_ADD_CHUNK]
            wait = 1.5
            for attempt in range(4):
                try:
                    song.add_songs_to_playlist(chunk)
                    break
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    rl = MusicApiService._sp_is_rate_limited(exc)
                    if attempt >= 3:
                        if rl:
                            raise RateLimitError("Spotify", 60) from exc
                        rejected.extend(ids[start:])
                        return rejected
                    time.sleep(wait)
                    wait *= 2
                    if rl:
                        wait = max(wait, 3.0)
            # Solo espacia lotes consecutivos; no penaliza una inserción suelta.
            if index < len(starts) - 1:
                time.sleep(random.uniform(0.4, 0.8))
        return rejected
