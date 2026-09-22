# 🎵 Melomaniac v4.5.0

Melomaniac is a desktop application for rebuilding playlists between **YouTube Music, Apple Music, and Spotify**. It can also import a local playlist or a single audio file, find each track on the destination, and create a new playlist.

The working version belongs to the `4.5.0` series: major version 4 is retained while the latest functional changes and fixes are consolidated before the next minor/patch number is fixed.

Matching combines metadata normalization, RapidFuzz, title/artist similarity, duration, explicit metadata, and ISRC when available. Uncertain results are preserved in the Post-Mortem view for review.

## Features

- Load playlists from YouTube Music, Apple Music, and Spotify.
- Import `TXT`, `CSV`, `M3U/M3U8`, `PLS`, `WPL`, `XSPF`, and XSPF-compatible XML playlists.
- Import audio files (`MP3`, `FLAC`, `AAC`, `OGG`, `WAV`, `M4A`, `WMA`, `OPUS`, `AIFF/AIF`) and read their tags with Mutagen.
- Search with Hunter Recovery using three query passes: cleaned metadata, original values, and a normalized title.
- Use ISRC for exact Apple Music matches and retain it in the search cache.
- Compare duration; Spotify also includes the `explicit` flag in its scoring.
- Search, select, sort, and split tracks by artist, album, title, or duration. The operation scope can be **All**, **Visible**, or **Selected**.
- Use **List**, **Dual**, and **Preview** modes while organizing/splitting, with responsive layout and navigation for **Home**, **Library**, **Downloads**, and **Config**.
- Show title, artist, album, duration, artwork, and transfer status for each track.
- Export the selected tracks or visible scope to `TXT`, `CSV`, `M3U`, `M3U8`, and `XSPF`, using artist-title or title-artist order.
- Edit the playlist name and description before creating it. Spotify's SpotAPI integration only saves the name; the description is reported as unsupported.
- Provide destination availability scanning, progress, console logs, and a Post-Mortem report exportable to `transfer_failed_report.txt`.
- Protect API usage with semaphores, circuit breakers, persistent caching, and batched writes.

## Requirements

| Requirement | Details |
|---|---|
| Python | 3.10 or newer |
| Dependencies | Pinned in [`requirements.txt`](requirements.txt) |
| Credentials | `config/browser.json`, `config/.env`, and `config/spotify_cookies.json` as needed |
| UI | Flet Desktop; the app configures a dark theme and local IBM Plex Sans fonts |

## Installation

```bash
git clone https://github.com/Brimx/Melomaniac.git
cd Melomaniac
python -m venv .venv
source .venv/bin/activate                 # Linux/macOS
# .venv\Scripts\activate                 # Windows PowerShell
python -m pip install -r requirements.txt
```

## Running

```bash
python app.py
```

The main screen lets you choose a source and a destination. The source can be one of the three streaming platforms or a local input; the destination is always a streaming platform.

### Configure credentials

The credential wizard has one tab per platform. On startup, `AuthManager` runs the session checks concurrently and identifies any platform that needs attention. After saving, credentials are reloaded without restarting the application.

YouTube Music requires the `Authorization` value (with the `SAPISIDHASH` prefix) and the `Cookie` value from a `browse` request in `music.youtube.com`:

```json
{
  "Authorization": "SAPISIDHASH ...",
  "Cookie": "...",
  "Accept": "*/*",
  "Content-Type": "application/json",
  "X-Goog-AuthUser": "0",
  "x-origin": "https://music.youtube.com"
}
```

Apple Music requires these values in `config/.env`:

```env
APPLE_AUTH_BEARER="Bearer eyJ..."
APPLE_MUSIC_USER_TOKEN="0.As..."
```

The `Bearer ` prefix is optional; the application adds it when missing. Values come from the request headers of a `catalog` request in Apple Music Web.

Spotify uses the format written by the wizard to `config/spotify_cookies.json`:

```json
{
  "identifier": "user@example.com",
  "cookies": {
    "sp_dc": "...",
    "sp_key": "..."
  }
}
```

Credentials and the search cache live under `config/`, which is excluded by `.gitignore`. Do not share or commit those files.

### Load a playlist

- **Streaming:** select the platform, enter the playlist ID, and click **Load**.
- **Local File:** select a supported playlist file; the parser uses the extension and content. XML files are interpreted as XSPF.
- **Audio File:** select a supported song; its tags provide the title, artist, album, duration, track number, date, and ISRC.
- **Paste Text:** paste one entry per line. Lines may use `Title - Artist`; lines without a separator are also accepted.

For local sources, select the destination explicitly before transferring. The source and destination cannot be the same platform.

### Review and transfer

1. Review the tracks and deselect anything you do not want to transfer.
2. Optionally use **Organize** or **Split**, choose the operation scope, and change the visible segment. The view can switch between **List**, **Dual**, and **Preview**.
3. Click **Transfer**, and confirm or edit the name and description.
4. Review progress, the console, and the **Post-Mortem** tab.

