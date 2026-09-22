# Release Notes — Melomaniac

Notas de versión generadas a partir de los commits taggeados. Enfoque en **qué cambió para el usuario**, no en código.

---

## v1.0 — Primera versión funcional (2026-04-21) `224df51`

Primera entrega del proyecto. App de escritorio con Flet para transferir playlists entre **Spotify, YouTube Music y Apple Music**.

**Qué incluía:**
- Estructura inicial `app.py`, `auth_manager.py`, `cache_handler.py`, `core`, `engine`, `services`, `ui`.
- Autenticación básica y flujo de transferencia inicial.
- Base para el motor de matching y el wizard de configuración.

> Punto de partida. Todo lo posterior son iteraciones sobre esta base.

---

## v3.0.0 — Estabilización mayor + SpotAPI triple (2026-08-29) `0c0c882`

Salto de `v1.0` a `3.0`. Consolidación de más de 30 commits. No es solo un bump de número: es la versión donde el proyecto deja de ser prototipo.

**Novedades:**
- **Spotify vía SpotAPI** como tercera plataforma real: `searchV2/tracksV2`, `Login.from_cookies`, portadas desde `albumOfTrack.coverArt`, chunk de escritura `SPOTIFY_ADD_CHUNK=50`, wizard con 3 tabs (YT/Apple/Spotify).
- **Hunter Recovery** optimizado: tupla triple `(título, artista, duración_ms)` para tolerar remasterizaciones y versiones en vivo.
- **Fuentes locales** y dependencias: se añade `requirements.txt` vía `pip freeze`.

**Mejoras:**
- **Motor de búsqueda YT/Apple** reescrito: endpoint Apple migrado a `api.music.apple.com` (antes iTunes), pacing async corregido, se evita 429 en transferencias a Apple, cache de búsqueda persistida.
- **Fuentes tipográficas**: IBM Plex Sans estáticas por peso (300-700) desde `resources/fonts`, se elimina carga remota y síntesis falsa de pesos.
- **Refactors fase 1-3**: tokens centralizados en `ui/tokens.py` (fin duplicación de colores), `core/config.py` como single source (`PLATFORM_ORDER`, `NETWORK_CONCURRENCY`, etc.), `core/cache.py` con `make_cache_key/unwrap`, `engine/match.py:pack_search_result` unificado, `ui/main_ui.py:_on_state_changed` dividido en 7 helpers.

