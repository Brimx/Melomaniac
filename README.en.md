# 🎵 Melomaniac v4.8.0

**Your music. Your library. Your way.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/UI-Flet-7C3AED)](https://flet.dev/)
[![RapidFuzz](https://img.shields.io/badge/Matching-RapidFuzz-00897B)](https://github.com/rapidfuzz/RapidFuzz)
[![YouTube Music](https://img.shields.io/badge/YouTube%20Music-Supported-FF0000?logo=youtubemusic&logoColor=white)](https://music.youtube.com/)
[![Apple Music](https://img.shields.io/badge/Apple%20Music-Supported-FA243C?logo=applemusic&logoColor=white)](https://music.apple.com/)
[![Spotify](https://img.shields.io/badge/Spotify-Supported-1DB954?logo=spotify&logoColor=white)](https://open.spotify.com/)

Melomaniac is a desktop application for syncing, rebuilding, downloading, and playing playlists between **YouTube Music, Apple Music, and Spotify**, as well as managing local music files.

The documented working version is `4.8.0`. Detailed changes for each version are documented in [`CHANGELOG.md`](CHANGELOG.md).

---

## Key Features

- 🔄 **Playlist Management & Transfer:** Rebuild and transfer playlists between YouTube Music, Apple Music, and Spotify. Supports local playlist files (`M3U/M3U8`, `PLS`, `WPL`, `XSPF`, `CSV`, `TXT`), audio files (`MP3`, `FLAC`, `AAC`, `OGG`, `WAV`, `M4A`, `OPUS`), or pasted plain text.
- 🎯 **Intelligent Matching Engine:** Multi-stage matching using RapidFuzz, exact ISRC matching (Apple Music), duration comparisons, and explicit tags. Features Cyrillic transliteration, single-flight deduplication (`SingleFlight`), and an interactive Post-Mortem view to resolve alternative candidates.
- 📚 **Unified Library:** Browse both your cloud streaming playlists and local music folders in a single place. Create streaming playlists directly from any local folder on your disk.
- 🎵 **Integrated Audio Player:** Persistent bottom player bar for local audio files (FLAC, MP3, M4A, Opus, WAV) with reorderable queue (`PlayQueue`), scrubbing support, shuffle/repeat modes, and persistent volume.
- ⬇️ **Audio Downloads with Full Metadata:** Download audio tracks in MP3, M4A, Opus, FLAC, or WAV via `yt-dlp`. Normalizes album art to square 800 px, embeds full ID3/Vorbis tags via `mutagen`, checks FFmpeg availability upfront, and automatically skips existing files.
- 🖥️ **Desktop Integration (MPRIS / D-Bus):** Full media player control on Linux via system widgets (Waybar, Quickshell, GNOME, KDE) and keyboard media keys, complete with real-time metadata and cover art.
- 🌐 **Frictionless Browser Sessions:** Automatically detects active sessions in memory from Firefox or Chromium-based browsers without needing manual cookie JSON exports.
- 🛠️ **Advanced Organization & Export:** List, Dual, and Preview view modes. Sort and group by artist, album, duration, or release date; split playlists into 2 to 5 manual divisions, and export locally to `M3U8`, `CSV`, `TXT`, or `XSPF`.

---

## System Requirements

| Requirement | Details |
|---|---|
| Python | 3.10 or newer |
| Dependencies | Pinned in [`requirements.txt`](requirements.txt) |
| Audio conversion | `ffmpeg` 6+ on `PATH` (required for extraction and conversion in Downloads) |
| Credentials | Active browser session in memory, or manual files in `config/` (`browser.json`, `.env`, `spotify_cookies.json`) |
| UI | Flet Desktop 1.0.5; dark theme, Stack Sans, IBM Plex, and optional CJK fallback |

---

## Installation and Running

### ⚡ Quick Start with One-Click Launchers (Recommended)

The repository includes automated launchers that set up the `.venv` virtual environment, install dependencies on first run, and start the app:

- **Windows:** Double-click `run.bat` (or run `.\run.bat` in terminal).
- **Linux / macOS:** Run in terminal:
  ```bash
  ./run.sh
  ```

---

### 🛠️ Manual Step-by-Step Installation

If you prefer running commands manually:

#### 1. Install Prerequisites (Python and FFmpeg)

<details>
<summary><b>📦 Commands by Operating System</b></summary>

- **Windows (PowerShell):**
  ```powershell
  winget install Python.Python.3.12 Gyan.FFmpeg Git.Git
  ```
  *(Restart your terminal after installation. If installing Python manually from python.org, remember to check **"Add python.exe to PATH"**).*

- **Linux (Debian / Ubuntu / Mint):**
  ```bash
  sudo apt update && sudo apt install -y python3 python3-venv python3-pip ffmpeg git
  ```

- **Linux (Arch / Manjaro):**
  ```bash
  sudo pacman -S python python-pip ffmpeg git
  ```

- **Linux (Fedora):**
  ```bash
  sudo dnf install -y python3 python3-pip ffmpeg git
  ```

- **macOS (via Homebrew):**
  ```bash
  brew install python ffmpeg git
  ```

</details>

#### 2. Clone and Run

##### 🐧 Linux / macOS

```bash
git clone https://github.com/Brimx/Melomaniac.git
cd Melomaniac
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

##### 🪟 Windows

- **Direct Method (avoids PowerShell ExecutionPolicy restrictions):**
  ```powershell
  git clone https://github.com/Brimx/Melomaniac.git
  cd Melomaniac
  python -m venv .venv
  .\.venv\Scripts\pip install -r requirements.txt
  .\.venv\Scripts\python app.py
  ```

- **Traditional Method:**
  ```powershell
  .\.venv\Scripts\Activate.ps1   # In classic CMD: .venv\Scripts\activate.bat
  pip install -r requirements.txt
  python app.py
  ```

---

### 🔄 How to update to a new version?

To update the application when new changes are published in the repository:

1. Open your terminal in the Melomaniac directory and pull the latest changes:
   ```bash
   git pull
   ```
   > [!NOTE]
   > `git pull` only downloads updated source code files. Your credentials (`config/`), personal settings, downloads (`downloads/`), and caches are ignored by Git, so they **are never deleted or modified**.

2. Start the application again:
   - **Windows:** Double-click `run.bat` (or run `.\run.bat`).
   - **Linux / macOS:** Run `./run.sh`.

*(Both `run.bat` and `run.sh` automatically detect if a new version updated `requirements.txt` and will seamlessly install any new dependencies).*

---

### 💡 Quick Troubleshooting

- **PowerShell blocks script activation (`File ... cannot be loaded because running scripts is disabled`)**:
  Use `run.bat` or the **Direct Method** (`.\.venv\Scripts\python app.py`), or enable scripts once in PowerShell:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
- **FFmpeg not detected in Downloads view**:
  Ensure FFmpeg was installed and **restart your terminal or IDE** so your system loads the new `PATH`.
- **`ensurepip is not available` on Ubuntu/Debian**:
  Install the missing package with `sudo apt install python3-venv`.

---

The main screen lets you choose a source and a destination. The source can be one of the three streaming platforms or a local input; the destination is always a streaming platform.

### Configure credentials

Melomaniac automatically detects active sessions directly in memory from your browser (Firefox and Chromium-based browsers), so in most cases **no manual setup is required**.

If you prefer manual credentials or use a non-standard browser, you can use the built-in wizard (accessible via the gear icon in the side rail):

- **YouTube Music:** Saves `Cookie` and `Authorization` headers (with the `SAPISIDHASH` prefix) from `music.youtube.com` into `config/browser.json`.
- **Apple Music:** Saves `APPLE_AUTH_BEARER` and `APPLE_MUSIC_USER_TOKEN` from `music.apple.com` into `config/.env`.
- **Spotify:** Saves your `sp_dc` and `sp_key` cookies into `config/spotify_cookies.json`.

Credentials, preferences, and caches live inside `config/`, which is ignored by `.gitignore`. Never share or commit these files.

### Load a playlist

- **Streaming:** Select the platform, enter the playlist ID or URL, and click **Load**.
- **Local File:** Select a supported playlist file (`M3U/M3U8`, `PLS`, `WPL`, `XSPF`, `CSV`, `TXT`) or an audio file to extract metadata tags.
- **Paste Text:** Paste a track list (one per line, in `Title - Artist` or plain format).

For local inputs, explicitly select your target streaming platform before transferring.

### Review, transfer, and playback

1. Review the tracks and uncheck any you wish to exclude.
2. Optionally click **Organize** to sort/group or **Split** to divide into manual segments (2 to 5 divisions).
3. Toggle between **List**, **Dual**, and **Preview** modes to inspect source and target.
4. Click **Transfer**, confirm name and description, and monitor live progress. When finished, the **Post-Mortem** tab allows searching and inserting alternative tracks for ambiguous results.
5. Play any local or downloaded track by clicking on the row or using the bottom **Player bar**.

If you choose **Local File (Export)** as the destination, you can export your playlist to `M3U8`, `CSV`, `TXT`, or `XSPF`.

## Authentication, APIs, and resilience

- **YouTube Music:** `ytmusicapi` for song search and playlist management.
- **Apple Music:** Web endpoint `amp-api.music.apple.com` with catalog search and ISRC/duration matching.
- **Spotify:** `spotapi` using `tracksV2`, duration in milliseconds, and `explicit` scoring.
- **Player & Audio:** `flet-audio` 1.0.5 (`FLET_DESKTOP_FLAVOR=full`) with `PlayQueue` and `services/mpris_service.py` (Linux D-Bus).
- **Semaphores & Limits:** 2 concurrent global network requests; Apple Music processes transfers sequentially with preventive pauses to avoid HTTP 423/429.
- **Atomic Cache:** `config/search_cache.json` is flushed in batches every 25 entries and on transfer completion.

## Configuration files

| File / Folder | Purpose |
|---|---|
| `config/settings.json` | Default download format, online artwork toggle, and local folders |
| `config/browser.json` | Manual YouTube Music headers |
| `config/.env` | `APPLE_AUTH_BEARER` and `APPLE_MUSIC_USER_TOKEN` tokens |
| `config/spotify_cookies.json` | Spotify `sp_dc` and `sp_key` cookies |
| `config/search_cache.json` | Cached search results (`title\|\|\|artist\|\|\|destination`) |
| `config/transfer_history.jsonl` | Persistent transfer history and resolution events |
| `config/cover_cache/` | Local normalized album artwork cache (800 px) |
| `exports/` | Local exporter output (ignored by Git) |
| `transfer_failed_report.txt` | On-demand UI report generated from Post-Mortem |

## Project structure

```text
Melomaniac/
├── app.py                         # Entry point, service composition, and lifecycle
├── run.bat                        # Automated one-click launcher for Windows
├── run.sh                         # Automated launcher for Linux/macOS
├── config/                        # Ignored runtime credentials, settings, and caches
├── core/
│   ├── availability.py            # Availability scanning and Apple ISRC preload
│   ├── cache.py                   # search_cache keys and compatibility helpers
│   ├── config.py                  # Constants, concurrency limits, and batch sizes
│   ├── library_models.py          # Library playlist summaries and state models
│   ├── library_store.py           # Atomic local persistence for Library metadata
│   ├── models.py                  # Universal Track, PlaylistMeta, SearchResult, and enums
│   ├── playback.py                # PlayQueue and playback state management
│   ├── state.py                   # Centralized AppState (BLoC / Observer)
│   └── transfer.py                # TransferReport and transfer failure tracking
├── engine/
│   ├── audio_metadata.py          # Tag reading and writing with Mutagen
│   ├── covers.py                  # Artwork normalization to 800 px squares
│   ├── downloader.py              # yt-dlp audio download queue with metadata tagging
│   ├── exporters.py               # Local export (M3U8, CSV, TXT, XSPF)
│   ├── match.py                   # Scoring and Hunter Recovery with RapidFuzz
│   ├── normalizer.py              # Metadata cleaning, ISRC, and Cyrillic transliteration
│   ├── organizer.py               # In-memory sorting and segment splitting
│   └── parsers.py                 # Local playlist parsing and Track building
├── services/
│   ├── api_service.py             # Streaming API facade and HTTP session handling
│   ├── authentication.py          # Runtime paths and credential preflights
│   ├── browser_session.py         # In-memory browser cookie extraction
│   ├── circuit_breaker.py         # Monotonic cooldowns and 429 rate-limit protection
│   ├── cover_cache.py             # Atomic artwork cache storage
│   ├── covers.py                  # Multi-source album artwork resolution pipeline
│   ├── library_state.py           # Library sync and state coordination
│   ├── mpris_service.py           # Linux D-Bus MPRIS service for system media controls
│   └── settings_store.py          # User preferences persistence
├── ui/
│   ├── auth_manager.py            # Credential checks, modal, and hot reloading
│   ├── config_view.py             # Settings view (downloads, artwork, paths)
│   ├── config_wizard.py           # Manual credential configuration wizard
│   ├── downloads_view.py          # Downloads queue view with progress and FFmpeg banner
│   ├── fonts.py                   # Unicode script detection and font registration
│   ├── library_view.py            # Library view with streaming and local music folders
│   ├── main_ui.py                 # Main window, responsive navigation rail, view modes
│   ├── organize_panel.py          # Organize, Split, and Reorder dialogs
│   ├── player_bar.py              # Interactive persistent bottom player bar
│   ├── player_queue.py            # Reorderable playback queue drawer
│   ├── playlist_card.py           # Visual playlist cards
│   ├── playlist_detail_view.py    # Playlist detail view with on-demand track loading
│   ├── playlist_meta_dialog.py    # Playlist name and description editor
│   ├── song_row.py                # Track rows with animated skeleton loaders
│   ├── telemetry.py               # Transfer monitor, live console, and Post-Mortem
│   ├── tokens.py                  # Color palette, spacing, border radii, and animations
│   ├── transfer_report.py         # Completion report and alternative track insertion
│   └── widgets.py                 # Reusable styled UI controls
└── resources/fonts/               # Bundled Stack Sans and IBM Plex fonts
```

Fonts are selected by script: Stack Sans for Latin text, IBM Plex Sans for
Cyrillic, IBM Plex Mono for technical data, and Noto CJK when available.
Without a registered CJK font, Flet uses the native fallback.

## Verification

The quick Python module check is:

```bash
python -m compileall -q app.py core engine services ui
```

The Flet UI is validated manually, especially the view modes, Organize/Split dialogs, responsive navigation, and export dialog. The `tests/` directory remains available for future regressions.

## Legal notice

Melomaniac is a local application for managing, organizing, transferring, and discovering music using information provided by third-party services.

Melomaniac does not host, provide, or redistribute copyrighted music content. File-acquisition features use third-party tools and sources and do not imply direct access to streaming service content.

The user is responsible for ensuring that any content obtained, stored, or used through Melomaniac is authorized for that use and for complying with applicable laws and the terms of the source services.

Melomaniac is not affiliated with, sponsored by, or endorsed by Spotify, Apple, Google, YouTube, or any other third-party service.

## Status and license

The documented version is `4.8.0` on the `main` branch. This is a personal-use project; each platform and its APIs are subject to their own terms of service.

## Thanks

[Flet](https://flet.dev) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) · [spotapi](https://github.com/spotapi) · [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) · [Mutagen](https://mutagen.readthedocs.io/)
