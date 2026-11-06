# 🎵 Melomaniac v4.8.0

**Español** · [English](README.en.md)

**Tu música. Tu librería. Tu elección.**

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Flet](https://img.shields.io/badge/UI-Flet-7C3AED)](https://flet.dev/)
[![RapidFuzz](https://img.shields.io/badge/Matching-RapidFuzz-00897B)](https://github.com/rapidfuzz/RapidFuzz)
[![YouTube Music](https://img.shields.io/badge/YouTube%20Music-Supported-FF0000?logo=youtubemusic&logoColor=white)](https://music.youtube.com/)
[![Apple Music](https://img.shields.io/badge/Apple%20Music-Supported-FA243C?logo=applemusic&logoColor=white)](https://music.apple.com/)
[![Spotify](https://img.shields.io/badge/Spotify-Supported-1DB954?logo=spotify&logoColor=white)](https://open.spotify.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-4c1f9e.svg)](LICENSE)

Aplicación de escritorio para **transferir playlists entre YouTube Music, Apple Music y Spotify**, gestionar archivos locales de música, descargar audio para uso personal y reproducirlo en la propia app.

> Documentación: [Arquitectura](ARCHITECTURE.md) (flujos, autenticación y detalles internos) · [Diseño](DESIGN.md) (tokens visuales) · [Cambios](CHANGELOG.md) · [Notas de release](RELEASE_NOTES.md)

---

## Funcionalidades

- 🔄 **Transferencia entre plataformas** — Reconstruye listas entre YouTube Music, Apple Music y Spotify desde una playlist en streaming, un archivo local (`M3U/M3U8`, `PLS`, `WPL`, `XSPF`, `CSV`, `TXT`), un archivo de audio o texto pegado.
- 🎯 **Motor de coincidencia** — Matching multi-fase con RapidFuzz, ISRC exacto (Apple Music), duración y flag explícito. Incluye transliteración cirílica, deduplicación de consultas y reporte Post-Mortem para resolver pistas dudosas.
- 📚 **Biblioteca unificada** — Tus playlists en streaming y tus carpetas locales en una sola vista; desde cualquier carpeta local puedes crear una playlist en streaming.
- 🎵 **Reproductor integrado** — Barra inferior para archivos locales (MP3, FLAC, M4A, Opus, WAV) con cola reordenable, shuffle/repeat, volumen persistente e integración MPRIS en Linux (teclas multimedia y widgets como Waybar o GNOME).
- ⬇️ **Descargas (uso personal)** — Guarda audio con `yt-dlp` en MP3, M4A, Opus, FLAC o WAV, con metadatos, carátula a 800 px y omisión de archivos ya descargados. Requiere `ffmpeg`.
- 🛠️ **Organización y exportación** — Vistas Lista/Doble/Preview, ordenación y agrupación por artista, álbum o fecha, división manual en 2–5 bloques y exportación a `M3U8`, `CSV`, `TXT` o `XSPF`.
- 🌐 **Sesión desde el navegador** — En la mayoría de los casos no hay nada que configurar: detecta tu sesión activa de Firefox/Chromium en memoria. Credenciales manuales disponibles vía asistente integrado.

---

## Aviso legal y licencia

- **Código MIT:** Melomaniac se distribuye bajo licencia MIT (ver [`LICENSE`](LICENSE)). La licencia cubre el software, no los contenidos musicales.
- **Uso personal:** la función de Descargas es solo para uso estrictamente personal y sin fines comerciales. No distribuyas, vendas, publiques ni pongas a disposición de terceros el audio descargado, ni lo uses con fines comerciales.
- **Sin alojamiento de contenido:** Melomaniac no aloja, proporciona ni redistribuye contenido musical protegido por derechos de autor.
- **Responsabilidad del usuario:** eres responsable de que cualquier contenido que obtengas, almacenes o utilices mediante Melomaniac esté autorizado, y de cumplir los términos de YouTube, YouTube Music, Apple Music y Spotify, así como la legislación local aplicable (incluida la colombiana: Ley 23 de 1982 y Ley 1915 de 2018).
- **Sin afiliación:** no está afiliado ni respaldado por Google, YouTube, Apple, Spotify ni sus titulares de licencia.
- **Sin garantía:** el software se proporciona «tal cual», sin garantía expresa o implícita; en la máxima medida permitida por la ley, los autores no responden por daños derivados de su uso.

---

## Instalación

### Binarios standalone (recomendado)

Cada release en GitHub incluye paquetes compilados que no requieren Python; úsalos si no quieres instalar desde código:

| Paquete | Archivo |
|---|---|
| Código fuente portátil | `Melomaniac-vX.Y.Z.zip` (+ `SHA256SUMS.txt`) |
| Linux | `Melomaniac-vX.Y.Z-x86_64.AppImage`, `Melomaniac-vX.Y.Z-linux-x64.tar.gz` (+ `SHA256SUMS-linux.txt`) |
| Windows | `Melomaniac-vX.Y.Z-windows-x64-Setup.exe`, `Melomaniac-vX.Y.Z-windows-x64.zip` (+ `SHA256SUMS-windows.txt`) |

En los binarios, la configuración se guarda fuera de la carpeta de instalación (`$XDG_CONFIG_HOME/melomaniac` en Linux, `%APPDATA%\Melomaniac` en Windows), salvo modo portable (carpeta `config/` escribible o `portable.flag`).

### Desde el código

**Requisitos:** Python 3.10+, `ffmpeg` 6+ en el `PATH` (para Descargas) y las dependencias fijadas en [`requirements.txt`](requirements.txt).

Los lanzadores automáticos crean el entorno `.venv`, instalan dependencias en el primer arranque y ejecutan la app:

- **Windows:** doble clic en `run.bat` (o `.\run.bat` en la terminal).
- **Linux / macOS:** `./run.sh`

<details>
<summary><b>Instalación manual paso a paso</b></summary>

```bash
git clone https://github.com/Brimx/Melomaniac.git
cd Melomaniac
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

En Windows/PowerShell, si la activación está bloqueada (`running scripts is disabled`), usa `run.bat` o la ruta directa: `.\.venv\Scripts\python app.py`. En Ubuntu/Debian, si aparece `ensurepip is not available`, instala `sudo apt install python3-venv`.

</details>

### Actualización

```bash
git pull
```

Vuelve a arrancar con `run.bat`/`run.sh` (detectan cambios en `requirements.txt` e instalan dependencias automáticamente). `config/`, descargas y cachés están ignorados por Git y no se tocan. Si `ffmpeg` no se detecta en Descargas, reinicia la terminal para que cargue el nuevo `PATH`.

---

## Uso

1. En la pantalla inicial elige origen (plataforma, archivo local o texto pegado) y destino (una plataforma de streaming, o *Archivo Local (Exportar)*).
2. Carga la playlist (ID o URL, archivo o lista pegada) y revisa las pistas; desmarca las que no quieras.
3. Opcional: **Organizar** (ordenar/agrupar) o **Dividir** (bloques manuales) y alterna entre vistas Lista, Doble y Preview.
4. Pulsa **Transferir**, confirma nombre y descripción, y sigue el progreso. Al terminar, el reporte Post-Mortem permite buscar alternativas para las pistas dudosas.
5. Reproduce cualquier archivo local o descargado desde la barra inferior o desde Biblioteca/Descargas.

La primera ejecución muestra una bienvenida guiada (reabrible desde Config).

### Credenciales y configuración

La sesión se resuelve en memoria desde tu navegador (Firefox/Chromium). Si necesitas credenciales manuales, el asistente (icono de engranaje del rail lateral) las guarda en `config/`:

| Archivo | Uso |
|---|---|
| `config/browser.json` | YouTube Music: `Cookie` y `Authorization` (`SAPISIDHASH`) |
| `config/.env` | Apple Music: `APPLE_AUTH_BEARER`, `APPLE_MUSIC_USER_TOKEN` |
| `config/spotify_cookies.json` | Spotify: `sp_dc`, `sp_key` |
| `config/settings.json` | Formato de descarga, carátulas online y carpetas locales |
| `config/search_cache.json` | Caché de búsquedas previas |
| `config/transfer_history.jsonl` | Historial de transferencias y resoluciones |

Los archivos de `config/` equivalen a tu sesión: no los compartas ni los subas a Git.

---

## Verificación

```bash
python -m compileall -q app.py core engine services ui scripts
```

La UI de Flet se valida manualmente (modos de vista, diálogos Organizar/Dividir, navegación responsive, exportación). La carpeta `tests/` está reservada para regresiones futuras.

## Agradecimientos

[Flet](https://flet.dev) · [ytmusicapi](https://github.com/sigma67/ytmusicapi) · [spotapi](https://github.com/spotapi) · [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) · [Mutagen](https://mutagen.readthedocs.io/)
