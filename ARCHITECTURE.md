# MelomaniacPass v5.0 — Arquitectura Técnica

Aplicación de escritorio para transferir playlists entre **YouTube Music** y **Apple Music**, con fuentes locales (CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML y texto plano) y matching inteligente mediante el motor **Hunter Recovery**.

> **Nota:** Spotify fue eliminado del proyecto por la inviabilidad de su API en esta implementación. La documentación refleja el código actual, no la rama histórica `deprecated`.

---

## Estructura del Proyecto

```text
melomaniacpass/
├── app.py                    # Entry point, composición y hard cleanup
├── auth_manager.py           # Credenciales, wizard UI y pre-flight
├── .env                      # Credenciales Apple Music (runtime, no se versiona)
├── browser.json              # Headers YouTube Music (runtime, no se versiona)
│
├── core/
│   ├── models.py             # Track, SearchResult, LoadState, TransferState
│   └── state.py              # AppState: estado central y coordinación BLoC-inspired
│
├── services/
│   └── api_service.py        # MusicApiService: fachada YTM + Apple Music
│
├── engine/
│   ├── normalizer.py         # Limpieza y normalización de metadatos
│   ├── match.py              # Hunter Recovery y scoring fuzzy
│   ├── parsers.py            # Parsers de playlists locales
│   └── organizer.py          # Ordenamiento y segmentación en memoria
│
├── ui/
│   ├── main_ui.py            # PlaylistManagerUI: interfaz principal
│   ├── song_row.py           # SongRow y SkeletonRow
│   ├── telemetry.py          # Monitor, consola y Post-Mortem
│   └── widgets.py            # Botones, iconos y componentes reutilizables
│
├── utils/
│   └── circuit_breaker.py    # CircuitBreaker y RateLimitError
│
└── resources/fonts/         # IBM Plex Sans local
```

## Plataformas Soportadas

| Plataforma | Tipo | Autenticación |
|---|---|---|
| **YouTube Music** | Streaming | Headers de sesión en `browser.json` |
| **Apple Music** | Streaming | Bearer token + user token en `.env` |
| **Archivo Local** | Fuente local | Sin autenticación |
| **Pegar Texto** | Fuente local | Sin autenticación |

Las fuentes locales se convierten a objetos `Track` y pueden transferirse a cualquiera de las dos plataformas de streaming.

## Flujo de Dependencias

```text
app.py
  ├── CircuitBreaker (por plataforma)
  ├── MusicApiService ── APIs, sesiones HTTP y caché
  ├── AppState ───────── modelos, progreso y coordinación
  ├── PlaylistManagerUI ─ filas, controles y telemetría
  └── AuthManager ─────── wizard, credenciales y pre-flight

engine/normalizer → engine/match / engine/parsers / engine/organizer
                         ↓
                    core/models
                         ↓
                    core/state ↔ services/api_service
                         ↓
                         ui
```

La inicialización concreta en `app.py` sigue `CircuitBreakers → Service → State → UI`; `AuthManager` se conecta después mediante referencias inyectadas para coordinar la recarga de credenciales y la UI.

## Librerías Utilizadas

| Librería / módulo | Uso principal |
|---|---|
| `flet` | Ventana, controles, diálogos y tema visual |
| `asyncio` | Operaciones asíncronas, concurrencia y ciclo de vida |
| `requests` | Sesiones HTTP para Apple Music, iTunes Search y pre-flight |
| `ytmusicapi` | Cliente de YouTube Music |
| `python-dotenv` | Lectura y escritura de `.env` |
| `rapidfuzz` | Scoring fuzzy de títulos y artistas |
| `csv`, `xml.etree.ElementTree` | Parsers CSV, iTunes XML, XSPF y WPL |
| `json`, `pathlib`, `re`, `unicodedata` | Configuración, rutas y normalización |
| `collections.defaultdict` | Agrupación de segmentos |

El repositorio no incluye todavía un manifiesto de dependencias (`requirements.txt` o `pyproject.toml`); la instalación está documentada en el README.

## Autenticación y Acceso a Archivos de Configuración

### `.env` — Apple Music

```env
APPLE_AUTH_BEARER="Bearer eyJ..."
APPLE_MUSIC_USER_TOKEN="0.AsH5..."
```

`auth_manager.py` centraliza la lectura y escritura mediante `dotenv_values`, `set_key` y `load_dotenv`. `services/api_service.py` consume los valores para construir headers y llamar al storefront y endpoints autenticados de Apple Music.

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

`auth_manager.py` es el único módulo que escribe este archivo. `MusicApiService` importa su ruta y construye el cliente `YTMusic` con esos headers.

### Flujo de Autenticación por Plataforma

```text
YouTube Music:
browser headers → ConfigWizard → browser.json → YTMusic → requests

Apple Music:
tokens del navegador → ConfigWizard → .env → headers HTTP → API Apple Music
```

**Pre-flight al iniciar:** `AuthManager` ejecuta en paralelo las comprobaciones de ambas plataformas. YouTube Music valida el archivo y realiza una llamada real; Apple Music valida tokens, storefront y catálogo. Los resultados actualizan los indicadores de sesión y abren el wizard en la pestaña que falle.

Durante el matching, Apple Music usa la iTunes Search API pública (`itunes.apple.com/search`) para obtener candidatos y sus `trackId`. Esto evita consumir innecesariamente el endpoint de búsqueda autenticado y reduce bloqueos temporales. La API autenticada de Apple Music se mantiene para consultar playlists, storefront y crear la playlist destino.

## Comunicación entre Módulos

### Inicialización en `app.py`

