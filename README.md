# 🎵 MelomaniacPass

**Transfiere tus playlists entre YouTube Music y Apple Music — con matching inteligente de canciones.**

MelomaniacPass es una aplicación de escritorio que te permite mover tus playlists de una plataforma de streaming a otra. Encuentra cada canción en la plataforma destino usando matching fuzzy, así no pierdes canciones por diferencias de nombres, remasterizaciones o versiones en vivo.

> **¿Por qué existe?** Las plataformas de streaming no te dejan exportar ni importar playlists entre ellas. Si quieres pasar de YouTube Music a Apple Music (o viceversa), tendrías que reconstruir cada playlist a mano. MelomaniacPass automatiza eso.

---

## ✨ Características

- **Transferencia entre plataformas** — Mueve playlists entre YouTube Music y Apple Music
- **Archivos locales** — Importa playlists desde CSV, M3U, XSPF, WPL, iTunes XML o texto plano
- **Matching inteligente** — El motor Hunter Recovery usa matching fuzzy para encontrar la canción correcta incluso cuando títulos/artistas difieren
- **Transferencia en lote** — Transfiere cientos de canciones concurrentemente con seguimiento de progreso
- **Reporte post-mortem** — Ve exactamente qué canciones coincidieron, cuáles fallaron y por qué
- **Gestor de credenciales integrado** — Wizard de configuración con instrucciones paso a paso para extraer tokens desde tu navegador
- **Protección contra rate-limit** — Circuit breakers que previenen baneos de API y muestran temporizadores
- **Organizar y dividir** — Ordena y segmenta tus playlists por artista, álbum o plataforma antes de transferir

---

## 📋 Requisitos

| Requisito | Versión |
|---|---|
| Python | 3.10+ |
| SO | Linux, macOS, Windows |

---

## 📦 Instalación

```bash
# Clonar el repositorio
git clone https://github.com/Brimx/MelomaniacPass.git
cd MelomaniacPass

# Crear y activar entorno virtual
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .venv\Scripts\activate         # Windows

# Instalar dependencias
pip install flet ytmusicapi requests python-dotenv rapidfuzz
```

---

## 🚀 Uso

```bash
python app.py
```

Se abrirá la ventana de la aplicación. Así se usa:

### 1. Configurar credenciales

En el primer arranque, la app verifica las credenciales de ambas plataformas. Si faltan o están expiradas, se abre el **Wizard de Configuración** automáticamente.

**YouTube Music:**
1. Abre [music.youtube.com](https://music.youtube.com) en tu navegador
2. Pulsa `F12` → ve a la pestaña **Network**
3. Filtra por `browse` y busca una petición POST
4. Copia el header `Authorization` (empieza con `SAPISIDHASH...`)
5. Copia el header `Cookie`
6. Pega ambos en el wizard → **Guardar y Aplicar**

**Apple Music:**
1. Abre [music.apple.com](https://music.apple.com) en tu navegador
2. Pulsa `F12` → ve a la pestaña **Network**
3. Filtra por `catalog` y busca una petición GET
4. Copia el header `Authorization` (empieza con `Bearer eyJ...`)
5. Copia el header `media-user-token`
6. Pega ambos en el wizard → **Guardar y Aplicar**

### 2. Cargar una playlist

- **Desde una plataforma de streaming:** Selecciona la plataforma origen, pega un ID de playlist y pulsa **Cargar**
- **Desde un archivo local:** Selecciona "Archivo Local" como origen, pulsa **Cargar** y elige un archivo (`.csv`, `.m3u`, `.xspf`, `.wpl`, `.xml`, `.txt`)
- **Desde texto pegado:** Selecciona "Pegar Texto" como origen, pulsa **Cargar** y pega tu lista de canciones (una por línea, formato: `Título - Artista`)

### 3. Revisar y transferir

- Usa los checkboxes para seleccionar qué canciones transferir
- Usa **Organizar** para ordenar por artista/álbum/título, o **Dividir** para agrupar por atributo
- Selecciona la plataforma destino
- Pulsa **Transferir**

### 4. Revisar resultados

- Observa la barra de progreso y el log en vivo en el panel de telemetría
- Al completar, un snackbar muestra cuántas canciones se transfirieron correctamente
- Pulsa **Ver Detalles** para abrir la pestaña Post-Mortem y ver qué canciones fallaron y por qué
- Exporta el reporte a un archivo de texto si lo necesitas

---

## 🔧 Archivos de configuración

MelomaniacPass usa dos archivos de configuración (ambos están en `.gitignore`):

| Archivo | Plataforma | Propósito |
|---|---|---|
| `.env` | Apple Music | Guarda `APPLE_AUTH_BEARER` y `APPLE_MUSIC_USER_TOKEN` |
| `browser.json` | YouTube Music | Guarda los headers de sesión (`Authorization` + `Cookie`) |

Ambos se gestionan desde el Wizard de Configuración de la app — no necesitas editarlos manualmente.

---

## 📁 Estructura del proyecto

```
melomaniacpass/
├── app.py              # Punto de entrada
├── auth_manager.py     # Gestión de credenciales y Wizard de configuración
├── core/               # Gestión de estado y modelos de datos
├── services/           # Fachada de APIs (YouTube Music + Apple Music)
├── engine/             # Matching, normalización, parsers, organizador
├── ui/                 # Componentes de interfaz (Flet)
└── utils/              # Circuit breaker
```

Para un desglose detallado de la arquitectura, responsabilidades de cada módulo y flujo de datos, ver **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## 📜 Licencia

Este proyecto es para uso personal. El matching de canciones depende de las APIs de YouTube Music y Apple Music, que pueden tener sus propios términos de servicio.

---

## 🙏 Agradecimientos

- [Flet](https://flet.dev) — Framework de UI para Python
- [ytmusicapi](https://github.com/sigma67/ytmusicapi) — Wrapper de la API de YouTube Music
- [RapidFuzz](https://github.com/maxbachmann/RapidFuzz) — Matching fuzzy de alto rendimiento