# Melomaniac v4.7.0 — Arquitectura

Melomaniac separa la interfaz Flet, el estado de la aplicación, los servicios de plataforma y el motor de normalización/matching. La versión actual soporta YouTube Music, Apple Music, Spotify y dos entradas locales: archivo y texto pegado.

La versión de trabajo documentada es `4.7.0`. Esta versión incorpora la descarga de audio con `yt-dlp`, junto con correcciones posteriores a `v4.6.0` en autenticación y Biblioteca.

## Capas y dependencias

```text
app.py
├── AppState                 # estado reactivo, progreso y transferencia
├── MusicApiService          # APIs, caché, sesiones y creación de playlists
├── AuthManager              # pre-flight, wizard y hot reload
└── PlaylistManagerUI        # Flet, navegación, eventos y telemetría
    └── LibraryState         # sincronización/caché de metadatos de Biblioteca

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

`LibraryState` es un estado separado de transferencias. `PlaylistManagerUI` lo crea para Biblioteca y `app.py` inyecta el `MusicApiService` real; la Biblioteca conserva resúmenes de playlists por plataforma, mientras el detalle carga las canciones bajo demanda.

## Estructura

```text
Melomaniac/
├── app.py
├── config/                         # runtime ignorado por Git
├── core/
│   ├── availability.py             # escaneo lazy y precarga ISRC de Apple
│   ├── cache.py                    # make_cache_key y compatibilidad legacy
│   ├── config.py                   # plataformas, límites y tamaños de lote
│   ├── library_models.py           # PlaylistSummary, LibrarySnapshot y estado
│   ├── library_store.py            # persistencia atómica de metadatos locales
│   ├── models.py                   # Track, PlaylistMeta, SearchResult, enums
│   ├── state.py                    # AppState y flujo BLoC/Observer
│   └── transfer.py                 # búsqueda con rate limit y errores breves
├── engine/
│   ├── audio_metadata.py           # tags de audio con Mutagen
│   ├── match.py                    # validación, scoring y Hunter Recovery
│   ├── normalizer.py               # limpieza, ruido e ISRC
│   ├── organizer.py                # sort_tracks y split_tracks en memoria
│   ├── exporters.py                # exportación local en varios formatos
│   ├── downloader.py               # resolución y descarga de audio con yt-dlp
│   └── parsers.py                  # formatos locales y Track local
├── services/
│   ├── api_service.py              # fachada YouTube/Apple/Spotify
│   ├── authentication.py           # archivos runtime y resultado de pre-flight
│   ├── browser_session.py          # detección de navegador y sesión en memoria
│   ├── circuit_breaker.py          # RateLimitError y cooldowns
│   ├── cover_cache.py              # caché de portadas
│   └── library_state.py            # sincronización de la Biblioteca
├── ui/
│   ├── auth_manager.py             # coordinación de autenticación
│   ├── config_wizard.py             # edición de credenciales
│   ├── main_ui.py                  # ventana principal
│   ├── organize_panel.py            # diálogos Organizar/Dividir/Reorganizar
│   ├── library_view.py              # Biblioteca y acceso al módulo Descargas
│   ├── downloads_view.py            # cola, progreso y resultados de descargas
│   ├── playlist_card.py             # tarjetas de playlist
│   ├── playlist_detail_view.py       # detalle y carga de pistas bajo demanda
│   ├── playlist_meta_dialog.py      # nombre/descripción de playlist
│   ├── song_row.py                 # filas y skeletons
│   ├── telemetry.py                # Monitor, Consola y Post-Mortem
│   ├── fonts.py                    # clasificación Unicode y registro de fuentes
│   ├── tokens.py                   # tokens de diseño
│   └── widgets.py                  # controles reutilizables
├── resources/fonts/                # Stack Sans e IBM Plex; CJK opcional
└── tests/                           # reservado para unittest y regresiones
```

## Modelos y estado

`Track` es el modelo universal. Además de título, artista, álbum, duración, plataforma y portada, puede incluir `duration_ms`, `is_explicit`, `isrc`, `source_path`, `source_url`, `genre`, `album_artist`, `track_number` y `release_date`. `PlaylistSummary` y `LibrarySnapshot` mantienen solo metadatos de Biblioteca; no persisten pistas.

`PlaylistMeta` conserva el nombre y la descripción de la playlist de origen. `SearchResult` conserva `track_id`, `needs_review`, `low_confidence` e `isrc`.

Estados principales:

- `LoadState`: `IDLE → LOADING_META → LOADING_TRACKS → READY` o `ERROR`.
- `TransferState`: `IDLE → RUNNING → DONE` o `ERROR`.
- `Track.transfer_status`: `pending`, `searching`, `found`, `not_found`, `revision_necesaria`, `transferred`, `error` y `local_pending`.

`AppState` mantiene la lista de trabajo, `source_tracks`, filtros, segmentos, selección, progreso, logs, fallos y candidatos para revisión. Expone `visible_tracks()` y `selected_in_scope()`; la UI de Organizar/Dividir no tiene actualmente un selector explícito Todo/Visibles/Seleccionadas. `dual_mode` controla `lista`, `doble` y `preview`; la UI escucha cambios con `subscribe()`/`notify()`.

`engine.organizer` permite ordenar internamente por `artist`, `album`, `name`, `duration_ms`, `release_date`, `track_number`, `original_position` y `genre`. `split_tracks` admite `artist`, `album`, `release_date`, `genre` y `name`; los vacíos se agrupan como `Desconocido`. La UI expone un subconjunto: ordenar y agrupar de forma estable, reordenar artistas/álbumes y crear de 2 a 5 divisiones manuales a partir de artistas, álbumes o canciones. `platform` no es criterio.

## Plataformas y autenticación

| Plataforma | Lectura/búsqueda | Creación | Credenciales |
|---|---|---|---|
| YouTube Music | `ytmusicapi` | `YTMusic.create_playlist` | Sesión del navegador (`SAPISID`) o `config/browser.json` |
| Apple Music | catálogo web `amp-api.music.apple.com` | endpoint de biblioteca | Sesión del navegador (`media-user-token` + bearer del bundle) o `config/.env` |
| Spotify | `spotapi`/`tracksV2` | `PrivatePlaylist` | Sesión del navegador (`sp_dc`/`sp_key`) o `config/spotify_cookies.json` |
| Archivo Local | parsers + Mutagen | — | — |
| Pegar Texto | parser de líneas | — | — |

`services.browser_session` resuelve primero las sesiones activas del navegador en memoria (Firefox-family copiando `cookies.sqlite` + `-wal`/`-shm` a RAM con `sqlite3`; Chromium-family en rutas estándar mediante `yt-dlp`) sin escribir nada a disco. `services.authentication` es el único dueño de las rutas runtime y de los archivos de override manual en `config/`:

- YouTube: `Cookie` y `SAPISIDHASH` recalculado en cada lectura desde `SAPISID` (el `Authorization` legacy guardado se ignora); los campos fijos (`Accept`, `Content-Type`, `X-Goog-AuthUser`, `x-origin`) se completan automáticamente.
- Apple: `media-user-token` de la cookie y bearer derivado del bundle público (`kid == "WebPlayKid"` e `iss == "AMPWebPlay"`) o `APPLE_AUTH_BEARER` / `APPLE_MUSIC_USER_TOKEN` en `config/.env`.
- Spotify: `cookies.sp_dc` y `cookies.sp_key` (con `identifier` opcional en `config/spotify_cookies.json`).

`AuthManager` valida las tres plataformas mediante `MusicApiService.init_youtube()`, `init_apple()` e `init_spotify()`, que iteran los candidatos (`*_candidates()`) y verifican contra la API real (`/v1/me/storefront` en Apple, `get_library_playlists(limit=1)` en YouTube y `_spotify_library_page(limit=1)` en Spotify). El preflight del arranque registra el origen ganador y actualiza los iconos sin abrir automáticamente el wizard. YouTube distingue rechazo de sesión (`401`/`403`) de límite (`429`) y fallos transitorios; estos últimos no cambian el estado de autenticación. Guardar o reintentar en el wizard vacía la caché de `browser_session` y recarga sin reinicio.

## Flujo de carga

```text
UI selecciona origen + ID o URL
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

