# 🎵 MelomaniacPass

**Transfiere playlists entre YouTube Music y Apple Music mediante matching inteligente de canciones.**

MelomaniacPass es una aplicación de escritorio para cargar una playlist desde YouTube Music, Apple Music o una fuente local, encontrar sus canciones en la plataforma destino y crear allí una nueva playlist. El matching fuzzy tolera diferencias de títulos, artistas, remasterizaciones y versiones en vivo.

> **¿Por qué existe?** Las plataformas de streaming no ofrecen una forma universal de exportar e importar playlists entre sí. MelomaniacPass automatiza la reconstrucción y deja un reporte de las coincidencias, fallos y casos que requieren revisión.

---

## ✨ Características

- **Transferencia entre plataformas** — YouTube Music ↔ Apple Music.
- **Fuentes locales** — CSV, M3U/M3U8, PLS, XSPF, WPL, iTunes XML y texto plano.
- **Hunter Recovery** — búsqueda con queries alternativas, normalización y scoring fuzzy.
- **Transferencia concurrente** — procesa varias canciones con límite de concurrencia para cuidar las APIs.
- **Post-mortem** — muestra coincidencias, canciones no encontradas, errores y razones de fallo; permite exportar el reporte.
- **Configuración guiada** — wizard para guardar y recargar credenciales sin reiniciar la aplicación.
- **Protección contra rate limits** — circuit breakers, reintentos con backoff y temporizadores visibles.
- **Organizar y dividir** — ordena o agrupa canciones por artista, álbum, título, duración o plataforma.
- **Interfaz de escritorio** — UI en Flet con búsqueda, selección, progreso, telemetría y estados por canción.

## 📋 Requisitos

| Requisito | Versión / detalle |
|---|---|
| Python | 3.10+ |
| Sistema operativo | Linux, macOS o Windows |
| Dependencias | `flet`, `ytmusicapi`, `requests`, `python-dotenv`, `rapidfuzz` |
| Credenciales | Sesión de YouTube Music y/o tokens de Apple Music para fuentes remotas |

El repositorio todavía no incluye `requirements.txt` ni `pyproject.toml`; las dependencias se instalan manualmente por ahora.

## 📦 Instalación

```bash
git clone https://github.com/Brimx/MelomaniacPass.git
cd MelomaniacPass

python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

pip install flet ytmusicapi requests python-dotenv rapidfuzz
```

## 🚀 Uso

```bash
python app.py
```

### 1. Configurar credenciales

En el primer arranque, el pre-flight check valida las sesiones disponibles. Si falta una credencial o expiró, abre el **Configuration Wizard** en la pestaña de la plataforma correspondiente.

**YouTube Music**

1. Abre [music.youtube.com](https://music.youtube.com) e inicia sesión.
2. Abre DevTools (`F12`) → **Network** y localiza una petición de API, por ejemplo `browse`.
3. Copia los headers `Authorization` y `Cookie`.
4. Pégalos en el wizard y pulsa **Guardar y Aplicar**.

**Apple Music**

1. Abre [music.apple.com](https://music.apple.com) e inicia sesión.
2. En DevTools → **Network**, localiza una petición de catálogo.
3. Copia `Authorization` y `media-user-token`.
4. Pégalos en el wizard y pulsa **Guardar y Aplicar**.

Las credenciales son datos de sesión sensibles. No las compartas ni las confirmes en Git.

### 2. Cargar una playlist

- **Streaming:** selecciona YouTube Music o Apple Music, introduce el ID de playlist y pulsa **Cargar**.
- **Archivo local:** selecciona **Archivo Local**, elige un archivo compatible y asigna un nombre.
- **Texto pegado:** selecciona **Pegar Texto** y pega una canción por línea, preferiblemente en formato `Título - Artista` o `Artista - Título`.

### 3. Revisar y transferir

Selecciona las canciones, usa **Organizar** o **Dividir** si lo necesitas, elige la plataforma destino y pulsa **Transferir**. Para fuentes locales es obligatorio seleccionar el destino antes de transferir.

### 4. Revisar resultados

La UI muestra progreso y telemetría en vivo. Al terminar, **Ver Detalles** abre el panel Post-Mortem con coincidencias, elementos no encontrados, errores y casos de baja confianza. El reporte puede exportarse como `transfer_failed_report.txt`.

## 🔧 Archivos de configuración

Ambos archivos están excluidos por `.gitignore` y se gestionan desde el wizard:

| Archivo | Plataforma | Contenido |
|---|---|---|
| `.env` | Apple Music | `APPLE_AUTH_BEARER` y `APPLE_MUSIC_USER_TOKEN` |
| `browser.json` | YouTube Music | Headers de sesión, incluidos `Authorization` y `Cookie` |

## 📁 Estructura del proyecto

```text
melomaniacpass/
├── app.py              # Punto de entrada, composición y limpieza
├── auth_manager.py     # Credenciales, pre-flight y wizard
├── core/               # Modelos y estado observable de la aplicación
├── services/           # Fachada de APIs de YouTube Music y Apple Music
├── engine/             # Normalización, matching, parsers y organización
├── ui/                 # Interfaz Flet, filas, widgets y telemetría
├── utils/              # Circuit breaker y errores de rate limit
└── resources/fonts/    # Fuentes IBM Plex Sans incluidas
```

Para el desglose de responsabilidades, dependencias y flujos de datos, consulta **[ARCHITECTURE.md](ARCHITECTURE.md)**. La versión en inglés está en **[ARCHITECTURE.en.md](ARCHITECTURE.en.md)**.

## Estado actual

La rama actual es `beta`. La base está organizada en módulos y cuenta con el flujo principal de carga, matching, transferencia, autenticación, telemetría y limpieza de recursos. La búsqueda de candidatos de Apple Music usa iTunes Search API para reducir bloqueos 423/429; las operaciones autenticadas continúan usando Apple Music. No se observan tests automatizados ni configuración de empaquetado en el repositorio; la validación actual se realiza ejecutando la aplicación y revisando los flujos de UI/API.

## 📜 Licencia

Proyecto para uso personal. El acceso a YouTube Music y Apple Music depende de sus APIs, sesiones y términos de servicio respectivos.

## 🙏 Agradecimientos

- [Flet](https://flet.dev) — framework de UI para Python.
- [ytmusicapi](https://github.com/sigma67/ytmusicapi) — wrapper de YouTube Music.
- [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) — matching fuzzy de alto rendimiento.
