# MelomaniacPass v5.0 — Arquitectura Técnica

Aplicación de escritorio para transferir playlists entre **YouTube Music** y **Apple Music** —con soporte de fuentes locales (CSV, M3U, XSPF, WPL, iTunes XML, texto plano)— usando matching inteligente de canciones mediante el motor **Hunter Recovery**.

> **Nota:** Spotify fue eliminado del proyecto (API inviable). La rama `deprecated` conserva la versión con Spotify.

---

## Estructura del Proyecto

```
melomaniacpass/
├── app.py                    # Entry point (~310 líneas)
├── auth_manager.py           # Autenticación (credenciales, wizard UI, pre-flight)
├── .env                      # Credenciales Apple Music
├── browser.json              # Headers de sesión YouTube Music
│
├── core/
│   ├── models.py             # Dataclasses: Track, SearchResult, LoadState, TransferState
│   └── state.py              # AppState — ViewModel central (patrón BLoC)
│
├── services/
│   └── api_service.py        # MusicApiService — Facade unificado YTM + Apple Music
│
├── engine/
│   ├── normalizer.py         # Limpieza y normalización de metadatos (Unicode, regex)
│   ├── match.py              # Sistema Hunter Recovery: validar_match, scoring fuzzy
│   ├── parsers.py            # Parsers de playlists locales (CSV, M3U, XSPF, WPL, iTunes XML)
│   └── organizer.py          # Ordenamiento y segmentación de listas en memoria
│
├── ui/
│   ├── main_ui.py            # PlaylistManagerUI — interfaz principal
│   ├── song_row.py           # SongRow, SkeletonRow
│   ├── telemetry.py          # TelemetryDrawer — panel Monitor / Consola / Post-Mortem
│   └── widgets.py            # Botones y componentes reutilizables
│
└── utils/
    └── circuit_breaker.py    # CircuitBreaker + RateLimitError
```

---

## Plataformas Soportadas

| Plataforma | Tipo | Autenticación |
|---|---|---|
| **YouTube Music** | Streaming | Headers de sesión (`browser.json`) |
| **Apple Music** | Streaming | Bearer + User Token (`.env`) |
| **Archivo Local** | Fuente local | Sin autenticación |
| **Pegar Texto** | Fuente local | Sin autenticación |

Las fuentes locales (Archivo Local, Pegar Texto) permiten cargar playlists desde archivos CSV, M3U, M3U8, PLS, WPL, XSPF, XML (iTunes) o texto plano, y transferirlas a YouTube Music o Apple Music.

---

## Flujo de Dependencias

```
auth_manager.py
        ↓
utils/circuit_breaker.py
        ↓
engine/  (normalizer → match → parsers → organizer)
        ↓
core/models.py
        ↓
services/api_service.py
        ↓
core/state.py
        ↓
ui/  (widgets → song_row → telemetry → main_ui)
        ↓
app.py
```

---

## Librerías Utilizadas