```python
circuit_breakers = {p: CircuitBreaker(p) for p in AppState.PLATFORMS}
service = MusicApiService(circuit_breakers)
state = AppState(service)
ui = PlaylistManagerUI(page, state)
auth_manager = AuthManager(page, service, state)

ui.auth_manager = auth_manager
service.auth_manager = auth_manager
```

### Patrón Observer (estado → UI)

`AppState` mantiene listeners. Cada mutación relevante actualiza el estado y llama `notify()`, y `PlaylistManagerUI` reconstruye la lista, progreso, botones, estados de autenticación y telemetría.

### Ejemplo: Búsqueda de canción

```text
ui → state.transfer_playlist()
state → clean_metadata() y búsqueda concurrente con backoff
service → search_with_fallback() en la plataforma destino
engine.match → validación, scores y clasificación de confianza
state → SearchResult y estado de Track
service → create_playlist() con IDs confirmados
ui → progreso y Post-Mortem
```

### Ejemplo: Carga desde fuente local

```text
ui → selección de archivo o diálogo de texto
engine.parsers → parse_local_playlist() → pares (artista, título)
engine.parsers → build_local_tracks() → list[Track]
state → load_local_tracks() → notify()
ui → renderiza la playlist
```

### Ejemplo: Organizar / Dividir lista

```text
ui → state.organize_sort(keys) o state.organize_split(key)
state → engine.organizer.sort_tracks() / split_tracks()
state → actualiza tracks o segments → notify()
ui → actualiza lista y selector de segmentos
```

## Módulos Clave — Resumen de Responsabilidades

### `engine/organizer.py`

Transformaciones en memoria sin I/O: `sort_tracks()` ordena por artista, álbum, título, duración o plataforma; `split_tracks()` agrupa por un atributo.

### `engine/match.py` — Sistema Hunter Recovery

- `validar_match()` aplica validación por capas: bypass para CJK/Hangul, substring y artista, filtro de cover/karaoke/tribute y fallback fuzzy.
- `_fuzzy_scores_triple()` calcula scores combinado, de título y de artista.
- `_ideal_pass_hunter()` acepta coincidencias de alta confianza.
- `_fuzzy_flags_elastic()` clasifica resultados en revisión o baja confianza.
- `_yt_select_best()` usa la duración como desempate cuando está disponible.

### `engine/normalizer.py`

`clean_metadata()` normaliza Unicode, elimina ruido y limpia paréntesis/corchetes. `build_search_query()` construye queries priorizando la obra. Umbrales documentados en el código: `FUZZY_IDEAL=85`, banda baja de log `70` y revisión `40`.

### `engine/parsers.py`

`parse_local_playlist()` detecta por extensión y contenido CSV, M3U/M3U8, PLS, WPL, XSPF, iTunes XML o texto plano. `build_local_tracks()` convierte los pares extraídos en `Track` con IDs locales.

### `utils/circuit_breaker.py`

`CircuitBreaker` abre un cooldown por plataforma y notifica a la UI; `RateLimitError` representa respuestas HTTP 429 con tiempo de espera.

### `services/api_service.py`

`MusicApiService` unifica carga, búsqueda y creación de playlists. Mantiene sesiones HTTP, caché de búsquedas, clientes autenticados, semáforo global de red (`NETWORK_CONCURRENCY=2`) y reintentos ante rate limiting (`RATE_LIMIT_BACKOFF_STEPS=10`). Convierte las respuestas HTTP 429 y 423 de Apple Music en `RateLimitError`; un 423 recibe un cooldown mínimo de 120 segundos.

### `core/state.py` y `core/models.py`

`Track` y `SearchResult` son los contratos de datos. `LoadState` y `TransferState` representan el ciclo de vida. `AppState` coordina carga, filtrado, selección, transferencia, progreso, segmentos, errores y notificaciones.

## ConfigWizard — Gestión de Credenciales

El wizard modal tiene dos pestañas:

| Tab | Plataforma | Campos | Archivo |
|---|---|---|---|
| 0 | YouTube Music | Authorization, Cookie | `browser.json` |
| 1 | Apple Music | Bearer, User Token | `.env` |

Incluye instrucciones para DevTools y el botón **Guardar y Aplicar**. Después de guardar, `reload_credentials()` actualiza los clientes del servicio sin reiniciar el proceso.

## Ciclo de Vida y Limpieza

`app.py` registra el pre-flight inicial, un sondeo de sesiones cada 90 segundos y un cierre profundo que:

1. Cancela circuit breakers y tareas de UI.
2. Detiene búsquedas y escaneos lazy pendientes.
3. Cancela tareas `asyncio` de autenticación y transferencia.
4. Limpia las sesiones HTTP reutilizables del servicio.
5. Ejecuta recolección de basura y aplica la salida de emergencia si quedan hilos bloqueados.

## Métricas de Refactorización

El estado actual conserva la separación del monolito original en módulos de core, services, engine, UI y utils. Las cifras históricas de tamaño y líneas no están automatizadas ni se mantienen como métrica de build; por eso no se presentan como valores exactos aquí.

| Área | Estado actual |
|---|---|
| Plataformas streaming | 2: YouTube Music y Apple Music |
| Fuentes locales | Archivo y texto pegado |
| Módulos de engine | 4: normalizer, match, parsers, organizer |
| Estado compartido | `AppState` observable |
| Resiliencia | Circuit breaker, backoff y caché |

## Respaldo

La documentación histórica menciona un monolito y una rama `deprecated` con Spotify. Esos elementos no forman parte del árbol actual inspeccionado; deben considerarse respaldo histórico y no dependencias del runtime actual.
