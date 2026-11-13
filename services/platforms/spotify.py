"""services/platforms/spotify.py — Operaciones de Spotify de MusicApiService.

Mixin: los métodos se mezclan en `MusicApiService` (services/api_service.py),
que conserva el estado compartido (circuit breakers, caché, sesiones).
"""

from __future__ import annotations

import asyncio
import random
import time
from typing import Optional

from engine.match import (
    _fuzzy_flags_elastic,
    _fuzzy_scores_triple,
    _ideal_pass_hunter,
    score_spotify_match,
)
from services import browser_session
from core.models import PlaylistMeta, SearchResult, Track
from engine.normalizer import (
    FUZZY_IDEAL,
    _normalize_title,
    build_search_query,
    normalize_isrc,
)
from core.library_models import PlaylistSummary
from core.transfer import normalize_playlist_id
from services.platforms._common import GLOBAL_API_SEMAPHORE, _as_text
from services.circuit_breaker import RateLimitError
from core.config import (
    SPOTIFY_ADD_CHUNK as CFG_SPOTIFY_ADD_CHUNK,
    SPOTIFY_HTTP_TIMEOUT as CFG_SPOTIFY_HTTP_TIMEOUT,
)

# Tamaño de lote de inserción (single source: core/config.py)


SPOTIFY_ADD_CHUNK = CFG_SPOTIFY_ADD_CHUNK


try:
    from spotapi import AlbumError, Artist, ArtistError, Config, Login, PrivatePlaylist, PublicAlbum, PublicPlaylist, Song
    from spotapi.utils.logger import NoopLogger
    HAS_SPOTIFY = True
except ImportError:
    HAS_SPOTIFY = False


