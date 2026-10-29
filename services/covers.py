"""
services/covers.py — Resolución de portadas de álbum
════════════════════════════════════════════════════════════════════
Antes de este módulo la única portada disponible para una descarga era el
thumbnail del vídeo de YouTube: una miniatura 16:9 que no es artwork. Aquí se
resuelve la portada real de cada pista por álbum, no por canción: todas las
pistas de un mismo ``album_artist|album|año`` comparten una sola consulta.

Cadena de resolución, de más veraz a más genérica:

1. Portada incrustada o sidecar junto al archivo (ya leída por
   ``engine.parsers`` / ``engine.audio_metadata``); el llamador la pasa hecha.
2. ``Track.img_url`` de la plataforma de origen, **si no es de YouTube**:
   Apple Music y Spotify entregan carátula cuadrada del álbum.
3. ISRC en MusicBrainz + Cover Art Archive (preciso, exige ``Track.isrc``).
4. iTunes Search por artista + álbum.
5. Deezer por artista + álbum.
6. ``Track.img_url`` de YouTube (miniatura de vídeo: casi siempre 16:9 y,
   aun cuadrada, ilustra el vídeo y no el disco).
7. Thumbnail dejado por ``yt-dlp``, solo si el llamador lo permite.

El orden lo decide ``CoverResolver._steps_for``; una portada de YouTube nunca
se consulta antes que las carátulas de álbum.

Los proveedores se consultan serializados con su propio intervalo y con un
``CircuitBreaker`` **independiente** de ``AppState.cb``: un fallo de portadas no
debe aparecer como "rate limit" de una plataforma ni bloquear la transferencia.
Por eso el breaker vive aquí y se cancela explícitamente en ``app.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

import requests
from rapidfuzz import fuzz

from core.config import (
    COVER_ALBUM_MIN_RATIO,
    COVER_ARTIST_MIN_RATIO,
    COVER_DEEZER_INTERVAL,
    COVER_HTTP_TIMEOUT,
    COVER_ITUNES_INTERVAL,
    COVER_MAX_BYTES,
    COVER_MUSICBRAINZ_INTERVAL,
    COVER_RATE_LIMIT_COOLDOWN,
    COVER_USER_AGENT,
)
from core.models import Track
from engine.covers import (
    _GOOGLE_CDN_HOSTS, is_acceptable_cover, normalize_cover, probe_cover, upscale_cover_url,
)
from engine.normalizer import _normalize_title
from services import cover_cache
from services.circuit_breaker import CircuitBreaker

__all__ = [
    "CoverResolution",
    "CoverResolver",
    "album_key",
    "cover_breaker",
]

_ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
_DEEZER_SEARCH_URL = "https://api.deezer.com/search/album"
_MUSICBRAINZ_RELEASE_URL = "https://musicbrainz.org/ws/2/release"
_COVERART_URL = "https://coverartarchive.org/release/{mbid}/front-500"

# Breaker propio del subsistema de portadas. Deliberadamente fuera de
# ``AppState.cb``: ``ui/main_ui.py`` suscribe todos los breakers de ahí al
# banner "Rate limit en {platform}" y usa `any(cb.is_open)` como red bloqueada.
cover_breaker = CircuitBreaker("covers", default_cooldown=COVER_RATE_LIMIT_COOLDOWN)


@dataclass(frozen=True)
class CoverResolution:
    """Resultado de resolver una portada.

    Attributes:
        data: Bytes JPEG ya normalizados a cuadrado, o ``b""`` si no hubo.
        source: Origen que resolvió la portada ("" si ninguno).
    """

    data: bytes = b""
    source: str = ""

    def __bool__(self) -> bool:
        return bool(self.data)


def album_key(track: Track) -> str:
    """Clave de caché por álbum: artista de álbum, álbum y año.

    Una pista sin álbum no puede compartir portada con otras, así que se usa
    una clave por pista (incluye el título) en lugar de agrupar todo lo vacío.
    """
    album = (track.album or "").strip()
    if not album:
        title = (track.name or track.id or "").strip().lower()
        return f"track:{track.platform}|{track.artist}|{title}" if title else ""
    artist = (track.album_artist or track.artist or "").strip()
    year = (track.release_date or "")[:4]
    raw = f"{artist}|{album}|{year}".lower()
    # El nombre del álbum puede traer caracteres que rompen un fichero/índice.
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{artist} - {album}".lower()


def _norm(text: str) -> str:
    """Normalización reutilizada de ``engine.normalizer`` (lower, sin ruido)."""
    return _normalize_title(text)


def _is_youtube_cover_url(url: str) -> bool:
    """``True`` si la URL es una miniatura de vídeo de YouTube/Google.

    Reutiliza los hosts del CDN declarados en ``engine.covers`` para no tener
    dos listas de hosts que mantener.
    """
    return any(host in url for host in _GOOGLE_CDN_HOSTS)


def _matches(track: Track, *, album: str, artist: str) -> bool:
    """Valida que el álbum del proveedor corresponda al ``Track``.

    Sin esta comprobación, buscar "The Wall" devuelve portadas de *otro* álbum
    homónimo y se incrusta artwork equivocado.
    """
    want_album = _norm(track.album)
    got_album = _norm(album)
    if want_album and got_album:
        if fuzz.token_sort_ratio(want_album, got_album) < COVER_ALBUM_MIN_RATIO:
            return False
    want_artist = _norm(track.album_artist or track.artist)
    got_artist = _norm(artist)
    if want_artist and got_artist:
        if fuzz.token_sort_ratio(want_artist, got_artist) < COVER_ARTIST_MIN_RATIO:
            return False
    return True


class CoverResolver:
    """Resuelve y cachea portadas por álbum.

    Args:
        session: ``requests.Session`` opcional a reutilizar.
        fetch: Callable ``(url) -> bytes`` opcional, inyectable para tests.
        get_json: Callable ``(url, params) -> dict`` opcional, inyectable para
            tests. Sin él, los proveedores se consultan por red.
    """

    def __init__(
        self,
        session: Optional[requests.Session] = None,
        fetch=None,
        get_json=None,
    ) -> None:
        self._session = session
        self._owns_session = session is None
        self._fetch = fetch
        self._get_json_hook = get_json
        self._last_call: dict[str, float] = {}
        self._album_memo: dict[str, CoverResolution] = {}
        # Contador de peticiones reales: el plan exige medirlo antes de
        # generalizar, porque con playlists heterogéneas se aproxima a una
        # petición por álbum distinto y no a las ~80 de 1.022 pistas.
        self.request_count: int = 0

    # ── red ────────────────────────────────────────────────────────────

    def _sess(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"User-Agent": COVER_USER_AGENT})
        return self._session

    def _throttle(self, provider: str, interval: float) -> None:
        """Serializa las peticiones de un proveedor con su intervalo mínimo."""
        last = self._last_call.get(provider, 0.0)
        wait = interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        self._last_call[provider] = time.monotonic()

    def _get_bytes(self, url: str, *, provider: str = "", interval: float = 0.0) -> bytes:
        """Descarga una imagen con tope de tamaño. Devuelve ``b""`` si falla."""
        if not url:
            return b""
        if self._fetch is not None:
            try:
                return self._fetch(url) or b""
            except Exception:
                return b""
        if interval:
            self._throttle(provider, interval)
        self.request_count += 1
        try:
            resp = self._sess().get(url, timeout=COVER_HTTP_TIMEOUT, stream=True)
            if resp.status_code == 429:
                cover_breaker.trip(COVER_RATE_LIMIT_COOLDOWN)
                return b""
            if resp.status_code >= 400:
                return b""
            total = 0
            chunks: list[bytes] = []
            for chunk in resp.iter_content(8192):
                if not chunk:
                    continue
                total += len(chunk)
                if total > COVER_MAX_BYTES:
                    return b""
                chunks.append(chunk)
            return b"".join(chunks)
        except Exception:
            return b""

    def _get_json(self, url: str, *, provider: str, params: dict, interval: float) -> dict:
        """GET JSON con throttle por proveedor. Devuelve ``{}`` si falla."""
        if cover_breaker.is_open:
            return {}
        if self._get_json_hook is not None:
            try:
                data = self._get_json_hook(url, params)
            except Exception:
                return {}
            return data if isinstance(data, dict) else {}
        self._throttle(provider, interval)
        self.request_count += 1
        try:
            resp = self._sess().get(url, params=params, timeout=COVER_HTTP_TIMEOUT)
            if resp.status_code == 429:
                cover_breaker.trip(COVER_RATE_LIMIT_COOLDOWN)
                return {}
            if resp.status_code >= 400:
                return {}
            data = resp.json()
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    # ── proveedores ────────────────────────────────────────────────────

    def _from_itunes(self, track: Track) -> CoverResolution:
        query = " ".join(x for x in (track.album_artist or track.artist, track.album) if x)
        if not query:
            return CoverResolution()
        data = self._get_json(
            _ITUNES_SEARCH_URL,
            provider="itunes",
            params={"term": query, "media": "music", "entity": "album", "limit": 5},
            interval=COVER_ITUNES_INTERVAL,
        )
        for item in data.get("results") or []:
            if not isinstance(item, dict):
                continue
            if not _matches(
                track,
                album=str(item.get("collectionName") or ""),
                artist=str(item.get("artistName") or ""),
            ):
                continue
            raw_url = str(item.get("artworkUrl100") or "")
            if not raw_url:
                continue
            # iTunes entrega 100x100 por defecto; el sufijo se puede subir.
            big = raw_url.replace("100x100bb", "600x600bb")
            payload = self._get_bytes(big, provider="itunes", interval=COVER_ITUNES_INTERVAL)
            cover = normalize_cover(payload) if payload else b""
            if cover:
                return CoverResolution(cover, "itunes")
        return CoverResolution()

    def _from_deezer(self, track: Track) -> CoverResolution:
        query = " ".join(x for x in (track.album_artist or track.artist, track.album) if x)
        if not query:
            return CoverResolution()
        data = self._get_json(
            _DEEZER_SEARCH_URL,
            provider="deezer",
            params={"q": query, "limit": 5},
            interval=COVER_DEEZER_INTERVAL,
        )
        for item in data.get("data") or []:
            if not isinstance(item, dict):
                continue
            artist = item.get("artist") or {}
            if not _matches(
                track,
                album=str(item.get("title") or ""),
                artist=str(artist.get("name") if isinstance(artist, dict) else ""),
            ):
                continue
            for key in ("cover_xl", "cover_big", "cover_medium"):
                raw_url = str(item.get(key) or "")
                if not raw_url:
                    continue
                payload = self._get_bytes(
                    raw_url, provider="deezer", interval=COVER_DEEZER_INTERVAL
                )
                cover = normalize_cover(payload) if payload else b""
                if cover:
                    return CoverResolution(cover, "deezer")
        return CoverResolution()

    def _from_isrc(self, track: Track) -> CoverResolution:
        """MusicBrainz por ISRC → Cover Art Archive. Solo si el Track trae ISRC."""
        isrc = (track.isrc or "").strip().upper()
        if not isrc:
            return CoverResolution()
        data = self._get_json(
            _MUSICBRAINZ_RELEASE_URL,
            provider="musicbrainz",
            params={"query": f"isrc:{isrc}", "fmt": "json", "limit": 5},
            interval=COVER_MUSICBRAINZ_INTERVAL,
        )
        releases = data.get("releases") or []
        if not releases or not isinstance(releases[0], dict):
            return CoverResolution()
        mbid = str(releases[0].get("id") or "")
        if not mbid:
            return CoverResolution()
        payload = self._get_bytes(
            _COVERART_URL.format(mbid=mbid),
            provider="musicbrainz",
            interval=COVER_MUSICBRAINZ_INTERVAL,
        )
        cover = normalize_cover(payload) if payload else b""
        return CoverResolution(cover, "musicbrainz") if cover else CoverResolution()

    # ── cadena ─────────────────────────────────────────────────────────

    def resolve(self, track: Track, *, allow_youtube_thumb: bool = False) -> CoverResolution:
        """Resuelve la portada de ``track``, cacheada por álbum.

        Args:
            track: Pista objetivo.
            allow_youtube_thumb: Si es una descarga, aceptar el thumbnail como
                último recurso. Solo se usa si es 1:1; uno 16:9 se descarta río
                arriba por ``is_acceptable_cover`` cuando hay proveedores.
        """
        key = album_key(track)
        if key and key in self._album_memo:
            return self._album_memo[key]

        cached = cover_cache.get_album_cover(key) if key else None
        if cached is not None:
            try:
                data = cached.read_bytes()
            except OSError:
                data = b""
            result = CoverResolution(data, "cache") if data else CoverResolution()
            if result:
                self._album_memo[key] = result
                return result

        result = CoverResolution()
        for step in self._steps_for(track):
            try:
                result = step()
            except Exception:
                result = CoverResolution()
            if result:
                break

        if not result and allow_youtube_thumb:
            result = self._from_platform_url(track, require_square=False)

        if result and key:
            self._album_memo[key] = result
            self._persist(key, result)
        return result

    def _persist(self, key: str, result: CoverResolution) -> None:
        """Guarda la portada resuelta en el caché de disco y la indexa por álbum."""
        if not result.data:
            return
        digest = hashlib.sha1(result.data).hexdigest()[:20]
        target = cover_cache.CACHE_DIR / f"{digest}.jpg"
        try:
            if not target.exists():
                cover_cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
                target.write_bytes(result.data)
            cover_cache.set_album_cover(key, target, source=result.source)
        except OSError:
            pass

    def _steps_for(self, track: Track):
        """Orden de proveedores para ``track``.

        El artwork de Apple Music (``mzstatic``) y Spotify (``scdn.co``) es
        1:1 real y coincide exactamente con el álbum, así que va primero. Las
        URL de YouTube/Google son miniaturas de vídeo: casi siempre 16:9 y, aun
        cuando son cuadradas, ilustran el vídeo y no el disco. Por eso se
        posponen detrás de iTunes/Deezer, que devuelven la carátula del álbum.
        """
        platform_first = not _is_youtube_cover_url(str(track.img_url or ""))
        platform = lambda: self._from_platform_url(track)  # noqa: E731
        chain = [
            lambda: self._from_isrc(track),
            lambda: self._from_itunes(track),
            lambda: self._from_deezer(track),
        ]
        if platform_first:
            return [platform, *chain]
        return [*chain, platform]

    def _from_platform_url(self, track: Track, *, require_square: bool = True) -> CoverResolution:
        """Portada ya asociada a la pista por su plataforma de origen."""
        url = upscale_cover_url((track.img_url or "").strip())
        if not url.startswith("http"):
            return CoverResolution()
        payload = self._get_bytes(url)
        if not payload:
            return CoverResolution()
        if require_square and not is_acceptable_cover(probe_cover(payload)):
            # Un thumbnail 16:9 no es artwork: se deja para el último paso,
            # que lo rellena en vez de presentarlo como portada cuadrada.
            return CoverResolution()
        cover = normalize_cover(payload)
        return CoverResolution(cover, "platform") if cover else CoverResolution()

    def resolve_many(
        self,
        tracks: Iterable[Track],
        *,
        allow_youtube_thumb: bool = False,
    ) -> dict[str, CoverResolution]:
        """Resuelve un lote agrupando por álbum; devuelve ``{album_key: result}``."""
        out: dict[str, CoverResolution] = {}
        for track in tracks:
            key = album_key(track)
            if not key or key in out:
                continue
            out[key] = self.resolve(track, allow_youtube_thumb=allow_youtube_thumb)
        return out

    async def resolve_many_async(
        self,
        tracks: Iterable[Track],
        *,
        allow_youtube_thumb: bool = False,
    ) -> dict[str, CoverResolution]:
        """Variante asíncrona: el trabajo de red corre en un hilo."""
        return await asyncio.to_thread(
            self.resolve_many, list(tracks), allow_youtube_thumb=allow_youtube_thumb
        )

    def close(self) -> None:
        """Cierra la sesión propia (no la inyectada)."""
        if self._owns_session and self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None