If you choose **Local File (Export)** as the destination, the button changes to **Export**. You can select the format, order, and output path; the default is `~/Documents/Melomaniac/Exports` when available, with `./exports` as a fallback.

`low_confidence` matches may continue for streaming sources. Local tracks use a strict threshold; matches below `85` are rejected. Matches marked `revision_necesaria` are not inserted automatically.

## Authentication, APIs, and resilience

- **YouTube Music:** `ytmusicapi` for song search and playlist read/create operations.
- **Apple Music:** `amp-api.music.apple.com`; catalog search uses `durationInMillis` and `isrc`.
- **Spotify:** `spotapi`; track search uses `tracksV2`, duration in milliseconds, and `explicit`.
- Global network semaphore: `2` concurrent requests.
- Configured transfer limits: `2` for Apple Music and `3` for the other platforms; Apple searches are currently processed sequentially to avoid request bursts.
- Apple: ISRC batches of `25`, insertion batches of `100`, a preventive limit of `50` requests followed by a `60` second pause; HTTP `423` enforces at least a `120` second cooldown.
- Spotify: inserts tracks in batches of `50`, with up to four attempts and progressive delays.
- Search results are atomically persisted in `config/search_cache.json`; legacy entries containing only `track_id` remain readable.
- Circuit breakers handle `429`; Apple also distinguishes authentication failures (`401/403`) from temporary locking (`423`).

## Configuration files

| File | Purpose |
|---|---|
| `config/.env` | `APPLE_AUTH_BEARER` and `APPLE_MUSIC_USER_TOKEN` |
| `config/browser.json` | Authenticated YouTube Music headers |
| `config/spotify_cookies.json` | `identifier`, `sp_dc`, and `sp_key` |
| `config/search_cache.json` | Results keyed by `title|||artist|||destination` |
| `exports/` | Local exporter output; excluded by `.gitignore` |
| `transfer_failed_report.txt` | On-demand UI report; not a configuration file |

## Project structure

```text
Melomaniac/
├── app.py                         # Entry point, composition, lifecycle
├── config/                        # Ignored runtime credentials and state
├── core/
│   ├── availability.py            # Availability scan and Apple ISRC preload
│   ├── cache.py                   # Cache keys and compatibility helpers
│   ├── config.py                  # Platforms, concurrency, and batch sizes
│   ├── models.py                  # Track, PlaylistMeta, SearchResult, states
│   ├── state.py                   # BLoC state and transfer workflow
│   └── transfer.py                # Search and rate-limit error helpers
├── engine/
│   ├── audio_metadata.py          # Mutagen tag read/write helpers
│   ├── match.py                   # Candidate scoring and validation
│   ├── normalizer.py              # Cleanup, ISRC, fuzzy thresholds
│   ├── organizer.py               # In-memory sorting and segmentation
│   ├── exporters.py               # TXT/CSV/M3U/XSPF local export
│   └── parsers.py                 # Local formats and Track construction
├── services/
│   ├── api_service.py             # API facade and HTTP sessions
│   ├── authentication.py           # Paths, credentials, pre-flight
│   └── circuit_breaker.py          # Cooldowns and RateLimitError
├── ui/
│   ├── auth_manager.py            # Checks and hot reload
│   ├── config_wizard.py            # Credential wizard
│   ├── main_ui.py                 # Main window and events
│   ├── playlist_meta_dialog.py     # Destination name and description
│   ├── song_row.py                # Track rows and skeletons
│   ├── telemetry.py               # Monitor, console, Post-Mortem
│   ├── fonts.py                    # Unicode classification and font registry
│   └── widgets.py / tokens.py      # Components and visual tokens
└── resources/fonts/               # IBM Plex Sans w300–w700; optional CJK
```

Track titles, artists, and albums are classified by Unicode block when their
text controls are created: CJK has priority, followed by Cyrillic and Latin.
The app looks for `NotoSansCJK-Regular.ttc` in bundled assets or standard
system paths; if it is unavailable, it keeps IBM Plex Sans and relies on the
native text fallback for missing glyphs.

## Verification

The quick Python module check is:

```bash
python -m compileall -q app.py core engine services ui
```

The Flet UI is validated manually, especially the view modes, Organize/Split scope, responsive navigation, and export dialog. The `tests/` directory remains available for future regressions.

## Legal notice

Melomaniac is a local application for managing, organizing, transferring, and discovering music using information provided by third-party services.

Melomaniac does not host, provide, or redistribute copyrighted music content. File-acquisition features use third-party tools and sources and do not imply direct access to streaming service content.

The user is responsible for ensuring that any content obtained, stored, or used through Melomaniac is authorized for that use and for complying with applicable laws and the terms of the source services.

Melomaniac is not affiliated with, sponsored by, or endorsed by Spotify, Apple, Google, YouTube, or any other third-party service.

## Status and license

The documented version is `4.5.0` on the `main` branch. This is a personal-use project; each platform and its APIs are subject to their own terms of service.

## Thanks

[Flet](https://flet.dev) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) · [spotapi](https://github.com/spotapi) · [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) · [Mutagen](https://mutagen.readthedocs.io/)
