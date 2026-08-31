# MelomaniacPass v3.2.0 — Technical Architecture

App de escritorio para transferir playlists entre **YouTube Music, Apple Music y Spotify** + fuentes locales (CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML, texto) con **Hunter Recovery** en tupla triple `(título, artista, duración_ms, isrc)`.

---

## Estructura

```
melomaniacpass/
├── app.py                    # Entry, composición CircuitBreakers→Service→State→UI, hard cleanup
├── auth_manager.py           # Credenciales, wizard 3 tabs, pre-flight paralelo 3 plataformas
├── .env                      # Apple Music (runtime)
├── browser.json              # YouTube Music headers (runtime)
├── spotify_cookies.json      # Spotify {identifier, cookies:{sp_dc,sp_key}} (runtime)
├── resources/search_cache.json # Caché persistida {track_id,needs_review,low_confidence,isrc}
│
├── core/
│   ├── models.py             # Track(duration_ms,is_explicit), SearchResult(isrc), LoadState/TransferState
│   └── state.py              # AppState BLoC, transfer, segments, lazy_scan, cache_key
│
├── services/
│   └── api_service.py        # MusicApiService: facade spotapi(Song/Login/PublicPlaylist/PrivatePlaylist) + ytmusicapi + amp-api
│
├── engine/
│   ├── normalizer.py         # clean_metadata, _normalize_title, FUZZY_IDEAL 85, ARTIST_EXACT 99
│   ├── match.py              # triple scores, _ideal_pass_hunter, score_spotify_match, validar_match, _yt_select_best
│   ├── parsers.py            # parse_local_playlist (detección por contenido) + build_local_tracks
│   └── organizer.py          # sort_tracks / split_tracks (memoria)
│
├── ui/
│   ├── main_ui.py            # PlaylistManagerUI, organize/split dialogs, _on_state_changed
│   ├── song_row.py           # SongRow/SkeletonRow ITEM_H=64, hover, _status_icon
│   ├── telemetry.py          # TelemetryDrawer docked>=700 / overlay handle, Monitor/Consola/Post-Mortem
│   └── widgets.py            # _primary_btn/_ghost_btn/_section_label/_status_icon
│
├── utils/
│   └── circuit_breaker.py    # CircuitBreaker (trip/check_or_raise/remaining/cancel/_auto_reset) + RateLimitError
│
└── resources/fonts/          # IBM Plex Sans w300-700 locales (Flet 0.86.5)
```

## Plataformas

| Plataforma | Tipo | Auth | Archivo |
|---|---|---|---|
| YouTube Music | Streaming | `SAPISIDHASH` + `Cookie` | `browser.json` |
| Apple Music | Streaming | `Bearer` + `media-user-token` | `.env` |
| Spotify | Streaming | `sp_dc` + `sp_key` + `identifier` via `spotapi.Login` | `spotify_cookies.json` |
| Archivo Local / Pegar Texto | Local | — | — |

Local → `Track(platform="local")` → transferible a cualquiera de las 3.

## Flujo de Dependencias

```
app.py
  ├── CircuitBreaker por plataforma (AppState.PLATFORMS)
  ├── MusicApiService ── spotapi Song/Login, YTMusic, requests.Session, GLOBAL_SEMAPHORE=2, SEARCH_CACHE, SPOTIFY_ADD_CHUNK=50
  ├── AppState ───────── models, progreso, transfer_sem 2/3, segments
  ├── PlaylistManagerUI ─ filas, diálogos, telemetría
  └── AuthManager ─────── wizard 3 tabs, pre-flight paralelo

engine/normalizer → engine/match → core/models
engine/parsers → core/models
core/state ↔ services/api_service ↔ ui
utils/circuit_breaker → core/state, services/api_service
```

Init `app.py:143-147` `CircuitBreakers → Service(state.cb) → State(service) → UI(page,state) → AuthManager(page,service,state)` con inyección `ui.auth_manager` / `service.auth_manager`.

## Librerías

| Librería | Uso |
|---|---|
| `flet==0.86.5` | Ventana, controles, tema OLED |
| `spotapi==1.2.8` | Spotify `Song.query_songs`, `Public/PrivatePlaylist`, `Login.from_cookies` |
| `ytmusicapi==1.12.1` | YouTube Music `search`/`get_playlist` |
| `requests` | Apple Music `amp-api/music.apple.com`, storefront, pre-flight |
| `rapidfuzz` | `token_sort_ratio` para triple scores |
| `python-dotenv` | `.env` read/write |
| `asyncio` | hunters, transfer, lifecycle |

Ver `requirements.txt` completo.

## Autenticación

### `.env` Apple
```env
APPLE_AUTH_BEARER="Bearer eyJ..."
APPLE_MUSIC_USER_TOKEN="0.As..."
```

### `browser.json` YouTube
```json
{"Accept":"*/*","Authorization":"SAPISIDHASH ...","Content-Type":"application/json","X-Goog-AuthUser":"0","x-origin":"https://music.youtube.com","Cookie":"..."}
```

### `spotify_cookies.json` Spotify
```json
{"identifier":"user@mail.com","cookies":{"sp_dc":"...","sp_key":"..."}}
```

`auth_manager.py` centraliza `read/write` de los 3. `services/api_service.py:374 _sync_init_spotify` hace `Login.from_cookies(dump, Config(NoopLogger()))` y cachea `self._sp_song = Song(client=cfg.client)` para reutilizar `TLSClient` (evita 5-8 req de setup por búsqueda).

