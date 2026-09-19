# 🎵 Melomaniac v4.1.0

Melomaniac es una aplicación de escritorio para reconstruir playlists entre **YouTube Music, Apple Music y Spotify**. También permite importar una lista local o una canción individual, buscar cada pista en el destino y crear una nueva playlist.

La versión de trabajo pertenece a la serie `4.1.0`: se conserva la versión mayor 4 mientras se consolidan los cambios funcionales y correcciones antes de fijar el siguiente número minor/parche.

El matching combina normalización de metadatos, RapidFuzz, artista/título, duración, contenido explícito e ISRC cuando está disponible. Los resultados dudosos se conservan en el Post-Mortem para revisión.

## Funcionalidades

- Carga playlists de YouTube Music, Apple Music y Spotify.
- Importa `TXT`, `CSV`, `M3U/M3U8`, `PLS`, `WPL`, `XSPF` y XML compatible con XSPF.
- Importa archivos de audio (`MP3`, `FLAC`, `AAC`, `OGG`, `WAV`, `M4A`, `WMA`, `OPUS`, `AIFF/AIF`) y lee sus tags con Mutagen.
- Busca con Hunter Recovery y tres pasadas de consulta: metadatos limpios, valores originales y título normalizado.
- Usa ISRC para coincidencias exactas en Apple Music y conserva el valor en la caché.
- Compara la duración; Spotify añade el indicador `explicit` al scoring.
- Permite buscar, seleccionar, ordenar y dividir la lista por artista, álbum, título o duración. El alcance puede ser **Todo**, **Visibles** o **Seleccionadas**.
- Ofrece los modos **Lista**, **Doble** y **Preview** durante Organizar/Dividir, con una vista responsive y navegación por **Inicio**, **Biblioteca**, **Descargas** y **Config**.
- Muestra título, artista, álbum, duración, portada y estado de cada pista.
- Exporta la selección o el alcance visible a `TXT`, `CSV`, `M3U`, `M3U8` y `XSPF`, con orden artista-título o título-artista.
- Permite editar nombre y descripción antes de crear la playlist. En Spotify, SpotAPI solo permite guardar el nombre; la descripción se informa como no soportada.
- Incluye precarga/escaneo de disponibilidad del destino, progreso, consola y reporte Post-Mortem exportable a `transfer_failed_report.txt`.
- Protege las APIs con semáforos, circuit breakers, caché persistida y control de lotes.

## Requisitos

| Requisito | Detalle |
|---|---|
| Python | 3.10 o superior |
| Dependencias | Versiones fijadas en [`requirements.txt`](requirements.txt) |
| Credenciales | `config/browser.json`, `config/.env` y `config/spotify_cookies.json` según el destino |
| Interfaz | Flet Desktop; la aplicación configura tema oscuro y fuentes IBM Plex Sans locales |

## Instalación

```bash
git clone https://github.com/Brimx/Melomaniac.git
cd Melomaniac
python -m venv .venv
source .venv/bin/activate                 # Linux/macOS
# .venv\Scripts\activate                 # Windows PowerShell
python -m pip install -r requirements.txt
```

## Ejecución

```bash
python app.py
```

La pantalla inicial permite elegir un origen y un destino. El origen puede ser una de las tres plataformas o una fuente local; el destino siempre es una plataforma de streaming.

### Configurar credenciales

El asistente de configuración tiene una pestaña por plataforma. Al iniciar, `AuthManager` ejecuta las comprobaciones de sesión en paralelo y muestra qué plataforma necesita atención. Después de guardar, las credenciales se recargan sin reiniciar la aplicación.

YouTube Music requiere el contenido de `Authorization` (con prefijo `SAPISIDHASH`) y `Cookie` de una solicitud `browse` en `music.youtube.com`:

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

Apple Music requiere estos valores en `config/.env`:

```env
APPLE_AUTH_BEARER="Bearer eyJ..."
APPLE_MUSIC_USER_TOKEN="0.As..."
```

El prefijo `Bearer ` es opcional: la aplicación lo añade si falta. Los valores se obtienen de los headers de una solicitud `catalog` en Apple Music Web.

Spotify usa el formato generado por el asistente en `config/spotify_cookies.json`:

```json
{
  "identifier": "usuario@email.com",
  "cookies": {
    "sp_dc": "...",
    "sp_key": "..."
  }
}
```

Las credenciales y la caché están dentro de `config/`, que está excluido por `.gitignore`. No compartas ni subas esos archivos.

### Cargar una playlist

- **Streaming:** selecciona la plataforma, introduce el ID de la playlist y pulsa **Cargar**.
- **Archivo Local:** selecciona un archivo de playlist compatible; la aplicación lo detecta por extensión y contenido. Los archivos XML se interpretan como XSPF.
- **Archivo de audio:** selecciona una canción compatible; sus tags se usan como título, artista, álbum, duración, número de pista, fecha e ISRC.
- **Pegar Texto:** pega una entrada por línea. Las líneas pueden usar el formato `Título - Artista`; también se aceptan líneas sin separador.

En fuentes locales debes elegir explícitamente el destino antes de transferir. El origen y el destino no pueden ser la misma plataforma.

### Revisar y transferir

1. Revisa las canciones y desmarca las que no quieras transferir.
2. Opcionalmente usa **Organizar** o **Dividir**, elige el alcance de la operación y cambia el segmento visible. La vista puede alternarse entre **Lista**, **Doble** y **Preview**.
3. Pulsa **Transferir**, confirma o edita el nombre y la descripción.
4. Revisa el progreso, la consola y la pestaña **Post-Mortem**.

