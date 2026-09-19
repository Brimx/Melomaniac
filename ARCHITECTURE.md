# MelomaniacPass v3.3.8 — Arquitectura

MelomaniacPass separa la interfaz Flet, el estado de la aplicación, los servicios de plataforma y el motor de normalización/matching. La versión actual soporta YouTube Music, Apple Music, Spotify y dos entradas locales: archivo y texto pegado.

## Capas y dependencias

```text
app.py
├── AppState                 # estado reactivo, progreso y transferencia
├── MusicApiService          # APIs, caché, sesiones y creación de playlists
├── AuthManager              # pre-flight, wizard y hot reload
└── PlaylistManagerUI        # Flet, eventos, filas y telemetría

ui ───────> core / engine / services
core ─────> engine / services.circuit_breaker
services ─> core / engine
engine ───> core.models
```

La composición real en `app.py` es:

```python
state = AppState(service=None)
service = MusicApiService(state.cb)
state.service = service
ui = PlaylistManagerUI(page, state)
auth_manager = AuthManager(page, service, state)
ui.auth_manager = auth_manager
service.auth_manager = auth_manager
```

`AppState` crea los circuit breakers una sola vez y los comparte con `MusicApiService`. Así, las operaciones de carga, búsqueda y creación usan el mismo cooldown por plataforma.

## Estructura

```text
MelomaniacPass/
├── app.py
├── config/                         # runtime ignorado por Git
├── core/
│   ├── availability.py             # escaneo lazy y precarga ISRC de Apple
│   ├── cache.py                    # make_cache_key y compatibilidad legacy
│   ├── config.py                   # plataformas, límites y tamaños de lote
│   ├── models.py                   # Track, PlaylistMeta, SearchResult, enums
│   ├── state.py                    # AppState y flujo BLoC/Observer
│   └── transfer.py                 # búsqueda con rate limit y errores breves
├── engine/
│   ├── audio_metadata.py           # tags de audio con Mutagen
│   ├── match.py                    # validación, scoring y Hunter Recovery
│   ├── normalizer.py               # limpieza, ruido e ISRC
│   ├── organizer.py                # sort_tracks y split_tracks en memoria
│   └── parsers.py                  # formatos locales y Track local
├── services/
│   ├── api_service.py              # fachada YouTube/Apple/Spotify
│   ├── authentication.py           # archivos runtime y pre-flight
│   └── circuit_breaker.py          # RateLimitError y cooldowns
├── ui/
│   ├── auth_manager.py             # coordinación de autenticación
│   ├── config_wizard.py             # edición de credenciales
│   ├── main_ui.py                  # ventana principal
│   ├── playlist_meta_dialog.py      # nombre/descripción de playlist
│   ├── song_row.py                 # filas y skeletons
│   ├── telemetry.py                # Monitor, Consola y Post-Mortem
│   ├── tokens.py                   # tokens de diseño
│   └── widgets.py                  # controles reutilizables
├── resources/fonts/                # IBM Plex Sans w300–w700
└── tests/                           # unittest y regresiones
```

## Modelos y estado

`Track` es el modelo universal. Además de título, artista, álbum, duración, plataforma y portada, puede incluir `duration_ms`, `is_explicit`, `isrc`, `source_path`, `album_artist`, `track_number` y `release_date`.

`PlaylistMeta` conserva el nombre y la descripción de la playlist de origen. `SearchResult` conserva `track_id`, `needs_review`, `low_confidence` e `isrc`.

Estados principales:

- `LoadState`: `IDLE → LOADING_META → LOADING_TRACKS → READY` o `ERROR`.
- `TransferState`: `IDLE → RUNNING → DONE` o `ERROR`.
- `Track.transfer_status`: `pending`, `searching`, `found`, `not_found`, `revision_necesaria`, `transferred`, `error` y `local_pending`.

`AppState` mantiene la lista maestra, filtro, segmentos, selección, contadores, logs, fallos, resultados pendientes de revisión y flags de sesión. La UI se suscribe mediante `subscribe()` y recibe actualizaciones con `notify()`.

## Plataformas y autenticación

| Plataforma | Lectura/búsqueda | Creación | Credenciales |
|---|---|---|---|
| YouTube Music | `ytmusicapi` | `YTMusic.create_playlist` | `config/browser.json` |
| Apple Music | catálogo web `amp-api.music.apple.com` | endpoint de biblioteca | `config/.env` |
| Spotify | `spotapi`/`tracksV2` | `PrivatePlaylist` | `config/spotify_cookies.json` |
| Archivo Local | parsers + Mutagen | — | — |
| Pegar Texto | parser de líneas | — | — |

`services.authentication` es el único dueño de las rutas y la persistencia de credenciales:

- YouTube: `Authorization` y `Cookie`; los campos fijos (`Accept`, `Content-Type`, `X-Goog-AuthUser`, `x-origin`) se escriben automáticamente.
- Apple: `APPLE_AUTH_BEARER` y `APPLE_MUSIC_USER_TOKEN`.
- Spotify: `identifier`, `cookies.sp_dc` y `cookies.sp_key`.

