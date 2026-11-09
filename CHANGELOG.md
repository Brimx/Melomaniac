# Changelog

## 4.9.0 — 2026-11-08 · Phosphor Icons y Bloque 1 de fiabilidad

> Cierra el **Bloque 1 — Fiabilidad** del roadmap (1.1–1.11) y la migración de la interfaz a Phosphor Icons que ya estaba en `main`. Los pendientes de los bloques 2 a 5 quedan fuera a propósito.

### Identidad visual

- **Migración a Phosphor Icons:** `ui/icons.py` centraliza los glifos de Phosphor 2.1.2 en `PIcons` y `ui/fonts.py` registra `Phosphor` y `Phosphor Fill`. Se migraron bienvenida, rail de navegación, reproductor, paneles de Config/reportes, listas, Biblioteca y Descargas, y se desactivó el splash nativo de Material. `app_icon` y `app_icon_button` normalizan la geometría de los glifos y el padding de los botones.
- **Animaciones de paneles:** el panel de cola de reproducción pasa a ser un overlay deslizante por transformación (`offset`/opacidad) en vez de animar su ancho dentro del `Row`, así que abrirlo ya no redimensiona las listas ni la grilla de Biblioteca. El colapso del rail recorta las etiquetas por ancho/opacidad con clip. Las tarjetas de Biblioteca conservan su tamaño fijo dentro de la celda de grilla.

### Fiabilidad

- **1.1 Validación de sesión antes de transferir:** `auth_session_ok` nace en `None` (sin validar) en lugar de `True`, y los iconos de sesión distinguen «sin validar» de «expirada». `_on_transfer` hace un preflight real del destino (`check_sessions`) después de comprobar selección y divisiones, y bloquea con un mensaje accionable si la sesión no responde.
- **1.2 Metadatos con Mutagen:** cuando la pista no trae artista, se deriva de `album_artist` o del nombre `Artista - Título` del archivo en vez de escribirse vacío. Se verificó MP3, M4A, Opus, FLAC y WAV; Opus y WAV conservan su `cover.jpg` lateral.
- **1.3 Portadas con prioridad 1:1:** las miniaturas de vídeo de YouTube (`i.ytimg.com`, `img.youtube.com`) se identifican por host y ruta y ya no entran como respaldo cuando existe cualquier artwork de álbum. `_resolve_artwork` exige además que la portada pase la validación de tamaño.
- **1.4 Recorte de barras:** `normalize_cover` detecta letterbox y pillarbox y recorta solo el borde uniforme, con topes por eje (12 px de tolerancia, 30 % máximo) y una guarda que impide recortar una imagen completamente uniforme. Se aplica antes de la rama cuadrada, así que un 1:1 con barras internas también se corrige.
- **1.5 Recalcular portadas existentes:** Config añade «Recalcular portadas», que informa cuántas cambiarían, agrupa por origen y pide confirmación antes de sobrescribir. `write_cover` escribe solo la portada, sin depender de que haya otros metadatos.
- **1.6 Mejor selección de yt-dlp:** la búsqueda pide `ytsearch5`, filtra directos y vídeos de más de 20 minutos y ordena por similitud de título/artista más cercanía de duración, en lugar de quedarse con el primer resultado. La resolución automática y la búsqueda manual comparten el mismo ranking.
- **1.7 Conservar playlist, orden y portada:** cada playlist descargada va a su subcarpeta con `playlist.json` (nombre, plataforma, orden original y ruta de cada pista). La Biblioteca lista esas subcarpetas como playlists descargadas dentro de Local y respeta el orden del manifiesto al abrirlas.
- **1.8 Pausa real de descargas:** pausar detiene también la pista en curso. yt-dlp no tiene pausa nativa, así que se aborta desde el progress hook, se conserva el `.part` y se reanuda con `continuedl` desde donde estaba. La UI muestra «En pausa · N pendientes» y alterna entre Pausar y Reanudar.
- **1.9 Rendimiento de bibliotecas locales:** índice persistente en `config/local_index.json` con validez por `path + mtime + size`, caché de portadas por carpeta y escaneo del detalle en un worker. El recuento de las tarjetas se detiene a 150 archivos. Medido con 300 archivos: 0,571 s la primera lectura y 0,014 s la segunda.
- **1.10 Animación de selección en Biblioteca:** la fila de una playlist restaura `scale` y `opacity` en `finally` y libera el task al terminar, así que un cambio de vista durante la animación ya no deja la fila encogida.
- **1.11 Panel de resultados de descarga:** el cierre de la cola agrupa descargadas, omitidas, fallidas y faltantes con formato, duración, ruta y motivo por pista. Las fallidas y las que nunca se intentaron permiten rebuscar en YouTube, ver los cinco candidatos con su duración y descargar otro, o reintentar la pista en otro contenedor.

