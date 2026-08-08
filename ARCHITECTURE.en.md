# MelomaniacPass v5.0 — Technical Architecture

Desktop application for transferring playlists between **YouTube Music** and **Apple Music**, with local sources (CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML, and plain text) and intelligent matching through the **Hunter Recovery** engine.

> **Note:** Spotify was removed because its API was not viable for this implementation. This document describes the current code, not the historical `deprecated` branch.

---

## Project Structure

```text
melomaniacpass/
├── app.py                    # Entry point, composition, and hard cleanup
├── auth_manager.py           # Credentials, wizard UI, and pre-flight
├── .env                      # Apple Music credentials (runtime, untracked)
├── browser.json              # YouTube Music headers (runtime, untracked)
│
├── core/
│   ├── models.py             # Track, SearchResult, LoadState, TransferState
│   └── state.py              # AppState: central BLoC-inspired coordination
├── services/
│   └── api_service.py        # MusicApiService: YTM + Apple Music facade
├── engine/
│   ├── normalizer.py         # Metadata cleanup and normalization
│   ├── match.py              # Hunter Recovery and fuzzy scoring
│   ├── parsers.py            # Local playlist parsers
│   └── organizer.py          # In-memory sorting and segmentation
├── ui/
│   ├── main_ui.py            # PlaylistManagerUI: main interface
│   ├── song_row.py           # SongRow and SkeletonRow
│   ├── telemetry.py          # Monitor, console, and Post-Mortem
│   └── widgets.py            # Buttons, icons, and reusable components
├── utils/
│   └── circuit_breaker.py    # CircuitBreaker and RateLimitError
└── resources/fonts/          # Local IBM Plex Sans fonts
```

## Supported Platforms

| Platform | Type | Authentication |
|---|---|---|
| **YouTube Music** | Streaming | Session headers in `browser.json` |
| **Apple Music** | Streaming | Bearer token + user token in `.env` |
| **Local File** | Local source | None |
| **Paste Text** | Local source | None |

Local sources become `Track` objects and can be transferred to either streaming platform.

## Dependency Flow

```text
app.py
  ├── CircuitBreaker (per platform)
  ├── MusicApiService ── APIs, HTTP sessions, and cache
  ├── AppState ───────── models, progress, and coordination
  ├── PlaylistManagerUI ─ rows, controls, and telemetry
  └── AuthManager ─────── wizard, credentials, and pre-flight

engine/normalizer → engine/match / engine/parsers / engine/organizer
                         ↓
                    core/models
                         ↓
                    core/state ↔ services/api_service
                         ↓
                         ui
```

`app.py` initializes `CircuitBreakers → Service → State → UI`; `AuthManager` is then connected through injected references to coordinate credential reloads and UI updates.

## Libraries Used

| Library / module | Main purpose |
|---|---|
| `flet` | Window, controls, dialogs, and theme |
| `asyncio` | Async operations, concurrency, and lifecycle |
| `requests` | HTTP sessions for Apple Music, iTunes Search, and pre-flight |
| `ytmusicapi` | YouTube Music client |
| `python-dotenv` | `.env` read/write |
| `rapidfuzz` | Title and artist fuzzy scoring |
| `csv`, `xml.etree.ElementTree` | CSV, iTunes XML, XSPF, and WPL parsers |
| `json`, `pathlib`, `re`, `unicodedata` | Configuration, paths, and normalization |
| `collections.defaultdict` | Segment grouping |

The repository does not yet include a dependency manifest (`requirements.txt` or `pyproject.toml`); installation is documented in the README.

## Authentication and Configuration Files

### `.env` — Apple Music

```env
APPLE_AUTH_BEARER="Bearer eyJ..."
APPLE_MUSIC_USER_TOKEN="0.AsH5..."
```

`auth_manager.py` centralizes read/write operations through `dotenv_values`, `set_key`, and `load_dotenv`. `services/api_service.py` consumes these values to build headers and call the authenticated Apple Music storefront and endpoints.

### `browser.json` — YouTube Music

```json
{
    "Accept": "*/*",
    "Authorization": "SAPISIDHASH ...",
    "Content-Type": "application/json",
    "X-Goog-AuthUser": "0",
    "x-origin": "https://music.youtube.com",
    "Cookie": "..."
}
```

`auth_manager.py` is the only module that writes this file. `MusicApiService` imports its path and builds the `YTMusic` client with these headers.

### Platform Authentication Flow

```text
YouTube Music:
browser headers → ConfigWizard → browser.json → YTMusic → requests

Apple Music:
browser tokens → ConfigWizard → .env → HTTP headers → Apple Music API
```

**Startup pre-flight:** `AuthManager` runs both platform checks in parallel. YouTube Music validates the file and makes a real call; Apple Music validates tokens, storefront, and catalog. Results update session indicators and open the wizard on the failing tab.

During matching, Apple Music uses the public iTunes Search API (`itunes.apple.com/search`) to obtain candidates and their `trackId` values. This avoids unnecessary use of the authenticated search endpoint and reduces temporary blocks. Authenticated Apple Music API calls remain responsible for playlist lookup, storefront access, and destination playlist creation.

