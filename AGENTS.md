# AGENTS.md — Melomaniac v4.2.0

> Regla guía: mantener el proyecto limpio, seguro y sin duplicación. Reutilizar animaciones/controles/métodos existentes; mantener animaciones uniformes por propósito y toda variable/token en su fuente única. Cada cambio debe preservar `.gitignore` de secretos y no dejar artefactos.

## Stack

- Python 3.10+, Flet 0.86.5 desktop (`flet-desktop`), `app.py:44` entrypoint `ft.run(main)`. Tema oscuro sólido + IBM Plex Sans local en `resources/fonts/`.
- Deps fijadas en `requirements.txt:1` — `ytmusicapi`, `spotapi`, `RapidFuzz`, `Mutagen`, `httpx/requests`. Instalar siempre desde ese archivo.
- Sin monorepo, sin `pyproject.toml`/linter/formatter/typecheck. `tests/` reservado vacío.

## Comandos

```bash
python -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements.txt
python app.py                          # única forma de ejecutar la app
python -m compileall -q app.py core engine services ui  # verificación rápida (README/ARCHITECTURE)
```

- No hay `pytest`/`ruff`/`mypy`. No inventar comandos de test. La UI se valida manualmente (modos Lista/Doble/Preview, alcance Organizar/Dividir, NavigationRail responsive, diálogo de exportación).
- No hay task runner ni CI workflows que consultar.

## Arquitectura — fuentes de verdad

```
app.py -> AppState -> MusicApiService -> AuthManager -> PlaylistManagerUI
ui -> core / engine / services
core -> engine / services.circuit_breaker
services -> core / engine
engine -> core.models
```

- Composición obligatoria en `app.py:132-145`:
  ```python
  state = AppState(service=None)
  service = MusicApiService(state.cb)
  state.service = service
  ui = PlaylistManagerUI(page, state)
  auth_manager = AuthManager(page, service, state)
  ui.auth_manager = auth_manager
  service.auth_manager = auth_manager
  ```
  `AppState` crea los `CircuitBreaker` una sola vez en `core/state.py:231`; `MusicApiService` debe reutilizar `state.cb` (`services/api_service.py:254`). No crear breakers duplicados — deja tasks huérfanas.

- `core/config.py:1` es single-source para `PLATFORM_ORDER`, `NETWORK_CONCURRENCY=2`, `TRANSFER_CONCURRENCY` (Apple 2, resto 3), `APPLE_ISRC_BATCH=25`, `APPLE_TRANSFER_BATCH=100`, `SPOTIFY_ADD_CHUNK=50`, `EXPORT_*`, `DEFAULT_COOLDOWN=60` y re-exports de umbrales fuzzy. No duplicar constantes.
- `services/authentication.py:19` es single-source para rutas runtime: `CONFIG_DIR=config/`, `BROWSER_JSON`, `ENV_FILE=config/.env`, `SPOTIFY_COOKIES_JSON`, `SEARCH_CACHE_JSON`. No consultar paths legacy en raíz.
- Ciclo de vida en `app.py:183-293` — `hard_cleanup` cancela breakers, `cancel_lazy_scan()`, tasks asyncio, `cleanup_sessions()` y fuerza `os._exit(0)`. Al añadir recursos nuevos, registrarlos ahí.

## Modelos y flujos clave

- `core/models.py:31` `Track` universal (`duration_ms`, `is_explicit`, `isrc`, `source_path`, `album_artist`, `track_number`, `release_date`). `SearchResult` con `needs_review` (<40) / `low_confidence` (70-84) / `isrc`.
- `core/state.py:78` `AppState` BLoC: `subscribe()`/`notify()`, `tracks` vs `source_tracks` (fuente inmutable), `segments`/`active_segment_keys`, `display_tracks`/`visible_tracks()`/`selected_in_scope(scope)`. Organizar/Dividir y exportar comparten el mismo alcance.
- Flujo carga: `AppState.load_playlist()` -> `MusicApiService.fetch_playlist()` -> `PlaylistMeta + list[Track]`. Local: `engine/parsers.py:314` -> `build_local_tracks()` (+ `Mutagen` si `source_path` es audio).
- Flujo búsqueda: `make_cache_key()` -> caché `config/search_cache.json` (escritura atómica `.tmp` + `os.replace` en `services/api_service.py:364`) -> `search_with_fallback()` 3 pasadas (clean, original, título normalizado) -> hunter por plataforma -> `create_playlist()` agrupado. Apple: ISRC lotes de 25 secuenciales + `AppleRequestLimiter` burst 50 / pausa 60s / `423` => 120s cooldown (`services/circuit_breaker.py:139` + `services/api_service.py:151`). Spotify: inserción lotes 50 con 4 reintentos.

## Motores