class SpotifyMixin:
    """Métodos de Spotify para `MusicApiService`."""

    async def init_spotify(self, creds: Optional[dict] = None) -> bool:
        return await asyncio.to_thread(self._sync_init_spotify, creds)

    def _sync_init_spotify(self, creds: Optional[dict] = None) -> bool:
        if not HAS_SPOTIFY:
            self.spotify_auth_error = "spotapi no instalado"
            return False
        candidates = (
            [("manual", creds)]
            if creds is not None
            else list(browser_session.spotify_candidates(self.spotify_preferred_source))
        )
        if not candidates:
            self.spotify_auth_error = "missing credentials (sin sesión en navegador ni config/spotify_cookies.json)"
            self.spotify_session_source = ""
            self._sp_login = None
            self._sp_song = None
            return False

        last_err = ""
        for source, dump in candidates:
            try:
                cookies = dump.get("cookies") if isinstance(dump, dict) else None
                if not isinstance(cookies, dict) or not cookies.get("sp_dc") or not cookies.get("sp_key"):
                    last_err = "config/spotify_cookies.json: falta sp_dc o sp_key"
                    continue
                payload = {
                    "identifier": str(dump.get("identifier") or "browser"),
                    "cookies": {
                        "sp_dc": str(cookies["sp_dc"]),
                        "sp_key": str(cookies["sp_key"]),
                    },
                }
                cfg = Config(logger=NoopLogger())
                login = Login.from_cookies(payload, cfg)
                if not login.logged_in:
                    last_err = "401 — cookies inválidas (login falló)"
                    continue
                self._sp_login = login
                self._sp_cfg   = cfg
                self._sp_cfg.client.timeout = CFG_SPOTIFY_HTTP_TIMEOUT
                # Sonda real a libraryV3: Login.from_cookies fija logged_in=True sin verificar.
                self._spotify_library_page(limit=1, offset=0)
                # Song cacheado sobre el MISMO TLSClient del login: el setup de
                # sesión/token/hashes se paga una sola vez, no por búsqueda.
                self._sp_song  = Song(client=cfg.client)
                self.spotify_auth_error = ""
                self.spotify_session_source = source
                return True
            except Exception as exc:  # pylint: disable=broad-exception-caught
                last_err = f"401 — {exc}" if "401" not in str(exc) else str(exc)
                print(f"[Spotify] init failed ({source}): {exc}")
                self._sp_login = None
                self._sp_cfg   = None
                self._sp_song  = None
        self.spotify_auth_error = last_err or "401 — cookies de Spotify rechazadas"
        self.spotify_session_source = ""
        return False

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

    @staticmethod
    def _spotify_cover_best(sources) -> str:
        if not sources:
            return ""
        try:
            return max(sources, key=lambda s: s.get("width", 0) or 0).get("url", "")
        except Exception:
            return sources[0].get("url", "") if sources else ""

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
        body = resp.response if isinstance(resp.response, dict) else {}
        errors = body.get("errors")
        if isinstance(errors, list) and errors:
            first_msg = str((errors[0] or {}).get("message") or "unauthorized")
            raise RuntimeError(f"401 — Spotify libraryV3 rechazó la sesión: {first_msg}")
        if (body.get("data") or {}).get("me") is None:
            raise RuntimeError("401 — Spotify libraryV3 devolvió me=None (sesión no autorizada)")
        return body

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

    def _sp_warm_base(self, base_obj) -> None:
        """Comparte hashes JS y tokens ya resueltos en ``_sp_song`` para evitar 5-8 requests extra."""
        song_base = getattr(self._sp_song, "base", None)
        if song_base is None or base_obj is None:
            return
        raw_hashes = getattr(song_base, "raw_hashes", None)
        if raw_hashes is None or type(raw_hashes).__name__ == "_Undefined":
            return
        target_hashes = getattr(base_obj, "raw_hashes", None)
        if target_hashes is None or type(target_hashes).__name__ == "_Undefined":
            for attr in (
                "js_pack",
                "raw_hashes",
                "access_token",
                "access_token_expires_at_ms",
                "client_token",
                "client_id",
                "client_version",
                "device_id",
            ):
                if hasattr(song_base, attr):
                    setattr(base_obj, attr, getattr(song_base, attr))

    def _sp_ensure_song(self):
        if self._sp_song is not None:
            return self._sp_song
        if not HAS_SPOTIFY:
            return None
        if not self._sp_cfg:
            self._sync_init_spotify()
        if not self._sp_cfg:
            self._sp_cfg = Config(logger=NoopLogger())
        self._sp_song = Song(client=self._sp_cfg.client)
        return self._sp_song

    @staticmethod
    def _sp_extract_id(data: dict) -> str:
        raw_id = str(data.get("id") or "").strip()
        if raw_id:
            return raw_id
        uri = str(data.get("uri") or "").strip()
        if ":" in uri:
            return uri.split(":")[-1].strip()
        return ""

    @staticmethod
    def _sp_artists_str(data: dict) -> str:
        items = (data.get("artists") or {}).get("items") or []
        names: list[str] = []
        for a in items:
            if not isinstance(a, dict):
                continue
            name = (a.get("profile") or {}).get("name") or a.get("name") or ""
            if name:
                names.append(str(name).strip())
        return ", ".join(names)

    @staticmethod
    def _sp_unwrap_node(item: dict) -> dict:
        if not isinstance(item, dict):
            return {}
        if isinstance(item.get("data"), dict):
            return item["data"]
        inner = item.get("item")
        if isinstance(inner, dict) and isinstance(inner.get("data"), dict):
            return inner["data"]
        if isinstance(item.get("track"), dict):
            return item["track"]
        return item

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

    def _sp_track_from_item(
        self,
        data: dict,
        *,
        fallback_album: str = "",
        fallback_img: str = "",
        fallback_release_date: str = "",
        fallback_album_artist: str = "",
        track_number: int = 0,
    ) -> Optional[Track]:
        """Convierte un item de búsqueda/álbum/artista de Spotify en Track para la UI."""
        tid = self._sp_extract_id(data)
        if not tid:
            return None
        artists = self._sp_artists_str(data)
        album = data.get("albumOfTrack") or {}
        dur_obj = data.get("duration") or data.get("trackDuration") or {}
        ms = int(dur_obj.get("totalMilliseconds", 0) or 0)
        covers = ((album.get("coverArt") or {}).get("sources") or [])
        img_url = self._spotify_cover_best(covers) or fallback_img
        rel = ""
        date_obj = album.get("date") or {}
        if isinstance(date_obj, dict):
            rel = str(date_obj.get("isoString") or date_obj.get("year") or "").strip()
        if not rel:
            rel = fallback_release_date
        explicit = bool(data.get("explicit")) or (
            str((data.get("contentRating") or {}).get("label") or "").upper() == "EXPLICIT"
        )
        return Track(
            id=tid,
            name=data.get("name", "Unknown"),
            artist=artists or fallback_album_artist or "Unknown",
            album=album.get("name") or fallback_album or "Unknown",
            duration=f"{ms // 60000}:{(ms // 1000) % 60:02d}" if ms else "0:00",
            img_url=img_url,
            platform="Spotify",
            duration_ms=ms,
            is_explicit=explicit,
            isrc=self._extract_spotify_isrc(data),
            release_date=rel,
            source_url=f"https://open.spotify.com/track/{tid}",
            album_artist=fallback_album_artist,
            track_number=int(data.get("trackNumber") or track_number or 0),
        )

    def _sp_search_candidates_sync(self, query: str, limit: int = 5) -> list[Track]:
        out: list[Track] = []
        for item in self._sp_search_items(query, limit=limit):
            data = (item.get("item") or {}).get("data") or {}
            track = self._sp_track_from_item(data)
            if track is not None:
                out.append(track)
        return out

    def _sp_album_item_from_raw(self, data: dict, *, fallback_artist: str = "") -> Optional[dict]:
        album_id = self._sp_extract_id(data)
        if not album_id:
            return None
        artist = self._sp_artists_str(data) or fallback_artist or "Unknown"
        date_obj = data.get("date") or {}
        year = ""
        if isinstance(date_obj, dict):
            raw_y = date_obj.get("year") or date_obj.get("isoString") or ""
            year = str(raw_y)[:4] if raw_y else ""
        covers = (data.get("coverArt") or {}).get("sources") or []
        item_type = str(data.get("type") or "").title() if data.get("type") else ""
        return self._catalog_album_item(
            album_id=album_id,
            name=str(data.get("name") or "Unknown"),
            artist=artist,
            img_url=self._spotify_cover_best(covers),
            external_url=f"https://open.spotify.com/album/{album_id}",
            platform="Spotify",
            year=year,
            item_type=item_type,
        )

    def _sp_artist_item_from_raw(self, data: dict) -> Optional[dict]:
        artist_id = self._sp_extract_id(data)
        if not artist_id:
            return None
        profile = data.get("profile") or {}
        name = str(profile.get("name") or data.get("name") or "Unknown").strip()
        covers = ((data.get("visuals") or {}).get("avatarImage") or {}).get("sources") or []
        return self._catalog_artist_item(
            artist_id=artist_id,
            name=name,
            img_url=self._spotify_cover_best(covers),
            external_url=f"https://open.spotify.com/artist/{artist_id}",
            platform="Spotify",
            subtitle="",
        )

    def _sp_search_catalog_sync(self, query: str, kind: str, limit: int = 20) -> list[dict]:
        capped = max(1, min(int(limit), 50))
        song = self._sp_ensure_song()
        if song is None:
            return []
        try:
            resp = song.query_songs(query, limit=capped)
            search_v2 = (resp.get("data") or {}).get("searchV2") or {}
            out: list[dict] = []
            if kind == "songs":
                items = (search_v2.get("tracksV2") or {}).get("items") or []
                for item in items:
                    track = self._sp_track_from_item(self._sp_unwrap_node(item))
                    if track is not None:
                        out.append(self._catalog_song_item(track))
            elif kind == "albums":
                items = (search_v2.get("albumsV2") or {}).get("items") or []
                for item in items:
                    entry = self._sp_album_item_from_raw(self._sp_unwrap_node(item))
                    if entry is not None:
                        out.append(entry)
            elif kind == "artists":
                items = (search_v2.get("artists") or {}).get("items") or []
                if not items and HAS_SPOTIFY:
                    artist_cli = getattr(self, "_sp_artist", None)
                    if artist_cli is None:
                        client = self._sp_cfg.client if self._sp_cfg else song.base.client
                        artist_cli = Artist(login=self._sp_login, client=client)
                        self._sp_warm_base(artist_cli.base)
                        self._sp_artist = artist_cli
                    art_resp = artist_cli.query_artists(query, limit=capped)
                    items = ((art_resp.get("data") or {}).get("searchV2") or {}).get("artists", {}).get("items") or []
                for item in items:
                    entry = self._sp_artist_item_from_raw(self._sp_unwrap_node(item))
                    if entry is not None:
                        out.append(entry)
            return out[:capped]
        except RateLimitError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            if self._sp_is_rate_limited(exc):
                raise RateLimitError("Spotify", 60) from exc
            raise

    def _sp_fetch_album_sync(self, album_id: str) -> tuple[PlaylistMeta, list[Track]]:
        if not HAS_SPOTIFY:
            raise RuntimeError("SpotAPI no disponible. Instala spotapi.")
        if not self._sp_cfg:
            self._sync_init_spotify()
        if not self._sp_cfg:
            self._sp_cfg = Config(logger=NoopLogger())
        try:
            album_cli = PublicAlbum(album_id, client=self._sp_cfg.client)
            self._sp_warm_base(album_cli.base)
            upper = 343
            first = album_cli.get_album_info(limit=upper, offset=0)
            union = (first.get("data") or {}).get("albumUnion") or {}
            name = str(union.get("name") or "Spotify Album")
            album_cover = self._spotify_cover_best((union.get("coverArt") or {}).get("sources") or [])
            album_artist = self._sp_artists_str(union)
            date_obj = union.get("date") or {}
            rel = str(date_obj.get("isoString") or date_obj.get("year") or "").strip() if isinstance(date_obj, dict) else ""

            tracks_v2 = union.get("tracksV2") or {}
            total = int(tracks_v2.get("totalCount") or 0)
            raw_items = list(tracks_v2.get("items") or [])
            offset = len(raw_items)
            while total and offset < total:
                page = album_cli.get_album_info(limit=upper, offset=offset)
                page_items = ((page.get("data") or {}).get("albumUnion") or {}).get("tracksV2", {}).get("items") or []
                if not page_items:
                    break
                raw_items.extend(page_items)
                offset += len(page_items)

            tracks: list[Track] = []
            for idx, item in enumerate(raw_items, 1):
                node = self._sp_unwrap_node(item)
                track = self._sp_track_from_item(
                    node,
                    fallback_album=name,
                    fallback_img=album_cover,
                    fallback_release_date=rel,
                    fallback_album_artist=album_artist,
                    track_number=idx,
                )
                if track is not None:
                    tracks.append(track)
            return PlaylistMeta(name=name, description=""), tracks
        except RateLimitError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            if self._sp_is_rate_limited(exc):
                raise RateLimitError("Spotify", 60) from exc
            raise

    def _sp_fetch_artist_sync(self, artist_id: str) -> dict:
        if not HAS_SPOTIFY:
            raise RuntimeError("SpotAPI no disponible. Instala spotapi.")
        if not self._sp_cfg:
            self._sync_init_spotify()
        if not self._sp_cfg:
            self._sp_cfg = Config(logger=NoopLogger())
        try:
            artist_cli = getattr(self, "_sp_artist", None)
            if artist_cli is None:
                artist_cli = Artist(login=self._sp_login, client=self._sp_cfg.client)
                self._sp_warm_base(artist_cli.base)
                self._sp_artist = artist_cli
            resp = artist_cli.get_artist(artist_id)
            union = (resp.get("data") or {}).get("artistUnion") or {}
            profile = union.get("profile") or {}
            name = str(profile.get("name") or "Artista").strip()
            bio = _as_text((profile.get("biography") or {}).get("text"))
            covers = ((union.get("visuals") or {}).get("avatarImage") or {}).get("sources") or []
            listeners = (union.get("stats") or {}).get("monthlyListeners")
            subtitle = f"{int(listeners):,} oyentes mensuales" if isinstance(listeners, int) and listeners > 0 else ""

            discography = union.get("discography") or {}
            top_tracks: list[Track] = []
            for idx, item in enumerate((discography.get("topTracks") or {}).get("items") or [], 1):
                node = self._sp_unwrap_node(item)
                track = self._sp_track_from_item(
                    node,
                    fallback_album_artist=name,
                    track_number=idx,
                )
                if track is not None:
                    top_tracks.append(track)

            albums: list[dict] = []
            seen_ids: set[str] = set()
            for sec_key in ("albums", "popularReleasesAlbums", "singles"):
                sec_items = (discography.get(sec_key) or {}).get("items") or []
                for item in sec_items:
                    releases = ((item.get("releases") or {}).get("items")) if isinstance(item, dict) else None
                    candidates = releases if isinstance(releases, list) else [item]
                    for rel_item in candidates:
                        if not isinstance(rel_item, dict):
                            continue
                        entry = self._sp_album_item_from_raw(rel_item, fallback_artist=name)
                        if entry is not None and entry["id"] not in seen_ids:
                            seen_ids.add(entry["id"])
                            albums.append(entry)

            artist_dict = {
                **self._catalog_artist_item(
                    artist_id=self._sp_extract_id(union) or artist_id,
                    name=name,
                    img_url=self._spotify_cover_best(covers),
                    external_url=f"https://open.spotify.com/artist/{self._sp_extract_id(union) or artist_id}",
                    platform="Spotify",
                    subtitle=subtitle,
                ),
                "description": bio,
            }
            return {
                "artist": artist_dict,
                "top_tracks": top_tracks,
                "albums": albums,
            }
        except RateLimitError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            if self._sp_is_rate_limited(exc):
                raise RateLimitError("Spotify", 60) from exc
            raise

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
        added = self._sp_add_tracks_to_playlist(playlist_id, [track_id])
        if added <= 0:
            return False, "Spotify rechazó la inserción"
        return True, playlist_id

    def _sp_add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]) -> int:
        if not track_ids:
            return 0
        playlist_id = normalize_playlist_id("Spotify", playlist_id)
        if not playlist_id:
            raise ValueError("Playlist de Spotify sin identificador")
        if not HAS_SPOTIFY:
            raise RuntimeError("SpotAPI no disponible")
        if not self._sp_login:
            self._sync_init_spotify()
        if not self._sp_login:
            raise RuntimeError("Spotify no disponible. Comprueba config/spotify_cookies.json.")
        try:
            pl = PrivatePlaylist(self._sp_login, playlist_id)
            # Reutiliza el Song de búsqueda: SpotAPI vuelve a pedir sesión,
            # client token y hashes JS (~5-8 requests) por cada BaseClient
            # nuevo; eso era el retraso de ~10 s al insertar una alternativa.
            song = self._sp_song
            if song is None:
                client = self._sp_cfg.client if self._sp_cfg is not None else getattr(self._sp_login, "client", None)
                song = Song(client=client) if client is not None else Song(pl)
                self._sp_song = song
            with self._sp_insert_lock:
                song.playlist = pl
                rejected = self._sp_add_tracks(pl, track_ids, song=song)
            return max(0, len(track_ids) - len(rejected))
        except RateLimitError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.spotify_auth_error = str(exc)
            if self._sp_is_rate_limited(exc):
                raise RateLimitError("Spotify", 60) from exc
            raise

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
                    rl = SpotifyMixin._sp_is_rate_limited(exc)
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
