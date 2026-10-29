"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.7.0                             ║
║              Interfaz Principal de Usuario                           ║
╚══════════════════════════════════════════════════════════════════════╝

Módulo: ui/main_ui.py
Descripción: Componente principal de la interfaz de usuario. Implementa
            PlaylistManagerUI, la vista principal que orquesta todos los
            componentes visuales y maneja la interacción del usuario.

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
Versión: 4.7.0
Fecha: 2026
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Optional

import flet as ft

from core.models import Track, LoadState, TransferState
from core.state import AppState
from core.config import EXPORT_DEST_LABEL, EXPORT_FORMATS, EXPORT_ORDERS
from engine.parsers import parse_local_playlist_with_paths, build_local_tracks
from engine.exporters import export_tracks, default_export_path
from ui.song_row import SongRow, SkeletonRow, PreviewSkeletonRow, build_preview_row, ITEM_H
from ui.fonts import (
    FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_HEADLINE_MEDIUM, FONT_TEXT,
    brand_family, font_family_for, mono_family,
)
from ui.telemetry import TelemetryDrawer
from ui.transfer_report import TransferReportDialog
from ui.widgets import (
    _primary_btn, _ghost_btn, _section_label, _status_icon,
    app_text_field, dialog_action, app_dialog, DialogMixin, notify,
    input_border, debounce, limit_label,
)

from ui.tokens import (
    BG_DEEP, BG_PANEL, BG_SURFACE, BG_HOVER, BG_INPUT, SIDEBAR_BG,
    BG_LIST, CHIP_BG, BORDER_LIGHT, BORDER_MUTED,
    BORDER_ROW,
    ACCENT, ACCENT_DIM, ACCENT_HALO,
    SUCCESS, WARNING, ERROR_COL,
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM, SKELETON_DARK,
    DIALOG_OPTION_LIMIT,
    RADIUS_BUTTON, RADIUS_RAIL, RADIUS_RAIL_ITEM,
    RAIL_W_COLLAPSED, RAIL_W_EXPANDED, RAIL_MARGIN,
    RAIL_PAD_H, RAIL_PAD_V, RAIL_ITEM_SPACING,
    RAIL_ITEM_PAD_H, RAIL_ITEM_PAD_V, RAIL_ITEM_H,
    RAIL_HEADER_H, RAIL_LABEL_W, RAIL_BRAND_W,
    RAIL_VERSION_H, RAIL_VERSION_H_COLLAPSED,
    RAIL_ANIM_MS, RAIL_ITEM_ANIM_MS,
    MOTION_PANEL_MS,
)

# Por encima de este número conviene un único update del ListView en vez de
# publicar una actualización por fila (transferencias masivas, reordenamientos).
_ROW_UPDATE_BATCH_THRESHOLD = 16