- `engine/normalizer.py:92` umbrales `FUZZY_IDEAL=85`, `LOW=70`, `REVISION=40`, `ARTIST_EXACT_MIN=99`. `normalize_isrc()` exige 12 chars compactos.
- `engine/match.py:131` scoring: `_fuzzy_scores_triple` (RapidFuzz `token_sort_ratio`), `_ideal_pass_hunter`, `_fuzzy_flags_elastic`, `score_spotify_match` (40 título + 20 artista + 30 duración + 10 explicit).
- `engine/organizer.py:14` allowlist: sort `artist/album/name/duration_ms/release_date/track_number/original_position`, split solo `artist/album`. `platform`/`genre` están excluidos intencionalmente. `sort_tracks`/`split_tracks` normalizan vacíos a `Desconocido`.
- `engine/parsers.py` <-> `engine/exporters.py` son espejo. Formatos `TXT/CSV/M3U/M3U8/PLS/WPL/XSPF/XML`. Orden `artist-title` (default TuneMyMusic) vs `title-artist` via param `order` — propagar siempre.

## UI

- `ui/main_ui.py` — `PlaylistManagerUI`, `NavigationRail` (Inicio/Biblioteca/Descargas/Config), `SegmentedButton` para alcance (`Todo/Visibles/Seleccionadas`) y modo (`Lista/Doble/Preview`). Biblioteca/Descargas son placeholders con cono; Config embebe `ui/config_wizard.py`.
- `ui/song_row.py:ITEM_H=64`, skeletons con `animate_opacity/scale` 600ms.
- `ui/fonts.py` clasifica CJK > cirílico > latino; busca `NotoSansCJK-Regular.ttc` o fallback nativo.

## Reutilización y uniformidad — obligatorio

- **DRY estricto:** antes de crear animación/control/método, buscar uso existente (`Grep`) y reutilizarlo. No duplicar lógica que hace lo mismo con otra forma.
- **Controles Flet:** si el objetivo es similar, reutilizar el mismo helper de `ui/widgets.py:1` — `app_text`, `app_text_field`, `app_dialog`/`dialog_action`, `organize_dropdown`, `notify`, `DialogMixin`, `_primary_btn`/`_ghost_btn`. No crear `TextField`/`AlertDialog`/`SnackBar` inline con estilos ad-hoc.
- **Animaciones uniformes por propósito:** skeleton `600ms EASE_IN_OUT` (`ui/song_row.py:106`), hover/botones `100-120ms` (`ui/song_row.py:316` / `ui/widgets.py:308`), rail `200-300ms` (`ui/main_ui.py:679`). No inventar duraciones/curvas nuevas; si una nueva animación/método resulta útil, extraerlo como helper reutilizable en su módulo single-source y migrar usos existentes.
- **Tokens/variables:** todo color/borde/overlay en `ui/tokens.py:1`, tipografías vía `ui/fonts.py` (`FONT_TEXT`, `FONT_HEADLINE`, `brand_family`, `font_family_for`, `mono_family`), medidas fijas (`ITEM_H`, `SKELETON_COUNT`, anchos rail) y constantes de `core/config.py` / `services/authentication.py`. Prohibido hardcodear hex/`ft.Colors` o redefinir constantes localmente.

## Seguridad y limpieza — obligatorio

- **Nunca commitear secretos.** `.gitignore:18` ignora `.env`, `browser.json`, `spotify_cookies.json` y `config/*` (excepto `config/.gitkeep`). Verificar con `git status` antes de cualquier commit.
- **Nunca imprimir ni loguear valores de secretos.** Usar `envsitter` para inspeccionar `.env` sin exponer valores: `envsitter_keys`, `envsitter_match`, `envsitter_scan`. No usar `cat config/.env` / `cat browser.json` en logs o respuestas.
- **Rutas de credenciales:** YouTube `config/browser.json` (`Authorization: SAPISIDHASH...` + `Cookie`, fijos en `services/authentication.py:26`), Apple `config/.env` (`APPLE_AUTH_BEARER`, `APPLE_MUSIC_USER_TOKEN` con prefijo `Bearer` opcional), Spotify `config/spotify_cookies.json` (`identifier` + `cookies.sp_dc/sp_key`). Respetar `ensure_config_dir()`/`load_runtime_env()`.
- **Caché:** `config/search_cache.json` soporta legacy `track_id` string; validar con `unwrap_search_result` en `core/cache.py`.
- **Artefactos:** no dejar `__pycache__/`, `*.heapsnapshot`, `exports/`, `transfer_failed_report.txt`, `.flet/`, `.venv/` en el repo. `__pycache__/exports/.flet/.venv` están en `.gitignore`; `*.heapsnapshot` debe borrarse manualmente.
- **Dependencias:** no añadir deps sin necesidad; si se añaden, fijar versión en `requirements.txt`.

## Gotchas verificados

- Apple Music es secuencial (`core/state.py:680` y `services/api_service.py:680`): no paralelizar búsquedas Apple aunque el semáforo global sea 2.
- Spotify `Song` se reutiliza sobre el mismo `TLSClient` en `services/api_service.py:275` para evitar 5-8 requests de setup por búsqueda. No recrear `Config` en cada hunter.
- `load_playlist` resetea `transfer_state/progress/log` (`core/state.py:396`); `load_local_tracks` pone `destination_confirmed=False` y exige elegir destino.
- `GLOBAL_API_SEMAPHORE` en `services/api_service.py:121` limita toda la red a 2 concurrentes.
- Fuentes locales: solo extensiones en `_LOCAL_AUDIO_EXTENSIONS` se resuelven a disco; URLs con `://` se rechazan (`engine/parsers.py:376`).
