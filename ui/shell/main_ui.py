"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.9.0                             ║
║              Interfaz Principal de Usuario                           ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: ui/shell/main_ui.py
Descripción: Componente principal de la interfaz de usuario. Implementa
            PlaylistManagerUI, la vista principal que orquesta controladores de
            layout, catálogo, acciones y divisiones sin mixins.

Estrategia de Diseño - Arquitectura Reactiva:
    PlaylistManagerUI es una clase UI pura que no conoce detalles de APIs.
    Implementa el patrón Observer suscribiéndose a cambios en AppState:
    
    1. Separación de Responsabilidades:
       - UI: Solo renderizado y eventos de usuario
       - State: Lógica de negocio y coordinación
       - Services: Comunicación con APIs
    
    2. Flujo Unidireccional de Datos:
       Usuario → UI → State → Services → State → UI
       
    3. Actualización Reactiva:
       - State.notify() → _schedule_state_render() → actualiza UI
       - Circuit breakers → _on_circuit_change() → deshabilita controles
    
    4. Gestión de Recursos:
       - Actualización por diferencias de las filas visibles
       - Preview construido solo cuando está visible
       - Skeleton screens durante carga
       - Tareas asyncio cancelables para búsquedas
    
    5. Feedback Visual Multi-Nivel:
       - Skeleton loading (reduce percepción de latencia)
       - Progress bars (transferencias)
       - Snackbars (notificaciones)
       - Diálogos (errores críticos)
       - Circuit breaker countdown (rate limiting)

Componentes Principales:
    - Sidebar: Navegación y controles de plataforma
    - Content: Lista de canciones y controles de transferencia
    - Telemetry: Panel de monitoreo y logs
    - File picker: Selector de archivos locales
    - Dialogs: Modales para errores y confirmaciones

Autor: Melomaniac Team
Versión: 4.9.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import time
from typing import Optional

import flet as ft

from core.models import Track, LoadState, TransferState
from core.state import AppState
from core.config import EXPORT_DEST_LABEL
from ui.components.song_row import (
    SongRow,
    build_preview_row,
    ITEM_H,
)
from ui.components.skeletons import (
    SkeletonRow,
    PreviewSkeletonRow,
)
from ui.theme.icons import PIcons
from ui.dialogs.transfer_report import TransferReportDialog
from ui.components.widgets import (
    app_text_field,
    close_dialog,
    notify,
    input_border,
    safe_update,
)

from ui.theme.tokens import (
    BORDER_LIGHT,
    ACCENT,
    SUCCESS,
    WARNING,
    ERROR_COL,
    TEXT_MUTED,
    TEXT_DIM,
)
from ui.dialogs.division_dialogs import DivisionController
from ui.shell.catalog_panel import CatalogController
from ui.shell.main_actions import ActionsController
from ui.shell.main_layout import LayoutController, compute_contextual_actions_state

# Por encima de este número conviene un único update del ListView en vez de
# publicar una actualización por fila (transferencias masivas, reordenamientos).
_ROW_UPDATE_BATCH_THRESHOLD = 16


