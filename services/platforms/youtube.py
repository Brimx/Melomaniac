"""services/platforms/youtube.py — Operaciones de YouTube Music de MusicApiService.

Mixin: los métodos se mezclan en `MusicApiService` (services/api_service.py),
que conserva el estado compartido (circuit breakers, caché, sesiones).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from engine.match import (
    _fuzzy_scores_triple,
    _ideal_pass_hunter,
    _joji_trikeyword_query,
    _yt_select_best,
    duration_to_seconds,
)
from services.platforms._common import GLOBAL_API_SEMAPHORE, _as_text
from core.models import PlaylistMeta, SearchResult, Track
from services import browser_session
from services.authentication import youtube_http_status
from engine.normalizer import _normalize_title, build_search_query, normalize_isrc
from core.library_models import PlaylistSummary

# Mensaje de error para sesión expirada de YouTube Music
_YTM_401_MSG = (
    "[ERROR] YouTube Music: la sesión de config/browser.json fue rechazada. "
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


try:
    from ytmusicapi import YTMusic
    HAS_YTMUSIC = True
except ImportError:
    HAS_YTMUSIC = False


class YouTubeMixin:
    """Métodos de YouTube Music para `MusicApiService`."""

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

    async def init_youtube(self, creds: Optional[dict] = None) -> bool:
        return await asyncio.to_thread(self._sync_init_youtube, creds)

    def _sync_init_youtube(self, creds: Optional[dict] = None) -> bool:
        with self._ytm_lock:
            return self._sync_init_youtube_locked(creds)

    def _sync_init_youtube_locked(self, creds: Optional[dict] = None) -> bool:
        if not HAS_YTMUSIC:
            self.youtube_auth_error = "ytmusicapi no instalado"
            return False
        candidates = (
            [("manual", creds)]
            if creds is not None
            else list(browser_session.youtube_candidates(self.youtube_preferred_source))
        )
        if not candidates:
            self.youtube_auth_error = "missing credentials (sin sesión en navegador ni config/browser.json)"
            self.youtube_session_source = ""
            self._ytm = None
            return False

        last_err = ""
        for source, headers in candidates:
            try:
                self._ytm = YTMusic(auth=headers, requests_session=self._yt_http_session)
                self._ytm_library_playlists(limit=1)
                self.youtube_auth_error = ""
                self.youtube_session_source = source
                return True
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_err = str(exc)
                if _is_ytm_unauthorized(exc):
                    print(_YTM_401_MSG)
                else:
                    print(f"[YouTube Music] init failed ({source}): {exc}")
                self._ytm = None
        self.youtube_auth_error = last_err or "No se pudo validar YouTube Music"
        self.youtube_session_source = ""
        return False

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

    @staticmethod
    def _ytm_cover_best(thumbnails) -> str:
        if not thumbnails:
            return ""
        try:
            # ytmusicapi ordena ascendente; mayor resolución al final
            return sorted(thumbnails, key=lambda t: t.get("width", 0))[-1].get("url", "")
        except Exception:
            return thumbnails[-1].get("url", "") if thumbnails else ""

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
                dur_s = duration_to_seconds(item.get("duration", "0:00"))
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
