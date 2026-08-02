"""
╔══════════════════════════════════════════════════════════════════════╗
║                    MelomaniacPass v5.0                               ║
║              Servicio Unificado de APIs Musicales                    ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: services/api_service.py
Descripción: Fachada unificada asíncrona sobre las APIs de YouTube Music
             y Apple Music. Abstrae las diferencias entre plataformas
             proporcionando una interfaz consistente.

Estrategia de Diseño - Patrón Facade:
    MusicApiService actúa como punto único de acceso a múltiples APIs
    externas, ocultando su complejidad y diferencias:
    
    1. Abstracción de Plataformas:
       - Interfaz unificada para búsqueda, carga y transferencia
       - Normalización de respuestas a modelos comunes (Track, SearchResult)
       - Manejo consistente de errores entre plataformas
    
    2. Gestión de Autenticación:
        - YouTube Music: Headers de sesión (Cookie + Authorization)
        - Apple Music: Bearer token + User token
    
    3. Resiliencia y Rate Limiting:
       - Circuit breakers por plataforma
       - Reintentos con backoff exponencial
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
       - NETWORK_CONCURRENCY: Límite de peticiones concurrentes (5)
       - RATE_LIMIT_BACKOFF_STEPS: Reintentos ante rate limiting (10)

   Funciones Auxiliares:
       - _is_ytm_unauthorized: Detecta HTTP 401 de YouTube Music

Autor: MelomaniacPass Team
Versión: 5.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import Callable, Optional
from urllib.parse import quote

import requests
from dotenv import load_dotenv

from auth_manager import BROWSER_JSON
from core.models import Track, SearchResult
from utils.circuit_breaker import CircuitBreaker, RateLimitError
from engine.normalizer import (
    clean_metadata, build_search_query, _normalize_title, FUZZY_IDEAL,
)
from engine.match import (
    _fuzzy_scores_triple, _fuzzy_flags_elastic, _ideal_pass_hunter,
    _joji_trikeyword_query, _duration_to_seconds,
    validar_match, _yt_select_best,
)

load_dotenv()

# ══════════════════════════════════════════════════════════════════════
# DETECCIÓN DE LIBRERÍAS OPCIONALES
# ══════════════════════════════════════════════════════════════════════

try:
    from ytmusicapi import YTMusic
    HAS_YTMUSIC = True
except ImportError:
    HAS_YTMUSIC = False

# ══════════════════════════════════════════════════════════════════════
# CONSTANTES DE CONFIGURACIÓN
# ══════════════════════════════════════════════════════════════════════

# Límite de peticiones HTTP concurrentes para evitar sobrecarga
NETWORK_CONCURRENCY = 2

# Número de reintentos ante rate limiting (HTTP 429)
RATE_LIMIT_BACKOFF_STEPS = 10

# Semáforo global para limitar concurrencia de peticiones
GLOBAL_API_SEMAPHORE = asyncio.Semaphore(NETWORK_CONCURRENCY)


# Mensaje de error para sesión expirada de YouTube Music
_YTM_401_MSG = (
    "[ERROR] YouTube Music: la sesion de browser.json ha expirado (401). "
    "Renueva Cookie + Authorization desde el navegador."
)


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
        Un 401 indica que los headers de browser.json han expirado y
        necesitan ser renovados desde el navegador.
    """
    s = str(exc).lower()
    if "401" in str(exc) or "status code: 401" in s or "unauthorized" in s:
        return True
    resp = getattr(exc, "response", None)
    return resp is not None and getattr(resp, "status_code", None) == 401


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
        self._cb  = circuit_breakers
        self._ytm = None
        self._am_headers:    dict = {}
        self._am_storefront: str  = "us"
        self._search_cache: dict[str, SearchResult] = {}
        self._shutdown_cleaned: bool = False
        self.youtube_auth_error: str = ""
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
        self._http_session = requests.Session()
        self._http_session.headers.update({"User-Agent": _am_ua})
        self._yt_http_session = requests.Session()

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

    # ── YouTube Music Auth ─────────────────────────────────────────────

    async def init_youtube(self) -> bool:
        return await asyncio.to_thread(self._sync_init_youtube)

    def _sync_init_youtube(self) -> bool:
        if not HAS_YTMUSIC:
            return False
        if not BROWSER_JSON.exists():
            self.youtube_auth_error = "missing browser.json"
            return False
        self.youtube_auth_error = ""
        try:
            self._ytm = YTMusic(auth=str(BROWSER_JSON), requests_session=self._yt_http_session)
            self._ytm.get_library_playlists(limit=1)
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
            resp = self._http_session.get(
                "https://amp-api.music.apple.com/v1/me/storefront",
                headers=am_headers, timeout=10,
            )
            if resp.status_code == 200:
                self._am_headers    = am_headers
                self._http_session.headers.update(am_headers)
                self._am_storefront = resp.json().get("data", [{}])[0].get("id", "us")
                return True
            return False
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[Apple Music] init failed: {exc}")
            return False


    # ── Playlist Fetching ──────────────────────────────────────────────

    async def fetch_playlist(
        self,
        platform: str,
        playlist_id: str,
        progress_cb: Optional[Callable[[int, int, str], None]] = None,
    ) -> tuple[str, list[Track]]:
        self._cb[platform].check_or_raise()
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._sync_fetch_youtube, playlist_id, progress_cb)
        elif platform == "Apple Music":
            return await asyncio.to_thread(self._sync_fetch_apple, playlist_id, progress_cb)
        raise ValueError(f"Unknown platform: {platform}")

    def _sync_fetch_youtube(self, pid: str, cb) -> tuple[str, list[Track]]:
        if not self._ytm:
            self._sync_init_youtube()
        if not self._ytm:
            raise RuntimeError("YouTube Music no disponible. Comprueba browser.json.")
        try:
            pl = self._ytm.get_playlist(pid, limit=None)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.youtube_auth_error = str(exc)
            if _is_ytm_unauthorized(exc):
                raise RuntimeError("Sesion YouTube Music expirada (401). Renueva browser.json.") from exc
            raise
        name  = pl.get("title", "YouTube Playlist")
        raw   = pl.get("tracks", [])
        total = len(raw)
        tracks = []
        for i, t in enumerate(raw, 1):
            thumbs = t.get("thumbnails", [])
            tracks.append(Track(
                id=t["videoId"], name=t["title"],
                artist=", ".join(a["name"] for a in t.get("artists", [])),
                album=(t.get("album") or {}).get("name", "Single"),
                duration=t.get("duration", "0:00"),
                img_url=thumbs[-1]["url"] if thumbs else "",
                platform="YouTube Music",
            ))
            if cb and i % 50 == 0:
                cb(i, total, name)
        return name, tracks

    def _sync_fetch_apple(self, pid: str, cb) -> tuple[str, list[Track]]:
        base     = "https://amp-api.music.apple.com/v1"
        is_lib   = pid.startswith("p.")
        info_url = (
            f"{base}/me/library/playlists/{pid}" if is_lib
            else f"{base}/catalog/{self._am_storefront}/playlists/{pid}"
        )
        name = "Apple Music Playlist"
        try:
            r = self._http_session.get(info_url, timeout=10)
            if r.status_code == 429:
                raise RateLimitError("Apple Music", int(r.headers.get("Retry-After", 60)))
            if r.ok:
                name = r.json()["data"][0]["attributes"].get("name", name)
        except RateLimitError:
            raise
        except Exception:  # pylint: disable=broad-exception-caught
            pass

        tracks, url = [], f"{info_url}/tracks"
        while url:
            full = url if url.startswith("http") else f"https://amp-api.music.apple.com{url}"
            r    = self._http_session.get(full, timeout=10)
            if r.status_code == 429:
                raise RateLimitError("Apple Music", int(r.headers.get("Retry-After", 60)))
            r.raise_for_status()
            data = r.json()
            for item in data.get("data", []):
                attrs  = item.get("attributes", {})
                ms     = attrs.get("durationInMillis", 0)
                arturl = attrs.get("artwork", {}).get("url", "")
                if arturl:
                    arturl = arturl.replace("{w}", "60").replace("{h}", "60")
                tracks.append(Track(
                    id=item["id"], name=attrs.get("name", "Unknown"),
                    artist=attrs.get("artistName", "Unknown"),
                    album=attrs.get("albumName", "Unknown"),
                    duration=f"{int(ms/60000)}:{int((ms/1000)%60):02d}",
                    img_url=arturl, platform="Apple Music",
                ))
            url = data.get("next")
            if cb:
                cb(len(tracks), 0, name)
        return name, tracks


    # ── Search ─────────────────────────────────────────────────────────

    async def search_track(
        self,
        platform: str,
        name: str,
        artist: str,
        local_duration_s: Optional[int] = None,
        local_duration_ms: int = 0,
        local_is_explicit: bool = False,
    ) -> SearchResult:
        ct, ca = clean_metadata(name, artist)
        self._cb[platform].check_or_raise()
        await asyncio.sleep(random.uniform(0.5, 1.5))
        if platform == "YouTube Music":
            return await self._yt_hunter_async(ct, ca, name, artist, local_duration_s)
        if platform == "Apple Music":
            return await self._am_hunter_async(ct, ca, name, artist)
        return SearchResult(None, False)

    async def search_with_fallback(
        self,
        platform: str,
        name: str,
        artist: str,
        local_duration_s: Optional[int] = None,
        local_duration_ms: int = 0,
        local_is_explicit: bool = False,
    ) -> SearchResult:
        base_t, base_a = clean_metadata(name, artist)
        passes = [
            (base_t, base_a),
            (name.strip(), artist.strip()),
            (_normalize_title(name), base_a),
        ]
        seen: set[tuple[str, str]] = set()
        for idx, (t_pass, a_pass) in enumerate(passes):
            t_pass = t_pass.strip()
            if not t_pass:
                continue
            key = (t_pass.lower(), a_pass.strip().lower())
            if key in seen:
                continue
            seen.add(key)
            result = await self.search_track(
                platform, t_pass, a_pass, local_duration_s,
                local_duration_ms=local_duration_ms,
                local_is_explicit=local_is_explicit,
            )
            if result.track_id:
                if idx == 0 and not result.needs_review and not result.low_confidence:
                    return result
                return result
        return SearchResult(None, False)

    # ── YouTube Music Hunter ───────────────────────────────────────────

    def _yt_pack_result(self, chosen: dict, orig_name: str, orig_artist: str) -> SearchResult:
        found_title = chosen.get("title", "")
        farts = ", ".join(
            a.get("name", "") for a in (chosen.get("artists") or []) if isinstance(a, dict)
        )
        comb, tit, art = _fuzzy_scores_triple(orig_name, orig_artist, found_title, farts)
        needs, low = _fuzzy_flags_elastic(comb, tit, art)
        return SearchResult(chosen.get("videoId"), needs, low_confidence=low)

    def _yt_sync_search_round(self, query, orig_name, orig_artist, local_duration_s, cached_results=None):
        results = cached_results if cached_results is not None else self._ytm.search(query, filter="songs", limit=8)
        if not results:
            return None
        vid = _yt_select_best(orig_name, orig_artist, results, local_duration_s)
        if not vid:
            return None
        chosen = next(
            (r for r in results[:8] if r.get("videoId") == vid and validar_match(orig_name, orig_artist, r, local_duration_s)),
            next((r for r in results if r.get("videoId") == vid), None)
        )
        if not chosen:
            return None
        found_title = chosen.get("title", "")
        farts = ", ".join(a.get("name", "") for a in (chosen.get("artists") or []) if isinstance(a, dict))
        comb, tit, art = _fuzzy_scores_triple(orig_name, orig_artist, found_title, farts)
        return chosen, comb, tit, art

    def _yt_search_songs_sync(self, query: str) -> list:
        if not self._ytm:
            return []
        r = self._ytm.search(query, filter="songs", limit=8)
        return list(r) if r else []

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
                    results = await asyncio.to_thread(self._yt_search_songs_sync, query)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.youtube_auth_error = str(exc)
                    if _is_ytm_unauthorized(exc):
                        raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
                    raise
            if results:
                strict_empty_api = False
            if not results:
                continue
            pack = await asyncio.to_thread(self._yt_sync_search_round, query, orig_name, orig_artist, local_duration_s, results)
            ideal = _process_pack(pack)
            if ideal is not None:
                return ideal
            if pack:
                chosen, comb, _, _ = pack
                if comb > best_comb:
                    best_comb, best = comb, chosen

        if strict_empty_api and raw_q:
            for query in raw_q:
                async with GLOBAL_API_SEMAPHORE:
                    try:
                        results = await asyncio.to_thread(self._yt_search_songs_sync, query)
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        self.youtube_auth_error = str(exc)
                        if _is_ytm_unauthorized(exc):
                            raise RuntimeError("Sesion YouTube Music expirada (401).") from exc
                        raise
                if not results:
                    continue
                pack = await asyncio.to_thread(self._yt_sync_search_round, query, orig_name, orig_artist, local_duration_s, results)
                ideal = _process_pack(pack)
                if ideal is not None:
                    return ideal
                if pack:
                    chosen, comb, _, _ = pack
                    if comb > best_comb:
                        best_comb, best = comb, chosen

        if best is None:
            return SearchResult(None, False)
        return self._yt_pack_result(best, orig_name, orig_artist)


    # ── Apple Music Hunter ─────────────────────────────────────────────

    def _am_candidates_for_term(self, term: str) -> list[tuple[str, str, tuple[str, str]]]:
        q   = quote(term)
        url = (
            f"https://api.music.apple.com/v1/catalog/{self._am_storefront}"
            f"/search?types=songs&term={q}&limit=5"
        )
        r = self._http_session.get(url, timeout=10)
        if r.status_code == 429:
            raise RateLimitError("Apple Music", int(r.headers.get("Retry-After", 60)))
        songs = r.json().get("results", {}).get("songs", {}).get("data", [])
        return [
            (
                f"{s['attributes'].get('name','')} - {s['attributes'].get('artistName','')}",
                s["id"],
                (s['attributes'].get('name', ''), s['attributes'].get('artistName', '')),
            )
            for s in songs
        ]

    def _am_pick_catalog_best(self, song_title, artist_name, candidates) -> SearchResult:
        if not candidates:
            return SearchResult(None, False)
        try:
            from rapidfuzz import fuzz as _fuzz  # pylint: disable=import-outside-toplevel
            ct, ca = clean_metadata(song_title, artist_name)
            ref = f"{ct} {ca}".lower()
            best_id, best_score, best_meta = None, -1, ("", "")
            for cand_str, tid, meta in candidates:
                sc = int(_fuzz.token_sort_ratio(ref, cand_str.lower()))
                if sc > best_score:
                    best_score, best_id, best_meta = sc, tid, meta
        except ImportError:
            best_id   = candidates[0][1]
            best_meta = candidates[0][2]
        if not best_id:
            return SearchResult(None, False)
        found_t, fa = best_meta
        comb, tit, art = _fuzzy_scores_triple(song_title, artist_name, found_t, fa)
        needs, low = _fuzzy_flags_elastic(comb, tit, art)
        return SearchResult(best_id, needs, low_confidence=low)

    async def _am_hunter_async(self, ct, ca, orig_name, orig_artist) -> SearchResult:
        terms: list[str] = []
        for t in (
            build_search_query(ct, ca),
            build_search_query(_normalize_title(orig_name), _normalize_title(orig_artist)),
            _normalize_title(orig_name),
        ):
            t = t.strip()
            if t and t not in terms:
                terms.append(t)
        merged: list[tuple[str, str, tuple[str, str]]] = []
        seen: set[str] = set()
        for term in terms:
            async with GLOBAL_API_SEMAPHORE:
                chunk = await asyncio.to_thread(self._am_candidates_for_term, term)
            for c in chunk:
                if c[1] not in seen:
                    seen.add(c[1])
                    merged.append(c)
        return self._am_pick_catalog_best(orig_name, orig_artist, merged)

    # ── Playlist Creation ──────────────────────────────────────────────

    async def create_playlist(self, platform: str, title: str, track_ids: list[str]) -> tuple[bool, str, int, list[str]]:
        self._cb[platform].check_or_raise()
        if platform == "YouTube Music":
            return await asyncio.to_thread(self._yt_create, title, track_ids)
        elif platform == "Apple Music":
            return await asyncio.to_thread(self._am_create, title, track_ids)
        return False, "Platform not supported", 0, []

    def _yt_create(self, title: str, ids: list[str]) -> tuple[bool, str, int, list[str]]:
        try:
            pl_id = self._ytm.create_playlist(title, "Transferida por MelomaniacPass", video_ids=ids)
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

    def _am_create(self, title: str, ids: list[str]) -> tuple[bool, str, int, list[str]]:
        payload = {
            "attributes": {"name": title, "description": "Transferida por MelomaniacPass"},
            "relationships": {"tracks": {"data": [{"id": i, "type": "songs"} for i in ids]}},
        }
        r = self._http_session.post(
            "https://amp-api.music.apple.com/v1/me/library/playlists",
            json=payload, timeout=15,
        )
        if r.status_code == 429:
            raise RateLimitError("Apple Music", int(r.headers.get("Retry-After", 60)))
        r.raise_for_status()
        return True, "Playlist creada", len(ids), []