class PlaylistManagerUI:
    """
    Interfaz principal de usuario para gestión de playlists.
    
    Clase UI pura que implementa el patrón Observer para reaccionar a
    cambios en AppState. No contiene lógica de negocio ni conocimiento
    de APIs, delegando toda la coordinación al estado.
    
    Attributes:
        page: Instancia de página Flet.
        state: Instancia de AppState (estado global).
        auth_manager: Referencia a AuthManager (inyectada externamente).
        root: Container raíz de la UI.
    
    Componentes Internos:
        _sidebar: Panel lateral con navegación y controles
        _content: Área principal con lista de canciones
        _telemetry: Panel de telemetría y logs
        _file_picker: Selector de archivos del sistema
        _skeleton_tasks: Tareas de animación de skeleton screens
    
    Flujo de Datos:
        1. Usuario interactúa con UI (click, input, etc)
        2. UI llama métodos de AppState
        3. AppState ejecuta lógica y actualiza su estado interno
        4. AppState.notify() dispara _schedule_state_render()
        5. UI se actualiza reflejando el nuevo estado
    
    Example:
        >>> state = AppState(service)
        >>> ui = PlaylistManagerUI(page, state)
        >>> ui.auth_manager = auth_manager  # Inyección de dependencia
        >>> page.add(ui.root)
    
    Note:
        La separación estricta entre UI y lógica de negocio permite:
        - Testing independiente de componentes
        - Reutilización de lógica en diferentes UIs
        - Cambios en UI sin afectar lógica de negocio
        - Múltiples vistas del mismo estado (ej: modo compacto)
    """

    # Número de filas skeleton mostradas durante carga
    SKELETON_COUNT = 14
    _CONTROLLER_ATTRS = ("_layout", "_actions", "_catalog", "_divisions")

    def __getattr__(self, name: str):
        """Delega métodos de los controladores compuestos manteniendo estado único en ``self``."""
        for attr in self._CONTROLLER_ATTRS:
            ctrl = self.__dict__.get(attr)
            if ctrl is not None and hasattr(ctrl, name):
                return getattr(ctrl, name)
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}")

    def _close_dlg(self, dlg) -> None:
        close_dialog(self.page, dlg)

    def _calc_skeleton_count(self) -> int:
        """Calcula cuántos SkeletonRow caben en alto visible — overfill garantizado."""
        try:
            h = self.page.height or self.page.window.height or 900  # type: ignore
            # si ambos son pequeños (init), usa fallback generoso
            if not h or h < 700:
                h = 900
        except Exception:
            h = 900
        # header + col_headers + progress + padding + tabs aproximado
        header_h = 70
        col_h = 36
        pad = 48
        prog = 40 if getattr(self, '_content_progress', None) and self._content_progress.visible else 0
        mode_bar = 40 if getattr(self, '_mode_bar', None) and getattr(self._mode_bar, 'visible', False) else 0
        avail = max(180, h - header_h - col_h - pad - prog - mode_bar - 60)  # -60 menor que -120 para overfill
        cnt = max(12, min(24, int(avail // ITEM_H)))
        # overfill: garantiza cubrir viewport incluso en ventana pequeña
        return cnt

    def _sync_skeleton_count(self) -> None:
        """Recrea skeletons si cambió el count según resolución (on_resize/dual) — ambos paneles."""
        try:
            new_cnt = self._calc_skeleton_count()
            cur_cnt = len(getattr(self, '_skeletons', []))
            if new_cnt != cur_cnt:
                # detener pulso previo antes de recrear (evita tasks huérfanas)
                try:
                    self._stop_skeleton_pulse()
                except Exception:
                    pass
                self._skeletons = [SkeletonRow(i) for i in range(new_cnt)]
                self._skeleton_view.controls = self._skeletons
                # preview skeletons espejo (mismo cnt, sin Estado/Sel)
                if hasattr(self, '_preview_skeleton_view'):
                    self._preview_skeletons = [PreviewSkeletonRow(i) for i in range(new_cnt)]
                    self._preview_skeleton_view.controls = self._preview_skeletons
                    try:
                        self._preview_skeleton_view.update()
                    except Exception:
                        pass
                # si skeleton está visible, reinicia pulso con nuevo count
                if getattr(self, '_skeleton_view_wrap', None) and self._skeleton_view_wrap.visible:
                    try:
                        self._ensure_skeletons_pulsing()
                    except Exception:
                        pass
                if getattr(self, '_preview_skeleton_wrap', None) and self._preview_skeleton_wrap.visible:
                    try:
                        self._ensure_preview_skeletons_pulsing()
                    except Exception:
                        pass
                self._skeleton_view.update()
        except Exception:
            pass

    def _trigger_shimmer_reload(self, duration_ms: int = 280) -> None:
        """Muestra Shimmer en ambas ListViews que quedarán visibles — Opción A."""
        try:
            if self._view_shimmer_task and not self._view_shimmer_task.done():
                self._view_shimmer_task.cancel()
        except Exception:
            pass
        s = self.state
        if s.load_state in (LoadState.LOADING_META, LoadState.LOADING_TRACKS):
            return
        # determina qué paneles quedarán visibles tras sync
        try:
            mode = getattr(s, 'dual_mode', 'lista')
            is_dual = bool(getattr(s, 'show_dual', False) or mode in ("doble", "preview"))
            # lista (izq) visible en lista y doble; preview (der) visible en doble y preview
            show_left = not is_dual or mode in ("lista", "doble")
            show_preview = is_dual and mode in ("doble", "preview")
            # si no hay dual pero hay transformación, igualmente preview oculto → solo left
            if not is_dual:
                show_preview = False
        except Exception:
            show_left, show_preview = True, False
        try:
            if show_left:
                self._skeleton_view_wrap.visible = True
                self._list_view_wrap.visible = False
                self._ensure_skeletons_pulsing()
                self._skeleton_view_wrap.update()
                try:
                    self._list_view_wrap.update()
                except Exception:
                    pass
            if show_preview and hasattr(self, '_preview_skeleton_wrap'):
                self._preview_skeleton_wrap.visible = True
                self._preview_view_wrap.visible = False
                self._preview_empty_wrap.visible = False
                self._ensure_preview_skeletons_pulsing()
                self._preview_skeleton_wrap.update()
                try:
                    self._preview_view_wrap.update()
                    self._preview_empty_wrap.update()
                except Exception:
                    pass
            elif not show_preview and hasattr(self, '_preview_skeleton_wrap'):
                # si preview no debe mostrar shimmer, asegúrate oculto
                try:
                    self._preview_skeleton_wrap.visible = False
                    self._preview_skeleton_wrap.update()
                except Exception:
                    pass
        except Exception:
            pass

        async def _hide():
            try:
                await asyncio.sleep(duration_ms / 1000)
            except asyncio.CancelledError:
                return
            try:
                self._stop_skeleton_pulse()
                if show_left:
                    self._skeleton_view_wrap.visible = False
                    self._list_view_wrap.visible = True
                    self._skeleton_view_wrap.update()
                    self._list_view_wrap.update()
                if show_preview and hasattr(self, '_preview_skeleton_wrap'):
                    self._preview_skeleton_wrap.visible = False
                    # preview contenido lo restaura _sync_preview en siguiente notify
                    self._preview_skeleton_wrap.update()
                self.page.update()
            except Exception:
                pass
        try:
            self._view_shimmer_task = asyncio.create_task(_hide())
        except Exception:
            pass

    def _sync_dual_view(self, s) -> None:
        """Sincroniza modo Lista|Doble|Preview — SOURCE|RESULT (§25). + Shimmer entre vistas."""
        mode = getattr(s, 'dual_mode', 'lista')
        # shimmer entre cambios de vista (se tardan un poco)
        try:
            if mode != getattr(self, '_prev_dual_mode', 'lista'):
                self._trigger_shimmer_reload(duration_ms=320)
                self._prev_dual_mode = mode
        except Exception:
            pass
        is_dual = bool(getattr(s, 'show_dual', False) or mode in ("doble", "preview"))
        # mode bar visible mientras exista transformación (§25) — no solo 'doble'
        try:
            has_transform = bool(s.segments) or s.show_dual or mode in ("doble", "preview")
            self._mode_bar.visible = bool((is_dual or has_transform) and not getattr(self, "_catalog_open", False))
        except Exception:
            pass
        # headers SOURCE|RESULT + Divisiones §1,6
        try:
            if is_dual:
                # izq = Playlist original base (§1), der = División activa §6
                self._lista_header_text.value = "PLAYLIST ORIGINAL"
                self._lista_header_icon.value = PIcons.CLOCK
                if s.segments:
                    seg = s.active_segment_key or ""
                    # §6 una División a la vez; §7 División activa vs seleccionadas
                    self._preview_header_text.value = f"DIVISIÓN — {seg}" if seg else f"DIVISIONES — {len(s.segments)} divisiones"
                    self._preview_header_icon.value = PIcons.GIT_BRANCH
                else:
                    self._preview_header_text.value = "RESULTADO — organizado"
                    self._preview_header_icon.value = PIcons.SORT_ASCENDING
            else:
                self._lista_header_text.value = "PLAYLIST ORIGINAL"
                self._lista_header_icon.value = PIcons.LIST_BULLETS
                self._preview_header_text.value = "DIVISIÓN ACTIVA — preview"
                self._preview_header_icon.value = PIcons.EYE
        except Exception:
            pass
        try:
            if hasattr(self._mode_seg, 'selected'):
                self._mode_seg.selected = [mode]
            else:
                self._mode_seg.value = mode
        except Exception:
            pass
        # Row persistente con ambos paneles — solo cambia visible (preserva ListView virtualizado)
        try:
            if not is_dual:
                # modo lista única
                self._dual_row.visible = True  # Row siempre visible, hijos controlan
                self._lista_panel.visible = True
                self._preview_panel.visible = False
                # _list_area siempre contiene _dual_row, no hay reparent
            else:
                if mode == "lista":
                    self._lista_panel.visible = True
                    self._preview_panel.visible = False
                elif mode == "preview":
                    self._lista_panel.visible = False
                    self._preview_panel.visible = True
                else:  # doble
                    self._lista_panel.visible = True
                    self._preview_panel.visible = True
                self._dual_row.visible = True
            # asegura _list_area contenido estable (evita duplicar controles)
            if getattr(self._list_area, "content", None) is not self._dual_row:
                self._list_area.content = self._dual_row
        except Exception:
            pass
        try:
            self._sync_preview(s)
        except Exception:
            pass
        try:
            self._sync_skeleton_count()
        except Exception:
            pass

    def _sync_preview(self, s) -> None:
        """Preview readonly: SOURCE|RESULT (§25) cuando hay transformación, si no selected_in_scope."""
        try:
            # En modo Lista el panel está oculto: no construir ni actualizar
            # controles que el usuario no puede ver.
            preview_panel = getattr(self, "_preview_panel", None)
            if preview_panel is None or not preview_panel.visible:
                self._preview_sig = None
                if self._preview_list_view.controls:
                    self._preview_list_view.controls.clear()
                return

            is_dual = bool(getattr(s, 'show_dual', False) or getattr(s, 'dual_mode', 'lista') in ("doble", "preview"))
            has_transform = bool(getattr(s, 'segments', {})) or bool(getattr(s, 'show_dual', False))
            # Si hay transformación (organizar/dividir), preview es RESULT (display_tracks con segmento activo)
            if is_dual or has_transform:
                preview_tracks = s.display_tracks
                label_suffix = f"{len(preview_tracks)} en resultado"
            else:
                preview_tracks = s.selected_in_scope("visible") if hasattr(s, 'selected_in_scope') else [t for t in s.tracks if t.selected]
                label_suffix = f"{len(preview_tracks)} seleccionadas"
            if self._preview_count.value != label_suffix:
                self._preview_count.value = label_suffix

            if not preview_tracks:
                self._preview_view_wrap.visible = False
                self._preview_empty_wrap.visible = True
                self._preview_sig = None
                if self._preview_list_view.controls:
                    self._preview_list_view.controls.clear()
                return

            self._preview_empty_wrap.visible = False
            self._preview_view_wrap.visible = True

            # Firma de lo que pinta la fila: evita reconstruir el preview en
            # cambios de selección, auth, progreso o telemetría.
            signature = tuple(
                (t.id, t.name, t.artist, t.album, t.duration, t.img_url, t.source_url)
                for t in preview_tracks
            )
            if signature == self._preview_sig:
                return

            self._preview_list_view.controls = [
                build_preview_row(tr, i) for i, tr in enumerate(preview_tracks, 1)
            ]
            self._preview_sig = signature
            self._preview_list_view.update()

            # mantener skeleton oculto cuando hay contenido (shimmer lo muestra temporalmente)
            if hasattr(self, '_preview_skeleton_wrap') and self._preview_skeleton_wrap.visible:
                self._preview_skeleton_wrap.visible = False
        except Exception:
            pass

    def __init__(self, page: ft.Page, state: AppState):
        """
        Inicializa la interfaz principal con todos sus componentes.
        
        Args:
            page: Instancia de página Flet.
            state: Instancia de AppState para suscripción reactiva.
        
        Note:
            El constructor solo inicializa estructuras de datos y construye
            la jerarquía de componentes. La suscripción a eventos y la
            configuración de callbacks se realiza al final para garantizar
            que todos los componentes estén listos.
        """
        self.page  = page
        self.state = state

        # ──────────────────────────────────────────────────────────────
        # ESTADO INTERNO DE UI
        # ──────────────────────────────────────────────────────────────
        
        self._search_task:            Optional[asyncio.Task] = None
        self._skeleton_tasks:         list[asyncio.Task]     = []
        self._preview_skeleton_tasks: list[asyncio.Task]     = []
        self._preview_sig:            tuple | None = None
        self._render_scheduled:       bool = False
        try:
            self._ui_loop: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            self._ui_loop = None
        self._failed_dialog_shown:    bool  = False
        self._report_dialog_shown:    bool  = False
        self._transfer_start:         float = 0.0
        self._completion_snack_shown: bool  = False
        self._pm_cleared_for_load:    bool  = False
        self.auth_manager                   = None
        self.on_show_welcome                = None  # inyectado por app.py
        self._prev_dual_mode: str = "lista"
        self._view_shimmer_task: Optional[asyncio.Task] = None
        self._catalog_open: bool = False
        self._catalog_tab: str = "songs"
        self._catalog_results: list[dict] = []
        self._catalog_detail_stack: list[dict] = []
        self._catalog_detail_tracks: list[Track] = []
        self._catalog_detail_title_str: str = ""
        self._catalog_detail_platform: str = ""
        self._catalog_search_task: Optional[asyncio.Task] = None

        # ──────────────────────────────────────────────────────────────
        # FILE PICKER
        # ──────────────────────────────────────────────────────────────
        # Selector de archivos del sistema operativo
        
        self._file_picker = ft.FilePicker()
        page.services.append(self._file_picker)
        self._save_picker = ft.FilePicker()
        page.services.append(self._save_picker)

        # ──────────────────────────────────────────────────────────────
        # CAMPO DE TEXTO PARA PEGAR LISTAS
        # ──────────────────────────────────────────────────────────────
        
        self._paste_field = app_text_field(
            hint_text="Pega aquí tu lista  (ej: Título - Artista, una por línea)",
            multiline=True, min_lines=10, max_lines=10, expand=True,
        )

        # ──────────────────────────────────────────────────────────────
        # CONTROLADORES COMPUESTOS Y CONSTRUCCIÓN DE COMPONENTES
        # ──────────────────────────────────────────────────────────────

        self._divisions = DivisionController(self)
        self._actions = ActionsController(self)
        self._catalog = CatalogController(self)
        self._layout = LayoutController(self)

        self._build_sidebar()
        self._build_content()

        self._build_nav_rail()
        self._build_modules_and_root()

        # ──────────────────────────────────────────────────────────────
        # SUSCRIPCIÓN A EVENTOS
        # ──────────────────────────────────────────────────────────────
        # Conecta la UI al sistema reactivo de estado y circuit breakers
        
        state.subscribe(self._schedule_state_render)
        for platform, cb in state.cb.items():
            cb.subscribe(lambda is_open, rem, p=platform: self._on_circuit_change(p, is_open, rem))
        def _on_resize(_):
            self._telemetry.sync_mode()
            try:
                self._sync_skeleton_count()
                self._sync_dual_view(self.state)
            except Exception:
                pass
            self.page.update()
        page.on_resize = _on_resize

        # ──────────────────────────────────────────────────────────────
        # SINCRONIZACIÓN INICIAL
        # ──────────────────────────────────────────────────────────────
        # Tarea asíncrona para sincronizar modo de telemetría tras render
        
        async def _initial_sync():
            await asyncio.sleep(0.15)
            self._telemetry.sync_mode()
            self.page.update()

        page.run_task(_initial_sync)

    # ── Auth helpers ───────────────────────────────────────────────────

    async def _refresh_auth_live(self, platform: str) -> None:
        am = getattr(self, "auth_manager", None)
        if not am:
            return
        results = await am.check_sessions([platform])
        am.ingest_preflight_results(results)

    async def _on_auth_probe(self, platform: str) -> None:
        am = getattr(self, "auth_manager", None)
        if not am:
            return
        self.state.log(f"[INFO] ⏳ Revalidando sesión de {platform}...")
        self.page.update()
        results = await am.check_sessions([platform])
        am.ingest_preflight_results(results)
        for r in results:
            if r.platform != platform:
                continue
            if not r.ok:
                self.state.log(f"[ERROR] ⚠ {platform} falló la validación: {r.error[:400]}")
                if r.expired:
                    am.open_wizard(platform)
                else:
                    self._snack(f"No se confirmó la sesión: {r.error[:180]}")
            else:
                self.state.log(f"[SUCCESS] ✓ {platform} validada correctamente.")
                self._snack(f"Sesión de {platform} válida y activa.")
            break

    def _on_open_wizard(self, _e: ft.ControlEvent) -> None:
        am = getattr(self, "auth_manager", None)
        if not am:
            self.state.log("[ERROR] AuthManager no disponible (wizard).")
            return
        am.open_wizard()

    def _close_postmortem_dialog(self) -> None:
        """Limpia el estado de post-mortem sin cerrar diálogos (manejado por telemetry)."""
        s = self.state
        s.pending_review_tracks.clear()
        s.failed_tracks.clear()
        s.api_rejected_tracks.clear()
        s.transfer_error_tracks.clear()
        self._failed_dialog_shown = False


    # ── BUILD SIDEBAR ──────────────────────────────────────────────────

    # ── MÉTODOS DE ORGANIZACIÓN Y DIVISIÓN ────────────────────────────

    # ── BUILD CONTENT ──────────────────────────────────────────────────

    # ── STATE REACTIONS ────────────────────────────────────────────────

    def _dispatch_to_ui_loop(self, callback) -> bool:
        """Reenvía callbacks desde hilos worker al event loop UI cuando existe."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            ui_loop = getattr(self, "_ui_loop", None)
            if ui_loop is not None and not ui_loop.is_closed():
                try:
                    ui_loop.call_soon_threadsafe(callback)
                except RuntimeError:
                    return False
                return True
            return False
        if getattr(self, "_ui_loop", None) is None:
            self._ui_loop = loop
        return False

    def _schedule_state_render(self) -> None:
        """Coalesce ráfagas de notify() en un solo render por tick sobre el loop UI."""
        if self._render_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            ui_loop = getattr(self, "_ui_loop", None)
            if ui_loop is not None and not ui_loop.is_closed():
                self._render_scheduled = True
                try:
                    ui_loop.call_soon_threadsafe(self._flush_state_render)
                except RuntimeError:
                    self._render_scheduled = False
                return
            # Entorno síncrono sin loop activo (tests unitarios): render directo.
            self._on_state_changed()
            return
        if getattr(self, "_ui_loop", None) is None:
            self._ui_loop = loop
        self._render_scheduled = True
        loop.call_soon(self._flush_state_render)

    def _flush_state_render(self) -> None:
        self._render_scheduled = False
        self._on_state_changed()

    def _on_state_changed(self) -> None:
        s = self.state
        if getattr(self, "_current_module", 0) != 0:
            self._gestion_dirty = True
            self._sync_report_dialog(s)
            if s.transfer_state == TransferState.DONE and not getattr(self, "_completion_snack_shown", False):
                self._sync_progress(s, False, True, False, False, False, True)
            return
        self._gestion_dirty = False
        is_local_src = s.source in AppState.LOCAL_SOURCES
        # Delegacion SRP - pasa datos simples (regla 10), metodos <50L (regla 6)
        self._sync_platform_ui(s, is_local_src)
        self._sync_titles(s)
        dest_ok = self._sync_auth_icons(s)
        is_loading = s.load_state in (LoadState.LOADING_META, LoadState.LOADING_TRACKS)
        is_ready = s.load_state == LoadState.READY
        is_error = s.load_state == LoadState.ERROR
        is_idle = s.load_state == LoadState.IDLE
        self._sync_list_state(s, is_loading, is_ready, is_error, is_idle)
        is_transferring = s.transfer_state == TransferState.RUNNING
        is_transfer_done = s.transfer_state == TransferState.DONE
        is_transfer_err = s.transfer_state == TransferState.ERROR
        xfer_active = is_transferring or is_transfer_done or is_transfer_err
        is_scan_run = getattr(s, "lazy_scan_running", False)
        is_scan_done = getattr(s, "lazy_scan_done", False)
        idle_xfer = s.transfer_state == TransferState.IDLE
        show_progress = xfer_active or is_scan_run or (is_scan_done and idle_xfer)
        self._sync_progress(s, is_transferring, xfer_active, is_scan_run, is_scan_done, idle_xfer, show_progress)
        self._sync_report_dialog(s)
        self._sync_telemetry(s, xfer_active, is_loading)
        net_blocked = any(cb.is_open for cb in s.cb.values())
        rule4_blocked = is_local_src and not s.destination_confirmed
        self._sync_action_buttons(s, is_local_src, dest_ok, is_loading, is_transferring, is_ready, net_blocked, rule4_blocked)
        try:
            self._sync_dual_view(s)
        except Exception:
            pass
        panel = getattr(self, "_panel_gestion", None)
        if not safe_update(panel):
            self.page.update()

    def _sync_platform_ui(self, s, is_local_src: bool) -> None:
        """Actualiza selectores y hint segun fuente (regla 6 SRP)."""
        if self._playlist_section.visible != (not is_local_src):
            self._playlist_section.visible = not is_local_src
            self._playlist_divider.visible = not is_local_src
            self._playlist_section.update()
            self._playlist_divider.update()
        _hint_map = {
            "Archivo Local": "Pulsa \u00abCargar\u00bb para abrir el explorador de archivos.",
            "Pegar Texto": "Pulsa \u00abCargar\u00bb para pegar tu lista de canciones.",
        }
        new_hint = _hint_map.get(s.source, "Introduce el ID o la URL en el panel izquierdo y pulsa \u00abCargar\u00bb.")
        if self._empty_hint_text.value != new_hint:
            self._empty_hint_text.value = new_hint
            self._empty_hint_text.update()
        if getattr(self._dst_dd, "_last_border_key", None) != (BORDER_LIGHT, ACCENT):
            self._dst_dd.border = input_border(BORDER_LIGHT, ACCENT)
            self._dst_dd._last_border_key = (BORDER_LIGHT, ACCENT)

    def _sync_titles(self, s) -> None:
        self._playlist_title.value = s.playlist_name
        n = len(s.display_tracks)
        total = len(s.tracks)
        self._track_count.value = (
            f"{n} canciones" if not s.search_query else f"{n} de {total} coincidencias"
        )

    def _sync_auth_icons(self, s) -> bool:
        for plat, ic in (("YouTube Music", self._auth_yt), ("Apple Music", self._auth_am), ("Spotify", self._auth_sp)):
            ok = s.auth_session_ok.get(plat)
            # None = preflight sin ejecutar todavía: gris, no verde.
            col = SUCCESS if ok else (TEXT_DIM if ok is None else ERROR_COL)
            if hasattr(ic, "content") and hasattr(ic.content, "color"):
                ic.content.color = col
            else:
                ic.icon_color = col
            hint = s.auth_session_hint.get(plat) or ""
            base = f"{plat}: clic para revalidar ahora"
            state_txt = "OK" if ok else ("sin validar" if ok is None else "fallo")
            ic.tooltip = f"{base} \u00b7 {hint}" if hint else f"{base} \u00b7 {state_txt}"
        # El destino se selecciona y valida con preflight en vivo dentro del diálogo
        # de transferencia (Bloques 2.1 y 2.9), sin bloqueo permanente en el sidebar.
        self._dest_session_warn.visible = False
        self._dest_session_warn.value = ""
        if s.source in AppState.PLATFORMS:
            src_ok = s.auth_session_ok.get(s.source)
            self._status_badge.value = (
                f"\u2713 Origen: {s.source}"
                if src_ok is True
                else (f"Origen: {s.source}" if src_ok is None else f"\u26a0 Origen: {s.source} (sin sesi\u00f3n)")
            )
            self._status_badge.color = SUCCESS if src_ok is True else (TEXT_MUTED if src_ok is None else WARNING)
        else:
            self._status_badge.value = f"Origen: {s.source}"
            self._status_badge.color = TEXT_MUTED
        self._select_all_chk.value = s.select_all
        return True

    def _sync_list_state(self, s, is_loading: bool, is_ready: bool, is_error: bool, is_idle: bool) -> None:
        self._empty_state.visible = is_idle
        self._skeleton_view_wrap.visible = is_loading
        self._list_view_wrap.visible = is_ready and not is_error
        self._error_state.visible = is_error
        catalog_open = bool(getattr(self, "_catalog_open", False))
        if hasattr(self, "_col_headers"):
            self._col_headers.visible = not catalog_open
        if hasattr(self, "_list_area"):
            self._list_area.visible = not catalog_open
        if hasattr(self, "_catalog_panel"):
            self._catalog_panel.visible = catalog_open
        if is_error:
            self._error_text.value = s.load_error
        if is_loading:
            self._ensure_skeletons_pulsing()
        if is_ready:
            self._stop_skeleton_pulse()
            # SOURCE|RESULT: izq = base original cuando hay transformación, si no display (lista única)
            is_dual = bool(getattr(s, 'show_dual', False) or getattr(s, 'dual_mode', 'lista') in ("doble", "preview"))
            if is_dual and getattr(s, 'source_tracks', None) and len(s.source_tracks) > 0:
                # izquierda = SOURCE intacta (§25) — muestra base sin filtro de segmento
                self._sync_list_view(s.source_tracks)
            else:
                self._sync_list_view(s.display_tracks)
        has_tracks = len(s.tracks) > 0
        self._organize_btn.disabled = not has_tracks
        self._split_btn.disabled = not has_tracks
        self._reorganize_btn.disabled = not has_tracks
        # compat: _segment_dd siempre oculto (movido junto a Vista)
        self._segment_dd.visible = False
        # selector División activa — Outlined + Popup dinámico junto a Vista
        try:
            if s.segments:
                self._refresh_division_popup()
                self._edit_division_btn.visible = True
            else:
                self._division_popup.visible = False
                self._division_col.visible = False
                self._edit_division_btn.visible = False
            self._partition_btn.visible = False
        except Exception:
            pass

    def _sync_progress(self, s, is_transferring: bool, xfer_active: bool, is_scan_run: bool, is_scan_done: bool, idle_xfer: bool, show_progress: bool) -> None:
        if is_transferring:
            if self._transfer_start == 0.0:
                self._transfer_start = time.monotonic()
                self._completion_snack_shown = False
        elif not xfer_active:
            self._transfer_start = 0.0
        self._content_progress.visible = show_progress
        _accent_ok = SUCCESS
        if not (show_progress and s.transfer_total):
            return
        if (is_scan_run or is_scan_done) and idle_xfer and not xfer_active:
            frac = min(1.0, s.transfer_progress / max(s.transfer_total, 1))
            self._content_progress_bar.value = frac
            if is_scan_done:
                ok_n = sum(1 for t in s.tracks if getattr(t, "transfer_status", "") == "found")
                fail_n = sum(1 for t in s.tracks if getattr(t, "transfer_status", "") in ("not_found", "error"))
                self._content_prog_label.value = f"B\u00fasqueda finalizada: {ok_n} \u00c9xitos / {fail_n} Fallos"
                self._content_prog_label.color = _accent_ok
                self._content_eta_label.value = ""
                self._content_progress.border = ft.Border.all(0.9, _accent_ok)
            else:
                self._content_prog_label.value = f"B\u00fasqueda en destino\u2026 {int(frac * 100)}%"
                self._content_prog_label.color = TEXT_MUTED
                self._content_eta_label.value = ""
                self._content_progress.border = ft.Border.all(0.8, BORDER_LIGHT)
            return
        if not xfer_active:
            return
        frac = (
            s.count_confirmed / s.count_detected
            if s.transfer_state == TransferState.DONE and s.count_detected
            else s.transfer_progress / s.transfer_total
        )
        self._content_progress_bar.value = min(1.0, frac)
        fallidas = len(s.failed_tracks)
        rechazadas = len(s.api_rejected_tracks)
        ejec = len(s.transfer_error_tracks)
        porcentaje = int(frac * 100)
        if s.transfer_state == TransferState.DONE:
            self._content_prog_label.value = f"Completado \u00b7 {porcentaje}%"
            self._content_prog_label.color = _accent_ok
            self._content_eta_label.value = ""
            self._content_progress.border = ft.Border.all(0.9, _accent_ok)
            if not self._completion_snack_shown:
                self._completion_snack_shown = True
                fail_n = fallidas + rechazadas + ejec
                notify(
                    self.page,
                    f"Transferencia completada: {s.count_confirmed} exitosas, {fail_n} errores",
                    action="Ver Detalles" if fail_n > 0 else None,
                    on_action=(lambda _: self._telemetry.show_postmortem()) if fail_n > 0 else None,
                )
        elif s.transfer_state == TransferState.ERROR:
            self._content_prog_label.value = f"{porcentaje}%  \u00b7  error \u00b7 {fallidas + rechazadas} incidencias"
            self._content_prog_label.color = WARNING
            self._content_eta_label.value = ""
            self._content_progress.border = ft.Border.all(0.8, WARNING)
        else:
            eta_text = ""
            if self._transfer_start > 0 and s.transfer_progress > 0:
                elapsed = time.monotonic() - self._transfer_start
                remaining = s.transfer_total - s.transfer_progress
                eta_s = (elapsed / s.transfer_progress) * remaining
                if 0 < eta_s < 3600:
                    eta_text = (f"~{int(eta_s)}s restantes" if eta_s < 60 else f"~{int(eta_s // 60)}m {int(eta_s % 60)}s restantes")
            self._content_prog_label.value = f"{porcentaje}%  \u00b7  {s.count_processed} ok  /  {fallidas + rechazadas} errores"
            self._content_prog_label.color = TEXT_MUTED
            self._content_eta_label.value = eta_text
            self._content_progress.border = ft.Border.all(0.8, BORDER_LIGHT)

    def _sync_report_dialog(self, s) -> None:
        """Abre el reporte una sola vez al cerrar la transferencia."""
        if s.transfer_state == TransferState.RUNNING:
            self._report_dialog_shown = False
            return
        if s.transfer_state not in (TransferState.DONE, TransferState.ERROR):
            return
        if self._report_dialog_shown:
            return
        report = getattr(s, "last_report", None)
        if report is None:
            return
        self._report_dialog_shown = True
        try:
            TransferReportDialog(self.page, s, report).show()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.state.log(f"[WARN] No se pudo abrir el reporte de transferencia: {exc}")

    def _sync_telemetry(self, s, xfer_active: bool, is_loading: bool) -> None:
        if xfer_active:
            self._telemetry.update_counters(
                s.count_detected, s.count_candidates, s.count_processed,
                s.count_confirmed, len(s.api_rejected_tracks),
            )
        self._telemetry.update_log(s.log_lines)
        report = getattr(s, "last_report", None)
        if report is not None:
            self._telemetry.update_postmortem(
                [issue.track for issue in report.missing],
                destination=report.destination,
                confirmed=report.confirmed,
                detected=report.detected,
                elapsed_s=report.elapsed_s,
                title=report.title,
            )
        elif s.transfer_state == TransferState.DONE:
            self._telemetry.update_postmortem(
                list(getattr(s, "failed_tracks", []))
                + list(getattr(s, "api_rejected_tracks", []))
                + list(getattr(s, "transfer_error_tracks", [])),
                destination=s.destination, confirmed=s.count_confirmed, detected=s.count_detected,
            )
        if is_loading and not self._pm_cleared_for_load:
            self._pm_cleared_for_load = True
            self._telemetry.clear_postmortem()
        elif not is_loading:
            self._pm_cleared_for_load = False
        self._telemetry.sync_mode()

    def _sync_action_buttons(self, s, is_local_src: bool, dest_ok: bool, is_loading: bool, is_transferring: bool, is_ready: bool, net_blocked: bool, rule4_blocked: bool) -> None:
        self._load_btn.disabled = net_blocked or is_loading
        self._transfer_btn.disabled = net_blocked or is_transferring or not is_ready
        self._clear_session_btn.disabled = is_loading or is_transferring
        self._id_clear_btn.visible = bool(self._id_field.value)
        if is_transferring:
            self._transfer_btn.tooltip = "Transferencia en curso"
        elif net_blocked:
            self._transfer_btn.tooltip = "Sin conexión (rate limit activo)"
        else:
            self._transfer_btn.tooltip = "Elegir destino y transferir o exportar la selección"
        if hasattr(self, "_apply_contextual_actions_state"):
            self._apply_contextual_actions_state(
                compute_contextual_actions_state(s, net_blocked=net_blocked)
            )


    def _sync_list_view(self, tracks: list[Track]) -> None:
        lv = self._list_view
        download_busy = self.state.download_state == TransferState.RUNNING
        done_by_id = {
            d.get("id"): d
            for d in getattr(self.state, "download_done", [])
            if isinstance(d, dict) and d.get("id")
        }
        if self._auto_play_download_ids and not download_busy:
            for tid in list(self._auto_play_download_ids):
                entry = done_by_id.get(tid)
                if entry and entry.get("path"):
                    self._auto_play_download_ids.discard(tid)
                    tr = next((t for t in self.state.tracks if t.id == tid), None)
                    if hasattr(self, "player_bar") and self.player_bar is not None:
                        self.player_bar.play_file(str(entry["path"]), tr)
            if self.state.download_state in (TransferState.DONE, TransferState.ERROR):
                self._auto_play_download_ids.clear()

        # compara orden además de conjunto para detectar reorden (SOURCE vs RESULT §25)
        existing_order = [c.track.id for c in lv.controls if hasattr(c, "track")]
        incoming_order = [t.id for t in tracks]
        if existing_order != incoming_order:
            # shimmer entre recargas organizar/dividir (reemplaza opacity, mantiene zoom)
            try:
                if existing_order:  # no en primera carga vacía
                    self._trigger_shimmer_reload(duration_ms=280)
            except Exception:
                pass
            lv.controls.clear()
            show_album = not getattr(getattr(self, "player_queue", None), "is_open", False)
            for i, track in enumerate(tracks, 1):
                lv.controls.append(
                    SongRow(
                        track, i, self.state.toggle_track,
                        on_move=self.state.move_track_to_position,
                        on_download=self._on_row_download,
                        download_busy=download_busy,
                        download_done=(track.id in done_by_id),
                        show_album=show_album,
                    )
                )
            try:
                lv.update()
            except Exception:
                pass
            return

        # Mismo orden: refresca solo las filas cuyo contenido cambió. El zip
        # conserva la correspondencia incluso si la fuente trae IDs repetidos.
        changed: list[SongRow] = []
        for row, current in zip(lv.controls, tracks):
            if not hasattr(row, "refresh"):
                continue
            try:
                if row.refresh(
                    current,
                    notify=False,
                    download_busy=download_busy,
                    download_done=(current.id in done_by_id),
                ):
                    changed.append(row)
            except Exception:
                pass
        if not changed:
            return
        try:
            if len(changed) <= _ROW_UPDATE_BATCH_THRESHOLD:
                for row in changed:
                    row.update()
            else:
                # Un solo mensaje para transferencias/reordenamientos masivos.
                lv.update()
        except Exception:
            pass

    # ── EVENT HANDLERS ─────────────────────────────────────────────────

    # ── selector de playlists de la biblioteca ─────────────────────────

    def _on_toggle_url_field(self, _e=None) -> None:
        """Alterna entre elegir de la biblioteca y pegar una URL o ID."""
        self._apply_url_mode(not self._url_mode)

    def _apply_url_mode(self, enabled: bool) -> None:
        """Muestra el campo de ID solo en modo avanzado; el selector, si no."""
        self._url_mode = bool(enabled)
        self._id_field.visible = self._url_mode
        self._url_toggle_label.value = (
            "Elegir de la biblioteca" if self._url_mode else "Usar URL o ID"
        )
        if self._url_mode:
            self._playlist_pick.visible = False
            self._playlist_help.visible = False
        else:
            self._id_field.value = ""
            # Se releen las opciones: mientras estaba en modo URL pudo
            # cambiar el origen o terminar una sincronización en Biblioteca.
            self._refresh_playlist_picker(self.state.source)
        for ctrl in (self._id_field, self._url_toggle_label, self._use_url_btn):
            try:
                ctrl.update()
            except Exception:
                pass

    def _on_playlist_pick(self, e=None) -> None:
        """Carga la playlist elegida en el selector."""
        pid = str(getattr(e.control, "value", "") or "")
        if not pid:
            return
        asyncio.create_task(self._load_picked_playlist(pid))

    async def _load_picked_playlist(self, pid: str) -> None:
        self._completion_snack_shown = False
        await self.state.load_playlist(pid)

    def _refresh_playlist_picker(self, platform: str) -> None:
        """Rellena el selector con lo ya sincronizado para ``platform``.

        Solo lee la caché de ``LibraryState``: abrir Gestión no debe disparar
        tres sincronizaciones contra las APIs.
        """
        if self._url_mode:
            return
        picker = getattr(self, "_playlist_pick", None)
        if picker is None:
            return
        lib = getattr(self, "library_state", None)
        items = []
        if lib is not None:
            try:
                items = lib.get_playlists(platform) or []
            except Exception:
                items = []
        picker.options = [
            ft.dropdown.Option(key=str(p.id), text=str(p.name or p.id))
            for p in items
        ]
        # Una recarga del origen no debe conservar ni cargar por accidente la
        # playlist anterior.
        picker.value = ""
        has_items = bool(picker.options)
        picker.visible = has_items
        self._playlist_help.visible = True
        if has_items:
            self._playlist_help.value = (
                "1 playlist sincronizada" if len(items) == 1
                else f"{len(items)} playlists sincronizadas"
            )
        else:
            self._playlist_help.value = (
                "Sin playlists sincronizadas: usa «Usar URL o ID» o sincroniza "
                "la plataforma en Biblioteca."
            )
        for ctrl in (picker, self._playlist_help):
            try:
                ctrl.update()
            except Exception:
                pass

    # ── Skeleton pulse ─────────────────────────────────────────────────

    def _ensure_skeletons_pulsing(self) -> None:
        from ui.components.skeletons import start_skeleton_pulse_tasks
        start_skeleton_pulse_tasks(self._skeletons, self._skeleton_tasks)

    def _ensure_preview_skeletons_pulsing(self) -> None:
        from ui.components.skeletons import start_skeleton_pulse_tasks
        if getattr(self, '_preview_skeleton_tasks', None) is None:
            self._preview_skeleton_tasks = []
        start_skeleton_pulse_tasks(
            getattr(self, '_preview_skeletons', []),
            self._preview_skeleton_tasks,
        )

    def _stop_skeleton_pulse(self) -> None:
        from ui.components.skeletons import stop_skeleton_pulse_tasks
        stop_skeleton_pulse_tasks(self._skeletons, self._skeleton_tasks)
        if hasattr(self, '_preview_skeleton_tasks'):
            stop_skeleton_pulse_tasks(
                getattr(self, '_preview_skeletons', []),
                self._preview_skeleton_tasks,
            )
        for sk in getattr(self, '_preview_skeletons', []):
            try:
                sk.stop_pulse()
            except Exception:
                pass

    def _on_config_changed(self) -> None:
        """Propaga un cambio de preferencias a las vistas que las leen.

        Descargas lee el formato por defecto en su constructor, así que hay
        que refrescarla para que un cambio en Config se vea al volver.
        """
        for panel in getattr(self, "_module_panels", []):
            if panel is getattr(self, "_panel_config", None):
                continue
            refresh = getattr(panel, "refresh", None) or getattr(panel, "_refresh", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception:
                    pass

    def stop(self) -> None:
        self._stop_skeleton_pulse()
        try:
            if hasattr(self, "player_bar") and self.player_bar is not None:
                self.player_bar.release()
        except Exception:
            pass
        try:
            if self._view_shimmer_task and not self._view_shimmer_task.done():
                self._view_shimmer_task.cancel()
        except Exception:
            pass
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        if getattr(self, "_catalog_search_task", None) and not self._catalog_search_task.done():
            self._catalog_search_task.cancel()

    # ── Circuit breaker reactions ──────────────────────────────────────

    def _on_circuit_change(self, platform: str, is_open: bool, remaining: int) -> None:
        if self._dispatch_to_ui_loop(lambda: self._on_circuit_change(platform, is_open, remaining)):
            return
        banner_text = self._rl_banner.content.controls[1]
        self._rl_banner.visible = is_open
        if is_open:
            banner_text.value = f"Rate limit en {platform} · {remaining}s"
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(self._countdown(platform, remaining))
        else:
            banner_text.value = ""
        self._rl_banner.update()

    async def _countdown(self, platform: str, seconds: int) -> None:
        banner_text = self._rl_banner.content.controls[1]
        for rem in range(seconds, 0, -1):
            banner_text.value = f"Rate limit en {platform} · {rem}s"
            try:
                banner_text.update()
            except Exception:  # pylint: disable=broad-exception-caught
                break
            await asyncio.sleep(1)

    # ── Helpers ────────────────────────────────────────────────────────

    def _snack(self, msg: str, error: bool = False) -> None:
        notify(self.page, msg, kind="error" if error else "info")