`engine.exporters` es el espejo de los parsers para la salida local. Exporta `TXT`, `CSV`, `M3U`, `M3U8` y `XSPF`, respeta el orden `artist-title` o `title-artist`, sanea el nombre de archivo y conserva el ISRC en XSPF. La UI lo expone como el destino `Archivo Local (Exportar)` y usa `FilePicker` cuando está disponible.

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
3. título normalizado;
4. transliteración latina si hay cirílico;
5. solo título como último recurso.

Un `SingleFlight` por plataforma y metadatos evita que dos pistas equivalentes busquen a la vez. El bucle conserva el mejor resultado (`sin match` < `needs_review` < `low_confidence` < alta confianza) y solo sigue probando mientras no haya match o el match requiera revisión. La caché de búsquedas se persiste cada `25` entradas y al cerrar la transferencia.

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

El diálogo Organizar conserva el orden actual o permite ordenar A→Z/Z→A y agrupar por artista, álbum o fecha de lanzamiento. Dividir crea de 2 a 5 divisiones manuales seleccionando artistas, álbumes o canciones; ambas operaciones actúan sobre la playlist completa. El usuario puede mostrar la lista, el preview o ambas columnas. El rail lateral flotante es un control custom docked: desplaza el contenido en vez de superponerlo, arranca colapsado en `68 px` y se expande a `220 px` con labels animados. Expone Gestión, Biblioteca, Descargas y Config; Config queda al fondo como módulo en desarrollo y el asistente de credenciales se abre desde el engranaje del panel izquierdo. Biblioteca sincroniza y conserva metadatos de playlists. Descargas permite seleccionar pistas, formato y destino, consultar el progreso/resultados y cancelar la cola.