Si eliges **Archivo Local (Exportar)** como destino, el botón cambia a **Exportar**. Puedes seleccionar el formato, el orden y la ruta de salida; por defecto se propone `~/Documentos/Melomaniac/Exports` cuando existe, o `./exports` como fallback.

Los matches con `low_confidence` pueden continuar en fuentes de streaming. En pistas locales se aplica un umbral estricto; las coincidencias por debajo de `85` se rechazan. Los matches marcados `revision_necesaria` no se insertan automáticamente.

## Autenticación, API y resiliencia

- **YouTube Music:** `ytmusicapi`, búsqueda de canciones y lectura/creación de playlists.
- **Apple Music:** endpoint web `amp-api.music.apple.com`; la búsqueda usa el catálogo de Apple y `durationInMillis`/`isrc`.
- **Spotify:** `spotapi`; usa la búsqueda `tracksV2`, duración en milisegundos y `explicit`.
- Semáforo global de red: `2` solicitudes concurrentes.
- Límites configurados de transferencia: `2` para Apple Music y `3` para las demás plataformas; Apple procesa sus búsquedas secuencialmente para evitar ráfagas.
- Apple: lotes ISRC de `25`, lotes de inserción de `100`, límite preventivo de `50` solicitudes y pausa de `60` segundos; el HTTP `423` fuerza al menos `120` segundos de cooldown.
- Spotify: inserción en lotes de `50`, con hasta cuatro intentos y espera progresiva.
- La caché de búsquedas se guarda atómicamente en `config/search_cache.json` y acepta resultados actuales y entradas legacy con solo `track_id`.
- Los circuit breakers gestionan `429`; Apple también distingue `401/403` de autenticación y `423` de bloqueo temporal.

## Archivos de configuración

| Archivo | Uso |
|---|---|
| `config/.env` | `APPLE_AUTH_BEARER` y `APPLE_MUSIC_USER_TOKEN` |
| `config/browser.json` | Headers autenticados de YouTube Music |
| `config/spotify_cookies.json` | `identifier`, `sp_dc` y `sp_key` |
| `config/search_cache.json` | Resultados por `título|||artista|||destino` |
| `exports/` | Salidas locales generadas por el exportador; está excluido por `.gitignore` |
| `transfer_failed_report.txt` | Reporte creado bajo demanda por la UI; no forma parte de la configuración |

## Estructura del proyecto

```text
Melomaniac/
├── app.py                         # Entrada, composición y ciclo de vida
├── config/                        # Credenciales y estado runtime ignorados
├── core/
│   ├── availability.py            # Escaneo de disponibilidad e ISRC en Apple
│   ├── cache.py                   # Claves y compatibilidad de caché
│   ├── config.py                  # Plataformas, concurrencia y lotes
│   ├── models.py                  # Track, PlaylistMeta, SearchResult y estados
│   ├── state.py                   # Estado BLoC y flujo de transferencia
│   └── transfer.py                # Búsqueda y errores de rate limit
├── engine/
│   ├── audio_metadata.py          # Lectura/escritura de tags con Mutagen
│   ├── match.py                   # Scoring y validación de candidatos
│   ├── normalizer.py              # Limpieza, ISRC y umbrales fuzzy
│   ├── organizer.py               # Ordenación y segmentación en memoria
│   ├── exporters.py               # Exportación local TXT/CSV/M3U/XSPF
│   └── parsers.py                 # Formatos locales y construcción de Track
├── services/
│   ├── api_service.py             # Fachada de APIs y sesiones HTTP
│   ├── authentication.py           # Rutas, credenciales y pre-flight
│   └── circuit_breaker.py          # Cooldowns y RateLimitError
├── ui/
│   ├── auth_manager.py            # Comprobaciones y hot reload
│   ├── config_wizard.py            # Asistente de credenciales
│   ├── main_ui.py                 # Ventana y eventos principales
│   ├── playlist_meta_dialog.py     # Nombre y descripción de destino
│   ├── song_row.py                # Filas de canciones y skeletons
│   ├── telemetry.py               # Monitor, consola y Post-Mortem
│   ├── fonts.py                    # Clasificación Unicode y registro dinámico
│   └── widgets.py / tokens.py      # Componentes y tokens visuales
└── resources/fonts/               # IBM Plex Sans w300–w700; CJK opcional
```

Los títulos y artistas se clasifican por bloques Unicode al crear cada
control de texto: CJK tiene prioridad, después cirílico y finalmente latino.
La aplicación busca `NotoSansCJK-Regular.ttc` en los assets o en rutas
estándar del sistema; si no está disponible, conserva IBM Plex Sans y deja que
el fallback nativo del motor de texto resuelva los glifos.

## Verificación

La comprobación rápida de los módulos Python es:

```bash
python -m compileall -q app.py core engine services ui
```

La UI Flet se valida manualmente, en especial los modos de vista, el alcance de Organizar/Dividir, la navegación responsive y el diálogo de exportación. La carpeta `tests/` queda reservada para regresiones futuras.

## Estado y licencia

La versión documentada es `4.1.0` en la rama `main`. Es un proyecto para uso personal; cada plataforma y sus APIs están sujetas a sus propios términos de servicio.

## Agradecimientos

[Flet](https://flet.dev) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) · [spotapi](https://github.com/spotapi) · [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) · [Mutagen](https://mutagen.readthedocs.io/)