## 4.8.2 — 2026-11-06 · Bienvenida guiada, bundle ZIP y empaquetado standalone

- **Bienvenida de primera ejecución:** overlay a pantalla completa (`ui/onboarding.py`, pasos en `ONBOARDING_STEPS`, fondo en `ui/onboarding_bg.py`) con siete pasos (bienvenida, Gestión, Biblioteca/Descargas, preferencias, sistema y actualizaciones, conectar plataformas, listo). Al finalizar guarda `onboarding_done` en `config/settings.json`; se reabre desde Config.
- **Integración con el sistema y accesos directos (`services/desktop_integration.py`):** creación e instalación de lanzadores `.desktop` en Linux (`$XDG_DATA_HOME/applications` e iconos hicolor) y accesos directos `.lnk` en Windows. Distingue entre AppImage (detecta `$APPIMAGE`), paquete binario, instalador de Inno Setup (que preserva accesos en actualización) y ZIP portable. Auto-sincroniza silenciosamente el lanzador en el arranque (`app.py`) si la ruta del AppImage o ejecutable cambia tras una actualización; solo actúa en builds empaquetadas, nunca al ejecutar desde el código fuente.
- **Comprobación de actualizaciones (`services/update_checker.py`):** consulta a GitHub Releases desde Config con instrucciones contextuales según el modo de empaquetado del usuario.
- **Bundle ZIP portátil:** `scripts/build_release_bundle.py` genera `Melomaniac-vX.Y.Z.zip` con los lanzadores `run.sh`/`run.bat` incluidos, reutilizable para cualquier versión.
- **Empaquetado standalone Linux y Windows con CI:** `scripts/build_linux.py` (PyInstaller + appimagetool) produce el AppImage y el `tar.gz`; `scripts/windows/installer.iss` (Inno Setup) y `python -m flet_cli.cli pack` producen el instalador y el ZIP de Windows. El workflow `.github/workflows/build-binaries.yml` compila ambos en GitHub Actions ante cada tag `v*` y sube los binarios a los assets del release (sin archivos `.txt` de hashes: GitHub muestra el SHA-256 de cada asset). El comando de empaquetado es `python -m flet_cli.cli pack` y `flet-cli==1.0.5` queda fijado en `requirements.txt`. En binarios (`sys.frozen`), `services/authentication.py` desacopla `CONFIG_DIR` de la carpeta de instalación (`$XDG_CONFIG_HOME/melomaniac` en Linux, `%APPDATA%\Melomaniac` en Windows), preservando el modo portable si existe `config/` escribible o `portable.flag`.

## 4.8.0 — 2026-10-29 · Reproductor integrado, sesión de navegador y MPRIS

> Versión de trabajo consolidada que incorpora el reproductor de audio nativo, derivación de sesiones de navegador en memoria y exposición D-Bus/MPRIS para el escritorio. Tag anterior: `v4.6.0`.

