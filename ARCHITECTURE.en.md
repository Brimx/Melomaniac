# Melomaniac v4.10.0 — Architecture

[← README](README.en.md) · [Español](ARCHITECTURE.md) · [Design](DESIGN.md) · [Changes](CHANGELOG.md)

Melomaniac separates the Flet UI, application state, platform services, and metadata normalization/matching engine. The current version supports YouTube Music, Apple Music, Spotify, and two local inputs: file and pasted text.

The documented working version is `4.10.0` (single source: `APP_VERSION` in `core/config.py:12`). This version closes Management Block 2 (library playlist picker, merge, local manifests, export/add to platforms, catalog search in Management, and contextual action bar), upgrades to Flet `1.1.2`, and consolidates the composition-based modular architecture. Per-version details live in `CHANGELOG.md`.

## Layers and dependencies

```text
app.py
├── AppState                 # reactive state, progress, transfer
├── MusicApiService          # APIs, cache, sessions, playlist creation
├── AuthManager              # pre-flight, wizard, hot reload
└── PlaylistManagerUI        # Flet, navigation, events, telemetry
    └── LibraryState         # Library metadata synchronization/cache

ui ───────> core / engine / services
core ─────> engine / services (authentication, circuit_breaker; covers and settings_store via lazy imports)
services ─> core / engine
engine ───> core (models, config); services.browser_session via lazy import only
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
├── app.py                         # Entry point, composition, and lifecycle
├── scripts/launchers/             # run.sh / run.bat: fallback for development and last resort
├── config/                        # Git-ignored runtime files (credentials, settings, caches)
├── core/
│   ├── availability.py            # Lazy scan and Apple ISRC preload
│   ├── cache.py                   # make_cache_key and legacy compatibility
│   ├── config.py                  # Platforms, limits, and batch sizes
│   ├── library_models.py          # PlaylistSummary, LibrarySnapshot, states
│   ├── library_store.py           # Atomic local metadata persistence
│   ├── models.py                  # Track, PlaylistMeta, SearchResult, enums
│   ├── playback.py                # PlayQueue, audio engine, repeat/shuffle modes
│   ├── download_flow.py           # DownloadRunner: yt-dlp queue, pause, and retries
│   ├── state.py                   # AppState and BLoC/Observer workflow
│   ├── transfer.py                # Transfer report and rate-limit aware search
│   └── transfer_flow.py           # TransferRunner: search, creation, and verification
├── engine/
│   ├── audio_metadata.py          # Mutagen audio tag reading and writing
│   ├── covers.py                  # Artwork normalization to 800 px squares
│   ├── download_cookies.py        # In-memory cookie jar for yt-dlp
│   ├── download_search.py         # YouTube candidate search and ranking
│   ├── downloader.py              # Audio download, metadata, and manifest (yt-dlp)
│   ├── exporters.py               # Multi-format local export (M3U8, CSV, TXT, XSPF)
│   ├── local_index.py             # Persistent local audio folder index
│   ├── local_manifests.py         # Local JSON manifests and track file paths
│   ├── match.py                   # Validation, scoring, and Hunter Recovery
│   ├── normalizer.py              # Cleanup, noise, ISRC, and Cyrillic transliteration
│   ├── organizer.py               # In-memory sort_tracks and split_tracks
│   └── parsers.py                 # Local formats and local Track creation
├── services/
│   ├── api_service.py             # YouTube/Apple/Spotify facade (state, cache, dispatch)
│   ├── platforms/                 # Composed platform clients (youtube.py, apple.py, spotify.py, _common.py)
│   ├── authentication.py          # Runtime paths and pre-flight checks
│   ├── browser_session.py         # In-memory browser cookie extraction
│   ├── circuit_breaker.py         # RateLimitError and monotonic cooldowns
│   ├── cover_cache.py             # Atomic artwork cache storage on disk
│   ├── covers.py                  # Multi-source album artwork resolution pipeline
│   ├── desktop_integration.py     # .desktop/.lnk launchers and binary auto-sync
│   ├── library_state.py           # Library synchronization and state management
│   ├── local_index.py             # Facade over local index and manifests
│   ├── mpris_service.py           # Linux D-Bus MPRIS service for media controls
│   ├── settings_store.py          # User settings persistence
│   └── update_checker.py          # Update checking against GitHub Releases
├── ui/
│   ├── theme/                     # tokens.py, fonts.py, icons.py
│   ├── components/                # widgets.py, song_row.py, skeletons.py, playlist_card.py, context_menu.py, telemetry.py
│   ├── dialogs/                   # organize_panel.py, division_dialogs.py, playlist_meta_dialog.py, transfer_*, export_dialog.py, merge_dialog.py, recovery_panel.py
│   ├── setup/                     # auth_manager.py, config_wizard.py, wizard_forms.py, onboarding.py, onboarding_bg.py
│   ├── views/                     # library_view.py, playlist_detail_*, downloads_view.py, download_recovery.py, config_view.py
│   ├── player/                    # player_bar.py, bar_layout.py, player_queue.py, player_mpris.py
│   └── shell/                     # main_ui.py, main_layout.py, main_actions.py, catalog_panel.py, nav_rail.py, sidebar.py, keymap.py
├── resources/fonts/               # Stack Sans and IBM Plex fonts; optional CJK
├── scripts/                       # Standalone packaging and portable bundle
│   ├── build_linux.py             # AppImage + tar.gz (SHA-256 digest via GitHub)
│   ├── build_release_bundle.py    # Portable source ZIP bundle
│   └── windows/installer.iss      # Inno Setup script for Windows
└── tests/                         # unittest suite and regressions
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
| YouTube Music | `ytmusicapi` | `YTMusic.create_playlist` | Browser session (`SAPISID`) or `config/browser.json` |
| Apple Music | `amp-api.music.apple.com` web catalog | library endpoint | Browser session (`media-user-token` + bundle bearer) or `config/.env` |
| Spotify | `spotapi`/`tracksV2` | `PrivatePlaylist` | Browser session (`sp_dc`/`sp_key`) or `config/spotify_cookies.json` |
| Local File | parsers + Mutagen | — | — |
| Pasted Text | line parser | — | — |

`services.browser_session` first resolves active browser sessions in memory (Firefox-family: copies `cookies.sqlite` + `-wal`/`-shm` to RAM via `sqlite3`; Chromium-family: standard paths via `yt-dlp`) without writing anything to disk. `services.authentication` is the single owner of runtime paths and manual-override files in `config/`:

- YouTube: `Cookie` and `SAPISIDHASH` recomputed on each read from `SAPISID` (a saved legacy `Authorization` is ignored); fixed fields (`Accept`, `Content-Type`, `X-Goog-AuthUser`, `x-origin`) are filled in automatically.
- Apple: `media-user-token` from the cookie and a bearer derived from the public bundle (`kid == "WebPlayKid"` and `iss == "AMPWebPlay"`) or `APPLE_AUTH_BEARER` / `APPLE_MUSIC_USER_TOKEN` in `config/.env`.
- Spotify: `cookies.sp_dc` and `cookies.sp_key` (with optional `identifier` in `config/spotify_cookies.json`).

`AuthManager` validates all three platforms via `MusicApiService.init_youtube()`, `init_apple()`, and `init_spotify()`, which iterate the candidates (`*_candidates()`) and verify against the real API (`/v1/me/storefront` on Apple, `get_library_playlists(limit=1)` on YouTube, `_spotify_library_page(limit=1)` on Spotify). Startup preflight records the winning source and refreshes the icons without opening the wizard automatically. YouTube distinguishes session rejection (`401`/`403`) from rate limits (`429`) and transient failures; the latter do not change authentication state. Saving or retrying in the wizard clears the `browser_session` cache and reloads without a restart.

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

The Organize dialog can preserve the current order or sort A→Z/Z→A, then group by artist, album, or release date. Split creates 2–5 manual divisions by selecting artists, albums, or tracks. These operations apply to the playlist as a whole. The user can show the list, preview, or both columns. The floating side rail is a custom docked control: it pushes the content instead of overlaying it, starts collapsed at `68 px`, and expands to `220 px` with animated labels. It exposes Manage, Library, Downloads, and Config; Config manages default audio formats, online artwork toggles, destination folders, and cache clearing, while the credential wizard opens from the gear button in the left panel. Library syncs and caches playlist metadata, and indexes local music folders. Downloads lets users select tracks, format, and destination, inspect progress/results with an FFmpeg preflight banner, and cancel the queue.

### Audio downloads

`AppState.download_selected()` coordinates the queue and runs blocking `yt-dlp` calls in threads (`asyncio.to_thread`), with maximum concurrency `DOWNLOAD_CONCURRENCY=2`. `engine.downloader` reuses the source URL for YouTube Music tracks or searches YouTube for a match for other sources; it validates title and duration, extracts audio only with FFmpeg, and writes metadata/artwork with Mutagen. The file is published via atomic rename after passing a minimum-size check. MP3 (default), M4A, Opus, FLAC, and WAV are available, with automated skipping for already downloaded files.

### Integrated Audio Player and MPRIS

Native audio playback is handled by `ui.player.player_bar.PlayerBar` using `flet-audio` over `FLET_DESKTOP_FLAVOR=full`. It supports FLAC, MP3, M4A, Opus, and WAV for local and downloaded tracks. Playback queues are managed by `core.playback.PlayQueue` (featuring repeat, shuffle, and reorderable track control via `ui.player.player_queue.PlayerQueuePanel`). On Linux, `services.mpris_service.MPRISService` (`org.mpris.MediaPlayer2.melomaniac`) exposes standard transport state, position seeking, artwork, and track metadata to the session D-Bus via `ui.player.player_mpris.MprisController`, enabling control through desktop widgets and physical keyboard media keys.

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

## Guided onboarding

On first run, `ui.setup.onboarding.OnboardingView` shows a full-screen overlay with the steps in `ONBOARDING_STEPS` (single list: any new visible feature adds or updates a step there; seven today, including "System and updates"). The animated background lives in `ui.setup.onboarding_bg`. On completion, `onboarding_done` is stored via `services.settings_store` (`config/settings.json`); the tour reopens from Config. Measurements live in `ui.theme.tokens` (`ONBOARDING_*`).

## System integration and updates

`services.desktop_integration` creates and installs `.desktop` launchers on Linux (`$XDG_DATA_HOME/applications` + hicolor icons from `resources/icon.png`) and `.lnk` shortcuts on Windows. It distinguishes AppImage (detects `$APPIMAGE`), binary, Inno Setup installer (preserves shortcuts on update), and portable ZIP. At startup (`app.py`) it silently auto-syncs the launcher if the executable path changed after an update; it only acts on packaged builds (`sys.frozen`), never from source.

`services.update_checker` queries GitHub Releases from Config and shows contextual instructions based on the user's packaging mode (AppImage, installer, portable ZIP, or source).

## Standalone releases

Every `v*` tag (or manual `workflow_dispatch`) triggers `.github/workflows/build-binaries.yml`, which builds the binaries in GitHub Actions and uploads them to the release assets. **No `.txt` hash files are published**: the verifiable SHA-256 is the `digest` GitHub shows per asset:

| Package | Script | Output |
|---|---|---|
| Portable source ZIP | `scripts/build_release_bundle.py` | `Melomaniac-vX.Y.Z.zip` (includes `scripts/launchers/`) |
| Linux | `scripts/build_linux.py` (PyInstaller + appimagetool) | `Melomaniac-vX.Y.Z-x86_64.AppImage`, `Melomaniac-vX.Y.Z-linux-x64.tar.gz` |
| Windows | `python -m flet_cli.cli pack` + Inno Setup (`scripts/windows/installer.iss`) | `Melomaniac-vX.Y.Z-windows-x64-Setup.exe`, `Melomaniac-vX.Y.Z-windows-x64.zip` |

In `sys.frozen` mode (binaries), `services.authentication` decouples `CONFIG_DIR` from the install folder: `$XDG_CONFIG_HOME/melomaniac` on Linux and `%APPDATA%\Melomaniac` on Windows. Portable mode is preserved if a writable `config/` folder or a `portable.flag` file exists.

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
python -m compileall -q app.py core engine services ui scripts
python -m unittest discover -s tests -t .
```

The unit test suite in `tests/` covers the engine, concurrency, layer composition, and UI rules/flows; end-to-end Flet UI flows are validated manually (List/Dual/Preview modes, Organize/Split dialogs, responsive navigation, animated skeletons, and local export).