| Librería | Módulos que la usan | Propósito |
|---|---|---|
| `flet` | app.py, ui/*, auth_manager.py | Framework de UI |
| `asyncio` | app.py, ui/main_ui.py, services/, core/state.py | Operaciones asíncronas |
| `requests` | services/api_service.py, auth_manager.py | Llamadas HTTP a APIs |
| `ytmusicapi` | services/api_service.py, auth_manager.py | SDK de YouTube Music |
| `python-dotenv` | app.py, services/api_service.py, auth_manager.py | Lectura/escritura de `.env` |
| `rapidfuzz` | engine/match.py, engine/normalizer.py, services/api_service.py | Matching fuzzy de alto rendimiento |
| `re` + `unicodedata` | engine/normalizer.py, engine/parsers.py, engine/match.py | Normalización de texto |
| `csv` | engine/parsers.py | Parseo de playlists CSV |
| `xml.etree.ElementTree` | engine/parsers.py | Parseo de iTunes XML, XSPF, WPL |
| `json` | auth_manager.py | Lectura/escritura de `browser.json` |
| `pathlib` | auth_manager.py | Rutas de archivos |
| `collections.defaultdict` | engine/organizer.py | Agrupación de segmentos |

---

## Autenticación y Acceso a Archivos de Configuración

### `.env` — Apple Music

```env
APPLE_AUTH_BEARER='Bearer eyJhbGc...'
APPLE_MUSIC_USER_TOKEN='0.AsH5+9...'
```

**Quién accede y cómo:**

```python
# auth_manager.py — lectura y escritura
from pathlib import Path
from dotenv import dotenv_values, load_dotenv, set_key

ENV_FILE = Path(__file__).parent / ".env"

env = dotenv_values(str(ENV_FILE))
bearer = env.get("APPLE_AUTH_BEARER", "")
set_key(str(ENV_FILE), "APPLE_AUTH_BEARER", nuevo_valor, quote_mode="always")

# services/api_service.py — solo lectura
from dotenv import load_dotenv
load_dotenv()
raw  = os.getenv("APPLE_AUTH_BEARER", "").strip()
utok = os.getenv("APPLE_MUSIC_USER_TOKEN", "").strip()

# app.py — carga inicial
from dotenv import load_dotenv
load_dotenv()
```

---

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

**Quién accede y cómo:**

```python
# auth_manager.py — lectura y escritura, exporta la ruta
import json
from pathlib import Path

BROWSER_JSON = Path(__file__).parent / "browser.json"

# lectura
def read_browser_json() -> dict:
    return json.loads(BROWSER_JSON.read_text(encoding="utf-8"))

# escritura desde ConfigWizard
def write_browser_json(authorization: str, cookie: str) -> None:
    ...

# services/api_service.py — importa la ruta desde auth_manager
from auth_manager import BROWSER_JSON

self._ytm = YTMusic(auth=str(BROWSER_JSON), requests_session=self._yt_http_session)
```

---

### Flujo de Autenticación por Plataforma

**YouTube Music (Headers de sesión):**
```
Usuario copia headers del navegador (F12 → Network)
    → ConfigWizard los guarda en browser.json via write_browser_json()
    → services/api_service.py los carga al iniciar con YTMusic(auth=...)
    → se incluyen en cada request a la API
```

**Apple Music (Bearer + User Token):**
```
Usuario obtiene tokens desde Apple Music Web
    → ConfigWizard los guarda en .env con set_key()
    → services/api_service.py los lee con os.getenv()
    → se incluyen en headers de cada request
```

**Pre-flight checks (auth_manager.py):**
```
Al iniciar la app → run_preflight() ejecuta en paralelo:
    _preflight_youtube()  → valida browser.json + llamada real a YTMusic (get_history)
    _preflight_apple()    → valida .env + GET /v1/me/storefront + catálogo
    → resultados → AuthManager.ingest_preflight_results()
    → iconos de estado en la UI actualizados
    → si una plataforma falla, abre el ConfigWizard en su tab
```

---

## Comunicación entre Módulos

### Inicialización en `app.py`

```python
circuit_breakers = {p: CircuitBreaker(p) for p in AppState.PLATFORMS}
service      = MusicApiService(circuit_breakers)
state        = AppState(service)
ui           = PlaylistManagerUI(page, state)
auth_manager = AuthManager(page, service, state)

# Referencias bidireccionales
service.auth_manager = auth_manager
ui.auth_manager      = auth_manager
```

### Patrón Observer (estado → UI)

```python
# core/state.py
class AppState:
    PLATFORMS = ["Apple Music", "YouTube Music"]
    LOCAL_SOURCES = frozenset({"Archivo Local", "Pegar Texto"})
    SOURCE_OPTIONS = ["Apple Music", "YouTube Music", "Archivo Local", "Pegar Texto"]

    def notify(self):
        for callback in self._listeners:
            callback()

# ui/main_ui.py
class PlaylistManagerUI:
    def __init__(self, page, state: AppState):
        state.subscribe(self._on_state_changed)
        for platform, cb in state.cb.items():
            cb.subscribe(lambda is_open, rem, p=platform: self._on_circuit_change(p, is_open, rem))
```

### Ejemplo: Búsqueda de canción

```
ui/main_ui.py         → state.load_playlist(playlist_id)
core/state.py         → service.fetch_playlist(source, id, progress_cb)
services/api_service  → request a plataforma origen (YTM o Apple Music)
                      → retorna (name, list[Track])
core/state.py         → self.tracks = tracks → notify()
ui/main_ui.py         → _on_state_changed() → actualiza lista
```

### Ejemplo: Transferencia de playlist

```
ui/main_ui.py         → state.transfer_playlist()
core/state.py         → para cada Track seleccionado (concurrente):
engine/normalizer     →   clean_metadata(title, artist)
services/api_service  →   search_with_fallback(platform, name, artist)
engine/match.py       →   validar_match() + _fuzzy_scores_triple() + _fuzzy_flags_elastic()
core/state.py         →   SearchResult → track_id o needs_review
services/api_service  →   create_playlist(platform, name, ids)
core/state.py         →   TransferState.DONE → notify()
ui/main_ui.py         → _on_state_changed() → muestra post-mortem
```

### Ejemplo: Carga desde fuente local

```
ui/main_ui.py         → _do_local_pick() o _open_paste_dialog()
engine/parsers.py     → parse_local_playlist(text, filename) → [(artist, title), ...]
                      → build_local_tracks(pairs) → list[Track]
core/state.py         → load_local_tracks(tracks, playlist_name)
                      → self.tracks = tracks → notify()
ui/main_ui.py         → _on_state_changed() → actualiza lista
```

### Ejemplo: Organizar / Dividir lista

```
ui/main_ui.py         → state.organize_sort(["artist"], reverse=False)
core/state.py         → engine/organizer.sort_tracks(tracks, keys, reverse)
                      → self.tracks = sorted_tracks → notify()

ui/main_ui.py         → state.organize_split("artist")
core/state.py         → engine/organizer.split_tracks(tracks, "artist")
                      → self.segments = {"Queen": [...], "Beatles": [...]}
                      → notify()
```

---

## Módulos Clave — Resumen de Responsabilidades

### `engine/organizer.py`
Transformaciones de datos en memoria sin I/O:
- `sort_tracks(tracks, keys, reverse)` — ordena por artista, álbum, título, duración o plataforma
- `split_tracks(tracks, key)` — segmenta la lista maestra en grupos por atributo

### `engine/match.py` — Sistema Hunter Recovery
- `validar_match()` — validación multi-capa L0→L3 para YouTube Music
  - L0: Bypass asiático (CJK/Hangul → match inmediato)
  - L1: Prueba de ácido (substring + solapamiento de artista)
  - L2: Filtro letal (cover, karaoke, tribute → reject)
  - L3: Fuzzy safety net (SequenceMatcher ≥ 0.65)
- `_fuzzy_scores_triple()` — scores desglosados: combinado, título, artista
- `_ideal_pass_hunter()` — criterios de aceptación automática (≥85% o artista exacto + título ≥60%)
- `_fuzzy_flags_elastic()` — clasifica en needs_review / low_confidence
- `_yt_select_best()` — selección del mejor resultado con tie-breaker por duración (±5s)

### `engine/normalizer.py`
- `clean_metadata(title, artist)` — limpieza agresiva: Unicode NFC, purga de paréntesis, eliminación de ruido
- `build_search_query(title, artist)` — query optimizado (Prioridad de Obra)
- Umbrales: `FUZZY_IDEAL=85`, `FUZZY_LOG_BAND_LOW=70`, `FUZZY_REVISION_THRESHOLD=40`

### `engine/parsers.py`
- `parse_local_playlist(text, filename)` — detección automática de formato
- `build_local_tracks(pairs)` — convierte pares (artista, título) en objetos Track
- Formatos soportados: CSV, M3U/M3U8, PLS, WPL, XSPF, iTunes XML, texto plano

### `utils/circuit_breaker.py`
- `CircuitBreaker` — patrón circuit breaker con auto-reset y notificaciones a UI
- `RateLimitError` — excepción para HTTP 429 con tiempo de espera

### `services/api_service.py`
- `MusicApiService` — fachada unificada para YouTube Music y Apple Music
- `search_with_fallback()` — búsqueda con múltiples passes (metadata limpia → original → normalizada)
- `_yt_hunter_async()` — Hunter Recovery para YouTube Music (strict queries → raw queries)
- `_am_hunter_async()` — Hunter Recovery para Apple Music (múltiples términos + fuzzy pick)
- `create_playlist()` — creación de playlist con confirmación de tracks insertados
- Semáforo global (`NETWORK_CONCURRENCY=2`) para limitar concurrencia
- Caché de búsquedas para evitar peticiones duplicadas

### `auth_manager.py`
- `AuthManager` — coordinador de autenticación a nivel de servicio
- `ConfigWizard` — diálogo Flet con 2 tabs editables (YouTube Music + Apple Music)
- `run_preflight()` — validación paralela de credenciales al iniciar
- `reload_credentials()` — hot-reload sin reiniciar el proceso
- Pre-flight: `_preflight_youtube()` (browser.json + get_history) y `_preflight_apple()` (.env + storefront + catálogo)

---

## ConfigWizard — Gestión de Credenciales

El ConfigWizard es un diálogo modal con dos pestañas:

| Tab | Plataforma | Campos editables | Archivo |
|---|---|---|---|
| 0 | YouTube Music | Authorization (SAPISIDHASH), Cookie | `browser.json` |
| 1 | Apple Music | `APPLE_AUTH_BEARER`, `APPLE_MUSIC_USER_TOKEN` | `.env` |

Ambas pestañas incluyen instrucciones paso a paso para extraer las credenciales desde DevTools del navegador. El botón "Guardar y Aplicar" escribe los cambios y recarga las credenciales en caliente.

---

## Ciclo de Vida y Limpieza

`app.py` implementa un protocolo de limpieza profunda (`hard_cleanup`) que garantiza:
1. Cancelación de circuit breakers (tareas de auto-reset)
2. Detención de la instancia de UI
3. Cancelación de tareas de escaneo lazy
4. Cancelación de tareas de recarga de autenticación
5. Cancelación de todas las tareas asyncio pendientes
6. Recolección de basura forzada (`gc.collect`)
7. Limpieza de sesiones HTTP
8. `os._exit(0)` como último recurso si threads bloqueados siguen vivos

Además, un bucle de sondeo (`_auth_poll_loop`) refresca los iconos de sesión cada 90 segundos.

---

## Métricas de Refactorización

| | Antes | Después |
|---|---|---|
| Archivos | 1 monolito | 15 módulos |
| Líneas entry point | ~5000 | ~310 |
| Tamaño entry point | 218 KB | ~12 KB |
| Módulos de engine | 0 | 4 (normalizer, match, parsers, organizer) |
| Plataformas streaming | 3 (Spotify, YTM, Apple) | 2 (YTM, Apple) |
| Fuentes locales | 0 | 2 (Archivo Local, Pegar Texto) |

---

## Respaldo

El monolito original está en `v. 0.1/refactor_backup/app.py` (218 KB). La versión con Spotify se conserva en la rama `deprecated`.