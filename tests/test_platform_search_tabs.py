"""
tests/test_platform_search_tabs.py — Búsqueda por plataforma (canciones/álbumes/artistas),
álbum/artista detail y add_tracks_to_playlist en MusicApiService (unittest).
"""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.config import PLATFORM_ORDER
from core.models import PlaylistMeta, SearchResult, Track
from services.api_service import MusicApiService
from services.circuit_breaker import CircuitBreaker, RateLimitError
from services.platforms import spotify as sp_mod


def _make_service(tmp_cache_path: str | None = None) -> MusicApiService:
    breakers = {p: CircuitBreaker(p) for p in PLATFORM_ORDER}
    with mock.patch("services.api_service.ensure_config_dir"), mock.patch(
        "services.api_service.MusicApiService._load_search_cache", return_value={}
    ):
        svc = MusicApiService(breakers)
    if tmp_cache_path is not None:
        svc.save_search_cache = mock.MagicMock()
    return svc


def _http_response(status_code: int, payload: dict, headers: dict | None = None) -> httpx.Response:
    req = httpx.Request("GET", "https://amp-api.music.apple.com/v1/test")
    return httpx.Response(status_code, json=payload, headers=headers or {}, request=req)


class CatalogValidationTest(unittest.TestCase):
    def test_search_catalog_rejects_invalid_platform_kind_and_limit(self) -> None:
        svc = _make_service()

        async def _run() -> None:
            with self.assertRaises(ValueError):
                await svc.search_catalog("Tidal", "queen")
            with self.assertRaises(ValueError):
                await svc.search_catalog("Spotify", "queen", kind="podcasts")
            with self.assertRaises(ValueError):
                await svc.search_catalog("Spotify", "queen", kind="songs", limit=0)
            with self.assertRaises(ValueError):
                await svc.search_catalog("Spotify", "queen", kind="songs", limit=True)
            self.assertEqual(await svc.search_catalog("Spotify", "   ", kind="songs"), [])

        asyncio.run(_run())

    def test_circuit_breaker_blocks_catalog_and_batch_add(self) -> None:
        svc = _make_service()

        async def _run() -> None:
            svc._cb["Apple Music"].trip(60)
            with self.assertRaises(RateLimitError):
                await svc.search_catalog("Apple Music", "queen", kind="albums")
            with self.assertRaises(RateLimitError):
                await svc.fetch_album_tracks("Apple Music", "123")
            with self.assertRaises(RateLimitError):
                await svc.fetch_artist_overview("Apple Music", "456")
            with self.assertRaises(RateLimitError):
                await svc.add_tracks_to_playlist("Apple Music", "p.1", [])

        asyncio.run(_run())

    def test_fetch_and_add_validate_ids_and_platform(self) -> None:
        svc = _make_service()

        async def _run() -> None:
            with self.assertRaises(ValueError):
                await svc.fetch_album_tracks("Unknown", "123")
            with self.assertRaises(ValueError):
                await svc.fetch_album_tracks("Spotify", "   ")
            with self.assertRaises(ValueError):
                await svc.fetch_artist_overview("Unknown", "123")
            with self.assertRaises(ValueError):
                await svc.fetch_artist_overview("Apple Music", "")
            with self.assertRaises(ValueError):
                await svc.add_tracks_to_playlist("Unknown", "pl1", [])
            with self.assertRaises(ValueError):
                await svc.add_tracks_to_playlist("Spotify", "   ", [])
            self.assertEqual(await svc.add_tracks_to_playlist("Spotify", "pl1", []), 0)

        asyncio.run(_run())


