# 🎵 MelomaniacPass v5.0

**Transfiere playlists entre YouTube Music, Apple Music y Spotify mediante matching inteligente con ISRC y duración.**

MelomaniacPass es una app de escritorio que carga una playlist desde YouTube Music, Apple Music, Spotify o fuente local, encuentra sus canciones en la plataforma destino y crea una nueva playlist. El motor **Hunter Recovery** tolera diferencias de títulos, artistas, remasterizaciones y versiones en vivo usando tupla triple `(título, artista, duración_ms, isrc)`.

> **¿Por qué existe?** Las plataformas no ofrecen exportación universal. MelomaniacPass reconstruye con scoring fuzzy + duración + `isrc` y deja reporte post-mortem.

---

## ✨ Características

- **Transferencia 3 plataformas** — YouTube Music ↔ Apple Music ↔ Spotify (via `spotapi`).
- **Fuentes locales** — CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML y texto plano.
- **Hunter Recovery** — queries alternativas (`clean_metadata` + `_normalize_title`), tupla triple `título/artista/duración/isrc` y scoring `score_spotify_match` (60 fuzzy +30 duración +10 explicit).
- **Concurrencia controlada** — `GLOBAL_API_SEMAPHORE=2` + `transfer_sem` 2 (Apple) /3 (otros) para cuidar APIs.
- **Post-mortem** — coincidencias, no encontradas, errores y `revision_necesaria` (<40%); exporta `transfer_failed_report.txt`.
- **Wizard guiado 3 tabs** — YouTube (`browser.json`), Apple (`.env`), Spotify (`spotify_cookies.json` con `sp_dc/sp_key`).
- **Protección 429/423** — `CircuitBreaker` por plataforma, `_am_check_status` (423 → 120s mínimo) y `_sp_is_rate_limited`, `SPOTIFY_ADD_CHUNK=50` con retry exponencial.
- **Caché persistida** — `resources/search_cache.json` permite reanudar tras 429/cierre sin re-buscar.
- **Organizar y dividir** — ordena (`engine/organizer.sort_tracks`) o agrupa (`split_tracks`) por artista/álbum/título/duración/plataforma.
- **UI Flet** — búsqueda, selección, progreso, telemetría docked/overlay, estados por canción, fuentes IBM Plex Sans locales.

## 📋 Requisitos

| Requisito | Detalle |
|---|---|
| Python | 3.10+ |
| OS | Linux, macOS o Windows |
| Dependencias | `flet==0.86.5`, `ytmusicapi==1.12.1`, `spotapi==1.2.8`, `requests`, `python-dotenv`, `rapidfuzz` (ver `requirements.txt`) |
| Credenciales | YouTube `browser.json`, Apple `.env`, Spotify `spotify_cookies.json` |

## 📦 Instalación

```bash
git clone https://github.com/Brimx/MelomaniacPass.git
cd MelomaniacPass
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

## 🚀 Uso

```bash
python app.py
```

### 1. Configurar credenciales
Pre-flight valida en paralelo las 3 plataformas al iniciar. Si falla, abre el wizard en la pestaña correspondiente.

**YouTube Music:** `music.youtube.com` → DevTools Network `browse` → copia `Authorization` (SAPISIDHASH) + `Cookie` → wizard.

**Apple Music:** `music.apple.com` → Network `catalog` → copia `Authorization Bearer` + `media-user-token` → wizard.

**Spotify:** `open.spotify.com` → DevTools Application → Cookies → copia `sp_dc`, `sp_key` e `identifier` → `spotify_cookies.json` via wizard.

No commitees `.env`/`browser.json`/`spotify_cookies.json` (en `.gitignore`).

### 2. Cargar playlist
- **Streaming:** elige plataforma, pega ID (`pl.u-...`/`37i9dQ...`/`p.xxx`) y **Cargar**.
- **Archivo local:** `Archivo Local` → elige archivo → asigna nombre.
- **Texto:** `Pegar Texto` → una por línea `Título - Artista`.

### 3. Revisar y transferir
Selecciona canciones, usa **Organizar/Dividir**, elige destino y **Transferir**. Fuentes locales exigen destino confirmado.

### 4. Resultados
Progreso y telemetría en vivo. **Ver Detalles** abre Post-Mortem. Exporta TXT.

## 🔧 Archivos de configuración

| Archivo | Plataforma | Contenido |
|---|---|---|
| `.env` | Apple | `APPLE_AUTH_BEARER`, `APPLE_MUSIC_USER_TOKEN` |
| `browser.json` | YouTube | `Authorization`, `Cookie`, `x-origin` |
| `spotify_cookies.json` | Spotify | `{identifier, cookies:{sp_dc, sp_key}}` |
| `resources/search_cache.json` | Cache | `{key: {track_id, needs_review, low_confidence, isrc}}` |

## 📁 Estructura

```
melomaniacpass/
├── app.py                 # Entry, composición, hard cleanup
├── auth_manager.py        # Credenciales, pre-flight y wizard 3 tabs
├── core/models.py         # Track (duration_ms/is_explicit), SearchResult(isrc)
├── core/state.py          # AppState BLoC, transfer+segments, cache_key
├── services/api_service.py# Facade spotapi/ytmusicapi/amp-api, hunters, chunks
├── engine/normalizer.py   # clean_metadata, umbrales FUZZY_IDEAL 85
├── engine/match.py        # triple scores, score_spotify_match, _yt_select_best
├── engine/parsers.py      # CSV/M3U/XSPF/WPL/PLS + build_local_tracks
├── engine/organizer.py    # sort_tracks / split_tracks
├── ui/main_ui.py          # PlaylistManagerUI, organize/split dialogs
├── ui/song_row.py         # SongRow/SkeletonRow ITEM_H=64
├── ui/telemetry.py        # Monitor/Consola/Post-Mortem docked/overlay
├── ui/widgets.py          # _primary_btn, _ghost_btn, _status_icon
├── utils/circuit_breaker.py # CircuitBreaker, RateLimitError
└── resources/fonts/       # IBM Plex Sans w300-700
```

Ver [ARCHITECTURE.md](ARCHITECTURE.md) para flujos y responsabilidades.

## Estado actual

Rama `main` (merge `beta`). 3 plataformas + 2 fuentes locales. Búsqueda Apple usa `amp-api/music.apple.com` oficial (con ISRC), Spotify usa `spotapi` `searchV2/tracksV2` con `totalMilliseconds/explicit`. Sin tests automatizados; validación manual via UI.

## 📜 Licencia

Uso personal. APIs sujetas a términos de cada plataforma.

## 🙏 Agradecimientos

- [Flet](https://flet.dev) - [ytmusicapi](https://github.com/sigma67/ytmusicapi) - [spotapi](https://github.com/spotapi) - [RapidFuzz](https://github.com/maxbachmann/RapidFuzz)