## Module Communication

### Initialization in `app.py`

```python
circuit_breakers = {p: CircuitBreaker(p) for p in AppState.PLATFORMS}
service = MusicApiService(circuit_breakers)
state = AppState(service)
ui = PlaylistManagerUI(page, state)
auth_manager = AuthManager(page, service, state)

ui.auth_manager = auth_manager
service.auth_manager = auth_manager
```

### Observer pattern (state → UI)

`AppState` keeps listeners. Relevant mutations update state and call `notify()`, while `PlaylistManagerUI` refreshes tracks, progress, buttons, authentication status, and telemetry.

### Track search example

```text
ui → state.transfer_playlist()
state → clean_metadata() and concurrent search with backoff
service → search_with_fallback() on the destination platform
engine.match → validation, scores, and confidence classification
state → SearchResult and Track status
service → create_playlist() with confirmed IDs
ui → progress and Post-Mortem
```

### Local source loading example

```text
ui → file selection or text dialog
engine.parsers → parse_local_playlist() → (artist, title) pairs
engine.parsers → build_local_tracks() → list[Track]
state → load_local_tracks() → notify()
ui → renders the playlist
```

### Organize / split example

```text
ui → state.organize_sort(keys) or state.organize_split(key)
state → engine.organizer.sort_tracks() / split_tracks()
state → updates tracks or segments → notify()
ui → refreshes the list and segment selector
```

## Key Modules — Responsibilities

### `engine/organizer.py`

In-memory transformations with no I/O: `sort_tracks()` sorts by artist, album, title, duration, or platform; `split_tracks()` groups by an attribute.

### `engine/match.py` — Hunter Recovery

- `validar_match()` applies layered validation: CJK/Hangul bypass, substring and artist checks, cover/karaoke/tribute filtering, and fuzzy fallback.
- `_fuzzy_scores_triple()` calculates combined, title, and artist scores.
- `_ideal_pass_hunter()` accepts high-confidence matches.
- `_fuzzy_flags_elastic()` classifies review and low-confidence results.
- `_yt_select_best()` uses duration as a tie-breaker when available.

### `engine/normalizer.py`

`clean_metadata()` normalizes Unicode, removes noise, and cleans parentheses/brackets. `build_search_query()` builds work-first queries. Code thresholds are `FUZZY_IDEAL=85`, low log band `70`, and review `40`.

### `engine/parsers.py`

`parse_local_playlist()` detects CSV, M3U/M3U8, PLS, WPL, XSPF, iTunes XML, or plain text by extension and content. `build_local_tracks()` converts extracted pairs into `Track` objects with local IDs.

### `utils/circuit_breaker.py`

`CircuitBreaker` starts a per-platform cooldown and notifies the UI; `RateLimitError` represents HTTP 429 responses with a retry delay.

### `services/api_service.py`

`MusicApiService` unifies loading, searching, and playlist creation. It maintains HTTP sessions, a search cache, authenticated clients, a global network semaphore (`NETWORK_CONCURRENCY=2`), and rate-limit retries (`RATE_LIMIT_BACKOFF_STEPS=10`). It converts Apple Music HTTP 429 and 423 responses into `RateLimitError`; 423 receives a minimum 120-second cooldown.

### `core/state.py` and `core/models.py`

`Track` and `SearchResult` are the data contracts. `LoadState` and `TransferState` model lifecycle states. `AppState` coordinates loading, filtering, selection, transfer, progress, segments, errors, and notifications.

## ConfigWizard — Credential Management

The modal wizard has two tabs:

| Tab | Platform | Fields | File |
|---|---|---|---|
| 0 | YouTube Music | Authorization, Cookie | `browser.json` |
| 1 | Apple Music | Bearer, User Token | `.env` |

It includes DevTools instructions and a **Save and Apply** button. After saving, `reload_credentials()` updates service clients without restarting the process.

## Lifecycle and Cleanup

`app.py` registers the initial pre-flight, a 90-second session poll, and a deep shutdown that:

1. Cancels circuit breakers and UI tasks.
2. Stops pending searches and lazy scans.
3. Cancels authentication and transfer `asyncio` tasks.
4. Closes the service's reusable HTTP sessions.
5. Runs garbage collection and uses emergency exit if blocked threads remain.

## Refactoring Metrics

The current state preserves the original monolith's separation into core, services, engine, UI, and utils modules. Historical size and line counts are not automated or maintained as build metrics, so they are not presented as exact values here.

| Area | Current state |
|---|---|
| Streaming platforms | 2: YouTube Music and Apple Music |
| Local sources | File and pasted text |
| Engine modules | 4: normalizer, match, parsers, organizer |
| Shared state | Observable `AppState` |
| Resilience | Circuit breaker, backoff, and cache |

## Backup

Historical documentation mentions a monolith and a `deprecated` branch with Spotify. Those elements are not part of the inspected current tree and should be treated as historical backup, not current runtime dependencies.