- **Lanzadores rápidos de un clic:** `run.bat` (Windows) y `run.sh` (Linux/macOS) automatizan la comprobación de Python, creación del entorno virtual `.venv`, instalación de dependencias y ejecución de la app en un solo paso.
- **Reproductor de audio integrado:** barra inferior persistente `PlayerBar` con `flet-audio` 1.0.5 (`FLET_DESKTOP_FLAVOR=full`), cola de reproducción reordenable `PlayQueue` y panel `PlayerQueuePanel`. Soporte de reproducción para FLAC, MP3, M4A, Opus y WAV. Control de volumen, mute, shuffle y modos repeat persistidos.
- **Exposición MPRIS (D-Bus) en Linux:** nuevo servicio `services/mpris_service.py` (`org.mpris.MediaPlayer2.melomaniac`) que expone las interfaces estándar `MediaPlayer2` y `Player`. Permite control por teclas multimedia y widgets de escritorio (Caelestia/Quickshell, playerctl, Waybar, GNOME/KDE). Incluye señal `Seeked`, sincronización de estado, métodos de transporte y conversión de portadas locales en Data URIs a archivos en caché `config/cover_cache/embedded_{hash}.jpg` (`file:///...`).
- **Sesión de navegador en memoria (Plan A):** `services/browser_session.py` resuelve sesiones activas de YouTube Music y Spotify directamente desde Firefox (copia en RAM de `cookies.sqlite`) y Chromium vía `yt-dlp`. Elimina la necesidad de exportar cookies manuales sin persistir `SAPISIDHASH` ni credenciales sensibles en disco.
- **Módulo Config y portadas:** vista `ui/config_view.py` para preferencias de descargas, carpetas destino y caché de portadas. Cadena de resolución de portadas por álbum (`services/covers.py`) con CircuitBreaker dedicado y caché atómica.
- **Biblioteca y Gestión:** integración de «Crear playlist» desde el detalle de carpetas locales en Biblioteca, selector de fuentes compacto y acciones de reproducción directa por pista en Biblioteca y Descargas.

## 4.7.0 — 2026-10-25 · Descargas de audio y mejoras de Biblioteca

> Versión de trabajo que consolida los commits posteriores al tag `v4.6.0`.

- Descargas: nueva cola de audio con `yt-dlp`; resuelve pistas en YouTube, extrae solo audio, escribe metadatos/portada y admite MP3, M4A, Opus, FLAC y WAV. La UI incluye progreso, resultados y cancelación.
- Descargas: preflight de FFmpeg. Sin `ffmpeg` en `PATH` la cola falla en bloque con un banner accionable y el botón de descarga deshabilitado, en lugar de fallar pista por pista. `DownloadResult.code` expone `NO_FFMPEG`.
- Portadas: nueva cadena de resolución por álbum (`services/covers.py`) sobre `services/cover_cache.py`: portada de la plataforma (subiendo la resolución del CDN de YouTube), ISRC en MusicBrainz/CAA, iTunes y Deezer, con validación de álbum/artista para descartar homónimos. Una sola consulta por `album_artist|album|año`.
- Portadas: todo se normaliza a JPEG cuadrado de 800 px. Una imagen no cuadrada se rellena con fondo neutro, nunca se recorta: un thumbnail 16:9 de YouTube ya no se usa como artwork.
- Portadas: `read_cover` lee el APIC de cualquier MP3 real (antes solo lo hacía si el objeto cargado ya era `ID3`) y `write_cover_sidecar` deja `cover.jpg` en Opus y WAV, que no admiten portada incrustada fiable.
- Descargas: estado «omitida» para archivos que ya existían, con resumen `OK / Fallidas / Omitidas`, y el formato elegido se persiste en `settings.json`.
- Config: el cuarto destino del rail deja de ser un placeholder. Ajusta formato de descarga, permiso de portadas online, carpeta destino y limpieza del caché de portadas. El wizard de credenciales sigue en el engranaje.
- Descargas reutiliza `source_url` para pistas de YouTube Music y la sesión existente en memoria si necesita cookies; no crea `cookies.txt`. No incluye reproducción integrada ni poda automática de caché.
- YouTube Music: estabiliza el preflight y la renovación de sesión; corrige la Biblioteca cuando el servidor responde con pantalla de inicio de sesión.
- Spotify: reduce el bloqueo durante la sincronización de Biblioteca al diferir la carga del detalle y aplicar timeout por página.
- Diseño: se probaron `#3B5AFF`, `#214AFF` y `#1B4CB6`; se restauró el acento original `#4F8BFF`.
- Dependencias: alinea `flet`, `flet-desktop` y `flet-audio` en `1.0.5`, e incorpora `yt-dlp`.
- Reproductor: el servicio `flet_audio.Audio` se crea en la primera reproducción y siempre con `src`. Crearlo vacío hacía que su `update()` en Dart lanzara `Audio must have "src" specified` y dejara el servicio inservible: la barra mostraba `0:00 / 0:00` y no sonaba nada. Los errores de `play` dejan de silenciarse y se registran en el panel de Consola.
- Escritorio: `app.py` fija `FLET_DESKTOP_FLAVOR=full` antes de lanzar el cliente. El flavor `light` (el predeterminado en Linux) no incluye el plugin de audio y el reproductor quedaba mudo sin ningún error visible.
- Bibliotecas y portadas: el orden de proveedores depende del origen. Apple Music y Spotify usan su artwork cuadrado; las URL del CDN de YouTube pasan detrás de iTunes/Deezer, porque son miniaturas de vídeo y no carátulas de álbum.
- Portadas: la miniatura de fila, tarjeta y detalle recibe el tamaño de su recuadro (`_thumb_content_for(..., box=N)`). `ft.Image` no admite `alignment` en Flet 1.0.5 y una portada con dimensiones inesperadas quedaba descentrada.
- Biblioteca: selector de fuente compacto de una sola fila con cuatro segmentos (YouTube / Apple / Spotify / Local), en lugar de etiquetas largas que se partían en tres filas.
- Biblioteca: el detalle de una carpeta local ofrece «Crear playlist», que carga sus pistas en Gestión con origen «Archivo Local» listo para elegir destino y transferir.
- Config: la vista usa el mismo chasis que Descargas (padding de 16, cabecera fija y divisoria) y la carpeta destino aparece desde el primer render.