### Descargas de audio

`AppState.download_selected()` coordina la cola y ejecuta las llamadas bloqueantes de `yt-dlp` en hilos (`asyncio.to_thread`), con concurrencia máxima `DOWNLOAD_CONCURRENCY=2`. `engine.downloader` reutiliza la URL de origen para pistas de YouTube Music o busca una coincidencia en YouTube para las demás fuentes; valida título y duración, extrae solo audio con FFmpeg y escribe metadatos/portada. El archivo se publica mediante renombrado atómico tras superar el tamaño mínimo. Se ofrecen MP3 (predeterminado), M4A, Opus, FLAC y WAV. Si se necesita, la sesión de YouTube Music se carga desde `config/browser.json` en memoria; no se crea `cookies.txt`. La vista no implementa reproducción ni poda automática de caché.

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

## Aviso legal

Melomaniac es una aplicación local para gestionar, organizar, transferir y descubrir música a partir de información proporcionada por servicios de terceros.

Melomaniac no aloja, proporciona ni redistribuye contenido musical protegido por derechos de autor. Las funciones de adquisición de archivos utilizan herramientas y fuentes de terceros y no implican acceso directo al contenido de los servicios de streaming.

El usuario es responsable de asegurarse de que cualquier contenido que obtenga, almacene o utilice mediante Melomaniac esté autorizado para dicho uso y de cumplir las leyes aplicables y los términos de los servicios de origen.

Melomaniac no está afiliado, patrocinado ni respaldado por Spotify, Apple, Google, YouTube ni otros servicios de terceros.

## Verificación

```bash
python -m compileall -q app.py core engine services ui
```

La UI Flet se valida manualmente, incluyendo los modos Lista/Doble/Preview, los diálogos de Organizar/Dividir, la navegación responsive, el skeleton animado y la exportación local. La carpeta `tests/` queda reservada para regresiones futuras.
