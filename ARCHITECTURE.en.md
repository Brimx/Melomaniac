# Melomaniac v4.8.0 — Architecture

Melomaniac separates the Flet UI, application state, platform services, and metadata normalization/matching engine. The current version supports YouTube Music, Apple Music, Spotify, and two local inputs: file and pasted text.

The documented working version is `4.8.0`. This version consolidates the integrated player, browser session detection, audio downloads with `yt-dlp`, and MPRIS desktop integration on Linux.

## Layers and dependencies

```text
app.py
├── AppState                 # reactive state, progress, transfer
├── MusicApiService          # APIs, cache, sessions, playlist creation
├── AuthManager              # pre-flight, wizard, hot reload
└── PlaylistManagerUI        # Flet, navigation, events, telemetry
    └── LibraryState         # Library metadata synchronization/cache

ui ───────> core / engine / services
core ─────> engine / services.circuit_breaker
services ─> core / engine
engine ───> core.models
```

The actual composition in `app.py` is:

```python
state = AppState(service=None)
service = MusicApiService(state.cb)
state.service = service
ui = PlaylistManagerUI(page, state)
auth_manager = AuthManager(page, service, state)
ui.auth_manager = auth_manager
service.auth_manager = auth_manager
```

`AppState` creates the circuit breakers once and shares them with `MusicApiService`. Loading, searching, and playlist creation therefore use the same per-platform cooldown.

`LibraryState` is separate from transfers. `PlaylistManagerUI` creates it for the Library, and `app.py` injects the real `MusicApiService`; the Library keeps playlist summaries by platform while details load tracks on demand.

## Structure

```text
Melomaniac/
├── app.py
├── config/                         # Git-ignored runtime files
├── core/
│   ├── availability.py             # lazy scan and Apple ISRC preload
│   ├── cache.py                    # make_cache_key and legacy compatibility
│   ├── config.py                  # platforms, limits, batch sizes
│   ├── library_models.py           # PlaylistSummary, LibrarySnapshot, states
│   ├── library_store.py            # atomic local metadata persistence
│   ├── models.py                  # Track, PlaylistMeta, SearchResult, enums
│   ├── state.py                   # AppState and BLoC/Observer workflow
│   └── transfer.py                # rate-limit aware search and short errors
├── engine/
│   ├── audio_metadata.py           # Mutagen audio tags
│   ├── match.py                    # validation, scoring, Hunter Recovery
│   ├── normalizer.py               # cleanup, noise, ISRC
│   ├── organizer.py                # in-memory sort_tracks/split_tracks
│   ├── exporters.py                # multi-format local export
│   ├── downloader.py               # yt-dlp audio resolution and download
│   └── parsers.py                 # local formats and local Track creation
├── services/
│   ├── api_service.py             # YouTube/Apple/Spotify facade
│   ├── authentication.py           # runtime files and pre-flight
│   ├── circuit_breaker.py          # RateLimitError and cooldowns
│   ├── cover_cache.py              # cover cache
│   └── library_state.py            # Library synchronization
├── ui/
│   ├── auth_manager.py            # authentication coordination
│   ├── config_wizard.py            # credential editing
│   ├── main_ui.py                 # main window
│   ├── organize_panel.py           # Organize/Split/Reorder dialogs
│   ├── library_view.py             # Library and entry to Downloads module
│   ├── downloads_view.py           # download queue, progress, and results
│   ├── playlist_card.py            # playlist cards
│   ├── playlist_detail_view.py     # details and on-demand track loading
│   ├── playlist_meta_dialog.py     # playlist name/description
│   ├── song_row.py                # rows and skeletons
│   ├── telemetry.py               # Monitor, Console, Post-Mortem
│   ├── fonts.py                   # Unicode classification and font registry
│   ├── tokens.py                  # design tokens
│   └── widgets.py                 # reusable controls
├── resources/fonts/               # Stack Sans and IBM Plex; optional CJK
└── tests/                          # reserved for unittest regressions
```

## Models and state

`Track` is the universal track model. In addition to title, artist, album, duration, platform, and artwork, it may include `duration_ms`, `is_explicit`, `isrc`, `source_path`, `source_url`, `genre`, `album_artist`, `track_number`, and `release_date`. `PlaylistSummary` and `LibrarySnapshot` store Library metadata only; they do not persist tracks.

`PlaylistMeta` preserves the source playlist name and description. `SearchResult` preserves `track_id`, `needs_review`, `low_confidence`, and `isrc`.