`run_preflight()` valida las tres plataformas en paralelo. `AuthManager` refleja el resultado en `AppState.auth_session_ok` y `auth_session_hint`, inicializa los servicios válidos y abre el tab correspondiente si una sesión está ausente o expirada. Guardar en el wizard activa un hot reload sin reinicio.

## Flujo de carga

```text
UI selecciona origen + ID
        │
        ├── streaming → AppState.load_playlist()
        │                 → MusicApiService.fetch_playlist()
        │                 → PlaylistMeta + list[Track]
        │
        └── local → parser por extensión/contenido
                    → Mutagen si hay ruta de audio
                    → AppState.load_local_tracks()
```

Los parsers aceptan `TXT`, `CSV`, `M3U/M3U8`, `PLS`, `WPL`, `XSPF` y XML compatible con XSPF. Las rutas locales solo se resuelven para extensiones de audio conocidas; las URLs remotas no se leen como archivos.

## Flujo de búsqueda y transferencia

```text
selección de Track
  → make_cache_key(título, artista, destino)
  → caché o búsqueda con rate-limit
  → normalización + hunter de la plataforma
  → SearchResult y estado visual
  → IDs válidos agrupados
  → create_playlist(nombre, descripción, IDs)
  → confirmación, rechazados y Post-Mortem
```

`search_with_fallback()` intenta, sin duplicar consultas equivalentes:

1. metadatos limpios;
2. título/artista originales;
3. título normalizado.

YouTube usa consultas de canciones y valida hasta los primeros tres candidatos, prefiriendo `resultType == song` y la duración más cercana en un margen de cinco segundos. Apple usa el catálogo web y selecciona por similitud y duración. Spotify consulta `tracksV2`, puntúa 40 puntos de título + 20 de artista, 30 de duración y 10 de coincidencia de `explicit`.

Antes de buscar en Apple se resuelven ISRC en lotes secuenciales de `25`. Las coincidencias exactas se escriben en la caché.

El matching común usa estos umbrales:

| Umbral | Uso |
|---:|---|
| `85` | match ideal |
| `70–84` | baja confianza pero aceptable para streaming |
| `<40` | `needs_review` / `revision_necesaria` |
| `99` de artista + `60` de título | salvamento por artista exacto |

En pistas locales, un resultado `low_confidence` también se rechaza para evitar transferencias ambiguas.

## Concurrencia, caché y resiliencia

Los valores están centralizados en `core/config.py`:

| Constante | Valor | Uso |
|---|---:|---|
| `NETWORK_CONCURRENCY` | `2` | semáforo global de API |
| `TRANSFER_CONCURRENCY[Apple Music]` | `2` | límite de transferencia Apple |
| `TRANSFER_CONCURRENCY[default]` | `3` | límite de transferencia restante |
| `APPLE_ISRC_BATCH` | `25` | consulta de ISRC |
| `APPLE_TRANSFER_BATCH` | `100` | creación/inserción Apple |
| `APPLE_REQUEST_BURST` | `50` | contador preventivo Apple |
| `APPLE_REQUEST_PAUSE` | `60 s` | pausa tras el burst |
| `SPOTIFY_ADD_CHUNK` | `50` | inserción Spotify |

La caché se carga al iniciar desde `config/search_cache.json` y se guarda con archivo temporal + `os.replace`. Soporta objetos `SearchResult` serializados y valores legacy que solo contienen un `track_id`.

`CircuitBreaker` usa reloj monotónico, notifica el estado, crea un auto-reset y puede cancelar su tarea durante el cierre. `429` abre el breaker; Apple convierte `423` en rate limit con mínimo de `120` segundos y trata `401/403` como errores de autenticación. Spotify detecta `429/423` en los errores de SpotAPI.

Apple procesa la transferencia de forma secuencial para evitar ráfagas. El resto usa tareas acotadas por semáforo. Las búsquedas no asociadas a rate limit tienen hasta tres intentos en `AppState`, con esperas de `1` y `2` segundos.

## Ciclo de vida

1. `app.py` prepara `config/`, entorno, ventana, tema y fuentes.
2. Se crean `AppState`, `MusicApiService`, `PlaylistManagerUI` y `AuthManager`.
3. La UI se monta y se ejecuta el pre-flight asíncrono.
4. Un sondeo de sesión actualiza los iconos cada `90` segundos.
5. Al cerrar, se cancelan breakers, escaneo lazy, recargas y tareas, se cierran sesiones HTTP y se libera la UI.

## Verificación

```bash
python -m unittest discover -s tests -p 'test_*.py'
```

La suite actual verifica el layout de paquetes, helpers de caché/rate limit, normalización ISRC, lectura de tags con Mutagen, limitador Apple, lotes Apple y errores de autenticación. La UI Flet se valida manualmente.
