"""services/platforms/apple.py — Operaciones de Apple Music de MusicApiService.

Mixin: los métodos se mezclan en `MusicApiService` (services/api_service.py),
que conserva el estado compartido (circuit breakers, caché, sesiones).
"""

from __future__ import annotations

import asyncio
import random
import threading
import time
from typing import Optional
from concurrent.futures import ThreadPoolExecutor

import httpx
from engine.match import _fuzzy_scores_triple, _ideal_pass_hunter
from core.models import PlaylistMeta, SearchResult, Track
from services import browser_session
from engine.normalizer import (
    _normalize_title,
    build_search_query,
    clean_metadata,
    normalize_isrc,
)
from core.library_models import PlaylistSummary
from core.config import (
    APPLE_API_BASE,
    APPLE_ISRC_BATCH,
    APPLE_PAGE_SIZE,
    APPLE_PAGE_WORKERS,
    APPLE_REQUEST_BURST,
    APPLE_REQUEST_PAUSE,
    APPLE_TRANSFER_BATCH,
)
from services.platforms._common import GLOBAL_API_SEMAPHORE, _as_text
from services.circuit_breaker import RateLimitError


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


class AppleMixin:
    """Métodos de Apple Music para `MusicApiService`."""

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

    async def init_apple(self, creds: Optional[dict] = None) -> bool:
        return await asyncio.to_thread(self._sync_init_apple, creds)

    def _sync_init_apple(self, creds: Optional[dict] = None) -> bool:
        candidates = (
            [("manual", creds)]
            if creds is not None
            else list(browser_session.apple_candidates(self.apple_preferred_source))
        )
        if not candidates:
            self.apple_auth_error = "missing credentials (sin sesión en navegador ni config/.env)"
            self.apple_session_source = ""
            return False

        last_err = ""
        for source, cand in candidates:
            raw = str(cand.get("bearer", "") or "").strip()
            utok = str(cand.get("user_token", "") or "").strip()
            if not raw or not utok:
                last_err = "APPLE_AUTH_BEARER or APPLE_MUSIC_USER_TOKEN missing"
                continue
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
                    self.apple_auth_error = ""
                    self.apple_session_source = source
                    return True
                last_err = f"Unexpected HTTP {resp.status_code}"
            except (AppleAuthError, RateLimitError) as exc:
                last_err = str(exc)
                print(f"[Apple Music] init failed ({source}): {exc}")
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
                    print("[Apple Music] init offline (storefront no resuelto) — fallback us")
                    self._am_headers    = am_headers
                    self._http_session.headers.update(am_headers)
                    self._am_storefront = "us"
                    self.apple_auth_error = ""
                    self.apple_session_source = source
                    return True
                last_err = msg
                print(f"[Apple Music] init failed ({source}): {exc}")
        self.apple_auth_error = last_err or "401 — Apple Music token expired"
        self.apple_session_source = ""
        return False

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

    @staticmethod
    def _apple_cover_normalized(url: str, size: int = 400) -> str:
        if not url:
            return ""
        return url.replace("{w}", str(size)).replace("{h}", str(size))

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

    def _am_delete(self, pid: str) -> tuple[bool, str]:
        try:
            r = self._am_request("delete", f"{APPLE_API_BASE}/me/library/playlists/{pid}", timeout=10)
            if r.status_code in (200, 204):
                return True, pid
            return False, f"Apple {r.status_code}"
        except Exception as exc:
            return False, str(exc)
