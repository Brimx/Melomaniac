# Melomaniac v4.5.0 — Architecture

Melomaniac separates the Flet UI, application state, platform services, and metadata normalization/matching engine. The current version supports YouTube Music, Apple Music, Spotify, and two local inputs: file and pasted text.

The working version remains in the `4.5.0` series; the exact minor/patch number will be fixed when this change cycle is closed.

## Layers and dependencies

```text
app.py
├── AppState                 # reactive state, progress, transfer
├── MusicApiService          # APIs, cache, sessions, playlist creation
├── AuthManager              # pre-flight, wizard, hot reload
└── PlaylistManagerUI        # Flet, events, rows, telemetry

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

## Structure

```text
Melomaniac/
├── app.py
├── config/                         # Git-ignored runtime files
├── core/
│   ├── availability.py             # lazy scan and Apple ISRC preload
│   ├── cache.py                    # make_cache_key and legacy compatibility
│   ├── config.py                  # platforms, limits, batch sizes
│   ├── models.py                  # Track, PlaylistMeta, SearchResult, enums
│   ├── state.py                   # AppState and BLoC/Observer workflow
│   └── transfer.py                # rate-limit aware search and short errors
├── engine/
│   ├── audio_metadata.py           # Mutagen audio tags
│   ├── match.py                    # validation, scoring, Hunter Recovery
│   ├── normalizer.py               # cleanup, noise, ISRC
│   ├── organizer.py                # in-memory sort_tracks/split_tracks
│   ├── exporters.py                # multi-format local export
│   └── parsers.py                 # local formats and local Track creation
├── services/
│   ├── api_service.py             # YouTube/Apple/Spotify facade
│   ├── authentication.py           # runtime files and pre-flight
│   └── circuit_breaker.py          # RateLimitError and cooldowns
├── ui/
│   ├── auth_manager.py            # authentication coordination
│   ├── config_wizard.py            # credential editing
│   ├── main_ui.py                 # main window
│   ├── playlist_meta_dialog.py     # playlist name/description
│   ├── song_row.py                # rows and skeletons
│   ├── telemetry.py               # Monitor, Console, Post-Mortem
│   ├── fonts.py                   # Unicode classification and font registry
│   ├── tokens.py                  # design tokens
│   └── widgets.py                 # reusable controls
├── resources/fonts/               # IBM Plex Sans w300–w700; optional CJK
└── tests/                          # reserved for unittest regressions
```

## Models and state

`Track` is the universal track model. In addition to title, artist, album, duration, platform, and artwork, it may include `duration_ms`, `is_explicit`, `isrc`, `source_path`, `album_artist`, `track_number`, and `release_date`.

`PlaylistMeta` preserves the source playlist name and description. `SearchResult` preserves `track_id`, `needs_review`, `low_confidence`, and `isrc`.

Main states:

- `LoadState`: `IDLE → LOADING_META → LOADING_TRACKS → READY` or `ERROR`.
- `TransferState`: `IDLE → RUNNING → DONE` or `ERROR`.
- `Track.transfer_status`: `pending`, `searching`, `found`, `not_found`, `revision_necesaria`, `transferred`, `error`, and `local_pending`.

`AppState` owns the master list, filter, segments, selection, counters, logs, failures, review candidates, and session flags. It also provides `visible_tracks()` and `selected_in_scope()` so Organize, Split, transfer, and export share the same scope. `dual_mode` controls the `list`, `dual`, and `preview` modes; the UI subscribes through `subscribe()` and receives updates through `notify()`.

`engine.organizer` limits sorting keys to `artist`, `album`, `name`, and `duration_ms`. `split_tracks` normalizes keys, trims whitespace, and groups empty values under `Desconocido`. `platform` is no longer offered as an Organize/Split key.

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

`run_preflight()` checks all three platforms concurrently. `AuthManager` mirrors results into `AppState.auth_session_ok` and `auth_session_hint`, initializes valid services, and opens the relevant wizard tab when a session is missing or expired. Saving in the wizard triggers a hot reload without restarting.

## Loading flow

```text
UI selects source + ID
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
3. normalized title.

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

During Organize/Split, the UI offers `All`, `Visible`, and `Selected` scopes. Applying an operation updates the segments and opens dual view; the user can show the list, preview, or both columns. `NavigationRail` organizes the Home, Library, Downloads, and Config modules and adapts to the available space.

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

The Flet UI is validated manually, including List/Dual/Preview modes, Organize/Split scope, responsive navigation, animated skeletons, and local export. The `tests/` directory remains available for future regressions.