**Correcciones:**
- Migración auth Spotify a **PKCE** con plantillas de config y limpieza de `circuit_breaker` (fix cierre inesperado de la app).
- Compatibilidad **Flet 0.83 / 0.85 / 0.86.5**: `page.dialog` → `page.open/close`, correcciones de clases.
- Extracción de metadatos: antes se cortaban nombres con `-`, ahora se preserva `artista - título` completo via tupla de candidatos.
- Merge `beta` → `main` (#1) que reintegró Spotify y unificó velocidad de transferencia.

**Notas:** Se elimina carpeta `Versiones viejas/`. Última versión con `MelomaniacPass v5.0` en headers antes de normalizar a `v3.x`.

---

## v3.2.0 — Apple Music estable + ISRC y Mutagen (2026-08-30) `2218906` `78cb5a1`

**Novedades:**
- **ISRC como identificador auxiliar**: búsqueda exacta por ISRC cuando está disponible, batch de 25, burst de 50, endpoint `amp-api.music.apple.com`.
- **Mutagen** para metadatos de audio locales: lectura de ISRC/duración desde archivos, nuevo módulo `utils/audio_metadata.py`.
- **Tupla triple persistida** + cache mejorada (`duration_ms` preferido, `is_explicit`).

**Mejoras:**
- Parsers de texto/listas mucho más robustos: soporte `artist - title` / `title - artist`, múltiples entradas, brackets.
- Normalizer con limpieza de remaster/live/version.

**Correcciones:**
- Headers y docs normalizados de `v5.0` a `v3.2.0`. Tests nuevos `test_isrc_and_apple.py`.

---

## v3.3.1 — Diálogo de personalización + columna Álbum (2026-09-09) `485034b` + `d68d5cc`

**Novedades:**
- **Diálogo de personalización de playlist**: antes la descripción estaba hardcodeada; ahora se trae la real de YT/Apple/Spotify y se abre un overlay animado con capa de marca para editar **nombre y descripción** antes de crear.
- **Nueva columna Álbum** en la lista de canciones, alineada con el resto y con skeleton acorde.

**Correcciones:**
- **Backdrop del diálogo** pasa a ser hijo directo del `Stack` raíz — fix de render en Flet que provocaba glitches/overlay mal posicionado.

---

## v3.3.4 — Reestructuración modular (2026-09-11) `fec82d3`

Release de arquitectura, no solo features.

**Novedades / Cambios:**
- Reorganización de módulos: `auth_manager.py` → `ui/config_wizard.py` + `ui/auth_manager.py`, `utils/circuit_breaker.py` → `services/circuit_breaker.py`, nuevo `services/authentication.py`, `engine/audio_metadata.py` movido.
- Estructura `config/.gitkeep`, actualización `CHANGELOG.md` y docs bilingües a `v3.3.4`.

**Correcciones:**
- Alineación de imports y referencias tras el split. Tests de estructura nuevos.

**Mejoras:**
- Docs `README/ARCHITECTURE` actualizadas a `v3.3.4` con descripción de tupla triple + ISRC + `searchV2`.

---

## v3.3.5 — Pulido del diálogo de playlist (2026-09-13) `3db3bd6` (+`b451bde`)

**Mejoras:**
- **Diálogo de nombre/descripción** simplificado: de 239 líneas a ~90, mejor UX, validación y cierre más claro.
- Headers y docs bump a `v3.3.5`.

**Notas:** `b451bde` mejora el diálogo, `3db3bd6` es el commit que realmente sube la versión en todos los headers.

---

## v3.3.6 — DRY UI y fin de hardcodes (2026-09-13) `f3bb10b`

Refactor grande de UI sin cambios visibles drásticos, pero con mucho impacto en mantenibilidad.

**Mejoras:**
- Eliminación de clases/métodos duplicados en `ui/*`.
- **Valores hardcodeados → tokens**: todo pasa por `ui/tokens.py` (15 líneas nuevas).
- `ui/widgets.py` crece +194 líneas: fábrica común de widgets, `Option` fixes.
- `ui/config_wizard.py` y `ui/main_ui.py` reescritos (~30% menos líneas cada uno), mejor separación.
- `ui/telemetry.py` y `ui/song_row.py` ajustados para usar familias tipográficas centralizadas.

**Correcciones:** —

---

## v3.3.7 — Separación de responsabilidades en `core` (2026-09-13) `272692c`

**Novedades:**
- Nuevos módulos `core/availability.py` y `core/transfer.py` — se extrae lógica que estaba toda en `core/state.py` (de 157 líneas menos en `state.py`).
- `tests/test_core_helpers.py` añadido.

**Correcciones:**
- Fix de `import` en `config_wizard.py` (`48084d8`).
- Limpieza de imports y relaciones entre clases.

**Mejoras:** Estado más testeable, menos acoplamiento.

---

## v3.3.8 — Fix crítico de arranque (2026-09-16) `3830713`

**Correcciones — crítico:**
- **Fix de `kwargs` y declaraciones** (`3ab8198`) que impedían iniciar la app tras los refactors 3.3.6/3.3.7. Sin esto, la app no arrancaba.

**Mejoras:**
- Docs expandidas (`README.md` +222 líneas) con explicación de flujos y pre-requisitos.
- Ajustes en `engine/parsers.py` y `services/api_service.py` (20 líneas).

**Notas:** Bump a `v3.3.8` en todos los headers.

---

## v4.0.0 — Export local ordenado + rename a Melomaniac (2026-09-18) `1f136b0`

Salto mayor de versión.

**Novedades:**
- **Exportar a Archivo Local** como destino: nuevo `engine/exporters.py` (204 líneas) con `pathlib` + `FilePicker`, formatos **TXT/CSV/M3U8/XSPF**, path por defecto `~/Documentos/Melomaniac/Exports`.
- **Orden al pegar/exportar**: `engine/parsers.py` soporta `artist - title` y `title - artist` en todas las entradas, configurable vía `SegmentedButton` en pegar/nombrar/exportar.
- **Rename visible** `MelomaniacPass` → `Melomaniac`.

**Mejoras:**
- `ui/main_ui.py` +255 líneas: destino *Archivo Local (Exportar)*, diálogos completos de export.
- `core/config/state` añade `EXPORT_DEST_LABEL` y `local_parse/export order`.

---

## v4.1.0 — Doble vista Lista|Preview + scope (2026-09-18) `425e6b3` + `1e9444b`

Feature release más grande de la serie 4.x.

**Novedades:**
- **Doble vista `F6` Lista|Preview**: `Row` doble con reparent dinámico + `AnimatedSwitcher`, modos `Lista|Doble|Preview` con `SegmentedButton` nativo de Flet.
- **Scope**: `Todo|Visibles|Seleccionadas` para organizar/dividir, con `active_segment_keys` multi-selección.
- **NavigationRail** de 72px + `VerticalDivider` + `Stack` de módulos `Inicio/Biblioteca/Descargas/Config`.

**Mejoras:**
- `engine/organizer.py` +56: allowlist `artist/album/name/duration_ms`, multi-key sort, split normalizado `trim→Desconocido`, se descarta `platform`.
- `ui/song_row.py` +57: `SkeletonRow` animado (`opacity 0.45↔1.0 + scale 0.98↔1.0 600ms EASE_IN_OUT stagger 60ms`) y conteo responsive (`available//64 clamp 4-24` en `on_resize`).
- Docs a `v4.1.0`/`v4.x.x`, `.gitignore` actualizado, suite de tests obsoleta eliminada.

---

## v4.2.0 — Rebranding tipográfico (2026-09-20) `b371543`

**Novedades:**
- **Nuevas familias Stack Sans**: `Stack Sans Notch`, `Stack Sans Headline`, `Stack Sans Text` (5 pesos cada una) + familias IBM Plex refinadas (`IBM Plex Sans/Mono/JP/KR/SC/TC`).
- Nuevo módulo `ui/fonts.py` (172 líneas) con constantes — fin de strings hardcodeados.

**Mejoras:**
- Jerarquía visual mejorada: portadas `Notch/Headline/Mono` según stack, `telemetry`, `config_wizard`, `song_row` usan familias correctas.
- Fix `BORDER_ROW` undefined, ajustes de visualización.

---

## v4.4.3 — Sidebar colapsable + Organizar/Agrupar separados (2026-09-22) `64b706d` — HEAD

Última versión en `main`. Acumula 4 commits intermedios sin bump (`73d3914`, `f429fee`, `91beb2d`, `4044525`) más el bump final.

**Novedades:**
- **Sidebar colapsable (opción A)**: `NavigationRail` animado `300ms EASE_IN_OUT`, portada con `Stack Sans` según módulo.
- **Organizar y Agrupar separados**: Organizar = `Original vs Alfabético A-Z/Z-A`; Agrupar = divisor `artista/álbum` sin alcance ni letra pequeña.
- **Vista `SOURCE|RESULT`**: `source_tracks` inmutable, `active_segment_keys` multi-selección con unión y preservando 1ª aparición, `selector de partición` movido junto a *Vista* como `OutlinedButton → dialog checklist buscable con Checkbox`.
- **MusicApiService con `release_date`**: soporte múltiples formatos de fecha de lanzamiento.

**Mejoras:**
- **Shimmer + skeleton** mejorado: carga de playlist y transiciones entre vistas en Organizar/Dividir, `SkeletonRow` refinado en `listview`.
- **Se retira opción de alcance** en Dividir, se mejora el orden al filtrar.
- **Portada derecha unificada** con izquierda (`Image BoxFit.COVER + fallback icono`), `Dropdown Option` fix, `Row` dual persistente.
- **Documentación** actualizada a `v4.4.3` y aviso legal de Melomaniac añadido (`325f57f`).
- Nuevo `ui/organize_panel.py` (391 líneas) para operaciones unificadas de ordenar/agrupar sin cerrar diálogo.

**Correcciones:**
- `fix: correction in the use of the mono font` (`1ab7551`).

---

### Cómo usar estas notas

Copia cada bloque `## vX.Y.Z` como *Release* en GitHub (título = `vX.Y.Z — resumen`, tag = `vX.Y.Z`). Para publicar tags: `git push --tags`.
