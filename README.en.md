# 🎵 MelomaniacPass

**Transfer playlists between YouTube Music and Apple Music with intelligent track matching.**

MelomaniacPass is a desktop application that loads a playlist from YouTube Music, Apple Music, or a local source, finds its tracks on the destination platform, and creates a new playlist there. Fuzzy matching tolerates differences in titles, artists, remasters, and live versions.

> **Why does it exist?** Streaming platforms do not provide a universal way to export and import playlists between services. MelomaniacPass automates the reconstruction and reports matches, failures, and items that need review.

## ✨ Features

- **Cross-platform transfer** — YouTube Music ↔ Apple Music.
- **Local sources** — CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML, and plain text.
- **Hunter Recovery** — alternate queries, metadata normalization, and fuzzy scoring.
- **Concurrent transfer** — processes multiple tracks with a concurrency limit to protect APIs.
- **Post-mortem report** — shows matches, missing tracks, errors, and failure reasons; exports the report.
- **Guided configuration** — wizard to save and reload credentials without restarting.
- **Rate-limit protection** — circuit breakers, exponential backoff, and visible timers.
- **Organize and split** — sort or group tracks by artist, album, title, duration, or platform.
- **Desktop UI** — Flet interface with search, selection, progress, telemetry, and per-track status.

## 📋 Requirements

| Requirement | Version / detail |
|---|---|
| Python | 3.10+ |
| Operating system | Linux, macOS, or Windows |
| Dependencies | `flet`, `ytmusicapi`, `requests`, `python-dotenv`, `rapidfuzz` |
| Credentials | YouTube Music session and/or Apple Music tokens for remote sources |

The repository does not yet include `requirements.txt` or `pyproject.toml`; install dependencies manually for now.

## 📦 Installation

```bash
git clone https://github.com/Brimx/MelomaniacPass.git
cd MelomaniacPass

python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

pip install flet ytmusicapi requests python-dotenv rapidfuzz
```

## 🚀 Usage

```bash
python app.py
```

### 1. Configure credentials

On startup, the pre-flight check validates available sessions. If a credential is missing or expired, the **Configuration Wizard** opens on the relevant platform tab.

**YouTube Music:** open [music.youtube.com](https://music.youtube.com), use DevTools (`F12`) → **Network**, find an API request such as `browse`, copy the `Authorization` and `Cookie` headers, then save them in the wizard.

**Apple Music:** open [music.apple.com](https://music.apple.com), use DevTools → **Network**, find a catalog request, copy `Authorization` and `media-user-token`, then save them in the wizard.

Session credentials are sensitive data. Never share them or commit them to Git.

### 2. Load a playlist

- **Streaming:** choose YouTube Music or Apple Music, enter a playlist ID, and click **Load**.
- **Local file:** choose **Local File**, select a supported file, and assign a name.
- **Pasted text:** choose **Paste Text** and paste one track per line, preferably as `Title - Artist` or `Artist - Title`.

### 3. Review and transfer

Select tracks, use **Organize** or **Split** if needed, choose the destination platform, and click **Transfer**. Local sources require a destination before transfer.

### 4. Review results

The UI displays live progress and telemetry. When complete, **View Details** opens the Post-Mortem panel with matches, missing items, errors, and low-confidence cases. The report can be exported as `transfer_failed_report.txt`.

## 🔧 Configuration files

Both files are excluded by `.gitignore` and managed by the wizard:

| File | Platform | Contents |
|---|---|---|
| `.env` | Apple Music | `APPLE_AUTH_BEARER` and `APPLE_MUSIC_USER_TOKEN` |
| `browser.json` | YouTube Music | Session headers, including `Authorization` and `Cookie` |

## 📁 Project structure

```text
melomaniacpass/
├── app.py              # Entry point, composition, and cleanup
├── auth_manager.py     # Credentials, pre-flight, and wizard
├── core/               # Application models and observable state
├── services/           # YouTube Music and Apple Music API facade
├── engine/             # Normalization, matching, parsers, and organization
├── ui/                 # Flet UI, rows, widgets, and telemetry
├── utils/              # Circuit breaker and rate-limit errors
└── resources/fonts/    # Bundled IBM Plex Sans fonts
```

See **[ARCHITECTURE.en.md](ARCHITECTURE.en.md)** for module responsibilities, dependencies, and data flows. The Spanish version is **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Current status

The current branch is `beta`. The codebase is modular and includes the main loading, matching, transfer, authentication, telemetry, and resource-cleanup flows. Apple Music candidate search uses the public iTunes Search API to reduce 423/429 blocks; authenticated operations still use Apple Music. No automated tests or packaging configuration are visible in the repository; current validation is done by running the application and checking UI/API flows.

## 📜 License

For personal use. Access to YouTube Music and Apple Music depends on their respective APIs, sessions, and terms of service.

## 🙏 Thanks

- [Flet](https://flet.dev) — Python UI framework.
- [ytmusicapi](https://github.com/sigma67/ytmusicapi) — YouTube Music wrapper.
- [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) — high-performance fuzzy matching.