## 4.6.0 — 2026-10-23 · Búsqueda deduplicada, variantes cirílicas y aborto temprano

> Release que consolida la versión de trabajo `4.5.2` (nunca etiquetada) y los cambios posteriores. Tag anterior: `v4.5.1`.

- Búsqueda: `SingleFlight` deduplica pistas equivalentes concurrentes por plataforma, metadatos limpios, duración, explicit e ISRC. Los errores no se cachean y cancelar un seguidor no cancela al líder.
- Matching: variantes de consulta ampliadas con transliteración cirílica (ruso, ucraniano, bielorruso y serbio) y fallback solo título. Se conserva el mejor resultado y solo se sigue buscando si no hay match o hay `needs_review`.
- Caché: `search_cache.json` se persiste cada `SEARCH_CACHE_FLUSH_EVERY=25` entradas y al cerrar la transferencia, en lugar de reescribirse por cada pista.
- Robustez: tras `TRANSFER_MAX_CONSECUTIVE_ERRORS=8` errores de red consecutivos se aborta la transferencia; las pistas no procesadas quedan `pending` y el reporte las marca como interrumpidas. Los fallos transitorios ya no se guardan como `not_found`.
- Consolida el trabajo de `4.5.2`: reporte de cierre con alternativas, rail flotante, normalización de IDs/URLs, paginación paralela de Apple, migración a Flet 1.0.1 y correcciones de Biblioteca/Spotify.

### Commits incluidos desde v4.5.1