class PlaylistManagerUI(DialogMixin):
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

    def _close_dlg(self, dlg) -> None:
        # Compat: ciclo de vida canónico en DialogMixin.
        self.close_dialog(self.page, dlg)

    def _make_order_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton para elegir Artista-Título vs Título-Artista (reusa tokens)."""
        # Fallback a Dropdown si SegmentedButton no está disponible en runtime
        try:
            return ft.SegmentedButton(
                selected=[current],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="artist-title", label=ft.Text("Artista - Título", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="title-artist", label=ft.Text("Título - Artista", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=on_change,
            )
        except Exception:
            # fallback Dropdown compacto
            dd = ft.Dropdown(
                value=current,
                width=200, height=38,
                bgcolor=BG_INPUT, border=input_border(),
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                options=[
                    ft.dropdown.Option("artist-title", "Artista - Título"),
                    ft.dropdown.Option("title-artist", "Título - Artista"),
                ],
                on_select=on_change,  # compat
            )
            return dd

    def _make_mode_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton Lista|Doble|Preview — modo doble solo al Organizar/Dividir."""
        try:
            return ft.SegmentedButton(
                selected=[current],
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="lista", label=ft.Text("Lista", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="doble", label=ft.Text("Doble", size=11, font_family=FONT_HEADLINE)),
                    ft.Segment(value="preview", label=ft.Text("Preview", size=11, font_family=FONT_HEADLINE)),
                ],
                on_change=on_change,
            )
        except Exception:
            return ft.Dropdown(
                value=current,
                width=180, height=38,
                bgcolor=BG_INPUT, border=input_border(),
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                options=[
                    ft.dropdown.Option("lista", "Lista"),
                    ft.dropdown.Option("doble", "Doble"),
                    ft.dropdown.Option("preview", "Preview"),
                ],
                on_select=on_change,
            )
                                  
    def _make_cono_empty(self, title: str, subtitle: str, visible: bool = True) -> ft.Container:
        """Empty con icono cono para Biblioteca/Descargas/Preview — reusa tokens."""
        return ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(controls=[
                ft.Container(content=ft.Icon(ft.Icons.CONSTRUCTION, size=52, color=TEXT_DIM),
                             bgcolor=CHIP_BG, border=ft.Border.all(0.8, BORDER_LIGHT),
                             border_radius=20, padding=ft.Padding.all(20)),
                ft.Text(title, size=18, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD, opacity=1.0),
                ft.Text(subtitle, size=12, color=TEXT_MUTED, font_family=FONT_TEXT, opacity=1.0, text_align=ft.TextAlign.CENTER),
                ft.Text("Función en desarrollo", size=11, color=TEXT_DIM, font_family=FONT_TEXT, opacity=0.8),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=visible,
            expand=True,
        )

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
            self._mode_bar.visible = bool(is_dual or has_transform)
        except Exception:
            pass
        # headers SOURCE|RESULT + Divisiones §1,6
        try:
            if is_dual:
                # izq = Playlist original base (§1), der = División activa §6
                self._lista_header_text.value = "PLAYLIST ORIGINAL"
                self._lista_header_icon.icon = ft.Icons.HISTORY
                if s.segments:
                    seg = s.active_segment_key or ""
                    # §6 una División a la vez; §7 División activa vs seleccionadas
                    self._preview_header_text.value = f"DIVISIÓN — {seg}" if seg else f"DIVISIONES — {len(s.segments)} divisiones"
                    self._preview_header_icon.icon = ft.Icons.CALL_SPLIT
                else:
                    self._preview_header_text.value = "RESULTADO — organizado"
                    self._preview_header_icon.icon = ft.Icons.SORT
            else:
                self._lista_header_text.value = "PLAYLIST ORIGINAL"
                self._lista_header_icon.icon = ft.Icons.LIST_ALT
                self._preview_header_text.value = "DIVISIÓN ACTIVA — preview"
                self._preview_header_icon.icon = ft.Icons.VISIBILITY_OUTLINED
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
        self._failed_dialog_shown:    bool  = False
        self._report_dialog_shown:    bool  = False
        self._transfer_start:         float = 0.0
        self._completion_snack_shown: bool  = False
        self._pm_cleared_for_load:    bool  = False
        self.auth_manager                   = None
        self._prev_dual_mode: str = "lista"
        self._view_shimmer_task: Optional[asyncio.Task] = None

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
        # CONSTRUCCIÓN DE COMPONENTES PRINCIPALES
        # ──────────────────────────────────────────────────────────────
        
        self._build_sidebar()
        self._build_content()

        # ──────────────────────────────────────────────────────────────
        # NAVIGATION RAIL FLOTANTE — docked, desplaza el contenido
        # Shell con margen exterior y radio de diálogo; ítems con radio de botón.
        # ──────────────────────────────────────────────────────────────
        self._current_module = 0  # 0=Gestión, 1=Biblioteca, 2=Descargas, 3=Config
        self._rail_collapsed = True  # arranca docked: solo iconos
        self._rail_w_collapsed = RAIL_W_COLLAPSED
        self._rail_w_expanded = RAIL_W_EXPANDED
        # metadata destinos (label, icon, selected_icon)
        self._rail_dests = [
            ("Gestión", ft.Icons.TUNE_OUTLINED, ft.Icons.TUNE),
            ("Biblioteca", ft.Icons.LIBRARY_MUSIC_OUTLINED, ft.Icons.LIBRARY_MUSIC),
            ("Descargas", ft.Icons.DOWNLOAD_OUTLINED, ft.Icons.DOWNLOAD),
            ("Config", ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS),
        ]
        self._rail_label_boxes: list[ft.Container] = []
        self._rail_item_refs: list[ft.Container] = []

        def _select_module(idx: int) -> None:
            if idx == self._current_module:
                return
            self._current_module = idx
            try:
                for i, panel in enumerate(self._module_panels):
                    panel.visible = (i == idx)
                    # Config lee preferencias al mostrarse: otra vista pudo
                    # cambiarlas mientras estaba oculto.
                    if i == idx and hasattr(panel, "refresh"):
                        panel.refresh()
                self._refresh_rail_selection()
                if idx == 0:
                    self._sync_skeleton_count()
                if hasattr(self, "player_bar") and self.player_bar is not None:
                    self.player_bar.log_audio_services_count(
                        f"vista={self._rail_dests[idx][0]}"
                    )
                self.page.update()
            except Exception:
                pass

        # Biblioteca y otras vistas necesitan cambiar de módulo después del
        # arranque (p. ej. «Crear playlist» desde una carpeta local).
        self._select_module = _select_module

        def _on_rail_item_click(e):
            try:
                idx = int(e.control.data)
            except Exception:
                idx = 0
            _select_module(idx)

        # refs para animación header/version
        self._rail_header_brand: ft.Text | None = None
        self._rail_header_brand_box: ft.Container | None = None
        self._rail_header_box: ft.Container | None = None
        self._rail_header_row: ft.Row | None = None
        self._rail_version_text: ft.Text | None = None
        self._rail_version_box: ft.Container | None = None

        # builder de items (Container con animate + ink) — Headline para labels, Notch para brand
        def _rail_item(idx: int, label: str, icon_out: str, icon_sel: str) -> ft.Container:
            is_sel = (idx == self._current_module)
            # Stack Sans Headline para labels (§ Headline: labels/botones)
            txt = ft.Text(
                label, size=13, color=TEXT_PRIMARY if is_sel else TEXT_MUTED,
                font_family=FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE,
                visible=True,
                expand=True, no_wrap=True, max_lines=1,
                overflow=ft.TextOverflow.CLIP,
            )
            # La caja del label anima ancho/margen/opacidad. Así el texto no
            # desaparece de golpe ni reacomoda la altura al colapsar.
            label_box = ft.Container(
                content=txt,
                width=RAIL_LABEL_W if not self._rail_collapsed else 0,
                height=20,
                margin=ft.Margin.only(left=12) if not self._rail_collapsed else ft.Margin.only(left=0),
                opacity=1.0 if not self._rail_collapsed else 0.0,
                alignment=ft.Alignment.CENTER_LEFT,
                clip_behavior=ft.ClipBehavior.HARD_EDGE,
                animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
                animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            )
            self._rail_label_boxes.append(label_box)
            icon = ft.Icon(icon_sel if is_sel else icon_out, size=20, color=ACCENT if is_sel else TEXT_DIM)
            # Row fija: el centrado/izquierda lo resuelve el Container padre.
            row = ft.Row(
                [icon, label_box],
                spacing=0,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                tight=True,
                alignment=ft.MainAxisAlignment.START,
            )
            cont = ft.Container(
                content=row,
                bgcolor=ACCENT_HALO if is_sel else ft.Colors.TRANSPARENT,
                border=ft.Border.all(0.6, ACCENT if is_sel else ft.Colors.TRANSPARENT),
                border_radius=RADIUS_RAIL_ITEM,
                padding=ft.Padding.symmetric(horizontal=RAIL_ITEM_PAD_H, vertical=RAIL_ITEM_PAD_V),
                height=RAIL_ITEM_H,
                ink=True,
                data=str(idx),
                tooltip=label if self._rail_collapsed else None,
                on_click=_on_rail_item_click,
                animate=ft.Animation(RAIL_ITEM_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
                animate_align=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
                alignment=ft.Alignment.CENTER if self._rail_collapsed else ft.Alignment.CENTER_LEFT,
            )
            self._rail_item_refs.append(cont)
            return cont

        # toggle colapso: ancho del shell + ancho/opacidad de labels en paralelo
        def _toggle_rail(_e=None):
            self._rail_collapsed = not self._rail_collapsed
            expanded = not self._rail_collapsed
            try:
                self._nav_rail.width = self._rail_w_expanded if expanded else self._rail_w_collapsed
                # icon toggle
                self._rail_toggle_btn.icon = ft.Icons.MENU_OPEN if expanded else ft.Icons.MENU
                self._rail_toggle_btn.tooltip = "Colapsar" if expanded else "Expandir"
                # header brand: caja que crece/decrece y se desvanece
                if self._rail_header_brand_box is not None:
                    self._rail_header_brand_box.width = RAIL_BRAND_W if expanded else 0
                    self._rail_header_brand_box.margin = ft.Margin.only(left=8) if expanded else ft.Margin.only(left=0)
                    self._rail_header_brand_box.opacity = 1.0 if expanded else 0.0
                if self._rail_header_box is not None:
                    self._rail_header_box.alignment = ft.Alignment.CENTER_LEFT if expanded else ft.Alignment.CENTER
                if self._rail_version_text is not None:
                    self._rail_version_text.opacity = 0.7 if expanded else 0.0
                if self._rail_version_box is not None:
                    # En compacto no deja espacio muerto bajo Config.
                    self._rail_version_box.height = RAIL_VERSION_H if expanded else RAIL_VERSION_H_COLLAPSED
                # items: el ancho del label y el align del ítem animan juntos
                for i, label_box in enumerate(self._rail_label_boxes):
                    label_box.width = RAIL_LABEL_W if expanded else 0
                    label_box.margin = ft.Margin.only(left=12) if expanded else ft.Margin.only(left=0)
                    label_box.opacity = 1.0 if expanded else 0.0
                    try:
                        cont = self._rail_item_refs[i]
                        cont.tooltip = None if expanded else self._rail_dests[i][0]
                        cont.alignment = ft.Alignment.CENTER_LEFT if expanded else ft.Alignment.CENTER
                    except Exception:
                        pass
                self._nav_rail.update()
            except Exception:
                pass

        self._rail_toggle_btn = ft.IconButton(
            icon=ft.Icons.MENU if self._rail_collapsed else ft.Icons.MENU_OPEN,
            icon_size=18, icon_color=TEXT_MUTED,
            tooltip="Expandir" if self._rail_collapsed else "Colapsar",
            on_click=_toggle_rail,
            style=ft.ButtonStyle(
                padding=4,
                bgcolor={ft.ControlState.HOVERED: BG_HOVER},
                shape=ft.RoundedRectangleBorder(radius=RADIUS_BUTTON),
            ),
        )
        # header del rail con toggle — Stack Sans Notch Bold, tamaño similar a sidebar (14-16)
        self._rail_header_brand = ft.Text(
            "Melomaniac", size=20, color=TEXT_PRIMARY,
            font_family=brand_family("bold"),
            visible=True,
            expand=True, no_wrap=True, max_lines=1,
            overflow=ft.TextOverflow.CLIP,
        )
        self._rail_header_brand_box = ft.Container(
            content=self._rail_header_brand,
            width=RAIL_BRAND_W if not self._rail_collapsed else 0,
            height=20,
            margin=ft.Margin.only(left=8) if not self._rail_collapsed else ft.Margin.only(left=0),
            opacity=1.0 if not self._rail_collapsed else 0.0,
            alignment=ft.Alignment.CENTER_LEFT,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        self._rail_header_row = ft.Row(
            [self._rail_toggle_btn, self._rail_header_brand_box],
            spacing=0, height=RAIL_HEADER_H, tight=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.START,
        )
        self._rail_header_box = ft.Container(
            content=self._rail_header_row,
            height=RAIL_HEADER_H,
            alignment=ft.Alignment.CENTER_LEFT if not self._rail_collapsed else ft.Alignment.CENTER,
            animate_align=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        rail_header = self._rail_header_box
        # Grupo principal: Gestión, Biblioteca y Descargas
        rail_items = [
            _rail_item(i, lbl, ico, sel)
            for i, (lbl, ico, sel) in enumerate(self._rail_dests[:3])
        ]
        # Config queda separado al fondo, antes de la versión
        config_item = _rail_item(3, *self._rail_dests[3])

        # helper para refrescar selección; el update lo hace el caller una sola vez
        def _refresh_rail_selection():
            for i, cont in enumerate(self._rail_item_refs):
                is_sel = (i == self._current_module)
                try:
                    cont.bgcolor = ACCENT_HALO if is_sel else ft.Colors.TRANSPARENT
                    cont.border = ft.Border.all(0.6, ACCENT if is_sel else ft.Colors.TRANSPARENT)
                    row = cont.content  # Row([icon, label_box])
                    if row and len(row.controls) >= 2:
                        icon_ctrl = row.controls[0]
                        label_box = row.controls[1]
                        lbl_ctrl = label_box.content if isinstance(label_box, ft.Container) else None
                        icon_ctrl.name = self._rail_dests[i][2] if is_sel else self._rail_dests[i][1]
                        icon_ctrl.color = ACCENT if is_sel else TEXT_DIM
                        if lbl_ctrl:
                            lbl_ctrl.color = TEXT_PRIMARY if is_sel else TEXT_MUTED
                            lbl_ctrl.font_family = FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE
                except Exception:
                    pass
        self._refresh_rail_selection = _refresh_rail_selection  # type: ignore

        # versión — IBM Plex Mono Light 300 (datos técnicos § Mono)
        self._rail_version_text = ft.Text(
            "v4.7.0", size=8, color=TEXT_DIM,
            font_family=mono_family("light"),
            visible=True,
            height=RAIL_VERSION_H, no_wrap=True, max_lines=1,
            opacity=0.7 if not self._rail_collapsed else 0.0,
            animate_opacity=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            text_align=ft.TextAlign.CENTER,
        )
        self._rail_version_box = ft.Container(
            content=self._rail_version_text,
            height=RAIL_VERSION_H if not self._rail_collapsed else RAIL_VERSION_H_COLLAPSED,
            clip_behavior=ft.ClipBehavior.HARD_EDGE,
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
        )
        rail_bottom = ft.Column(
            controls=[config_item, self._rail_version_box],
            spacing=2,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        # Shell flotante: margen respecto a la ventana + radio de diálogo
        self._nav_rail = ft.Container(
            width=self._rail_w_expanded if not self._rail_collapsed else self._rail_w_collapsed,
            margin=ft.Margin.all(RAIL_MARGIN),
            bgcolor=SIDEBAR_BG,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_RAIL,
            padding=ft.Padding.symmetric(horizontal=RAIL_PAD_H, vertical=RAIL_PAD_V),
            shadow=ft.BoxShadow(
                blur_radius=18,
                color=ft.Colors.with_opacity(0.45, BG_DEEP),
                offset=ft.Offset(0, 4),
            ),
            animate=ft.Animation(RAIL_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                controls=[
                    rail_header,
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    ft.Column(
                        controls=rail_items,
                        spacing=RAIL_ITEM_SPACING,
                        horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    ft.Container(expand=True),
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    rail_bottom,
                ],
                spacing=10, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )

        # Paneles de módulos — Gestión es Row([sidebar, content]); Biblioteca y Descargas tienen vistas propias
        self._panel_gestion = ft.Row(controls=[self._sidebar, self._content], spacing=0, expand=True,
                                     vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        # Biblioteca: LibraryView con selector de fuente (plataformas + Local)
        try:
            from services.library_state import LibraryState
            from ui.library_view import LibraryView
            # library_state con inyección service (respeta core->services)
            lib_state = LibraryState(service=getattr(state, "service", None) or getattr(self.state, "service", None))
            # si service aún None (init temprano), se inyectará luego via app.py/ui.library_state
            self.library_state = lib_state  # type: ignore
            self._panel_biblioteca = LibraryView(
                page, lib_state,
                getattr(state, "service", None) or getattr(self.state, "service", None),
                app_state=state,
                on_open_downloads=lambda: _select_module(2),
                on_open_gestion=self._open_folder_in_gestion,
            )
        except Exception as exc:
            # fallback placeholder no rompe app
            try:
                state.log(f"[WARN] Biblioteca fallback: {exc}")  # type: ignore
            except Exception:
                pass
            self._panel_biblioteca = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                                  content=self._make_cono_empty("Biblioteca", "Colección local, historial 20 y portadas — pronto.", visible=True))
        self._auto_play_download_ids: set[str] = set()
        from ui.player_bar import PlayerBar
        from ui.player_queue import PlayerQueuePanel

        self.player_bar = PlayerBar(
            page,
            state,
            on_toggle_queue=lambda: self.player_queue.toggle(),
        )
        self.player_queue = PlayerQueuePanel(page, self.player_bar)
        self.player_bar.log_audio_services_count("init")
        if hasattr(self, "_panel_biblioteca") and self._panel_biblioteca is not None:
            self._panel_biblioteca.player_bar = self.player_bar

        try:
            from ui.downloads_view import DownloadsView
            self._panel_descargas = DownloadsView(page, state, library_state=getattr(self, "library_state", None))
            self._panel_descargas.player_bar = self.player_bar  # type: ignore[attr-defined]
        except Exception as exc:
            try:
                state.log(f"[WARN] Descargas fallback: {exc}")  # type: ignore
            except Exception:
                pass
            self._panel_descargas = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                                 content=self._make_cono_empty("Descargas", "yt-dlp + progreso — no disponible.", visible=True))
        # Config: preferencias persistentes. El wizard de credenciales sigue
        # abriéndose desde el engranaje del panel izquierdo.
        try:
            from ui.config_view import ConfigView
            self._panel_config = ConfigView(page, on_change=self._on_config_changed)
        except Exception as exc:
            try:
                state.log(f"[WARN] Config fallback: {exc}")  # type: ignore
            except Exception:
                pass
            self._panel_config = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                              content=self._make_cono_empty("Configuración", "Preferencias no disponibles.", visible=True))
        self._module_panels = [self._panel_gestion, self._panel_biblioteca, self._panel_descargas, self._panel_config]
        # apila módulos, solo uno visible a la vez
        for i, p in enumerate(self._module_panels):
            p.visible = (i == 0)
        # El stack debe entregar toda la altura a cada panel para que los
        # estados vacíos puedan centrar su contenido también verticalmente.
        self._module_stack = ft.Stack(
            controls=self._module_panels,
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

        # ──────────────────────────────────────────────────────────────
        # ENSAMBLAJE DE LAYOUT RAÍZ
        # ──────────────────────────────────────────────────────────────
        # Layout principal: rail flotante + módulo + cola lateral, barra de reproducción abajo
        self.root = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            self._nav_rail,
                            self._module_stack,
                            self.player_queue,
                        ],
                        spacing=0, expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    self.player_bar,
                ],
                spacing=0, expand=True,
            ),
            bgcolor=BG_LIST, expand=True,
        )

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

    def _build_sidebar(self) -> None:
        s = self.state

        self.btn_wizard = ft.IconButton(
            icon=ft.Icons.SETTINGS_OUTLINED, icon_color=TEXT_DIM, icon_size=16,
            tooltip="Configurar credenciales", on_click=self._on_open_wizard,
            style=ft.ButtonStyle(overlay_color={ft.ControlState.HOVERED: BG_HOVER},
                                 shape=ft.RoundedRectangleBorder(radius=8)),
        )
        logo = ft.Column([
            ft.Row([
                ft.Container(
                    content=ft.Icon(ft.Icons.HEADPHONES, color=ACCENT, size=22),
                    bgcolor=ACCENT_HALO, border_radius=8, padding=ft.Padding.all(6),
                ),
                ft.Column([
                    ft.Text(spans=[
                        ft.TextSpan("Melomaniac", ft.TextStyle(size=20,
                                                               color=TEXT_PRIMARY, font_family=brand_family("bold"))),
                    ], opacity=1.0),
                    ft.Text("v4.7.0", size=9, color=TEXT_DIM, font_family=FONT_TEXT,
                            style=ft.TextStyle(letter_spacing=0.8), opacity=1.0),
                ], spacing=0, tight=True, expand=True),
                self.btn_wizard,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=0)

        _dd_style = dict(
            bgcolor=BG_INPUT, border=input_border(),
            label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=8, vertical=0),
            expand=True,
        )

        def _on_src_select(e) -> None:
            val = e.control.value
            s.set_source(val)
            if val == "Archivo Local":
                asyncio.create_task(self._do_local_pick())
            elif val == "Pegar Texto":
                self._open_paste_dialog()
            elif val in AppState.PLATFORMS:
                asyncio.create_task(self._refresh_auth_live(val))

        def _on_dst_select(e) -> None:
            val = e.control.value
            s.set_destination(val)
            if val == EXPORT_DEST_LABEL:
                # Export local no requiere sesión
                self._transfer_btn.content.value = "Exportar"  # type: ignore
                self._transfer_btn.icon = ft.Icons.SAVE_ALT  # type: ignore
                self._transfer_btn.update()
                return
            else:
                # restaura icono transfer si venía de export
                try:
                    self._transfer_btn.content.value = "Transferir"  # type: ignore
                    self._transfer_btn.icon = ft.Icons.SWAP_HORIZ  # type: ignore
                except Exception:
                    pass
            if val in AppState.PLATFORMS:
                asyncio.create_task(self._refresh_auth_live(val))

        self._src_dd = ft.Dropdown(
            label="Origen", value=s.source,
            options=[ft.dropdown.Option(key=p, text=p) for p in AppState.SOURCE_OPTIONS],
            on_select=_on_src_select, **_dd_style,
        )
        _dst_opts = [*AppState.PLATFORMS, EXPORT_DEST_LABEL]
        self._dst_dd = ft.Dropdown(
            label="Destino", value=s.destination,
            options=[ft.dropdown.Option(key=p, text=p) for p in _dst_opts],
            on_select=_on_dst_select, **_dd_style,
        )
        self._status_badge      = ft.Text("", size=10, color=SUCCESS, font_family=mono_family(), opacity=1.0)
        self._dest_session_warn = ft.Text("", size=9, color=ERROR_COL, font_family=FONT_TEXT, visible=False)

        platform_section = ft.Column([
            _section_label("PLATAFORMAS"),
            ft.Row([self._src_dd, self._dst_dd], spacing=8),
            self._status_badge,
            self._dest_session_warn,
        ], spacing=8)

        self._id_clear_btn = ft.IconButton(
            icon=ft.Icons.CLEAR, icon_color=TEXT_DIM, icon_size=10,
            width=24, height=24, padding=0,
            tooltip="Limpiar ID", visible=False,
            on_click=self._on_clear_id,
            style=ft.ButtonStyle(bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT}),
        )

        def _on_id_change(_):
            self._id_clear_btn.visible = bool(self._id_field.value)
            self._id_clear_btn.update()

        self._id_field = app_text_field(
            label="ID o URL de la Playlist",
            hint_text="pl.xxxx  /  PLxxxx  /  URL",
            suffix=self._id_clear_btn,
            on_change=_on_id_change,
            on_submit=lambda e: asyncio.create_task(self._do_cloud_load(e)),
        )
        self._playlist_section = ft.Column([
            _section_label("PLAYLIST"), self._id_field,
        ], spacing=8, visible=(s.source not in AppState.LOCAL_SOURCES))
        self._playlist_divider = ft.Divider(
            height=1, color=BORDER_MUTED, thickness=0.5,
            visible=(s.source not in AppState.LOCAL_SOURCES),
        )

        _BTN_W, _BTN_H = 129, 44
        self._load_btn     = _primary_btn("Cargar",      ft.Icons.DOWNLOAD,   self._on_load,     width=_BTN_W, height=_BTN_H)
        self._transfer_btn = _ghost_btn(  "Transferir",  ft.Icons.SWAP_HORIZ, self._on_transfer, width=_BTN_W, height=_BTN_H)
        self._organize_btn = _ghost_btn(  "Organizar",   ft.Icons.SORT,       self._on_organize, width=_BTN_W, height=_BTN_H, disabled=True)
        self._split_btn    = _ghost_btn(  "Dividir",     ft.Icons.CALL_SPLIT, self._on_split,    width=_BTN_W, height=_BTN_H, disabled=True)
        self._reorganize_btn = _ghost_btn("Reorganizar", ft.Icons.REORDER, self._on_reorganize, width=_BTN_W*2+6, height=_BTN_H, disabled=True)

        actions = ft.Column([
            ft.Row([self._load_btn,  self._transfer_btn], spacing=6),
            ft.Row([self._organize_btn, self._split_btn], spacing=6),
            ft.Row([self._reorganize_btn], spacing=6),
        ], spacing=6)

        self._rl_banner = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.TIMER_OUTLINED, color=WARNING, size=14),
                ft.Text("", size=10, color=WARNING, font_family=mono_family("light"), opacity=1.0),
            ], spacing=6),
            bgcolor=BG_PANEL, border=ft.Border.all(0.8, WARNING),
            border_radius=8, padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            visible=False,
        )

        self._progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
        self._progress_row = ft.Container(
            content=ft.Column([
                self._progress_bar,
                ft.Row([ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)],
                       alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
            ], spacing=4),
            visible=False,
            border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

        self._telemetry = TelemetryDrawer(self.page, sidebar_width=300)

        fixed_top = ft.Column(controls=[
            logo,
            ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
            platform_section,
            ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
            self._playlist_section,
            self._playlist_divider,
            _section_label("ACCIONES"),
            actions,
        ], spacing=12)

        scrollable_bottom = ft.Column(controls=[
            self._rl_banner,
            self._telemetry.container,
        ], spacing=12, scroll=ft.ScrollMode.ADAPTIVE, expand=True)

        sidebar_col   = ft.Column(controls=[fixed_top, scrollable_bottom], spacing=12, expand=True)
        sidebar_stack = ft.Stack(controls=[sidebar_col, self._telemetry.handle], expand=True)

        self._sidebar = ft.Container(
            width=300, padding=ft.Padding.all(18),
            bgcolor=SIDEBAR_BG, clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            border_radius=ft.BorderRadius.only(top_right=14, bottom_right=14),
            border=ft.Border.only(right=ft.BorderSide(1, BORDER_LIGHT)),
            content=sidebar_stack,
        )

    # ── MÉTODOS DE ORGANIZACIÓN Y DIVISIÓN ────────────────────────────

    def _on_organize(self, _e: ft.ControlEvent) -> None:
        """Organizar separado — Mantener orden (agrupar) o A→Z/Z→A + agrupar; sin alcance."""
        try:
            from ui.organize_panel import show_organize_dialog
            show_organize_dialog(self.page, self.state)
        except Exception as exc:
            # fallback legacy mínimo — incluye Mantener orden para agrupar
            try:
                from ui.widgets import organize_dropdown
                from engine.organizer import group_tracks_stable
                _dd = organize_dropdown([("artist","Artista"),("album","Álbum"),("name","Título"),("duration_ms","Duración"),("release_date","Fecha")], getattr(self.state, "organize_sort_key", "artist"),"Ordenar por")
                _ord_raw = getattr(self.state, "organize_order", "az")
                if _ord_raw == "mantener":
                    _ord_raw = "original"
                _orden = {"value": _ord_raw if _ord_raw in ("az","za","original") else "az"}
                try:
                    _seg = ft.SegmentedButton(selected=[_orden["value"]], allow_empty_selection=False, allow_multiple_selection=False, show_selected_icon=False,
                        style=ft.ButtonStyle(bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE}, color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED}),
                        segments=[ft.Segment(value="original", label=ft.Text("Mantener orden", size=10)), ft.Segment(value="az", label=ft.Text("A → Z", size=11)), ft.Segment(value="za", label=ft.Text("Z → A", size=11))],
                        on_change=lambda e: _orden.__setitem__("value", list(e.control.selected)[0] if hasattr(e.control,"selected") and e.control.selected else e.control.value))
                except Exception:
                    _seg = ft.Dropdown(value=_orden["value"], width=240, bgcolor=BG_INPUT, border=input_border(), options=[ft.dropdown.Option("original","Mantener orden"), ft.dropdown.Option("az","A → Z"), ft.dropdown.Option("za","Z → A")], on_select=lambda e: _orden.__setitem__("value", e.control.value))
                def _ap(_e):
                    # persist fallback también
                    try:
                        self.state.organize_sort_key = _dd.value
                        self.state.organize_order = _orden["value"]
                    except Exception:
                        pass
                    if _orden["value"] == "original":
                        # Mantener orden sirve para agrupar: si hay agrupación previa usa group, si no restaura
                        # fallback sin selector de agrupar → solo restaura
                        self.state.organize_sort(["original_position"], False, scope="all")
                    else:
                        rev = _orden["value"]=="za"
                        self.state.organize_sort([_dd.value], rev, scope="all")
                    self._close_dlg(dlg)
                def _cl(_e):
                    try:
                        self.state.clear_organize()
                    except Exception:
                        self.state.organize_sort(["original_position"], False, scope="all")
                    self._close_dlg(dlg)
                _has = bool(getattr(self.state, "show_dual", False) or getattr(self.state, "segments", {}) or (getattr(self.state, "source_tracks", None) and self.state.tracks != self.state.source_tracks))
                _acts: list[ft.Control] = []
                if _has:
                    _acts.append(dialog_action("Limpiar", _cl, kind="danger"))
                _acts += [dialog_action("Cancelar", lambda _:self._close_dlg(dlg), kind="muted"), dialog_action("Aplicar", _ap, kind="primary")]
                dlg=app_dialog("Organizar", ft.Column([_dd, _seg], tight=True, spacing=12), _acts, width=420, bgcolor=BG_SURFACE, radius=10,
                               actions_alignment=ft.MainAxisAlignment.SPACE_BETWEEN if len(_acts)==3 else ft.MainAxisAlignment.END)
                self.page.show_dialog(dlg)
            except Exception:
                self.state.log(f"[ERROR] Organizar fallback falló: {exc}")

    def _on_reorganize(self, _e: ft.ControlEvent) -> None:
        """Reorganizar por niveles §8 — control específico Artistas/Álbumes drag & drop."""
        try:
            from ui.organize_panel import show_reorganize_dialog
            show_reorganize_dialog(self.page, self.state)
        except Exception as exc:
            self.state.log(f"[ERROR] Reorganizar falló: {exc}")

    def _on_split(self, _e: ft.ControlEvent) -> None:
        """Dividir — genera Divisiones por Artista/Álbum/Género/Título §2,5."""
        try:
            from ui.organize_panel import show_group_dialog
            show_group_dialog(self.page, self.state)
        except Exception as exc:
            try:
                from ui.widgets import organize_dropdown
                _dd=organize_dropdown([("artist","Artista"),("album","Álbum"),("genre","Género"),("name","Título")],"artist","Dividir por")
                def _ap(_e): self.state.organize_split(_dd.value, scope="all"); self._close_dlg(dlg)
                def _cl(_e): self.state.clear_split(); self._close_dlg(dlg)
                acts=[]
                if bool(self.state.segments): acts.append(dialog_action("Limpiar División",_cl,kind="danger"))
                acts+=[dialog_action("Cancelar",lambda _:self._close_dlg(dlg),kind="muted"),dialog_action("Dividir",_ap,kind="primary")]
                dlg=app_dialog("Dividir", ft.Column([_dd], tight=True, spacing=12), acts, width=400, bgcolor=BG_SURFACE, radius=10)
                self.page.show_dialog(dlg)
            except Exception:
                self.state.log(f"[ERROR] Dividir fallback falló: {exc}")

    def _partition_btn_label(self) -> str:
        s = self.state
        if not s.segments:
            return "Todos"
        total = len(s.segments)
        active = s.active_segment_keys
        if active is None or len(active) == total or len(active) == 0:
            return f"Todos ({total})"
        if len(active) == 1:
            return next(iter(active))
        return f"{len(active)} seleccionados"

    def _refresh_division_popup(self) -> None:
        """Popup dinámico 2..5 divisiones — texto del botón = activa."""
        try:
            s = self.state
            if not s.segments:
                self._division_popup.visible = False
                self._division_col.visible = False
                return
            active = s.active_segment_key or next(iter(s.segments.keys()))
            self._division_btn_text.value = active
            items = []
            for k in s.segments.keys():
                cnt = len(s.segments.get(k, []))
                def _on_pick(e, _k=k):
                    try:
                        s.set_active_segment(_k)
                        self._division_btn_text.value = _k
                        self._division_popup.update()
                    except Exception:
                        pass
                items.append(ft.PopupMenuItem(content=ft.Text(f"{k} ({cnt})"), data=k, on_click=_on_pick, checked=(k == active)))
            self._division_popup.items = items
            self._division_popup.visible = True
            self._division_col.visible = True
            try:
                self._division_popup.update()
                self._division_col.update()
            except Exception:
                pass
        except Exception:
            pass

    def _open_partition_picker(self) -> None:
        s = self.state
        if not s.segments:
            self._snack("No hay divisiones — divide primero por Artista/Álbum/Género/Título", error=True)
            return
        all_keys = list(s.segments.keys())  # orden 1ª aparición O(n) §12
        # estado local mutable — §7 División activa vs seleccionadas para transferir
        selected: set[str] = set(s.active_segment_keys) if s.active_segment_keys is not None else set(s.segments.keys())

        search_field = app_text_field(hint_text="Buscar división…", prefix_icon=ft.Icons.SEARCH, width=360, height=36, content_padding=ft.Padding.symmetric(horizontal=10, vertical=6))
        list_view = ft.ListView(height=260, spacing=4, padding=ft.Padding.all(4), expand=False)

        def _rebuild(filter_q: str = ""):
            q = (filter_q or "").strip().lower()
            list_view.controls.clear()
            for k in all_keys:
                if q and q not in k.lower():
                    continue
                cnt = len(s.segments.get(k, []))
                is_checked = k in selected
                # checkbox por partición — buscable
                chk = ft.Checkbox(
                    label=f"{k} ({cnt})",
                    value=is_checked,
                    fill_color={ft.ControlState.SELECTED: ACCENT},
                    check_color=TEXT_PRIMARY,
                    label_style=ft.TextStyle(color=TEXT_PRIMARY if is_checked else TEXT_MUTED, size=11, font_family=FONT_HEADLINE),
                    border_side=ft.BorderSide(1.2, ACCENT if is_checked else TEXT_DIM),
                    data=k,
                    on_change=lambda e, _k=k: _on_toggle(_k, bool(e.control.value)),
                )
                list_view.controls.append(chk)
            try:
                list_view.update()
            except Exception:
                pass

        def _on_toggle(key: str, checked: bool):
            if checked:
                selected.add(key)
            else:
                selected.discard(key)
            # no cierra, solo actualiza estado visual; si queda 0 → interpreta como Todos luego

        def _on_search(e):
            _rebuild(e.control.value or "")

        search_field.on_change = _on_search
        _rebuild("")

        dlg_ref: dict = {}

        def _apply(_e):
            # si todos seleccionados → None (Todos)
            if len(selected) == len(all_keys) or len(selected) == 0:
                s.set_active_segments(None)
            else:
                s.set_active_segments(set(selected))
            self._close_dlg(dlg_ref.get("dlg"))

        def _select_all(_e):
            selected.clear()
            selected.update(all_keys)
            _rebuild(search_field.value or "")

        def _clear(_e):
            selected.clear()
            _rebuild(search_field.value or "")

        dlg = app_dialog(
            "Filtrar divisiones",
            ft.Column([
                search_field,
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                list_view,
            ], tight=True, spacing=8),
            [
                dialog_action("Seleccionar todos", _select_all, kind="muted"),
                dialog_action("Limpiar", _clear, kind="muted"),
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg_ref.get("dlg")), kind="muted"),
                dialog_action("Aplicar", _apply, kind="primary"),
            ],
            width=420, bgcolor=BG_SURFACE, radius=10,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)

    def _open_edit_division_dialog(self) -> None:
        """Editar división §4 — nombre editable, criterio artista/álbum/canción (canción default), quitar/agregar expand full."""
        s = self.state
        if not s.segments:
            self._snack("No hay divisiones para editar — divide primero", error=True)
            return
        active = s.active_segment_key or next(iter(s.segments.keys()))
        src_tracks = getattr(s, "source_tracks", s.tracks) or s.tracks
        div_tracks = s.segments.get(active, [])
        # nombre editable de la división (playlist derivada) — sin expand vertical: ancho fijo
        name_field = app_text_field(value=active, label="Nombre división", hint_text=active, width=780, height=36, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4))
        # alto de listas adaptado a la ventana (sin flex vertical: alturas explícitas, sin huecos)
        try:
            _win_h = self.page.height or self.page.window.height or 700  # type: ignore
        except Exception:
            _win_h = 700
        lv_h = max(200, min(320, int(_win_h - 420)))
        def _on_name(e):
            nonlocal active
            new = (e.control.value or "").strip()
            if new and new != active and new not in s.segments:
                s.segments[new] = s.segments.pop(active)
                if s.active_segment_key == active:
                    s.active_segment_key = new
                    s.active_segment_keys = {new}
                # Sin notify por tecla: la UI de fondo se actualiza al aplicar.
                active = new
        name_field.on_change = _on_name
        name_field.on_submit = _on_name
        # Solo se restauran los miembros de la división; no hace falta copiar
        # los Track completos (deepcopy de 1.000 pistas era costoso).
        _snapshot = list(s.segments.get(active, []))
        # criterio añadir / eliminar: artista/álbum/canción (canción default)
        add_criterion = {"value": "song"}
        remove_criterion = {"value": "song"}
        def _icon_for(v: str):
            return ft.Icons.PERSON if v == "artist" else ft.Icons.ALBUM if v == "album" else ft.Icons.MUSIC_NOTE
        # listas buscables
        left_sel: set[str] = set()
        right_sel: set[str] = set()
        left_search = app_text_field(hint_text="Buscar en ORIGINAL…", prefix_icon=ft.Icons.SEARCH, expand=True, height=32, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4))
        right_search = app_text_field(hint_text="Buscar en División…", prefix_icon=ft.Icons.SEARCH, expand=True, height=32, content_padding=ft.Padding.symmetric(horizontal=8, vertical=4))
        left_lv = ft.ListView(spacing=4, padding=ft.Padding.all(6), height=lv_h)
        right_lv = ft.ListView(spacing=4, padding=ft.Padding.all(6), height=lv_h)
        left_limit = ft.Text("", size=9, color=TEXT_DIM, font_family=FONT_TEXT)
        right_limit = ft.Text("", size=9, color=TEXT_DIM, font_family=FONT_TEXT)

        def _rebuild_left(q: str = ""):
            ql = (q or "").strip().lower()
            left_lv.controls.clear()
            crit = add_criterion["value"]
            if crit == "artist":
                artists = sorted(
                    (a for a in set(t.artist for t in src_tracks if t.artist)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = artists[:DIALOG_OPTION_LIMIT]
                left_limit.value = limit_label(len(shown), len(artists))
                for a in shown:
                    # cuenta canciones de ese artista en ORIGINAL
                    cnt = sum(1 for t in src_tracks if t.artist == a)
                    label = f"{a} ({cnt})"
                    chk = ft.Checkbox(label=label, value=a in left_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in left_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in left_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (left_sel.add(_a) if e.control.value else left_sel.discard(_a)))
                    left_lv.controls.append(chk)
            elif crit == "album":
                albums = sorted(
                    (a for a in set(t.album for t in src_tracks if t.album)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = albums[:DIALOG_OPTION_LIMIT]
                left_limit.value = limit_label(len(shown), len(albums))
                for a in shown:
                    cnt = sum(1 for t in src_tracks if t.album == a)
                    label = f"{a} ({cnt})"
                    chk = ft.Checkbox(label=label, value=a in left_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in left_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in left_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (left_sel.add(_a) if e.control.value else left_sel.discard(_a)))
                    left_lv.controls.append(chk)
            else:
                matches = []
                for t in src_tracks:
                    label = f"{t.name[:24]} — {t.artist[:16]}"
                    if not ql or ql in label.lower():
                        matches.append((t, label))
                shown = matches[:DIALOG_OPTION_LIMIT]
                left_limit.value = limit_label(len(shown), len(matches))
                for t, label in shown:
                    chk = ft.Checkbox(label=label, value=t.id in left_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if t.id in left_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if t.id in left_sel else TEXT_DIM), data=t.id, on_change=lambda e, _tid=t.id: (left_sel.add(_tid) if e.control.value else left_sel.discard(_tid)))
                    left_lv.controls.append(chk)
            try: left_lv.update(); left_limit.update()
            except: pass

        def _rebuild_right(q: str = ""):
            ql = (q or "").strip().lower()
            right_lv.controls.clear()
            crit = remove_criterion["value"]
            if crit == "artist":
                artists = sorted(
                    (a for a in set(t.artist for t in div_tracks if t.artist)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = artists[:DIALOG_OPTION_LIMIT]
                right_limit.value = limit_label(len(shown), len(artists))
                for a in shown:
                    cnt = sum(1 for t in div_tracks if t.artist == a)
                    chk = ft.Checkbox(label=f"{a} ({cnt})", value=a in right_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in right_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in right_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (right_sel.add(_a) if e.control.value else right_sel.discard(_a)))
                    right_lv.controls.append(chk)
            elif crit == "album":
                albums = sorted(
                    (a for a in set(t.album for t in div_tracks if t.album)
                     if not ql or ql in a.lower()),
                    key=lambda x: x.lower(),
                )
                shown = albums[:DIALOG_OPTION_LIMIT]
                right_limit.value = limit_label(len(shown), len(albums))
                for a in shown:
                    cnt = sum(1 for t in div_tracks if t.album == a)
                    chk = ft.Checkbox(label=f"{a} ({cnt})", value=a in right_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if a in right_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if a in right_sel else TEXT_DIM), data=a, on_change=lambda e, _a=a: (right_sel.add(_a) if e.control.value else right_sel.discard(_a)))
                    right_lv.controls.append(chk)
            else:
                matches = []
                for t in div_tracks:
                    label = f"{t.name[:24]} — {t.artist[:16]}"
                    if not ql or ql in label.lower():
                        matches.append((t, label))
                shown = matches[:DIALOG_OPTION_LIMIT]
                right_limit.value = limit_label(len(shown), len(matches))
                for t, label in shown:
                    chk = ft.Checkbox(label=label, value=t.id in right_sel, fill_color={ft.ControlState.SELECTED: ACCENT}, check_color=TEXT_PRIMARY, label_style=ft.TextStyle(size=10, color=TEXT_PRIMARY if t.id in right_sel else TEXT_MUTED), border_side=ft.BorderSide(1.0, ACCENT if t.id in right_sel else TEXT_DIM), data=t.id, on_change=lambda e, _tid=t.id: (right_sel.add(_tid) if e.control.value else right_sel.discard(_tid)))
                    right_lv.controls.append(chk)
            try: right_lv.update(); right_limit.update()
            except: pass

        # IconButton + ContextMenu a la IZQUIERDA de cada buscador, icono cambia según criterio.
        # Se usa el icon nativo de PopupMenuButton (sin IconButton anidado, que consumía el click).
        add_popup = ft.PopupMenuButton(
            icon=ft.Icons.MUSIC_NOTE, icon_size=16, icon_color=ACCENT, tooltip="Añadir por",
            items=[
                ft.PopupMenuItem(content=ft.Text("Artista"), icon=ft.Icons.PERSON, on_click=lambda _: _set_add_crit("artist")),
                ft.PopupMenuItem(content=ft.Text("Álbum"), icon=ft.Icons.ALBUM, on_click=lambda _: _set_add_crit("album")),
                ft.PopupMenuItem(content=ft.Text("Canción"), icon=ft.Icons.MUSIC_NOTE, on_click=lambda _: _set_add_crit("song")),
            ],
        )
        remove_popup = ft.PopupMenuButton(
            icon=ft.Icons.MUSIC_NOTE, icon_size=16, icon_color=WARNING, tooltip="Eliminar por",
            items=[
                ft.PopupMenuItem(content=ft.Text("Artista"), icon=ft.Icons.PERSON, on_click=lambda _: _set_remove_crit("artist")),
                ft.PopupMenuItem(content=ft.Text("Álbum"), icon=ft.Icons.ALBUM, on_click=lambda _: _set_remove_crit("album")),
                ft.PopupMenuItem(content=ft.Text("Canción"), icon=ft.Icons.MUSIC_NOTE, on_click=lambda _: _set_remove_crit("song")),
            ],
        )
        def _set_add_crit(v: str):
            add_criterion["value"] = v
            add_popup.icon = _icon_for(v)
            try: add_popup.update()
            except: pass
            _rebuild_left(left_search.value or "")
        def _set_remove_crit(v: str):
            remove_criterion["value"] = v
            remove_popup.icon = _icon_for(v)
            try: remove_popup.update()
            except: pass
            _rebuild_right(right_search.value or "")

        left_search.on_change = lambda e: debounce(
            "edit_division_left",
            lambda: _rebuild_left(left_search.value or ""),
        )
        right_search.on_change = lambda e: debounce(
            "edit_division_right",
            lambda: _rebuild_right(right_search.value or ""),
        )
        _rebuild_left("")
        _rebuild_right("")

        def _add_to_division(_e):
            crit = add_criterion["value"]
            if crit == "artist":
                target_ids = [t.id for t in src_tracks if t.artist in left_sel]
            elif crit == "album":
                target_ids = [t.id for t in src_tracks if t.album in left_sel]
            else:
                target_ids = list(left_sel)
            s.add_tracks_to_division(target_ids, active, notify=False)
            left_sel.clear()
            nonlocal div_tracks
            div_tracks = s.segments.get(active, [])
            _rebuild_left(left_search.value or "")
            _rebuild_right(right_search.value or "")
            self.page.update()

        def _remove_from_division(_e):
            nonlocal div_tracks
            crit = remove_criterion["value"]
            if crit == "artist":
                target_ids = [t.id for t in list(div_tracks) if t.artist in right_sel]
            elif crit == "album":
                target_ids = [t.id for t in list(div_tracks) if t.album in right_sel]
            else:
                target_ids = list(right_sel)
            s.remove_tracks_from_division(target_ids, active, notify=False)
            right_sel.clear()
            div_tracks = s.segments.get(active, [])
            _rebuild_right(right_search.value or "")
            self.page.update()

        def _apply_edit(_e):
            s.apply_search(s.search_query)
            self._close_dlg(dlg_ref.get("dlg"))

        def _close_edit(_e):
            # Cerrar conserva los cambios, igual que el flujo anterior.
            s.apply_search(s.search_query)
            self._close_dlg(dlg_ref.get("dlg"))

        def _revert_edit(_e):
            s.segments[active] = list(_snapshot)
            s.apply_search(s.search_query)
            try:
                nonlocal div_tracks
                div_tracks = s.segments.get(active, [])
                _rebuild_left(left_search.value or "")
                _rebuild_right(right_search.value or "")
                self.page.update()
            except: pass

        dlg_ref: dict = {}
        dlg = app_dialog(
            f"Editar división — {active}",
            ft.Container(
                content=ft.Column([
                    name_field,
                    ft.Row([
                        ft.Column([ft.Row([add_popup, left_search], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER), left_limit, ft.Container(content=left_lv, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, height=lv_h + 12, width=390), ft.TextButton("Agregar →", icon=ft.Icons.ARROW_FORWARD, on_click=_add_to_division, style=ft.ButtonStyle(color=ACCENT))], spacing=6, width=390),
                        ft.Column([ft.Row([remove_popup, right_search], spacing=4, vertical_alignment=ft.CrossAxisAlignment.CENTER), right_limit, ft.Container(content=right_lv, bgcolor=CHIP_BG, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=8, height=lv_h + 12, width=390), ft.TextButton("← Quitar", icon=ft.Icons.ARROW_BACK, on_click=_remove_from_division, style=ft.ButtonStyle(color=WARNING))], spacing=6, width=390),
                    ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.START),
                ], tight=True, spacing=8),
                width=820,
            ),
            [
                dialog_action("Cerrar", _close_edit, kind="muted"),
                dialog_action("Revertir", _revert_edit, kind="muted"),
                dialog_action("Aplicar", _apply_edit, kind="primary"),
            ],
            width=820, bgcolor=BG_SURFACE, radius=10,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)

    # ── BUILD CONTENT ──────────────────────────────────────────────────

    def _build_content(self) -> None:
        self._playlist_title = ft.Text(
            "Cargar una playlist", size=22,
            color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD, opacity=1.0,
        )
        self._track_count = ft.Text("", size=12, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)
        self._search_field = app_text_field(
            hint_text="Buscar título, artista…",
            prefix_icon=ft.Icons.SEARCH,
            width=240, height=38,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            on_change=self._on_search_change,
        )
        self._segment_dd = ft.Dropdown(
            width=160, height=38,
            bgcolor=BG_INPUT, border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            on_select=lambda e: self.state.set_active_segment(e.control.value),
            visible=False,
            hint_text="Segmento..."
        )

        _ib_style = dict(icon_size=17, style=ft.ButtonStyle(
            padding=4, bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT}))
        self._auth_yt = ft.IconButton(icon=ft.Icons.VIDEO_LIBRARY_OUTLINED, icon_color=TEXT_DIM,
                                      tooltip="YouTube Music · clic = validar sesión ahora",
                                      on_click=lambda _: asyncio.create_task(self._on_auth_probe("YouTube Music")),
                                      **_ib_style)
        self._auth_am = ft.IconButton(icon=ft.Icons.APPLE, icon_color=TEXT_DIM,
                                      tooltip="Apple Music · clic = validar sesión ahora",
                                      on_click=lambda _: asyncio.create_task(self._on_auth_probe("Apple Music")),
                                      **_ib_style)
        self._auth_sp = ft.IconButton(icon=ft.Icons.MUSIC_NOTE, icon_color=TEXT_DIM,
                                      tooltip="Spotify · clic = validar sesión ahora",
                                      on_click=lambda _: asyncio.create_task(self._on_auth_probe("Spotify")),
                                      **_ib_style)
        self._auth_strip = ft.Row(controls=[self._auth_yt, self._auth_am, self._auth_sp],
                                  spacing=2, vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self._select_all_chk = ft.Checkbox(
            label="Todo",
            label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family=FONT_HEADLINE),
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=lambda _: self.state.toggle_select_all(),
        )

        self._content_progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
        self._content_prog_label   = ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=0.6)
        self._content_eta_label    = ft.Text("", size=10, color=TEXT_DIM,   font_family=mono_family("light"), opacity=0.45)
        self._content_progress = ft.Container(
            content=ft.Column([
                self._content_progress_bar,
                ft.Row([self._content_prog_label, ft.Container(expand=True), self._content_eta_label], spacing=0),
            ], spacing=4),
            visible=False,
            border=ft.Border.all(0.8, BORDER_LIGHT), border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

        self._clear_session_btn = ft.IconButton(
            icon=ft.Icons.DELETE_SWEEP, icon_color=TEXT_DIM, icon_size=17,
            tooltip="Limpiar playlist cargada",
            on_click=self._on_clear_session,
            style=ft.ButtonStyle(padding=4, bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT}),
        )

        # header sin selector de partición (movido junto a Vista §)
        header_bar = ft.Row(controls=[
            ft.Column([self._playlist_title, self._track_count], spacing=2),
            ft.Container(expand=True),
            self._auth_strip, self._search_field, self._select_all_chk,
            self._clear_session_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def _col_header(text, width=None, expand=False, center=False):
            align = ft.Alignment.CENTER if center else ft.Alignment.CENTER_LEFT
            ctrl  = ft.Text(text, size=9, color=TEXT_DIM, font_family=FONT_HEADLINE_BOLD,
                            style=ft.TextStyle(letter_spacing=0.8),
                            text_align=ft.TextAlign.CENTER if center else ft.TextAlign.LEFT, opacity=1.0)
            return ft.Container(content=ctrl, width=width, expand=expand, alignment=align)

        col_headers = ft.Container(
            content=ft.Row(controls=[
                _col_header("#",               width=32, center=True),
                _col_header("PORTADA",         width=55, center=True),
                _col_header("TÍTULO / ARTISTA", expand=3),
                _col_header("ÁLBUM",           expand=2),
                _col_header("DUR.",            width=48, center=True),
                _col_header("",                width=26, center=True),
                _col_header("SEL.",            width=32, center=True),
            ], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor=CHIP_BG, border_radius=12,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

        self._list_view = ft.ListView(item_extent=ITEM_H, spacing=0, expand=True,
                                      padding=ft.Padding.only(bottom=20))
        _sf = dict(left=0, top=0, right=0, bottom=0)
        self._list_view_wrap     = ft.Container(content=self._list_view, bgcolor=BG_LIST, visible=False, **_sf)
        # skeleton count responsive (se recalcula en _calc_skeleton_count; inicial 14)
        self._skeletons          = [SkeletonRow(i) for i in range(self._calc_skeleton_count() if hasattr(self, '_calc_skeleton_count') else self.SKELETON_COUNT)]
        self._skeleton_view      = ft.ListView(item_extent=ITEM_H, spacing=0, expand=True,
                                               controls=self._skeletons, visible=True)
        self._skeleton_view_wrap = ft.Container(content=self._skeleton_view, bgcolor=BG_LIST, visible=False, **_sf)

        self._empty_hint_text = ft.Text(
            "Introduce el ID o la URL en el panel izquierdo y pulsa «Cargar».",
            size=12, color=TEXT_DIM, font_family=FONT_TEXT, opacity=1.0, text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(controls=[
                ft.Container(content=ft.Icon(ft.Icons.LIBRARY_MUSIC, size=52, color=TEXT_DIM),
                             bgcolor=CHIP_BG, border=ft.Border.all(0.8, BORDER_LIGHT),
                             border_radius=20, padding=ft.Padding.all(20)),
                ft.Text("Carga una playlist", size=20, color=TEXT_PRIMARY, font_family=FONT_HEADLINE_BOLD,
                        opacity=1.0),
                ft.Text("Sin playlist cargada", size=14, color=TEXT_MUTED, font_family=FONT_HEADLINE,
                        opacity=1.0),
                self._empty_hint_text,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=True, **_sf,
        )

        self._error_text  = ft.Text("", size=13, color=ERROR_COL, font_family=FONT_TEXT, opacity=1.0)
        self._error_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column([ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ERROR_COL), self._error_text],
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=False, **_sf,
        )

        # Preview readonly (derecha del doble)
        self._preview_list_view = ft.ListView(item_extent=ITEM_H, spacing=0, expand=True,
                                              padding=ft.Padding.only(bottom=20))
        self._preview_count = ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)
        self._preview_empty = self._make_cono_empty("Preview vacío", "Marca canciones y organiza para ver cómo se va a pasar.", visible=False)
        self._preview_empty_wrap = ft.Container(content=self._preview_empty, bgcolor=BG_LIST, visible=False, **_sf)
        self._preview_view_wrap = ft.Container(content=self._preview_list_view, bgcolor=BG_LIST, visible=False, **_sf)
        # skeleton preview espejo (mismo cnt, sin Estado/Sel)
        _init_cnt = len(self._skeletons) if hasattr(self, '_skeletons') and self._skeletons else self._calc_skeleton_count()
        self._preview_skeletons = [PreviewSkeletonRow(i) for i in range(_init_cnt)]
        self._preview_skeleton_view = ft.ListView(item_extent=ITEM_H, spacing=0, expand=True, controls=self._preview_skeletons, visible=True)
        self._preview_skeleton_wrap = ft.Container(content=self._preview_skeleton_view, bgcolor=BG_LIST, visible=False, **_sf)
        self._preview_stack = ft.Stack(controls=[self._preview_view_wrap, self._preview_skeleton_wrap, self._preview_empty_wrap], expand=True)
        self._preview_header_text = ft.Text("Preview — cómo se va a pasar", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD)
        self._preview_header_icon = ft.Icon(ft.Icons.VISIBILITY_OUTLINED, size=14, color=TEXT_MUTED)
        self._preview_panel = ft.Container(
            expand=True, bgcolor=BG_LIST, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column([
                ft.Row([
                    self._preview_header_icon,
                    self._preview_header_text,
                    ft.Container(expand=True),
                    self._preview_count,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                self._preview_stack,
            ], spacing=8, expand=True),
            padding=ft.Padding.all(10), visible=True,
        )

        # Lista panel (izq) — SOURCE (base) cuando dual, RESULT cuando single
        list_stack = ft.Stack(controls=[
            self._list_view_wrap, self._skeleton_view_wrap, self._empty_state, self._error_state,
        ], expand=True)
        self._lista_header_text = ft.Text("Lista editable", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD)
        self._lista_header_icon = ft.Icon(ft.Icons.LIST_ALT, size=14, color=TEXT_MUTED)
        self._lista_panel = ft.Container(
            expand=True, bgcolor=BG_LIST, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column([
                ft.Row([
                    self._lista_header_icon,
                    self._lista_header_text,
                ], spacing=6),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                list_stack,
            ], spacing=8, expand=True),
            padding=ft.Padding.all(10), visible=True,
        )

        # Modo Lista|Doble|Preview (SegmentedButton principal, solo visible cuando hay dual)
        def _on_mode_change(e):
            try:
                v = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                v = getattr(e.control, "value", "lista")
            self.state.set_dual_mode(v)

        self._mode_seg = self._make_mode_segmented(getattr(self.state, "dual_mode", "lista"), on_change=_on_mode_change)
        # División activa — Outlined + PopupMenu dinámico 2..5 junto a Vista (reemplaza Dropdown)
        self._division_btn_text = ft.Text("División 1", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE)
        self._division_label = ft.Text("División", size=9, color=TEXT_DIM, font_family=FONT_HEADLINE_BOLD)
        self._division_popup = ft.PopupMenuButton(
            content=ft.Row([
                ft.Icon(ft.Icons.FILTER_LIST, size=14, color=TEXT_DIM),
                self._division_btn_text,
                ft.Icon(ft.Icons.ARROW_DROP_DOWN, size=16, color=TEXT_DIM),
            ], spacing=4, tight=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            items=[],
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                side={ft.ControlState.DEFAULT: ft.BorderSide(0.7, BORDER_LIGHT)},
                shape=ft.RoundedRectangleBorder(radius=8),
                bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT},
            ),
            tooltip="División activa — cambia preview",
            visible=False,
        )
        self._division_col = ft.Column([self._division_label, self._division_popup], spacing=0, tight=True)
        # Compat: dropdown antiguo eliminado, botón Todos oculto (dropdown cumple papel)
        self._partition_dd = None
        self._partition_btn = ft.OutlinedButton(
            content=ft.Text("Todos", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE),
            icon=ft.Icons.FILTER_LIST,
            on_click=lambda _: self._open_partition_picker(),
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                side={ft.ControlState.DEFAULT: ft.BorderSide(0.7, BORDER_LIGHT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
            visible=False,
            tooltip="Seleccionar divisiones para transferir — multi §7",
        )
        self._edit_division_btn = ft.OutlinedButton(
            content=ft.Text("Editar", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE),
            icon=ft.Icons.EDIT_NOTE,
            on_click=lambda _: self._open_edit_division_dialog(),
            style=ft.ButtonStyle(
                padding=ft.Padding.symmetric(horizontal=10, vertical=6),
                side={ft.ControlState.DEFAULT: ft.BorderSide(0.7, BORDER_LIGHT)},
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
            visible=False,
            tooltip="Editar división — quitar/agregar entre ORIGINAL y División activa §4 (animación nativa)",
        )
        self._mode_bar = ft.Row([
            ft.Text("Vista:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
            self._mode_seg,
            ft.VerticalDivider(width=1, color=BORDER_MUTED),
            ft.Icon(ft.Icons.CALL_SPLIT, size=14, color=TEXT_DIM),
            self._division_col,
            self._edit_division_btn,
            ft.Container(expand=True),
            ft.IconButton(icon=ft.Icons.CLOSE, icon_size=14, icon_color=TEXT_DIM, tooltip="Cerrar doble vista",
                          on_click=lambda _: self.state.set_show_dual(False),
                          style=ft.ButtonStyle(padding=4, bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT})),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False)

        # Doble fila — Row persistente con ambos paneles (cookbook: evita reparent, preserva virtualización ListView)
        # _lista_panel y _preview_panel viven siempre dentro de _dual_row; se controla via visible, no moviendo hijos.
        self._dual_row = ft.Row(
            controls=[self._lista_panel, self._preview_panel],
            spacing=10, expand=True, visible=True,
        )
        # estado inicial: solo lista visible (show_dual=False)
        self._preview_panel.visible = False
        self._lista_panel.visible = True
        # list_area siempre contiene _dual_row (estable, animación cookbook)
        self._list_area = ft.Container(
            content=self._dual_row, expand=True,
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_scale=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            opacity=1.0, scale=1.0,
        )
        list_area = self._list_area

        self._content = ft.Container(
            expand=True, bgcolor=BG_LIST, padding=ft.Padding.all(24),
            content=ft.Column(controls=[self._content_progress, header_bar, col_headers, self._mode_bar, list_area],
                              spacing=10, expand=True),
        )


    # ── STATE REACTIONS ────────────────────────────────────────────────

    def _schedule_state_render(self) -> None:
        """Coalesce ráfagas de notify() en un solo render por tick."""
        if self._render_scheduled:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Notify desde un hilo sin loop: conserva el comportamiento previo.
            self._on_state_changed()
            return
        self._render_scheduled = True
        loop.call_soon(self._flush_state_render)

    def _flush_state_render(self) -> None:
        self._render_scheduled = False
        self._on_state_changed()

    def _on_state_changed(self) -> None:
        s = self.state
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
        dest_needs_confirm = is_local_src and not s.destination_confirmed
        new_dst_border = WARNING if dest_needs_confirm else BORDER_LIGHT
        new_dst_focused = WARNING if dest_needs_confirm else ACCENT
        if getattr(self._dst_dd, "_last_border_key", None) != (new_dst_border, new_dst_focused):
            self._dst_dd.border = input_border(new_dst_border, new_dst_focused)
            self._dst_dd._last_border_key = (new_dst_border, new_dst_focused)
            self._dst_dd.update()

    def _sync_titles(self, s) -> None:
        self._playlist_title.value = s.playlist_name
        n = len(s.display_tracks)
        total = len(s.tracks)
        self._track_count.value = (
            f"{n} canciones" if not s.search_query else f"{n} de {total} coincidencias"
        )

    def _sync_auth_icons(self, s) -> bool:
        for plat, ic in (("YouTube Music", self._auth_yt), ("Apple Music", self._auth_am), ("Spotify", self._auth_sp)):
            ok = s.auth_session_ok.get(plat, True)
            ic.icon_color = SUCCESS if ok else ERROR_COL
            hint = s.auth_session_hint.get(plat) or ""
            base = f"{plat}: clic para revalidar ahora"
            ic.tooltip = f"{base} \u00b7 {hint}" if hint else f"{base} \u00b7 {'OK' if ok else 'fallo'}"
        if s.destination == EXPORT_DEST_LABEL:
            dest_ok = True
            self._dest_session_warn.visible = False
            self._dest_session_warn.value = ""
        else:
            dest_ok = s.auth_session_ok.get(s.destination, True)
            self._dest_session_warn.visible = not dest_ok
            self._dest_session_warn.value = "" if dest_ok else f"No se confirmó la sesión en {s.destination}"
        has_transform = bool(s.segments or getattr(s, 'show_dual', False) or (getattr(s, 'source_tracks', None) and s.tracks != s.source_tracks))
        if s.source == s.destination and not has_transform:
            self._status_badge.value = "\u26a0 Origen y destino iguales"
            self._status_badge.color = WARNING
        elif s.source == s.destination and has_transform:
            self._status_badge.value = f"\u2713 {s.source} \u2192 {s.destination}"
            self._status_badge.color = SUCCESS
        elif s.destination == EXPORT_DEST_LABEL:
            self._status_badge.value = f"\u2713 {s.source} \u2192 Exportar local"
            self._status_badge.color = SUCCESS
        else:
            self._status_badge.value = f"\u2713 {s.source} \u2192 {s.destination}"
            self._status_badge.color = SUCCESS
        self._select_all_chk.value = s.select_all
        # actualiza label del botón transferir/exportar según destino
        try:
            is_export = s.destination == EXPORT_DEST_LABEL
            label = "Exportar" if is_export else "Transferir"
            icon = ft.Icons.SAVE_ALT if is_export else ft.Icons.SWAP_HORIZ
            if self._transfer_btn.content.value != label:  # type: ignore[attr-defined]
                self._transfer_btn.content.value = label  # type: ignore[attr-defined]
            if self._transfer_btn.icon != icon:
                self._transfer_btn.icon = icon
        except Exception:
            pass
        return dest_ok

    def _sync_list_state(self, s, is_loading: bool, is_ready: bool, is_error: bool, is_idle: bool) -> None:
        self._empty_state.visible = is_idle
        self._skeleton_view_wrap.visible = is_loading
        self._list_view_wrap.visible = is_ready and not is_error
        self._error_state.visible = is_error
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
        is_export = s.destination == EXPORT_DEST_LABEL
        self._load_btn.disabled = net_blocked or is_loading
        if is_export:
            # export local no depende de red/auth
            self._transfer_btn.disabled = is_transferring or not is_ready or rule4_blocked
        else:
            self._transfer_btn.disabled = net_blocked or is_transferring or not is_ready or not dest_ok or rule4_blocked
        self._clear_session_btn.disabled = is_loading or is_transferring
        self._id_clear_btn.visible = bool(self._id_field.value)
        if is_export:
            self._transfer_btn.tooltip = "Exportar selección a archivo (TXT/CSV/M3U/XSPF)"
        elif rule4_blocked:
            self._transfer_btn.tooltip = "\u26a0 Elige un destino antes de transferir"
        elif not dest_ok:
            self._transfer_btn.tooltip = f"Sesi\u00f3n expirada en {s.destination}"
        else:
            self._transfer_btn.tooltip = "Transferir selecci\u00f3n al destino"


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
            for i, track in enumerate(tracks, 1):
                lv.controls.append(
                    SongRow(
                        track, i, self.state.toggle_track,
                        on_move=self.state.move_track_to_position,
                        on_download=self._on_row_download,
                        download_busy=download_busy,
                        download_done=(track.id in done_by_id),
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

    def _on_row_download(self, track_id: str) -> None:
        if self.state.download_state == TransferState.RUNNING:
            self._snack("Descarga en curso", error=True)
            return
        target = next((t for t in self.state.tracks if t.id == track_id), None)
        if target is None:
            target = next((t for t in self.state.source_tracks if t.id == track_id), None)
        if target is None:
            return
        from services import settings_store
        self._auto_play_download_ids.add(target.id)
        try:
            self.state.download_tracks(
                [target],
                destination_dir=settings_store.get_download_dir(),
            )
            self._snack(f"Descargando: {target.artist} — {target.name}")
        except Exception as exc:  # noqa: BLE001
            self._auto_play_download_ids.discard(target.id)
            self._snack(f"No se pudo iniciar la descarga: {exc}", error=True)


    # ── EVENT HANDLERS ─────────────────────────────────────────────────

    def _on_load(self, _) -> None:
        src = self.state.source
        if src in AppState.LOCAL_SOURCES and not self.state.destination_confirmed:
            self._snack("⚠ Selecciona primero una plataforma de Destino", error=True)
            self._dst_dd.border = input_border(WARNING, WARNING)
            self._dst_dd._last_border_key = (WARNING, WARNING)
            self._dst_dd.update()
            return
        if src == "Archivo Local":
            asyncio.create_task(self._do_local_pick())
        elif src == "Pegar Texto":
            self._open_paste_dialog()
        else:
            asyncio.create_task(self._do_cloud_load())

    async def _do_cloud_load(self, _=None) -> None:
        pid = self._id_field.value.strip()
        if not pid:
            self._snack("Introduce un ID o una URL de playlist")
            return
        self._completion_snack_shown = False
        await self.state.load_playlist(pid)

    def _on_clear_id(self, _) -> None:
        """Limpia solo el campo de ID pegado."""
        self._id_field.value = ""
        self._id_clear_btn.visible = False
        self._id_field.update()

    def _on_clear_session(self, _) -> None:
        """Limpia el ID, la lista cargada y el estado de transferencia."""
        self._id_field.value = ""
        self._id_clear_btn.visible = False
        self._search_field.value = ""
        self.state.reset_session()
        self._snack("Sesión limpiada")

    def _open_folder_in_gestion(self, name: str, tracks: list) -> None:
        """Carga una carpeta local de la Biblioteca en Gestión como playlist.

        Deja el origen en «Archivo Local» para que Transferir pida destino en
        vez de tratar la lista como una playlist remota, y alinea el selector
        de origen: ``AppState.source`` no se sincroniza solo con el widget.
        """
        if not tracks:
            self._snack("La carpeta no tiene pistas", error=True)
            return
        self._completion_snack_shown = False
        self._pm_cleared_for_load = True
        self._telemetry.clear_postmortem()
        self.state.load_local_tracks(list(tracks), playlist_name=name or "Playlist Local")
        self.state.set_source("Archivo Local")
        self._src_dd.value = "Archivo Local"
        self._select_module(0)
        self._snack(f"{len(tracks)} pistas de «{name}» cargadas en Gestión")

    def _open_paste_dialog(self) -> None:
        self._paste_field.value = ""

        def _on_order_change(e):
            # SegmentedButton -> set, Dropdown -> value
            try:
                val = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_parse_order = val

        order_seg = self._make_order_segmented(self.state.local_parse_order, on_change=_on_order_change)

        def _close_paste():
            self._close_dlg(paste_dlg)

        def _process(_):
            text = self._paste_field.value or ""
            _close_paste()
            if not text.strip():
                self._snack("El campo de texto está vacío", error=True)
                return
            import datetime as _dt
            default_ts = _dt.datetime.now().strftime("%H:%M")
            self._ask_playlist_name_then_ingest(text=text, filename="",
                                                suggested_name=f"Local_Import_{default_ts}")

        paste_dlg = app_dialog(
            "Pegar Texto",
            ft.Column([
                ft.Container(content=self._paste_field, width=480, height=220),
                ft.Row([
                    ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                    order_seg
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Elige cómo está escrito cada línea (TuneMyMusic usa Artista - Título).",
                        size=9, color=TEXT_DIM, font_family=FONT_HEADLINE),
            ], spacing=10, tight=True),
            [
                dialog_action("Procesar", _process, kind="primary", icon=ft.Icons.PLAY_ARROW_OUTLINED),
                dialog_action("Cancelar", lambda _: _close_paste(), kind="muted"),
            ],
            width=520,
        )
        self.page.show_dialog(paste_dlg)

    def _ask_playlist_name_then_ingest(self, text: str, filename: str, suggested_name: str) -> None:
        import datetime as _dt
        name_field = app_text_field(
            value=suggested_name, hint_text=f"Ej. {suggested_name}",
            label="Nombre de la Playlist",
            autofocus=True, on_submit=lambda _: _confirm(None),
        )

        def _on_order_change2(e):
            try:
                val = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_parse_order = val

        order_seg2 = self._make_order_segmented(self.state.local_parse_order, on_change=_on_order_change2)

        def _close():
            self._close_dlg(name_dlg)

        def _confirm(_):
            raw        = (name_field.value or "").strip()
            final_name = raw if raw else (suggested_name or f"Local_Import_{_dt.datetime.now().strftime('%H:%M')}")
            _close()
            self._ingest_text(text, label=final_name, filename=filename)

        name_dlg = app_dialog(
            "Nombra esta playlist",
            ft.Column([
                ft.Text("Asigna un nombre antes de importar. Si lo dejas vacío se usará el nombre sugerido.",
                        size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                name_field,
                ft.Row([
                    ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                    order_seg2
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Confirma el orden de cada línea antes de importar.",
                        size=9, color=TEXT_DIM, font_family=FONT_HEADLINE),
            ], spacing=10, tight=True),
            [
                dialog_action("Importar", _confirm, kind="primary", icon=ft.Icons.CHECK_OUTLINED),
                dialog_action("Cancelar", lambda _: _close(), kind="muted"),
            ],
            width=460,
            icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE,
        )
        self.page.show_dialog(name_dlg)

    async def _do_local_pick(self) -> None:
        files = await self._file_picker.pick_files(
            dialog_title="Seleccionar playlist",
            allowed_extensions=[
                "txt", "csv", "m3u", "m3u8", "pls", "wpl", "xspf", "xml",
                "mp3", "flac", "aac", "ogg", "wav", "m4a", "wma", "opus", "aiff", "aif",
            ],
        )
        if not files:
            return
        f = files[0]
        if os.path.splitext(f.name)[1].lower() in {
            ".mp3", ".flac", ".aac", ".ogg", ".wav", ".m4a", ".wma", ".opus", ".aiff", ".aif",
        }:
            self._ingest_audio_file(f.path, os.path.splitext(os.path.basename(f.name))[0])
            return
        try:
            with open(f.path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError as exc:
            self.state.log(f"[ERROR] No se pudo leer '{f.name}': {exc}")
            self._snack(f"Error leyendo archivo: {exc}", error=True)
            return
        base_name = os.path.splitext(os.path.basename(f.name))[0] or "Playlist Local"
        self._ask_playlist_name_then_ingest(text=text, filename=f.path, suggested_name=base_name)

    def _ingest_text(self, text: str, label: str = "", filename: str = "") -> None:
        try:
            pairs = parse_local_playlist_with_paths(text, filename=filename or label, order=self.state.local_parse_order)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.state.log(f"[ERROR] Parser ingesta: {exc}")
            self._snack(f"Error en el parser: {exc}", error=True)
            return
        if not pairs:
            self.state.log(f"[WARN] No se encontraron pistas en '{label or 'texto'}'")
            self._snack("No se reconocieron pistas en el archivo", error=True)
            return
        tracks = build_local_tracks(pairs)
        name   = label.strip() if label and label.strip() else "Playlist Local"
        self._completion_snack_shown = False
        self._pm_cleared_for_load    = True
        self._telemetry.clear_postmortem()
        self.state.load_local_tracks(tracks, playlist_name=name)
        self._snack(f"{len(tracks)} canciones importadas de '{name}'")
        self.state.log(f"[INFO] Ingesta completa · {len(tracks)} pistas desde '{label}'")

    def _ingest_audio_file(self, path: str, label: str) -> None:
        """Importa un archivo de audio leyendo sus tags con Mutagen."""
        try:
            tracks = build_local_tracks([("", os.path.basename(path), path)])
        except Exception as exc:  # Mutagen no debe tumbar la UI
            self.state.log(f"[ERROR] Metadatos de audio: {exc}")
            self._snack(f"Error leyendo metadatos: {exc}", error=True)
            return
        if not tracks:
            self._snack("No se pudieron leer los metadatos del audio", error=True)
            return
        name = label.strip() or "Archivo de audio"
        self._completion_snack_shown = False
        self._pm_cleared_for_load = True
        self._telemetry.clear_postmortem()
        self.state.load_local_tracks(tracks, playlist_name=name)
        self._snack(f"Audio importado: '{name}'")
        self.state.log(f"[INFO] Audio local importado · {path}")

    async def _on_transfer(self, _) -> None:
        # Ruta export local como destino — no va por MusicApiService
        if self.state.destination == EXPORT_DEST_LABEL:
            await self._open_export_dialog()
            return
        # Misma plataforma permitida si hay Organizar/Dividir (has_transform) §11 — antes bloqueado
        has_transform = bool(self.state.segments or getattr(self.state, 'show_dual', False) or (getattr(self.state, 'source_tracks', None) and self.state.tracks != self.state.source_tracks))
        if self.state.source == self.state.destination and not has_transform:
            self._snack("Origen y destino no pueden ser iguales (solo permitido tras Organizar/Dividir)", error=True)
            return
        if self.state.source in AppState.LOCAL_SOURCES and not self.state.destination_confirmed:
            self._snack("⚠ Selecciona una plataforma de destino antes de transferir", error=True)
            self._dst_dd.border = input_border(WARNING, WARNING)
            self._dst_dd._last_border_key = (WARNING, WARNING)
            self._dst_dd.update()
            return
        # División: si hay divisiones, se transfiere por división; si no, selección global
        has_divisions = bool(getattr(self.state, 'segments', {}))
        if has_divisions:
            # El flujo por divisiones crea varias playlists y no genera un
            # reporte único; evita que un reporte anterior se reabra.
            self._report_dialog_shown = True
            # para divisiones, cuenta seleccionadas para transferir = divisiones seleccionadas
            sel_divs = self.state.active_segment_keys if self.state.active_segment_keys is not None else set(self.state.segments.keys())
            if not sel_divs:
                self._snack("Selecciona al menos una división para transferir", error=True)
                return
        else:
            if self.state.selected_count == 0:
                self._snack("Selecciona al menos una canción", error=True)
                return
        from ui.playlist_meta_dialog import PlaylistMetaDialog
        src_requires_auth = self.state.source not in AppState.LOCAL_SOURCES and self.state.auth_session_ok.get(self.state.source, True)
        divisions_for_dialog = list(sel_divs) if has_divisions else None
        meta = await PlaylistMetaDialog(
            self.page,
            default_title=self.state.playlist_name,
            default_description=self.state.playlist_description,
            source_requires_auth=src_requires_auth,
            divisions=divisions_for_dialog,
            global_destination=self.state.destination,
        ).show()
        if not meta.confirmed:
            return
        if not meta.title.strip():
            self._snack("El nombre de la playlist no puede estar vacío", error=True)
            return
        # Flujo Dividir (múltiples playlists) vs Organizar/single §10-12 — per-división destino variable solo Dividir
        if has_divisions:
            sel_divs = self.state.active_segment_keys if self.state.active_segment_keys is not None else set(self.state.segments.keys())
            per_div_dests = getattr(meta, 'per_division_destinations', None) or {}
            successes = 0
            for div in sorted(sel_divs, key=lambda k: list(self.state.segments.keys()).index(k) if k in self.state.segments else 9999):
                div_tracks = self.state.segments.get(div, [])
                div_selected = [t for t in div_tracks if t.selected] if any(t.selected for t in div_tracks) else div_tracks
                if not div_selected:
                    continue
                dest = per_div_dests.get(div, "Mantener")
                if dest == "Mantener" or not dest:
                    dest = self.state.destination
                try:
                    ok, msg, confirmed, rejected = await self.state.service.create_playlist(
                        dest, f"{meta.title.strip()} — {div}", [t.id for t in div_selected if t.id], meta.description
                    )
                    if ok:
                        successes += 1
                        self.state.log(f"[SUCCESS] División '{div}' → {dest}: {confirmed} canciones")
                    else:
                        self.state.log(f"[ERROR] División '{div}' fallo: {msg}")
                except Exception as exc:
                    self.state.log(f"[ERROR] División '{div}' excepción: {exc}")
            # mensaje sin referencia a punto cumplido
            self._snack(f"{successes}/{len(sel_divs)} divisiones transferidas")
            if per_div_dests:
                self.state.division_destinations.update(per_div_dests)
        else:
            await self.state.transfer_playlist(
                title_override=meta.title,
                description_override=meta.description,
            )
        # Reemplazar/eliminar original si marcado (§11)
        if meta.delete_original and self.state.playlist_id and self.state.source not in AppState.LOCAL_SOURCES:
            try:
                ok, msg = await self.state.service.delete_playlist(self.state.source, self.state.playlist_id)
                if ok:
                    self._snack(f"Original eliminada ({self.state.source})")
                    self.state.log(f"[INFO] Original {self.state.playlist_id} eliminada tras reemplazo")
                else:
                    self._snack(f"No se pudo eliminar original: {msg}", error=True)
            except Exception as exc:
                self._snack(f"Error eliminando original: {exc}", error=True)

    async def _open_export_dialog(self) -> None:
        """Diálogo de export local: formato + orden + ruta (pathlib + FilePicker)."""
        if not self.state.tracks:
            self._snack("No hay playlist cargada para exportar", error=True)
            return
        # Tracks a exportar: seleccionadas si las hay, si no display
        src_tracks = [t for t in self.state.tracks if t.selected]
        if not src_tracks:
            src_tracks = list(self.state.display_tracks) if hasattr(self.state, "display_tracks") else list(self.state.tracks)
        if not src_tracks:
            self._snack("No hay canciones para exportar", error=True)
            return

        # Estado local export (persiste en AppState)
        fmt_val = getattr(self.state, "local_export_format", "txt")
        order_val = getattr(self.state, "local_export_order", "artist-title")

        def _on_fmt_change(e):
            self.state.local_export_format = e.control.value
        def _on_order_change_exp(e):
            try:
                val = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                val = getattr(e.control, "value", "artist-title")
            if val in ("artist-title", "title-artist"):
                self.state.local_export_order = val

        fmt_dd = ft.Dropdown(
            label="Formato", value=fmt_val,
            width=160, height=38,
            bgcolor=BG_INPUT, border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            options=[ft.dropdown.Option(k, k.upper()) for k in EXPORT_FORMATS],
            on_select=_on_fmt_change,
        )
        order_seg = self._make_order_segmented(order_val, on_change=_on_order_change_exp)

        default_path = str(default_export_path(self.state.playlist_name, fmt_val))  # type: ignore
        path_field = app_text_field(
            value=default_path, label="Ruta destino", hint_text=str(default_path),
            prefix_icon=ft.Icons.FOLDER_OUTLINED, expand=True,
        )

        async def _pick_save(_):
            # usa FilePicker.save_file (Flet 0.86) con fallback a pick
            try:
                res = await self._save_picker.save_file(
                    dialog_title="Guardar playlist",
                    file_name=path_field.value.split("/")[-1].split("\\")[-1] or "playlist.txt",
                    allowed_extensions=list(EXPORT_FORMATS),
                )
                # save_file retorna FilePickerFile o None según versión
                if res and getattr(res, "path", None):
                    path_field.value = res.path
                    path_field.update()
                elif isinstance(res, str) and res:
                    path_field.value = res
                    path_field.update()
            except Exception:
                # fallback: usa path por defecto
                pass

        pick_btn = _ghost_btn("Elegir…", ft.Icons.FOLDER_OPEN, lambda e: self.page.run_task(_pick_save(e)), width=90, height=38)

        # preview count
        info_txt = ft.Text(f"{len(src_tracks)} canciones · {self.state.playlist_name[:30]}", size=10, color=TEXT_MUTED, font_family=mono_family("light"))

        # necesitamos capturar dlg en closure
        dlg_ref: dict = {}

        def _do_export(_):
            fmt = getattr(self.state, "local_export_format", "txt")
            order = getattr(self.state, "local_export_order", "artist-title")
            path = (path_field.value or "").strip() or str(default_export_path(self.state.playlist_name, fmt))  # type: ignore
            # asegura extensión coincide con fmt
            from pathlib import Path as _P
            p = _P(path).expanduser()
            if not p.suffix:
                p = _P(str(p) + f".{fmt.replace('m3u','m3u8')}")
            try:
                out = export_tracks(src_tracks, p, fmt=fmt, order=order, playlist_name=self.state.playlist_name)  # type: ignore
                self._close_dlg(dlg_ref["dlg"])
                self._snack(f"Exportado {len(src_tracks)} canciones → {out}")
                self.state.log(f"[INFO] Export local · {len(src_tracks)} → {out} ({fmt}, {order})")
            except Exception as exc:
                self._snack(f"Error al exportar: {exc}", error=True)
                self.state.log(f"[ERROR] Export falló: {exc}")

        dlg = app_dialog(
            "Exportar playlist",
            ft.Column([
                ft.Text("Guarda la selección actual con el mismo bloque que lee TXT/CSV/M3U/XSPF. Elige orden para round-trip con TuneMyMusic.",
                        size=10, color=TEXT_MUTED, font_family=FONT_TEXT),
                info_txt,
                ft.Row([fmt_dd, ft.Container(expand=True), order_seg], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([path_field, pick_btn], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ], spacing=12, tight=True),
            [
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
                dialog_action("Exportar", _do_export, kind="primary", icon=ft.Icons.SAVE_ALT),
            ],
            width=560,
            icon=ft.Icons.SAVE_OUTLINED,
        )
        dlg_ref["dlg"] = dlg
        self.page.show_dialog(dlg)

    async def _on_search_change(self, e: ft.ControlEvent) -> None:
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()
        query = e.control.value
        self._search_task = asyncio.create_task(self._do_search(query))

    async def _do_search(self, query: str) -> None:
        await asyncio.sleep(0.30)
        self.state.apply_search(query)

    # ── Skeleton pulse ─────────────────────────────────────────────────

    def _ensure_skeletons_pulsing(self) -> None:
        if self._skeleton_tasks:
            return
        for sk in self._skeletons:
            self._skeleton_tasks.append(asyncio.create_task(sk.start_pulse()))

    def _ensure_preview_skeletons_pulsing(self) -> None:
        if getattr(self, '_preview_skeleton_tasks', None) is None:
            self._preview_skeleton_tasks = []
        if self._preview_skeleton_tasks:
            return
        for sk in getattr(self, '_preview_skeletons', []):
            self._preview_skeleton_tasks.append(asyncio.create_task(sk.start_pulse()))

    def _stop_skeleton_pulse(self) -> None:
        for task in self._skeleton_tasks:
            task.cancel()
        self._skeleton_tasks.clear()
        for sk in self._skeletons:
            sk.stop_pulse()
        # preview skeletons
        if hasattr(self, '_preview_skeleton_tasks'):
            for task in self._preview_skeleton_tasks:
                task.cancel()
            self._preview_skeleton_tasks.clear()
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

    # ── Circuit breaker reactions ──────────────────────────────────────

    def _on_circuit_change(self, platform: str, is_open: bool, remaining: int) -> None:
        banner_text = self._rl_banner.content.controls[1]
        self._rl_banner.visible = is_open
        if is_open:
            banner_text.value = f"Rate limit en {platform} · {remaining}s"
            asyncio.create_task(self._countdown(platform, remaining))
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