Main states:

- `LoadState`: `IDLE → LOADING_META → LOADING_TRACKS → READY` or `ERROR`.
- `TransferState`: `IDLE → RUNNING → DONE` or `ERROR`.
- `Track.transfer_status`: `pending`, `searching`, `found`, `not_found`, `revision_necesaria`, `transferred`, `error`, and `local_pending`.

`AppState` owns the working list, `source_tracks`, filters, segments, selection, progress, logs, failures, and review candidates. It provides `visible_tracks()` and `selected_in_scope()`; the current Organize/Split UI has no explicit All/Visible/Selected scope selector. `dual_mode` controls `list`, `dual`, and `preview`; the UI listens for changes through `subscribe()`/`notify()`.

`engine.organizer` supports internal sort keys `artist`, `album`, `name`, `duration_ms`, `release_date`, `track_number`, `original_position`, and `genre`. `split_tracks` supports `artist`, `album`, `release_date`, `genre`, and `name`; empty values are grouped as `Desconocido`. The UI exposes a subset: stable grouping, sorting, artist/album reordering, and 2–5 manual divisions by artist, album, or track. `platform` is not a criterion.

## Platforms and authentication

| Platform | Read/search | Create | Credentials |
|---|---|---|---|
| YouTube Music | `ytmusicapi` | `YTMusic.create_playlist` | `config/browser.json` |
| Apple Music | `amp-api.music.apple.com` web catalog | library endpoint | `config/.env` |
| Spotify | `spotapi`/`tracksV2` | `PrivatePlaylist` | `config/spotify_cookies.json` |
| Local File | parsers + Mutagen | — | — |
| Pasted Text | line parser | — | — |

`services.authentication` is the single owner of credential paths and persistence:

- YouTube: `Authorization` and `Cookie`; fixed fields (`Accept`, `Content-Type`, `X-Goog-AuthUser`, `x-origin`) are written automatically.
- Apple: `APPLE_AUTH_BEARER` and `APPLE_MUSIC_USER_TOKEN`.
- Spotify: `identifier`, `cookies.sp_dc`, and `cookies.sp_key`.

`AuthManager` checks YouTube through `MusicApiService` and Apple/Spotify through `run_preflight()`, only at startup or after a user action. YouTube distinguishes session rejection (`401`/`403`) from rate limits (`429`) and transient failures; transient failures do not change authentication state. Saving in the wizard triggers a hot reload without restarting.

## Loading flow

```text
UI selects source + ID or URL
        │
        ├── streaming → AppState.load_playlist()
        │                 → MusicApiService.fetch_playlist()
        │                 → PlaylistMeta + list[Track]
        │
        └── local → parser by extension/content
                    → Mutagen when an audio path is available
                    → AppState.load_local_tracks()
```

Parsers accept `TXT`, `CSV`, `M3U/M3U8`, `PLS`, `WPL`, `XSPF`, and XSPF-compatible XML. Local paths are resolved only for known audio extensions; remote URLs are not read as local files.

`engine.exporters` mirrors the parsers for local output. It exports `TXT`, `CSV`, `M3U`, `M3U8`, and `XSPF`, supports `artist-title` and `title-artist` order, sanitizes filenames, and preserves ISRC in XSPF. The UI exposes it as the `Local File (Export)` destination and uses `FilePicker` when available.

## Search and transfer flow

```text
selected Track
  → make_cache_key(title, artist, destination)
  → cache or rate-limit aware search
  → platform hunter and metadata normalization
  → SearchResult and UI status
  → collect valid destination IDs
  → create_playlist(name, description, IDs)
  → confirmation, rejected IDs, Post-Mortem
```

`search_with_fallback()` tries equivalent-free query passes:

1. cleaned metadata;
2. original title/artist;
3. normalized title;
4. Latin transliteration when Cyrillic is present;
5. title only as the last resort.

A per-platform, per-metadata `SingleFlight` prevents two equivalent tracks from searching at the same time. The loop keeps the best result (`no match` < `needs_review` < `low_confidence` < high confidence) and only continues while there is no match or the match needs review. The search cache is persisted every `25` entries and when the transfer closes.

YouTube searches songs and validates up to the first three candidates, preferring `resultType == song` and the closest duration within five seconds. Apple searches the web catalog and selects by similarity and duration. Spotify queries `tracksV2` and scores 40 title points + 20 artist points, 30 duration points, and 10 explicit-match points.

Before Apple searches, available ISRCs are resolved in sequential batches of `25`; exact matches are written to the cache.

