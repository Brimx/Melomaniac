# 🎵 Melomaniac v4.8.0

**Tu música. Tu librería. Tu elección.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/UI-Flet-7C3AED)](https://flet.dev/)
[![RapidFuzz](https://img.shields.io/badge/Matching-RapidFuzz-00897B)](https://github.com/rapidfuzz/RapidFuzz)
[![YouTube Music](https://img.shields.io/badge/YouTube%20Music-Supported-FF0000?logo=youtubemusic&logoColor=white)](https://music.youtube.com/)
[![Apple Music](https://img.shields.io/badge/Apple%20Music-Supported-FA243C?logo=applemusic&logoColor=white)](https://music.apple.com/)
[![Spotify](https://img.shields.io/badge/Spotify-Supported-1DB954?logo=spotify&logoColor=white)](https://open.spotify.com/)

Melomaniac es una aplicación de escritorio para sincronizar, reconstruir, descargar y reproducir playlists entre **YouTube Music, Apple Music y Spotify**, así como gestionar archivos locales de música.

La versión de trabajo documentada es `4.8.0`. Los cambios detallados de cada versión están en [`CHANGELOG.md`](CHANGELOG.md).

---

## Funcionalidades principales

- 🔄 **Gestión y transferencia de playlists:** Reconstruye listas entre YouTube Music, Apple Music y Spotify. Soporta entradas locales por archivo (`M3U/M3U8`, `PLS`, `WPL`, `XSPF`, `CSV`, `TXT`), archivos de audio (`MP3`, `FLAC`, `AAC`, `OGG`, `WAV`, `M4A`, `OPUS`) o texto pegado.
- 🎯 **Motor de coincidencia inteligente:** Algoritmo multi-fase con RapidFuzz, ISRC exacto (Apple Music), comparación de duración y etiquetas explícitas. Incluye transliteración cirílica, deduplicación de consultas (`SingleFlight`) y reporte Post-Mortem para resolver alternativas de pistas dudosas o no encontradas.
- 📚 **Biblioteca unificada:** Explora tus playlists en streaming y tus carpetas locales en una sola vista. Permite crear una nueva playlist de streaming directamente a partir de cualquier carpeta local de tu disco.
- 🎵 **Reproductor de audio integrado:** Barra de reproducción nativa en la parte inferior para archivos locales (FLAC, MP3, M4A, Opus, WAV) con cola reordenable (`PlayQueue`), soporte de arrastre de posición (*scrubbing*), modos shuffle/repeat y volumen persistente.
- ⬇️ **Descargas con metadatos completos:** Descarga canciones en MP3, M4A, Opus, FLAC o WAV vía `yt-dlp`. Normaliza carátulas a 800 px cuadrados, escribe metadatos ID3/Vorbis con `mutagen`, valida la presencia de FFmpeg y omite automáticamente pistas ya descargadas.
- 🖥️ **Integración con escritorio (MPRIS / D-Bus):** En Linux, control total desde widgets de escritorio (Waybar, Quickshell, GNOME, KDE) y teclas multimedia físicas del teclado, con carátulas y metadatos en tiempo real.
- 🌐 **Sesión de navegador sin enredos:** Conexión automática de credenciales en memoria leyendo las cookies seguras de Firefox o Chromium sin necesidad de exportar JSONs manualmente.
- 🛠️ **Organización avanzada y exportación:** Modos de vista Lista, Doble y Preview. Ordena y agrupa por artista, álbum, duración o fecha; divide playlists en segmentos manuales (2 a 5 divisiones) y exporta a `M3U8`, `CSV`, `TXT` o `XSPF`.

---

## Requisitos del sistema

| Requisito | Detalle |
|---|---|
| Python | 3.10 o superior |
| Dependencias | Versiones fijadas en [`requirements.txt`](requirements.txt) |
| Conversión de audio | `ffmpeg` 6 o superior en `PATH` (necesario para extracción y conversión en Descargas) |
| Credenciales | Sesión activa en el navegador, o archivos manuales en `config/` (`browser.json`, `.env`, `spotify_cookies.json`) |
| Interfaz | Flet Desktop 1.0.5; tema oscuro, Stack Sans, IBM Plex y fallback CJK opcional |

---

## Instalación y ejecución

### ⚡ Inicio rápido con scripts de un clic (Recomendado)

El repositorio incluye lanzadores automáticos que crean el entorno virtual `.venv`, instalan las dependencias en el primer arranque y ejecutan la aplicación:

- **Windows:** Haz doble clic en `run.bat` (o ejecuta `.\run.bat` en la terminal).
- **Linux / macOS:** Ejecuta en la terminal:
  ```bash
  ./run.sh
  ```

---

### 🛠️ Instalación manual paso a paso

Si prefieres realizar el proceso comando por comando:

#### 1. Instalar prerrequisitos (Python y FFmpeg)

<details>
<summary><b>📦 Comandos por sistema operativo</b></summary>

- **Windows (PowerShell):**
  ```powershell
  winget install Python.Python.3.12 Gyan.FFmpeg Git.Git
  ```
  *(Tras instalar, reinicia la terminal. Si descargas Python manualmente desde python.org, recuerda marcar **"Add python.exe to PATH"**).*

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

- **macOS (con Homebrew):**
  ```bash
  brew install python ffmpeg git
  ```

</details>

#### 2. Clonar y arrancar

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

- **Método directo (sin problemas de permisos de PowerShell):**
  ```powershell
  git clone https://github.com/Brimx/Melomaniac.git
  cd Melomaniac
  python -m venv .venv
  .\.venv\Scripts\pip install -r requirements.txt
  .\.venv\Scripts\python app.py
  ```

- **Método tradicional:**
  ```powershell
  .\.venv\Scripts\Activate.ps1   # En CMD clásico: .venv\Scripts\activate.bat
  pip install -r requirements.txt
  python app.py
  ```

---

### 🔄 ¿Cómo actualizar a una nueva versión?

Para actualizar la aplicación cuando se publiquen cambios nuevos en el repositorio:

1. Abre la terminal en la carpeta de Melomaniac y descarga las novedades:
   ```bash
   git pull
   ```
   > [!NOTE]
   > `git pull` solo actualiza los archivos del repositorio. Tus credenciales (`config/`), configuraciones personales, descargas (`downloads/`) y cachés están ignorados por Git, por lo que **no se tocan ni se pierden**.

2. Vuelve a arrancar la aplicación:
   - **Windows:** Haz doble clic en `run.bat` (o ejecuta `.\run.bat`).
   - **Linux / macOS:** Ejecuta `./run.sh`.

*(Los scripts `run.bat` y `run.sh` detectan automáticamente si la nueva versión actualizó `requirements.txt` e instalan las dependencias necesarias de forma transparente).*

---

### 💡 Solución rápida a problemas frecuentes

- **PowerShell bloquea la activación (`File ... cannot be loaded because running scripts is disabled`)**:
  Usa el script `run.bat` o el **Método directo** (`.\.venv\Scripts\python app.py`), o habilita scripts una vez en PowerShell:
  ```powershell
  Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
  ```
- **FFmpeg no detectado en la vista de Descargas**:
  Asegúrate de haber instalado FFmpeg y haber **reiniciado tu terminal o editor** para que cargue la variable de entorno `PATH`.
- **Error `ensurepip is not available` en Ubuntu/Debian**:
  Instala el paquete faltante con `sudo apt install python3-venv`.

---

La pantalla inicial permite elegir un origen y un destino. El origen puede ser una de las tres plataformas o una fuente local; el destino siempre es una plataforma de streaming.

### Configurar credenciales

Melomaniac detecta automáticamente tu sesión activa en el navegador (Firefox y navegadores basados en Chromium) directamente en memoria, por lo que en la mayoría de los casos **no necesitas configurar nada manualmente**.

Si prefieres usar credenciales manuales o tu navegador no es estándar, puedes usar el asistente integrado (accesible desde el icono de engranaje en la barra lateral):

- **YouTube Music:** Guarda en `config/browser.json` las cabeceras `Cookie` y `Authorization` (con prefijo `SAPISIDHASH`) obtenidas de `music.youtube.com`.
- **Apple Music:** Guarda en `config/.env` los tokens `APPLE_AUTH_BEARER` y `APPLE_MUSIC_USER_TOKEN` de `music.apple.com`.
- **Spotify:** Guarda en `config/spotify_cookies.json` tus cookies `sp_dc` y `sp_key`.

Las credenciales, ajustes y cachés residen dentro de `config/`, ignorado en `.gitignore`. No compartas ni subas esos archivos.

### Cargar una playlist

- **Streaming:** Selecciona la plataforma, introduce el ID o la URL de la playlist y pulsa **Cargar**.
- **Archivo Local:** Selecciona un archivo de playlist (`M3U/M3U8`, `PLS`, `WPL`, `XSPF`, `CSV`, `TXT`) o un archivo de audio compatible para extraer sus etiquetas.
- **Pegar Texto:** Pega una lista de canciones (una por línea, en formato `Título - Artista` o texto plano).

En fuentes locales debes elegir explícitamente la plataforma de destino antes de transferir.

### Revisar, transferir y reproducir

1. Revisa las canciones y desmarca las que no desees incluir.
2. Opcionalmente pulsa **Organizar** para ordenar/agrupar o **Dividir** para separar la lista en bloques manuales (de 2 a 5 divisiones).
3. Alterna entre los modos **Lista**, **Doble** y **Preview** para comparar origen y destino.
4. Pulsa **Transferir**, confirma nombre y descripción, y monitorea el progreso en vivo. Al finalizar, la pestaña **Post-Mortem** te permite buscar alternativas a canciones dudosas.
5. Puedes reproducir cualquier archivo local o descargado haciendo clic en la pista o usando la barra del **Reproductor** inferior.

Si eliges **Archivo Local (Exportar)** como destino, podrás guardar la lista en `M3U8`, `CSV`, `TXT` o `XSPF`.

## Autenticación, API y resiliencia

- **YouTube Music:** `ytmusicapi` para búsqueda y gestión de playlists.
- **Apple Music:** Endpoint web `amp-api.music.apple.com` con catálogo y matching por ISRC/duración.
- **Spotify:** `spotapi` con búsqueda `tracksV2`, duración en milisegundos y scoring con flag `explicit`.
- **Reproductor y Audio:** `flet-audio` 1.0.5 (`FLET_DESKTOP_FLAVOR=full`) con cola `PlayQueue` y `services/mpris_service.py` (D-Bus en Linux).
- **Semáforo y límites:** 2 solicitudes concurrentes globales en red; Apple Music procesa transferencias secuencialmente y con pausas preventivas para evitar HTTP 423/429.
- **Caché atómica:** `config/search_cache.json` se persiste en disco por lotes cada 25 entradas y al completar transferencias.

## Archivos de configuración

| Archivo / Carpeta | Uso |
|---|---|
| `config/settings.json` | Formato predeterminado de descarga, carátulas online y carpetas locales |
| `config/browser.json` | Headers manuales de YouTube Music |
| `config/.env` | Tokens `APPLE_AUTH_BEARER` y `APPLE_MUSIC_USER_TOKEN` |
| `config/spotify_cookies.json` | Cookies `sp_dc` y `sp_key` de Spotify |
| `config/search_cache.json` | Caché de búsquedas previas (`título\|\|\|artista\|\|\|destino`) |
| `config/transfer_history.jsonl` | Historial persistente de transferencias y resoluciones |
| `config/cover_cache/` | Caché local de carátulas normalizadas (800 px) |
| `exports/` | Salidas generadas por el exportador local (ignorado por Git) |
| `transfer_failed_report.txt` | Reporte generado bajo demanda desde la UI |

## Estructura del proyecto

```text
Melomaniac/
├── app.py                         # Entrada, composición de servicios y ciclo de vida
├── run.bat                        # Lanzador automático de un clic para Windows
├── run.sh                         # Lanzador automático para Linux/macOS
├── config/                        # Credenciales, ajustes y cachés (ignorado en Git)
├── core/
│   ├── availability.py            # Escaneo de disponibilidad e ISRC en Apple
│   ├── cache.py                   # Manejo y compatibilidad de search_cache
│   ├── config.py                  # Constantes, límites de red y tamaños de lote
│   ├── library_models.py          # Modelos de resúmenes de playlists de Biblioteca
│   ├── library_store.py           # Persistencia de metadatos de Biblioteca
│   ├── models.py                  # Track, PlaylistMeta, SearchResult y enums
│   ├── playback.py                # Cola PlayQueue y estados de reproducción
│   ├── state.py                   # AppState centralizado (BLoC / Observer)
│   └── transfer.py                # TransferReport y control de fallos en transferencias
├── engine/
│   ├── audio_metadata.py          # Lectura y escritura de tags con Mutagen
│   ├── covers.py                  # Normalización de portadas a 800 px
│   ├── downloader.py              # Cola y descarga de audio con yt-dlp
│   ├── exporters.py               # Exportación local (M3U8, CSV, TXT, XSPF)
│   ├── match.py                   # Scoring y Hunter Recovery con RapidFuzz
│   ├── normalizer.py              # Limpieza de títulos, ISRC y cirílico
│   ├── organizer.py               # Ordenación y divisiones en memoria
│   └── parsers.py                 # Lectura de playlists y archivos locales
├── services/
│   ├── api_service.py             # Fachada de APIs para streaming
│   ├── authentication.py          # Rutas runtime y preflight de credenciales
│   ├── browser_session.py         # Extracción de sesiones de navegador en memoria
│   ├── circuit_breaker.py         # Cooldowns monotónicos y protección contra 429
│   ├── cover_cache.py             # Almacenamiento atómico de carátulas en disco
│   ├── covers.py                  # Resolución inteligente de carátulas por álbum
│   ├── library_state.py           # Sincronización y estado de Biblioteca
│   ├── mpris_service.py           # Servicio D-Bus MPRIS para control multimedia en Linux
│   └── settings_store.py          # Persistencia de preferencias del usuario
├── ui/
│   ├── auth_manager.py            # Orquestación de credenciales y recarga en caliente
│   ├── config_view.py             # Pantalla de configuración y preferencias
│   ├── config_wizard.py           # Asistente gráfico de credenciales
│   ├── downloads_view.py          # Vista de descargas con progreso y preflight FFmpeg
│   ├── fonts.py                   # Clasificación Unicode y fuentes tipográficas
│   ├── library_view.py             # Vista de Biblioteca y exploración de carpetas locales
│   ├── main_ui.py                 # Ventana principal, navegación por rail y vistas
│   ├── organize_panel.py          # Diálogos de Organizar, Dividir y Reordenar
│   ├── player_bar.py              # Barra inferior de reproducción interactiva
│   ├── player_queue.py            # Drawer lateral con la cola de pistas
│   ├── playlist_card.py           # Tarjetas visuales de playlist
│   ├── playlist_detail_view.py    # Vista detallada de canciones en Biblioteca
│   ├── playlist_meta_dialog.py    # Edición de metadatos de playlist
│   ├── song_row.py                # Filas de canciones y skeletons animados
│   ├── telemetry.py               # Monitor de transferencia, consola y Post-Mortem
│   ├── tokens.py                  # Paleta, radios, elevaciones y animaciones
│   ├── transfer_report.py         # Reporte interactivo con inserción de alternativas
│   └── widgets.py                 # Componentes estilizados reutilizables
└── resources/fonts/               # Fuentes locales Stack Sans e IBM Plex
```

La tipografía se elige por escritura: Stack Sans para texto latino, IBM Plex
Sans para cirílico, IBM Plex Mono para datos técnicos y Noto CJK si está
disponible. Sin una fuente CJK registrada, Flet usa el fallback nativo.

## Verificación

La comprobación rápida de los módulos Python es:

```bash
python -m compileall -q app.py core engine services ui
```

La UI Flet se valida manualmente, en especial los modos de vista, los diálogos de Organizar/Dividir, la navegación responsive y el diálogo de exportación. La carpeta `tests/` queda reservada para regresiones futuras.

## Aviso legal

### Español

Melomaniac es una aplicación local para gestionar, organizar, transferir y descubrir música a partir de información proporcionada por servicios de terceros.

Melomaniac no aloja, proporciona ni redistribuye contenido musical protegido por derechos de autor. Las funciones de adquisición de archivos utilizan herramientas y fuentes de terceros y no implican acceso directo al contenido de los servicios de streaming.

El usuario es responsable de asegurarse de que cualquier contenido que obtenga, almacene o utilice mediante Melomaniac esté autorizado para dicho uso y de cumplir las leyes aplicables y los términos de los servicios de origen.

Melomaniac no está afiliado, patrocinado ni respaldado por Spotify, Apple, Google, YouTube ni otros servicios de terceros.

### English

Melomaniac is a local application for managing, organizing, transferring, and discovering music using information provided by third-party services.

Melomaniac does not host, provide, or redistribute copyrighted music content. File-acquisition features use third-party tools and sources and do not imply direct access to streaming service content.

The user is responsible for ensuring that any content obtained, stored, or used through Melomaniac is authorized for that use and for complying with applicable laws and the terms of the source services.

Melomaniac is not affiliated with, sponsored by, or endorsed by Spotify, Apple, Google, YouTube, or any other third-party service.

## Estado y licencia

La versión documentada es `4.8.0` en la rama `main`. Es un proyecto para uso personal; cada plataforma y sus APIs están sujetas a sus propios términos de servicio.

## Agradecimientos

[Flet](https://flet.dev) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) · [spotapi](https://github.com/spotapi) · [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) · [Mutagen](https://mutagen.readthedocs.io/)
