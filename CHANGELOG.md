# Changelog

## 4.5.2 — 2026-10-05 · F6 + migración Flet 1.0

> Versión de trabajo documentada; no se ha creado un tag `v4.5.2`.

- Migra a `flet==1.0.1`: helper canónico `input_border()` en `ui/widgets.py`, `Icon.name` → `Icon.icon`, `PopupMenuButton` con `icon=` nativo.
- Corrige `NameError` por import faltante de `input_border` en `ui/song_row.py` que rompía `_sync_list_view`.

- Refina las vistas **Lista**, **Doble** y **Preview**, los skeletons responsive y la navegación `NavigationRail`.
- 2026-10-22: rail de navegación flotante y docked (`68 px` colapsado / `220 px` expandido), con «Gestión» en lugar de «Inicio», Config al fondo y labels animados sin saltos. Reutiliza radios canónicos en inputs, botones y diálogos.
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