class YouTubeSearchAndOverviewTest(unittest.TestCase):
    def test_youtube_keeps_requests_session(self) -> None:
        svc = _make_service()
        self.assertIsInstance(svc._yt_http_session, requests.Session)
        self.assertIsInstance(svc._http_session, httpx.Client)
        svc.cleanup_sessions()

    def test_search_catalog_songs_albums_artists(self) -> None:
        svc = _make_service()
        ytm = mock.MagicMock()

        def _fake_search(query, filter=None, limit=20):
            if filter == "songs":
                return [{
                    "videoId": "vid1",
                    "title": "Bohemian Rhapsody",
                    "artists": [{"name": "Queen"}],
                    "album": {"name": "A Night at the Opera"},
                    "duration": "5:55",
                    "duration_seconds": 355,
                    "isExplicit": False,
                    "thumbnails": [{"url": "https://img/s.jpg", "width": 120}],
                }]
            if filter == "albums":
                return [{
                    "browseId": "MPREb_123",
                    "title": "A Night at the Opera",
                    "artists": [{"name": "Queen"}],
                    "year": "1975",
                    "type": "Album",
                    "thumbnails": [{"url": "https://img/a.jpg", "width": 544}],
                }]
            if filter == "artists":
                return [{
                    "browseId": "UC_queen",
                    "artist": "Queen",
                    "subscribers": "18M subscribers",
                    "thumbnails": [{"url": "https://img/art.jpg", "width": 544}],
                }]
            return []

        ytm.search.side_effect = _fake_search
        svc._ytm = ytm

        async def _run():
            songs = await svc.search_catalog("YouTube Music", "Queen", kind="songs", limit=5)
            albums = await svc.search_catalog("YouTube Music", "Queen", kind="albums", limit=5)
            artists = await svc.search_catalog("YouTube Music", "Queen", kind="artists", limit=5)
            return songs, albums, artists

        songs, albums, artists = asyncio.run(_run())

        self.assertEqual(len(songs), 1)
        self.assertEqual(songs[0]["id"], "vid1")
        self.assertEqual(songs[0]["kind"], "songs")
        self.assertEqual(songs[0]["artist"], "Queen")
        self.assertIn("Queen", songs[0]["subtitle"])
        self.assertIsInstance(songs[0]["track"], Track)
        self.assertEqual(songs[0]["track"].duration_ms, 355000)

        self.assertEqual(len(albums), 1)
        self.assertEqual(albums[0]["id"], "MPREb_123")
        self.assertEqual(albums[0]["kind"], "albums")
        self.assertEqual(albums[0]["artist"], "Queen")
        self.assertIn("1975", albums[0]["subtitle"])
        self.assertEqual(albums[0]["external_url"], "https://music.youtube.com/browse/MPREb_123")

        self.assertEqual(len(artists), 1)
        self.assertEqual(artists[0]["id"], "UC_queen")
        self.assertEqual(artists[0]["kind"], "artists")
        self.assertEqual(artists[0]["name"], "Queen")
        self.assertEqual(artists[0]["subtitle"], "18M subscribers")
        self.assertEqual(artists[0]["external_url"], "https://music.youtube.com/channel/UC_queen")

    def test_fetch_album_tracks_and_artist_overview(self) -> None:
        svc = _make_service()
        ytm = mock.MagicMock()
        ytm.get_album.return_value = {
            "title": "News of the World",
            "description": "Sixth studio album",
            "year": "1977",
            "artists": [{"name": "Queen"}],
            "thumbnails": [{"url": "https://img/alb.jpg", "width": 544}],
            "tracks": [
                {
                    "videoId": "v1",
                    "title": "We Will Rock You",
                    "artists": [{"name": "Queen"}],
                    "duration": "2:02",
                    "duration_seconds": 122,
                    "thumbnails": None,
                    "trackNumber": 1,
                },
            ],
        }
        ytm.get_artist.return_value = {
            "name": "Queen",
            "channelId": "UC_queen",
            "description": "British rock band",
            "monthlyListeners": "50M",
            "thumbnails": [{"url": "https://img/q.jpg", "width": 544}],
            "songs": {
                "results": [
                    {
                        "videoId": "v1",
                        "title": "We Will Rock You",
                        "artists": [{"name": "Queen"}],
                        "album": {"name": "News of the World"},
                        "thumbnails": [{"url": "https://img/s.jpg", "width": 120}],
                    }
                ]
            },
            "albums": {
                "results": [
                    {
                        "browseId": "MPREb_1",
                        "title": "News of the World",
                        "year": "1977",
                        "thumbnails": [{"url": "https://img/a1.jpg", "width": 226}],
                    }
                ]
            },
            "singles": {
                "results": [
                    {
                        "browseId": "MPREb_2",
                        "title": "Under Pressure",
                        "year": "1981",
                        "type": "Single",
                        "thumbnails": [{"url": "https://img/a2.jpg", "width": 226}],
                    }
                ]
            },
        }
        svc._ytm = ytm

        async def _run():
            meta, tracks = await svc.fetch_album_tracks(
                "YouTube Music", "https://music.youtube.com/browse/MPREb_1"
            )
            overview = await svc.fetch_artist_overview(
                "YouTube Music", "https://music.youtube.com/channel/UC_queen"
            )
            return meta, tracks, overview

        meta, tracks, overview = asyncio.run(_run())
        ytm.get_album.assert_called_once_with("MPREb_1")
        self.assertEqual(meta, PlaylistMeta(name="News of the World", description="Sixth studio album"))
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].img_url, "https://img/alb.jpg")
        self.assertEqual(tracks[0].album, "News of the World")
        self.assertEqual(tracks[0].release_date, "1977")

        ytm.get_artist.assert_called_once_with("UC_queen")
        self.assertEqual(overview["artist"]["name"], "Queen")
        self.assertEqual(len(overview["top_tracks"]), 1)
        self.assertEqual([a["id"] for a in overview["albums"]], ["MPREb_1", "MPREb_2"])

    def test_youtube_add_tracks_to_playlist_batch(self) -> None:
        svc = _make_service()
        ytm = mock.MagicMock()
        ytm.add_playlist_items.return_value = {"status": "STATUS_SUCCEEDED", "playlistEditResults": []}
        svc._ytm = ytm
        tracks = [
            Track(id="v1", name="S1", artist="A", album="", duration="", img_url="", platform="YouTube Music"),
            Track(id="v2", name="S2", artist="A", album="", duration="", img_url="", platform="YouTube Music"),
        ]
        added = asyncio.run(svc.add_tracks_to_playlist(
            "YouTube Music", "https://music.youtube.com/playlist?list=PL123", tracks
        ))
        self.assertEqual(added, 2)
        ytm.add_playlist_items.assert_called_once_with("PL123", ["v1", "v2"])