The shared matcher uses these thresholds:

| Threshold | Meaning |
|---:|---|
| `85` | ideal match |
| `70–84` | low confidence but acceptable for streaming |
| `<40` | `needs_review` / `revision_necesaria` |
| artist `99` + title `60` | exact-artist rescue path |

For local tracks, a `low_confidence` result is also rejected to avoid ambiguous transfers.

The Organize dialog can preserve the current order or sort A→Z/Z→A, then group by artist, album, or release date. Split creates 2–5 manual divisions by selecting artists, albums, or tracks. These operations apply to the playlist as a whole. The user can show the list, preview, or both columns. The floating side rail is a custom docked control: it pushes the content instead of overlaying it, starts collapsed at `68 px`, and expands to `220 px` with animated labels. It exposes Manage, Library, Downloads, and Config; Config stays pinned at the bottom as a work-in-progress module, and the credential wizard opens from the gear button in the left panel. Library syncs and caches playlist metadata. Downloads lets users select tracks, format, and destination, inspect progress/results, and cancel the queue.

### Audio downloads

`AppState.download_selected()` coordinates the queue and runs blocking `yt-dlp` calls in threads (`asyncio.to_thread`), with maximum concurrency `DOWNLOAD_CONCURRENCY=2`. `engine.downloader` reuses the source URL for YouTube Music tracks or searches YouTube for a match for other sources; it validates title and duration, extracts audio only with FFmpeg, and writes metadata/artwork. The file is published via atomic rename after passing a minimum-size check. MP3 (default), M4A, Opus, FLAC, and WAV are available. If needed, the YouTube Music session is loaded from `config/browser.json` in memory; no `cookies.txt` is written. The view does not implement playback or automatic cache pruning.

## Concurrency, caching, and resilience

Values are centralized in `core/config.py`:

| Constant | Value | Use |
|---|---:|---|
| `NETWORK_CONCURRENCY` | `2` | global API semaphore |
| `TRANSFER_CONCURRENCY[Apple Music]` | `2` | Apple transfer limit |
| `TRANSFER_CONCURRENCY[default]` | `3` | other transfer limit |
| `APPLE_ISRC_BATCH` | `25` | ISRC lookup |
| `APPLE_TRANSFER_BATCH` | `100` | Apple creation/insertion |
| `APPLE_REQUEST_BURST` | `50` | Apple preventive counter |
| `APPLE_REQUEST_PAUSE` | `60 s` | pause after burst |
| `SPOTIFY_ADD_CHUNK` | `50` | Spotify insertion |

The cache loads at startup from `config/search_cache.json` and is saved using a temporary file plus `os.replace`. It accepts serialized `SearchResult` objects and legacy values containing only `track_id`.

`CircuitBreaker` uses a monotonic clock, notifies listeners, schedules an auto-reset, and can cancel its task during shutdown. `429` opens the breaker; Apple converts `423` to a rate limit with a minimum of `120` seconds and treats `401/403` as authentication failures. Spotify detects `429/423` in SpotAPI errors.

Apple processes transfers sequentially to avoid bursts. Other platforms use tasks bounded by a semaphore. Non-rate-limit searches get up to three attempts in `AppState`, with one- and two-second waits.

## Lifecycle

1. `app.py` prepares `config/`, the environment, window, theme, and fonts.
2. It creates `AppState`, `MusicApiService`, `PlaylistManagerUI`, and `AuthManager`.
3. The UI is mounted and the asynchronous pre-flight starts.
4. A session poll refreshes the auth icons every `90` seconds.
5. On close, breakers, lazy scans, reloads, and tasks are cancelled, HTTP sessions are closed, and the UI is released.

## Legal notice

Melomaniac is a local application for managing, organizing, transferring, and discovering music using information provided by third-party services.

Melomaniac does not host, provide, or redistribute copyrighted music content. File-acquisition features use third-party tools and sources and do not imply direct access to streaming service content.

The user is responsible for ensuring that any content obtained, stored, or used through Melomaniac is authorized for that use and for complying with applicable laws and the terms of the source services.

Melomaniac is not affiliated with, sponsored by, or endorsed by Spotify, Apple, Google, YouTube, or any other third-party service.

## Verification

```bash
python -m compileall -q app.py core engine services ui
```

The Flet UI is validated manually, including List/Dual/Preview modes, Organize/Split dialogs, responsive navigation, animated skeletons, and local export. The `tests/` directory remains available for future regressions.