- `a3744c4` fix: elimina playlists de Spotify con el ID correcto
- `4f8efab` fix: normaliza IDs y URLs de playlists
- `03c3980` docs: documenta rail flotante y siguientes pasos
- `65ecc69` feat(ui): rail flotante colapsable con Config al fondo
- `483a108` fix: recuerda alternativas resueltas en la caché de búsquedas
- `8e24020` fix: acelera inserciones de Spotify y ajusta UI del reporte
- `95fd270` perf: optimiza renders y operaciones masivas en la UI
- `a02b392` Update documentation and design specifications for Melomaniac v4.5.2
- `4f4784f` feat: reporte de cierre con búsqueda de alternativas y verificación Apple
- `040bbe2` fix: Solucionados errores en la biblioteca relacionados a spotify y apple music
- `536de83` chore: deja AGENTS.md fuera del repo (local + ignorado)
- `9a98fbc` chore: gitignore actualized
- `06fa308` chore: bump 4.5.1 -> 4.5.2
- `bde17a3` docs: agrega AGENTS.md con reglas del proyecto
- `a0247d6` chore: flet 0.86.5 -> 1.0.1 + gitignore opencode.json
- `f132995` fix(ui): migracion Flet 1.0 con input_border canonico + import faltante en song_row
- `8f939dc` fix(biblioteca): skeleton paridad Inicio + album + scale + Apple count + Spotify ctor + launch_url
- `bff3cff` fix: PopupMenuItem content= en vez de text= (Flet 0.86) + Editar queda Outlined aparte
- `11fc6d0` feat: division preview popup, transfer por division con guardar/siguiente, editar expand con aplicar/revertir
- `c5ca5e3` fix: mejoras Dividir, correcciones, UI, lógica y experiencia

## 4.5.2 — 2026-10-05 · F6 + migración Flet 1.0

> Versión de trabajo documentada; no se creó un tag `v4.5.2`. Su contenido se consolida en `4.6.0`.

- Migra a `flet==1.0.1`: helper canónico `input_border()` en `ui/widgets.py`, `Icon.name` → `Icon.icon`, `PopupMenuButton` con `icon=` nativo.
- Corrige `NameError` por import faltante de `input_border` en `ui/song_row.py` que rompía `_sync_list_view`.

- Refina las vistas **Lista**, **Doble** y **Preview**, los skeletons responsive y la navegación `NavigationRail`.
- 2026-10-22: rail de navegación flotante y docked (`68 px` colapsado / `220 px` expandido), con «Gestión» en lugar de «Inicio», Config al fondo y labels animados sin saltos. Reutiliza radios canónicos en inputs, botones y diálogos.
- 2026-10-22: normaliza IDs y URLs de playlist (`spotify:playlist:<id>`, enlaces web de Spotify/Apple/YouTube) al cargar, crear, insertar y borrar. Corrige el doble prefijo de Spotify en la inserción manual y la URL de «Abrir playlist»; la UI de carga acepta ID o URL.
- 2026-10-22: corrige la eliminación de playlists de Spotify: `PrivatePlaylist.delete_playlist()` no recibe el ID; se construye con el ID y se invoca sin argumentos. Verificado con una playlist temporal creada y eliminada en vivo.
- Mejora Organizar/Dividir: preview por división, transferencia secuencial por división, y aplicar/revertir ajustes; la UI actual no tiene selector de alcance y `platform` no es criterio.
- Corrige la Biblioteca existente: skeletons, metadatos de álbum, conteo Apple Music, inicialización Spotify y apertura de URLs externas; Descargas sigue en desarrollo.
- Migra a Flet 1.0.1 y corrige compatibilidad de controles, incluido `PopupMenuItem`.
- Actualiza reglas locales de `AGENTS.md`, `.gitignore` y documentación bilingüe para reflejar el estado vigente.
- Documenta `4.5.2` como versión de trabajo actual.
- 2026-10-16: corrige errores de Biblioteca relacionados con Spotify y Apple Music.
- 2026-10-22: Apple Music pasa a `httpx.Client` y las playlists de más de 100 pistas se paginan por `offset` en paralelo (`APPLE_PAGE_WORKERS=5`), reutilizando la primera página de `include=tracks`. Lectura de 1.022 pistas: 8,5 s → 2,1 s con resultado y orden idénticos. `requests` se mantiene para YouTube Music porque `ytmusicapi` exige `requests.Session`.
- 2026-10-22: reporte de cierre de transferencia siempre visible (`ui/transfer_report.py`): nombre/descripción, confirmadas, tiempo y faltantes agrupados (`not_found`, `revision`, `api_rejected`, `error`). Permite re-buscar alternativas con consulta editable e insertarlas en la playlist recién creada. Historial append-only en `config/transfer_history.jsonl`.
- 2026-10-22: Apple verifica la playlist creada (`_am_playlist_track_ids`) y devuelve los IDs realmente rechazados; las playlists de catálogo sin `meta.total` se paginan por olas hasta la primera página incompleta.
- 2026-10-22: retira `spotipy`, `pymemcache` y `fonttools` de `requirements.txt` (sin imports ni dependencias activas).
- 2026-10-23: adopta patrones de la investigación externa sin copiar código: singleflight de búsquedas en `MusicApiService.search_with_fallback` (clave por plataforma, metadatos limpios, duración, explicit e ISRC), persistencia de `search_cache.json` por lotes (`SEARCH_CACHE_FLUSH_EVERY=25`) con flush al cerrar la transferencia, transliteración cirílica + fallback solo título con ranking del mejor resultado, y aborto temprano tras 8 errores de red consecutivos (`TRANSFER_MAX_CONSECUTIVE_ERRORS=8`) sin cachear fallos transitorios como `not_found`. Validado con `compileall` y pruebas ad hoc; medición con playlists reales pendiente.