**Pre-flight paralelo** `AuthManager.run_startup_check()` valida YT (`YTMusic.get_history`), Apple (`/v1/me/storefront` + `/v1/catalog/.../search`) y Spotify (`Login.logged_in`). Actualiza `AppState.auth_session_ok/hint` y abre wizard en tab fallida.

Durante matching, Apple usa `api.music.apple.com/v1/catalog/{storefront}/search?types=songs` oficial (devuelve `durationInMillis` + `isrc` para tupla triple y tie-break). Spotify usa `searchV2/tracksV2` con `duration.totalMilliseconds` + `explicit`. No se usa iTunes Search.

## Comunicación

### Init `app.py`

```python
circuit_breakers = {p: CircuitBreaker(p) for p in AppState.PLATFORMS}
service = MusicApiService(circuit_breakers)
state = AppState(service)  # state.cb sobrescribe service._cb (single source)
ui = PlaylistManagerUI(page, state)
auth_manager = AuthManager(page, service, state)
ui.auth_manager = service.auth_manager = auth_manager
```

### Observer

`AppState.subscribe(notify)` → `PlaylistManagerUI._on_state_changed()` refresca `display_tracks`, progreso, `auth strip`, `segments`, telemetría.

### Búsqueda

```
ui → state.transfer_playlist() → _transfer_one(track) con cache_key `cn|||ca|||dest`
state → _search_with_exponential_rl_backoff (fail-fast, trip breaker, raise)
service → search_with_fallback 3 passes (clean, raw, normalized) → search_track → _*_hunter_async
engine/match → triple scores + _ideal_pass_hunter (85 o artist 99 + title 60) → SearchResult(track_id, needs_review, low_confidence, isrc)
state → Track.transfer_status
service → create_playlist chunk 50 + retry 4x exp
ui → progreso + Post-Mortem
```

## Módulos Clave

### `utils/circuit_breaker.py`
`CircuitBreaker.trip(retry_after)` abre `is_open`, guarda `monotonic+wait`, `notify` y `asyncio.create_task(_auto_reset)`. `check_or_raise` lanza `RateLimitError`. `cancel` limpia task huérfana. `remaining` con `monotonic`.

### `services/api_service.py`
Unifica carga/búsqueda/creación. `GLOBAL_API_SEMAPHORE=2`, `SEARCH_CACHE_JSON`, `SPOTIFY_ADD_CHUNK=50`. `_am_check_status` convierte `429/423` en `RateLimitError` (423 → min 120s). `_sp_is_rate_limited` detecta `Status Code: 429/423` de `spotapi`. `_load/save_search_cache` con tmp+replace atómico. Socket reuse `requests.Session`.

### `core/state.py` / `models.py`
`Track` con `duration_ms/is_explicit` para scoring exacto. `SearchResult` con `isrc`. `AppState` coordina `load_playlist`, `transfer_playlist` (semáforo 2/3), `apply_search`, `organize_sort/split`, `lazy_scan`.

### `engine/match.py` Hunter Recovery
Capas: `validar_match` L0 CJK bypass, L1 substring, L2 lethal `cover/karaoke`, L3 fuzzy 0.65. `_fuzzy_scores_triple` → `comb/tit/art`. `_ideal_pass_hunter` (≥85). `score_spotify_match` 60 fuzzy (40 tit+20 art) +30 duración (≤2s 30, ≤5s 15 else -20) +10 explicit. `_yt_select_best` top3 + `resultType==song` + duración ±5s.

### `engine/normalizer.py`
`clean_metadata` NFC + purga brackets + `PURGE_NOISE_WORDS` + `strip_noise`. `build_search_query` prior. obra. Umbrales `FUZZY_IDEAL 85 / LOG 70 / REVIEW 40`.

### `engine/parsers.py` / `organizer.py`
`parse_local_playlist` detecta por extensión y contenido, `build_local_tracks` → `Track`. `sort_tracks`/`split_tracks` in-memory.

### `ui/main_ui.py` / `telemetry.py` / `widgets.py`
`main_ui` maneja `DROPDOWN PLATFORMS`, `Organizar/Dividir`, `segment_dd`, skeletons `ITEM_H=64`. `telemetry` docked/overlay con `Monitor/Consola/Post-Mortem`. `widgets` tokens `ACCENT/SUCCESS`.

## ConfigWizard

| Tab | Plataforma | Campos | Archivo |
|---|---|---|---|
| 0 | YouTube Music | Authorization, Cookie | `browser.json` |
| 1 | Apple Music | Bearer, User Token | `.env` |
| 2 | Spotify | identifier, sp_dc, sp_key | `spotify_cookies.json` |

`Guardar y Aplicar` → `reload_credentials()` sin reiniciar.

## Ciclo de Vida

`app.py:173 _auth_poll_loop` cada 90s `refresh_session_icons`. `194 hard_cleanup` cancela breakers/UI/lazy/auth, `asyncio.all_tasks().cancel()`, `gc.collect()`, `cleanup_sessions()` en `to_thread`, `force_exit 3s` vía `os._exit`.

## Métricas

| Área | Estado |
|---|---|
| Plataformas | 3 streaming (YT, Apple, Spotify) +2 locales |
| Engine | 4 módulos |
| Concurrencia | global 2, transfer 2/3 |
| Resiliencia | breaker 429/423, cache persistida, chunk 50 |