class AppleSearchAndOverviewTest(unittest.TestCase):
    def test_search_catalog_is_sequential_and_uses_limiter(self) -> None:
        svc = _make_service()
        svc._am_storefront = "mx"
        svc._am_rate_limiter = mock.MagicMock()

        def _fake_get(url, params=None, headers=None, timeout=10):
            kind = (params or {}).get("types")
            if kind == "songs":
                return _http_response(200, {
                    "results": {
                        "songs": {
                            "data": [{
                                "id": "am_s1",
                                "attributes": {
                                    "name": "Starlight",
                                    "artistName": "Muse",
                                    "albumName": "Black Holes",
                                    "durationInMillis": 240000,
                                    "artwork": {"url": "https://is1.mzstatic.com/{w}x{h}.jpg"},
                                    "url": "https://music.apple.com/mx/song/am_s1",
                                    "isrc": "GBAHT0600001",
                                },
                            }]
                        }
                    }
                })
            if kind == "albums":
                return _http_response(200, {
                    "results": {
                        "albums": {
                            "data": [{
                                "id": "am_a1",
                                "attributes": {
                                    "name": "Black Holes and Revelations",
                                    "artistName": "Muse",
                                    "releaseDate": "2006-07-03",
                                    "artwork": {"url": "https://is1.mzstatic.com/{w}x{h}.jpg"},
                                    "url": "https://music.apple.com/mx/album/am_a1",
                                },
                            }]
                        }
                    }
                })
            return _http_response(200, {
                "results": {
                    "artists": {
                        "data": [{
                            "id": "am_art1",
                            "attributes": {
                                "name": "Muse",
                                "genreNames": ["Alternative"],
                                "artwork": {"url": "https://is1.mzstatic.com/{w}x{h}.jpg"},
                                "url": "https://music.apple.com/mx/artist/am_art1",
                            },
                        }]
                    }
                }
            })

        svc._http_session = mock.MagicMock()
        svc._http_session.get.side_effect = _fake_get

        async def _run():
            s = await svc.search_catalog("Apple Music", "Muse", kind="songs", limit=40)
            a = await svc.search_catalog("Apple Music", "Muse", kind="albums", limit=10)
            r = await svc.search_catalog("Apple Music", "Muse", kind="artists", limit=10)
            return s, a, r

        s, a, r = asyncio.run(_run())
        self.assertEqual(svc._am_rate_limiter.wait_turn.call_count, 3)
        # Apple search cap at 25
        first_call_params = svc._http_session.get.call_args_list[0].kwargs["params"]
        self.assertEqual(first_call_params["limit"], 25)

        self.assertEqual(s[0]["id"], "am_s1")
        self.assertEqual(s[0]["img_url"], "https://is1.mzstatic.com/400x400.jpg")
        self.assertIsInstance(s[0]["track"], Track)
        self.assertEqual(a[0]["id"], "am_a1")
        self.assertIn("2006", a[0]["subtitle"])
        self.assertEqual(r[0]["id"], "am_art1")
        self.assertEqual(r[0]["subtitle"], "Alternative")

    def test_fetch_album_tracks_paginated_and_artist_overview(self) -> None:
        svc = _make_service()
        svc._am_storefront = "us"
        svc._am_rate_limiter = mock.MagicMock()

        def _fake_get(url, params=None, headers=None, timeout=10):
            if "/albums/999/tracks" in url:
                return _http_response(200, {
                    "data": [{
                        "id": "t2",
                        "attributes": {"name": "Apocalypse Please", "artistName": "Muse", "durationInMillis": 252000},
                    }]
                })
            if "/albums/999" in url:
                return _http_response(200, {
                    "data": [{
                        "id": "999",
                        "attributes": {
                            "name": "Absolution",
                            "artistName": "Muse",
                            "releaseDate": "2003-09-15",
                            "editorialNotes": {"short": "Third studio album"},
                            "artwork": {"url": "https://img/{w}x{h}.jpg"},
                        },
                        "relationships": {
                            "tracks": {
                                "data": [{
                                    "id": "t1",
                                    "attributes": {"name": "Intro", "artistName": "Muse", "durationInMillis": 22000},
                                }],
                                "next": "/v1/catalog/us/albums/999/tracks?offset=1",
                            }
                        },
                    }]
                })
            if "/artists/1093360" in url:
                return _http_response(200, {
                    "data": [{
                        "id": "1093360",
                        "attributes": {
                            "name": "Muse",
                            "genreNames": ["Rock"],
                            "artwork": {"url": "https://img/art/{w}x{h}.jpg"},
                            "editorialNotes": {"standard": "English rock band"},
                        },
                        "views": {
                            "top-songs": {
                                "data": [{
                                    "id": "ts1",
                                    "attributes": {
                                        "name": "Uprising",
                                        "artistName": "Muse",
                                        "albumName": "The Resistance",
                                        "durationInMillis": 304000,
                                    },
                                }]
                            },
                            "full-albums": {
                                "data": [{
                                    "id": "999",
                                    "attributes": {
                                        "name": "Absolution",
                                        "artistName": "Muse",
                                        "releaseDate": "2003-09-15",
                                    },
                                }]
                            },
                        },
                    }]
                })
            return _http_response(404, {})

        svc._http_session = mock.MagicMock()
        svc._http_session.get.side_effect = _fake_get

        async def _run():
            meta, tracks = await svc.fetch_album_tracks(
                "Apple Music", "https://music.apple.com/us/album/absolution/999?i=1"
            )
            overview = await svc.fetch_artist_overview(
                "Apple Music", "https://music.apple.com/us/artist/muse/1093360"
            )
            return meta, tracks, overview

        meta, tracks, overview = asyncio.run(_run())
        self.assertEqual(meta, PlaylistMeta(name="Absolution", description="Third studio album"))
        self.assertEqual([t.id for t in tracks], ["t1", "t2"])
        self.assertEqual(tracks[0].album, "Absolution")
        self.assertEqual(tracks[0].img_url, "https://img/400x400.jpg")
        self.assertEqual(overview["artist"]["name"], "Muse")
        self.assertEqual(overview["artist"]["description"], "English rock band")
        self.assertEqual([t.id for t in overview["top_tracks"]], ["ts1"])
        self.assertEqual([a["id"] for a in overview["albums"]], ["999"])

    def test_apple_add_tracks_to_playlist_batch_and_429_retry(self) -> None:
        svc = _make_service()
        svc._am_rate_limiter = mock.MagicMock()
        post_calls = []

        def _fake_post(url, json=None, timeout=15):
            post_calls.append(json)
            if len(post_calls) == 1:
                return _http_response(429, {}, headers={"Retry-After": "2"})
            return _http_response(201, {})

        svc._http_session = mock.MagicMock()
        svc._http_session.post.side_effect = _fake_post

        tracks = [
            Track(id="10", name="A", artist="X", album="", duration="", img_url="", platform="Apple Music"),
            Track(id="20", name="B", artist="X", album="", duration="", img_url="", platform="Apple Music"),
        ]
        added = asyncio.run(svc.add_tracks_to_playlist("Apple Music", "p.abc", tracks))
        self.assertEqual(added, 2)
        svc._am_rate_limiter.penalize.assert_called_once_with(2)
        self.assertEqual(len(post_calls[1]["data"]), 2)