## 4.0.0 — 2026-09-19 · Export local + orden configurable + preparación navegación

- **BREAKING**: Bump semver a `4.0.0` por cambios de arquitectura (export/import con orden, destino local, base para Rail/Tabs/Drawer y doble vista Organizar/Dividir). Renombrado visible a **Melomaniac** (sin sufijo Pass).
- `engine/parsers.py`: añade `order="artist-title"|"title-artist"` a `_parse_local_line/_parse_csv/_parse_wpl/parse_local_playlist` con correct handling NFC y fallback posicional; default `artist-title` compatible TuneMyMusic.
- `engine/exporters.py` nuevo espejo de parsers: `export_tracks`/`default_export_path` con `pathlib` + `FilePicker.save_file`, formatos TXT/CSV/M3U8/XSPF, headers según orden, saneado de nombres, ISRC en XSPF.
- `core/config.py`: `EXPORT_DEST_LABEL`, `EXPORT_FORMATS/ORDERS`, `DEFAULT_EXPORT_ORDER/FORMAT`.
- `core/state.py`: `local_parse_order/local_export_order/local_export_format/local_export_dir` en `AppState`.
- `ui/main_ui.py`: destino `Archivo Local (Exportar)` en dropdown, botón `Transferir↔Exportar` dinámico, diálogos `Pegar Texto` y `Nombra playlist` con `SegmentedButton` de orden, `_open_export_dialog` con formato/orden/ruta + `FilePicker`.
- Actualiza `ARCHITECTURE.md`/`README.md` y headers a `v4.0.0`.

## 4.0.0 — 2026-09-16 · Actualización documental

- Sincroniza `README.md` y `README.en.md` con las funcionalidades actuales: importación de audio y metadatos Mutagen, escaneo de disponibilidad, caché, límites de concurrencia y lotes por plataforma.
- Actualiza `ARCHITECTURE.md` y `ARCHITECTURE.en.md` con la composición real de `AppState`, `MusicApiService`, `AuthManager` y la UI.
- Documenta el comportamiento actual de descripciones en Spotify, la resolución de ISRC en Apple Music y los formatos locales soportados.

## 4.0.0 — 2026-09-09

- Permite editar el nombre y la descripción de la playlist antes de iniciar la transferencia.
- Añade el álbum como metadato visible en cada fila de la playlist.
- Corrige el diálogo modal de metadatos: el backdrop ahora es hijo directo del `Stack` raíz, gestiona `on_click` directamente y usa `ink=False`.
- Reorganiza la configuración runtime en `config/` y mueve autenticación, metadatos de audio y resiliencia a sus paquetes correspondientes.
- Mantiene la versión del proyecto alineada en `4.0.0`.
- Incluye pruebas unitarias para ISRC, Mutagen y Apple Music; la UI continúa validándose manualmente.
