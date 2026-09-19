# Changelog

## 4.1.0 — Unreleased · F6: doble vista, alcance y exportación local

- Añade los modos **Lista**, **Doble** y **Preview** para Organizar/Dividir, con `AnimatedSwitcher`, skeleton responsive y navegación `NavigationRail`.
- Permite aplicar Organizar/Dividir al alcance **Todo**, **Visibles** o **Seleccionadas**; `platform` deja de ser un criterio disponible.
- Añade exportación local en `TXT`, `CSV`, `M3U`, `M3U8` y `XSPF`, con orden artista-título o título-artista y destino `Archivo Local (Exportar)`.
- Sincroniza la documentación bilingüe con la estructura actual y amplía `.gitignore` para entornos virtuales, herramientas locales, cachés y salidas generadas.
- Mantiene la serie mayor `4.1.0` sin fijar todavía un número minor/parche de publicación.

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