class SpotifySearchAndOverviewTest(unittest.TestCase):
    def test_search_catalog_songs_albums_and_artists(self) -> None:
        svc = _make_service()
        fake_song = mock.MagicMock()
        fake_song.query_songs.return_value = {
            "data": {
                "searchV2": {
                    "tracksV2": {
                        "items": [{
                            "item": {
                                "data": {
                                    "id": "sp_t1",
                                    "name": "Time",
                                    "artists": {"items": [{"profile": {"name": "Pink Floyd"}}]},
                                    "albumOfTrack": {
                                        "name": "Dark Side",
                                        "coverArt": {"sources": [{"url": "https://sp/t1.jpg", "width": 300}]},
                                        "date": {"year": 1973},
                                    },
                                    "duration": {"totalMilliseconds": 413000},
                                    "explicit": False,
                                }
                            }
                        }]
                    },
                    "albumsV2": {
                        "items": [{
                            "data": {
                                "uri": "spotify:album:sp_alb1",
                                "name": "The Dark Side of the Moon",
                                "artists": {"items": [{"profile": {"name": "Pink Floyd"}}]},
                                "coverArt": {"sources": [{"url": "https://sp/a1.jpg", "width": 640}]},
                                "date": {"year": 1973},
                            }
                        }]
                    },
                    "artists": {
                        "items": [{
                            "data": {
                                "uri": "spotify:artist:sp_art1",
                                "profile": {"name": "Pink Floyd"},
                                "visuals": {"avatarImage": {"sources": [{"url": "https://sp/art1.jpg", "width": 640}]}},
                            }
                        }]
                    },
                }
            }
        }
        svc._sp_song = fake_song
        svc._sp_cfg = mock.MagicMock()

        async def _run():
            s = await svc.search_catalog("Spotify", "Pink Floyd", kind="songs", limit=5)
            a = await svc.search_catalog("Spotify", "Pink Floyd", kind="albums", limit=5)
            r = await svc.search_catalog("Spotify", "Pink Floyd", kind="artists", limit=5)
            return s, a, r

        s, a, r = asyncio.run(_run())
        self.assertEqual(s[0]["id"], "sp_t1")
        self.assertIsInstance(s[0]["track"], Track)
        self.assertEqual(a[0]["id"], "sp_alb1")
        self.assertEqual(a[0]["external_url"], "https://open.spotify.com/album/sp_alb1")
        self.assertEqual(r[0]["id"], "sp_art1")
        self.assertEqual(r[0]["name"], "Pink Floyd")

    def test_fetch_album_tracks_and_artist_overview(self) -> None:
        svc = _make_service()
        svc._sp_cfg = mock.MagicMock()

        fake_album = mock.MagicMock()
        fake_album.get_album_info.return_value = {
            "data": {
                "albumUnion": {
                    "uri": "spotify:album:alb99",
                    "name": "Animals",
                    "date": {"year": 1977},
                    "coverArt": {"sources": [{"url": "https://sp/animals.jpg", "width": 640}]},
                    "artists": {"items": [{"profile": {"name": "Pink Floyd"}}]},
                    "tracksV2": {
                        "totalCount": 1,
                        "items": [{
                            "track": {
                                "uri": "spotify:track:tr99",
                                "name": "Dogs",
                                "trackNumber": 2,
                                "duration": {"totalMilliseconds": 1024000},
                                "contentRating": {"label": "NONE"},
                                "artists": {"items": [{"profile": {"name": "Pink Floyd"}}]},
                            }
                        }],
                    },
                }
            }
        }

        fake_artist = mock.MagicMock()
        fake_artist.get_artist.return_value = {
            "data": {
                "artistUnion": {
                    "uri": "spotify:artist:art99",
                    "profile": {"name": "Pink Floyd", "biography": {"text": "Progressive rock"}},
                    "visuals": {"avatarImage": {"sources": [{"url": "https://sp/pf.jpg", "width": 640}]}},
                    "stats": {"monthlyListeners": 20000000},
                    "discography": {
                        "topTracks": {
                            "items": [{
                                "track": {
                                    "id": "tr100",
                                    "name": "Wish You Were Here",
                                    "duration": {"totalMilliseconds": 334000},
                                    "albumOfTrack": {
                                        "name": "Wish You Were Here",
                                        "coverArt": {"sources": [{"url": "https://sp/wywh.jpg", "width": 300}]},
                                    },
                                    "artists": {"items": [{"profile": {"name": "Pink Floyd"}}]},
                                }
                            }]
                        },
                        "albums": {
                            "items": [{
                                "releases": {
                                    "items": [{
                                        "uri": "spotify:album:alb99",
                                        "name": "Animals",
                                        "date": {"year": 1977},
                                        "coverArt": {"sources": [{"url": "https://sp/animals.jpg", "width": 640}]},
                                        "type": "ALBUM",
                                    }]
                                }
                            }]
                        },
                    },
                }
            }
        }

        with mock.patch.object(sp_mod, "PublicAlbum", return_value=fake_album), mock.patch.object(
            sp_mod, "Artist", return_value=fake_artist
        ):
            meta, tracks = asyncio.run(
                svc.fetch_album_tracks("Spotify", "https://open.spotify.com/album/alb99?si=1")
            )
            overview = asyncio.run(
                svc.fetch_artist_overview("Spotify", "spotify:artist:art99")
            )

        self.assertEqual(meta.name, "Animals")
        self.assertEqual(len(tracks), 1)
        self.assertEqual(tracks[0].id, "tr99")
        self.assertEqual(tracks[0].album, "Animals")
        self.assertEqual(tracks[0].img_url, "https://sp/animals.jpg")

        self.assertEqual(overview["artist"]["name"], "Pink Floyd")
        self.assertEqual(overview["artist"]["description"], "Progressive rock")
        self.assertIn("20,000,000", overview["artist"]["subtitle"])
        self.assertEqual([t.id for t in overview["top_tracks"]], ["tr100"])
        self.assertEqual([a["id"] for a in overview["albums"]], ["alb99"])

    def test_add_tracks_to_playlist_resolves_cross_platform_tracks(self) -> None:
        svc = _make_service(tmp_cache_path="mocked")
        svc._sp_login = mock.MagicMock()
        svc._sp_cfg = mock.MagicMock()
        fake_song = mock.MagicMock()
        svc._sp_song = fake_song

        tracks = [
            Track(id="sp_direct", name="Direct", artist="Artist", album="", duration="", img_url="", platform="Spotify"),
            Track(id="local_1", name="Resolved Song", artist="Artist", album="", duration="", img_url="", platform="Local"),
        ]

        with mock.patch.object(
            svc, "search_with_fallback",
            new=mock.AsyncMock(return_value=SearchResult(track_id="sp_resolved", needs_review=False)),
        ) as mock_search, mock.patch.object(sp_mod, "PrivatePlaylist"):
            added = asyncio.run(
                svc.add_tracks_to_playlist("Spotify", "https://open.spotify.com/playlist/pl555", tracks)
            )

        self.assertEqual(added, 2)
        mock_search.assert_awaited_once()
        fake_song.add_songs_to_playlist.assert_called_once_with(["sp_direct", "sp_resolved"])


if __name__ == "__main__":
    unittest.main()
