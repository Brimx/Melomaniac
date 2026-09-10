# 🎵 MelomaniacPass v3.3.1

**Transfiere playlists entre YouTube Music, Apple Music y Spotify mediante matching inteligente con ISRC y duración.**

MelomaniacPass es una app de escritorio que carga una playlist desde YouTube Music, Apple Music, Spotify o fuente local, encuentra sus canciones en la plataforma destino y crea una nueva playlist. El motor **Hunter Recovery** tolera diferencias de títulos, artistas, remasterizaciones y versiones en vivo mediante la tupla triple de búsqueda `(título, artista, duración_ms)`; el ISRC se conserva como identificador auxiliar para búsquedas exactas y caché.

> **¿Por qué existe?** Las plataformas no ofrecen exportación universal. MelomaniacPass reconstruye con scoring fuzzy + duración + `isrc` y deja reporte post-mortem.

---

## ✨ Características

- **Transferencia 3 plataformas** — YouTube Music ↔ Apple Music ↔ Spotify (via `spotapi`).
- **Fuentes locales** — CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML y texto plano.
- **Hunter Recovery** — queries alternativas (`clean_metadata` + `_normalize_title`), matching triple `título/artista/duración` y scoring `score_spotify_match` (60 fuzzy +30 duración +10 explicit); el ISRC permite resolver coincidencias exactas cuando está disponible.
- **Concurrencia controlada** — `GLOBAL_API_SEMAPHORE=2` + `transfer_sem` 2 (Apple) /3 (otros) para cuidar APIs.
- **Post-mortem** — coincidencias, no encontradas, errores y `revision_necesaria` (<40%); exporta `transfer_failed_report.txt`.
- **Wizard guiado 3 tabs** — YouTube (`browser.json`), Apple (`.env`), Spotify (`spotify_cookies.json` con `sp_dc/sp_key`).
- **Protección 429/423** — `CircuitBreaker` por plataforma, `_am_check_status` (423 → 120s mínimo) y `_sp_is_rate_limited`, `SPOTIFY_ADD_CHUNK=50` con retry exponencial.
- **Caché persistida** — `resources/search_cache.json` permite reanudar tras 429/cierre sin re-buscar.
- **Organizar y dividir** — ordena (`engine/organizer.sort_tracks`) o agrupa (`split_tracks`) por artista/álbum/título/duración/plataforma.
- **Metadatos visibles** — muestra el álbum junto al título, artista, duración y estado de cada canción.
- **Personalización de playlist** — antes de transferir, permite editar nombre y descripción en un diálogo modal animado; el backdrop cancela al hacer clic fuera de la tarjeta.
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
├── core/models.py         # Track (album/duration_ms/is_explicit), SearchResult(isrc)
├── core/state.py          # AppState BLoC, transfer+segments, cache_key
├── services/api_service.py# Facade spotapi/ytmusicapi/amp-api, hunters, chunks
├── engine/normalizer.py   # clean_metadata, umbrales FUZZY_IDEAL 85
├── engine/match.py        # matching scores, score_spotify_match, _yt_select_best
├── engine/parsers.py      # CSV/M3U/XSPF/WPL/PLS + build_local_tracks
├── engine/organizer.py    # sort_tracks / split_tracks
├── ui/main_ui.py          # PlaylistManagerUI, organize/split dialogs
├── ui/playlist_meta_dialog.py # diálogo modal para nombre/descripción antes de transferir
├── ui/song_row.py         # SongRow/SkeletonRow ITEM_H=64
├── ui/telemetry.py        # Monitor/Consola/Post-Mortem docked/overlay
├── ui/widgets.py          # _primary_btn, _ghost_btn, _status_icon
├── utils/circuit_breaker.py # CircuitBreaker, RateLimitError
└── resources/fonts/       # IBM Plex Sans w300-700
```

Ver [ARCHITECTURE.md](ARCHITECTURE.md) para flujos y responsabilidades.
Consulta [CHANGELOG.md](CHANGELOG.md) para el historial de versiones.

## Estado actual

Versión `3.3.1` en la rama `main`. Incluye el diálogo de personalización de playlist, la columna visible de álbum y la corrección de su backdrop como hijo directo del `Stack` raíz para evitar errores de renderizado en Flet. La búsqueda mantiene la tupla triple de título, artista y duración; Apple usa además ISRC cuando está disponible para resolver coincidencias exactas. Spotify usa `spotapi` `searchV2/tracksV2` con `totalMilliseconds/explicit`. Incluye pruebas unitarias para ISRC, Mutagen y Apple Music; la UI se valida manualmente.

Para ejecutar las pruebas unitarias:

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

## 📜 Licencia

Uso personal. APIs sujetas a términos de cada plataforma.

## 🙏 Agradecimientos

- [Flet](https://flet.dev) - [ytmusicapi](https://github.com/sigma67/ytmusicapi) - [spotapi](https://github.com/spotapi) - [RapidFuzz](https://github.com/maxbachmann/RapidFuzz)
