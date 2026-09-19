"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.x.x                             ║
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
       - State.notify() → _on_state_changed() → actualiza UI
       - Circuit breakers → _on_circuit_change() → deshabilita controles
    
    4. Gestión de Recursos:
       - Caché de filas (SongRow) para evitar recreación
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
Versión: 4.x.x
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
from ui.song_row import SongRow, SkeletonRow, ITEM_H
from ui.telemetry import TelemetryDrawer
from ui.widgets import (
    _primary_btn, _ghost_btn, _section_label, _status_icon,
    app_text_field, dialog_action, app_dialog, DialogMixin, notify,
)

from ui.tokens import (
    BG_DEEP, BG_PANEL, BG_SURFACE, BG_HOVER, BG_INPUT, SIDEBAR_BG,
    BG_LIST, CHIP_BG, BORDER_LIGHT, BORDER_MUTED,
    ACCENT, ACCENT_DIM, ACCENT_HALO,
    SUCCESS, WARNING, ERROR_COL,
    TEXT_PRIMARY, TEXT_MUTED, TEXT_DIM, SKELETON_DARK,
)


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
        _row_cache: Caché de SongRow para evitar recreación
        _skeleton_tasks: Tareas de animación de skeleton screens
    
    Flujo de Datos:
        1. Usuario interactúa con UI (click, input, etc)
        2. UI llama métodos de AppState
        3. AppState ejecuta lógica y actualiza su estado interno
        4. AppState.notify() dispara _on_state_changed()
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
                selected={current},
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="artist-title", label=ft.Text("Artista - Título", size=11)),
                    ft.Segment(value="title-artist", label=ft.Text("Título - Artista", size=11)),
                ],
                on_change=on_change,
            )
        except Exception:
            # fallback Dropdown compacto
            dd = ft.Dropdown(
                value=current,
                width=200, height=38,
                bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                options=[
                    ft.dropdown.Option("artist-title", "Artista - Título"),
                    ft.dropdown.Option("title-artist", "Título - Artista"),
                ],
                on_select=on_change,  # compat
            )
            return dd

    def _make_scope_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton Todo|Visibles|Seleccionadas — reusa tokens."""
        try:
            return ft.SegmentedButton(
                selected={current},
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="all", label=ft.Text("Todo", size=11)),
                    ft.Segment(value="visible", label=ft.Text("Visibles", size=11)),
                    ft.Segment(value="selected", label=ft.Text("Seleccionadas", size=11)),
                ],
                on_change=on_change,
            )
        except Exception:
            return ft.Dropdown(
                value=current,
                width=200, height=38,
                bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
                content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
                options=[
                    ft.dropdown.Option("all", "Todo"),
                    ft.dropdown.Option("visible", "Visibles"),
                    ft.dropdown.Option("selected", "Seleccionadas"),
                ],
                on_select=on_change,
            )

    def _make_mode_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton Lista|Doble|Preview — modo doble solo al Organizar/Dividir."""
        try:
            return ft.SegmentedButton(
                selected={current},
                allow_empty_selection=False,
                allow_multiple_selection=False,
                show_selected_icon=False,
                style=ft.ButtonStyle(
                    bgcolor={ft.ControlState.SELECTED: ACCENT, ft.ControlState.DEFAULT: BG_SURFACE},
                    color={ft.ControlState.SELECTED: TEXT_PRIMARY, ft.ControlState.DEFAULT: TEXT_MUTED},
                ),
                segments=[
                    ft.Segment(value="lista", label=ft.Text("Lista", size=11)),
                    ft.Segment(value="doble", label=ft.Text("Doble", size=11)),
                    ft.Segment(value="preview", label=ft.Text("Preview", size=11)),
                ],
                on_change=on_change,
            )
        except Exception:
            return ft.Dropdown(
                value=current,
                width=180, height=38,
                bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
                text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
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
                ft.Text(title, size=18, color=TEXT_PRIMARY, font_family="IBM Plex Sans Bold", opacity=1.0),
                ft.Text(subtitle, size=12, color=TEXT_MUTED, font_family="IBM Plex Sans", opacity=1.0, text_align=ft.TextAlign.CENTER),
                ft.Text("Función en desarrollo", size=11, color=TEXT_DIM, font_family="IBM Plex Sans", opacity=0.8),
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=visible,
            left=0, top=0, right=0, bottom=0,
        )

    def _calc_skeleton_count(self) -> int:
        """Calcula cuántos SkeletonRow caben en alto visible (responsive)."""
        try:
            h = self.page.height or self.page.window.height or 650  # type: ignore
        except Exception:
            h = 650
        # header + col_headers + progress + padding + tabs aproximado
        header_h = 70
        col_h = 36
        pad = 48
        prog = 40 if getattr(self, '_content_progress', None) and self._content_progress.visible else 0
        mode_bar = 40 if getattr(self, '_mode_bar', None) and getattr(self._mode_bar, 'visible', False) else 0
        avail = max(180, h - header_h - col_h - pad - prog - mode_bar - 120)  # sidebar no afecta listado
        cnt = max(4, min(24, int(avail // ITEM_H)))
        return cnt

    def _sync_skeleton_count(self) -> None:
        """Recrea skeletons si cambió el count según resolución (on_resize/dual)."""
        try:
            new_cnt = self._calc_skeleton_count()
            cur_cnt = len(getattr(self, '_skeletons', []))
            if new_cnt != cur_cnt:
                self._skeletons = [SkeletonRow(i) for i in range(new_cnt)]
                self._skeleton_view.controls = self._skeletons
                self._skeleton_view.update()
        except Exception:
            pass

    def _sync_dual_view(self, s) -> None:
        """Sincroniza modo Lista|Doble|Preview y visibilidad de paneles (solo tras Organizar/Dividir)."""
        is_dual = getattr(s, 'show_dual', False)
        mode = getattr(s, 'dual_mode', 'lista')
        # mode bar solo cuando dual activo
        try:
            self._mode_bar.visible = is_dual
            self._mode_bar.update()
        except Exception:
            pass
        try:
            # sync segmented selected
            if hasattr(self._mode_seg, 'selected'):
                self._mode_seg.selected = {mode}
                self._mode_seg.update()
            else:
                self._mode_seg.value = mode
                self._mode_seg.update()
        except Exception:
            pass
        # single vs dual — reparent dinámico de _lista_panel para evitar duplicar controles
        try:
            if not is_dual:
                # modo lista: lista sola
                if self._list_area.content != self._lista_panel:
                    # saca _lista_panel de _dual_row si estaba ahí
                    try:
                        if self._lista_panel in self._dual_row.controls:
                            self._dual_row.controls.remove(self._lista_panel)
                    except Exception:
                        pass
                    self._dual_row.visible = False
                    self._list_area.content = self._lista_panel
                    self._list_area.update()
                self._lista_panel.visible = True
                self._preview_panel.visible = False
            else:
                # dual activo: decide contenido según mode
                if mode == "lista":
                    if self._list_area.content != self._lista_panel:
                        try:
                            if self._lista_panel in self._dual_row.controls:
                                self._dual_row.controls.remove(self._lista_panel)
                        except Exception:
                            pass
                        self._dual_row.visible = False
                        self._list_area.content = self._lista_panel
                        self._list_area.update()
                    self._lista_panel.visible = True
                    self._preview_panel.visible = False
                elif mode == "preview":
                    # preview solo: muestra preview_panel solo
                    try:
                        if self._lista_panel in self._dual_row.controls:
                            self._dual_row.controls.remove(self._lista_panel)
                    except Exception:
                        pass
                    self._dual_row.visible = False
                    self._list_area.content = self._preview_panel
                    self._list_area.update()
                    self._lista_panel.visible = False
                    self._preview_panel.visible = True
                else:  # doble
                    # doble: Row con ambos
                    self._lista_panel.visible = True
                    self._preview_panel.visible = True
                    # asegura que _lista_panel esté dentro de _dual_row
                    if self._lista_panel not in self._dual_row.controls:
                        # si estaba en list_area, sácalo
                        if self._list_area.content == self._lista_panel:
                            self._list_area.content = None  # type: ignore
                        self._dual_row.controls = [self._lista_panel, self._preview_panel]
                    else:
                        if self._preview_panel not in self._dual_row.controls:
                            self._dual_row.controls.append(self._preview_panel)
                    self._dual_row.visible = True
                    self._list_area.content = self._dual_row
                    self._list_area.update()
        except Exception:
            pass
        # actualiza preview readonly
        try:
            self._sync_preview(s)
        except Exception:
            pass
        # recalcula skeletons para nuevo alto
        try:
            self._sync_skeleton_count()
        except Exception:
            pass

    def _sync_preview(self, s) -> None:
        """Preview readonly: selected_in_scope en orden final + conteo."""
        try:
            preview_tracks = s.selected_in_scope("visible") if hasattr(s, 'selected_in_scope') else [t for t in s.tracks if t.selected]
            # usa cache para preview? simple rebuild
            if not preview_tracks:
                self._preview_view_wrap.visible = False
                self._preview_empty_wrap.visible = True
                self._preview_count.value = "0 seleccionadas"
            else:
                self._preview_empty_wrap.visible = False
                self._preview_view_wrap.visible = True
                self._preview_count.value = f"{len(preview_tracks)} seleccionadas"
                # rebuild preview list (readonly, sin checkbox)
                # Reusa SongRow pero en modo readonly? Por ahora filas simples con texto
                # Para no duplicar lógica compleja, usamos Column de Containers ligeros
                # Si hay muchas, usa ListView con items simples
                self._preview_list_view.controls = []
                for i, tr in enumerate(preview_tracks, 1):
                    row = ft.Container(
                        height=ITEM_H, padding=ft.Padding.symmetric(horizontal=12, vertical=6),
                        border=ft.Border.only(bottom=ft.BorderSide(0.5, BORDER_ROW)), bgcolor=BG_LIST,
                        content=ft.Row([
                            ft.Text(str(i), size=10, color=TEXT_MUTED, width=28, text_align=ft.TextAlign.CENTER),
                            ft.Icon(ft.Icons.MUSIC_NOTE, size=16, color=TEXT_DIM),
                            ft.Column([
                                ft.Text(tr.name, size=12, color=TEXT_PRIMARY, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                                ft.Text(tr.artist, size=10, color=TEXT_MUTED, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                            ], spacing=1, expand=True, tight=True),
                            ft.Text(tr.album or "—", size=10, color=TEXT_DIM, expand=True, overflow=ft.TextOverflow.ELLIPSIS, max_lines=1),
                            ft.Text(tr.duration or "", size=10, color=TEXT_DIM, width=40, text_align=ft.TextAlign.CENTER),
                        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    )
                    self._preview_list_view.controls.append(row)
                self._preview_list_view.update()
                self._preview_count.update()
                self._preview_empty_wrap.update()
                self._preview_view_wrap.update()
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
        self._row_cache:              dict[str, SongRow]     = {}
        self._failed_dialog_shown:    bool  = False
        self._transfer_start:         float = 0.0
        self._completion_snack_shown: bool  = False
        self._pm_cleared_for_load:    bool  = False
        self.auth_manager                   = None
        self._auth_poll_task: Optional[asyncio.Task] = None

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
        # NAVIGATION RAIL + MÓDULOS (Inicio/Biblioteca/Descargas/Config)
        # ──────────────────────────────────────────────────────────────
        # Rail lateral 72px (core ft.NavigationRail) — reusa tokens SIDEBAR_BG/ACCENT
        self._current_module = 0  # 0=Inicio, 1=Biblioteca, 2=Descargas, 3=Config

        def _on_nav_change(e):
            idx = e.control.selected_index if e.control.selected_index is not None else 0
            self._current_module = idx
            try:
                for i, panel in enumerate(self._module_panels):
                    panel.visible = (i == idx)
                self._module_stack.update()
                # ajusta skeletons cuando vuelve a Inicio
                if idx == 0:
                    self._sync_skeleton_count()
                self.page.update()
            except Exception:
                pass

        try:
            self._nav_rail = ft.NavigationRail(
                selected_index=0,
                label_type=ft.NavigationRailLabelType.ALL,
                min_width=72, min_extended_width=200,
                group_alignment=-0.9,
                bgcolor=SIDEBAR_BG,
                indicator_color=ACCENT,
                use_indicator=True,
                on_change=_on_nav_change,
                destinations=[
                    ft.NavigationRailDestination(icon=ft.Icons.HOME_OUTLINED, selected_icon=ft.Icons.HOME, label="Inicio"),
                    ft.NavigationRailDestination(icon=ft.Icons.LIBRARY_MUSIC_OUTLINED, selected_icon=ft.Icons.LIBRARY_MUSIC, label="Biblioteca"),
                    ft.NavigationRailDestination(icon=ft.Icons.DOWNLOAD_OUTLINED, selected_icon=ft.Icons.DOWNLOAD, label="Descargas"),
                    ft.NavigationRailDestination(icon=ft.Icons.SETTINGS_OUTLINED, selected_icon=ft.Icons.SETTINGS, label="Config"),
                ],
            )
        except Exception:
            # fallback si NavigationRail no disponible en versión
            self._nav_rail = ft.Container(
                width=72, bgcolor=SIDEBAR_BG,
                content=ft.Column([
                    ft.IconButton(icon=ft.Icons.HOME, icon_color=ACCENT, tooltip="Inicio", on_click=lambda _: _on_nav_change(type('E',(),{'control':type('C',(),{'selected_index':0})})())),
                    ft.IconButton(icon=ft.Icons.LIBRARY_MUSIC, icon_color=TEXT_DIM, tooltip="Biblioteca", on_click=lambda _: _on_nav_change(type('E',(),{'control':type('C',(),{'selected_index':1})})())),
                    ft.IconButton(icon=ft.Icons.DOWNLOAD, icon_color=TEXT_DIM, tooltip="Descargas", on_click=lambda _: _on_nav_change(type('E',(),{'control':type('C',(),{'selected_index':2})})())),
                    ft.IconButton(icon=ft.Icons.SETTINGS, icon_color=TEXT_DIM, tooltip="Config", on_click=lambda _: _on_nav_change(type('E',(),{'control':type('C',(),{'selected_index':3})})())),
                ], spacing=12, alignment=ft.MainAxisAlignment.START),
                padding=ft.Padding.symmetric(vertical=12),
            )

        # Paneles de módulos — Inicio es Row([sidebar, content]), resto empty cono / placeholder
        self._panel_inicio = ft.Row(controls=[self._sidebar, self._content], spacing=0, expand=True,
                                    vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self._panel_biblioteca = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                              content=self._make_cono_empty("Biblioteca", "Colección local, historial 20 y portadas — pronto.", visible=True))
        self._panel_descargas = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                             content=self._make_cono_empty("Descargas", "yt-dlp + player + progreso — pronto.", visible=True))
        # Config: si hay wizard disponible se montará al abrir, si no placeholder
        self._panel_config = ft.Container(expand=True, bgcolor=BG_LIST, alignment=ft.Alignment.CENTER,
                                          content=self._make_cono_empty("Configuración", "Auth wizard + ajustes + About — abre con ⚙ en Inicio.", visible=True))
        self._module_panels = [self._panel_inicio, self._panel_biblioteca, self._panel_descargas, self._panel_config]
        # apila módulos, solo uno visible a la vez
        for i, p in enumerate(self._module_panels):
            p.visible = (i == 0)
        self._module_stack = ft.Stack(controls=self._module_panels, expand=True)

        # ──────────────────────────────────────────────────────────────
        # ENSAMBLAJE DE LAYOUT RAÍZ
        # ──────────────────────────────────────────────────────────────
        # Layout principal: [Rail 72 | MóduloStack]
        self.root = ft.Container(
            content=ft.Row(
                controls=[
                    self._nav_rail,
                    ft.VerticalDivider(width=1, color=BORDER_LIGHT),
                    self._module_stack,
                ],
                spacing=0, expand=True,
                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
            bgcolor=BG_LIST, expand=True,
        )

        # ──────────────────────────────────────────────────────────────
        # SUSCRIPCIÓN A EVENTOS
        # ──────────────────────────────────────────────────────────────
        # Conecta la UI al sistema reactivo de estado y circuit breakers
        
        state.subscribe(self._on_state_changed)
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

    async def _refresh_auth_live(self) -> None:
        am = getattr(self, "auth_manager", None)
        if am:
            await am.refresh_session_icons()

    async def _on_auth_probe(self, platform: str) -> None:
        am = getattr(self, "auth_manager", None)
        if not am:
            return
        self.state.log(f"[INFO] ⏳ Revalidando sesión de {platform}...")
        self.page.update()
        results = await am.check_all_sessions()
        am.ingest_preflight_results(results)
        for r in results:
            if r.platform != platform:
                continue
            if not r.ok:
                self.state.log(f"[ERROR] ⚠ {platform} falló la validación: {r.error[:400]}")
                am.open_wizard(platform)
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
                                                               color=TEXT_PRIMARY, font_family="IBM Plex Sans Light")),
                    ], opacity=1.0),
                    ft.Text("v4.x.x", size=9, color=TEXT_DIM, font_family="IBM Plex Sans",
                            style=ft.TextStyle(letter_spacing=0.8), opacity=1.0),
                ], spacing=0, tight=True, expand=True),
                self.btn_wizard,
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ], spacing=0)

        _dd_style = dict(
            bgcolor=BG_INPUT, border_color=BORDER_LIGHT,
            label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family="IBM Plex Sans"),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family="IBM Plex Sans"),
            border_radius=10, expand=True,
        )

        def _on_src_select(e) -> None:
            val = e.control.value
            s.set_source(val)
            if val == "Archivo Local":
                asyncio.create_task(self._do_local_pick())
            elif val == "Pegar Texto":
                self._open_paste_dialog()
            else:
                asyncio.create_task(self._refresh_auth_live())

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
                    self._transfer_btn.update()
                except Exception:
                    pass
            asyncio.create_task(self._refresh_auth_live())

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
        self._status_badge      = ft.Text("", size=10, color=SUCCESS, font_family="IBM Plex Sans", opacity=1.0)
        self._dest_session_warn = ft.Text("", size=9, color=ERROR_COL, font_family="IBM Plex Sans", visible=False)

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
            label="ID de la Playlist",
            hint_text="pl.u-xxxx  /  PLxxxx  /  37i9dQ…",
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

        actions = ft.Column([
            ft.Row([self._load_btn,  self._transfer_btn], spacing=6),
            ft.Row([self._organize_btn, self._split_btn], spacing=6),
        ], spacing=6)

        self._rl_banner = ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.TIMER_OUTLINED, color=WARNING, size=14),
                ft.Text("", size=10, color=WARNING, font_family="IBM Plex Sans", opacity=1.0),
            ], spacing=6),
            bgcolor=BG_PANEL, border=ft.Border.all(0.8, WARNING),
            border_radius=8, padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            visible=False,
        )

        self._progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
        self._progress_row = ft.Container(
            content=ft.Column([
                self._progress_bar,
                ft.Row([ft.Text("", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans", opacity=1.0)],
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
        """Abre el diálogo para organizar la lista de canciones (sin platform)."""
        from ui.widgets import organize_dropdown
        _dd_field = organize_dropdown(
            [("artist", "Artista"), ("album", "Álbum"), ("name", "Título"),
             ("duration_ms", "Duración")],
            "artist", "Ordenar por",
        )
        _switch_rev = ft.Switch(label="Descendente", value=False, active_color=ACCENT)
        _scope = {"value": "visible"}  # Todo|Visibles|Seleccionadas

        def _on_scope_change(e):
            try:
                v = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                v = getattr(e.control, "value", "visible")
            if v in ("all", "visible", "selected"):
                _scope["value"] = v

        _scope_seg = self._make_scope_segmented(_scope["value"], on_change=_on_scope_change)

        def _apply(_e):
            self.state.organize_sort([_dd_field.value], _switch_rev.value, scope=_scope["value"])
            self._close_dlg(dlg)

        dlg = app_dialog(
            "Organizar lista",
            ft.Column([
                _dd_field,
                _switch_rev,
                ft.Text("Alcance:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
                _scope_seg,
            ], tight=True, spacing=12),
            [
                dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
                dialog_action("Aplicar", _apply, kind="primary"),
            ],
            width=380,
            bgcolor=BG_SURFACE, radius=10,
        )
        self.page.show_dialog(dlg)

    def _on_split(self, _e: ft.ControlEvent) -> None:
        """Abre el diálogo para dividir la lista maestra en segmentos (sin platform)."""
        from ui.widgets import organize_dropdown
        _dd_field = organize_dropdown(
            [("artist", "Artista"), ("album", "Álbum")],
            "artist", "Agrupar por",
        )
        _scope = {"value": "visible"}

        def _on_scope_change(e):
            try:
                v = list(e.control.selected)[0] if hasattr(e.control, "selected") and e.control.selected else e.control.value
            except Exception:
                v = getattr(e.control, "value", "visible")
            if v in ("all", "visible", "selected"):
                _scope["value"] = v

        _scope_seg = self._make_scope_segmented(_scope["value"], on_change=_on_scope_change)

        def _apply(_e):
            self.state.organize_split(_dd_field.value, scope=_scope["value"])
            self._close_dlg(dlg)

        def _clear(_e):
            self.state.clear_split()
            self._close_dlg(dlg)

        actions: list[ft.Control] = []
        if bool(self.state.segments):
            actions.append(dialog_action("Limpiar División", _clear, kind="danger"))
        actions += [
            dialog_action("Cancelar", lambda _: self._close_dlg(dlg), kind="muted"),
            dialog_action("Agrupar", _apply, kind="primary"),
        ]
        dlg = app_dialog(
            "Dividir lista",
            ft.Column([
                ft.Text("Agrupa tu playlist en segmentos independientes.", size=12, color=TEXT_MUTED),
                _dd_field,
                ft.Text("Alcance:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
                _scope_seg,
            ], tight=True, spacing=12),
            actions,
            width=380,
            actions_alignment=(
                ft.MainAxisAlignment.SPACE_BETWEEN if len(actions) == 3
                else ft.MainAxisAlignment.END
            ),
            bgcolor=BG_SURFACE, radius=10,
        )
        self.page.show_dialog(dlg)


    # ── BUILD CONTENT ──────────────────────────────────────────────────

    def _build_content(self) -> None:
        self._playlist_title = ft.Text(
            "Cargar una playlist", size=22,
            color=TEXT_PRIMARY, font_family="IBM Plex Sans Bold", opacity=1.0,
        )
        self._track_count = ft.Text("", size=12, color=TEXT_MUTED, font_family="IBM Plex Sans", opacity=1.0)
        self._search_field = app_text_field(
            hint_text="Buscar título, artista…",
            prefix_icon=ft.Icons.SEARCH,
            width=240, height=38,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=6),
            on_change=self._on_search_change,
        )
        self._segment_dd = ft.Dropdown(
            width=160, height=38,
            bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family="IBM Plex Sans"),
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
            label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family="IBM Plex Sans"),
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=lambda _: self.state.toggle_select_all(),
        )

        self._content_progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
        self._content_prog_label   = ft.Text("", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans", opacity=0.6)
        self._content_eta_label    = ft.Text("", size=10, color=TEXT_DIM,   font_family="IBM Plex Sans", opacity=0.45)
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

        header_bar = ft.Row(controls=[
            ft.Column([self._playlist_title, self._track_count], spacing=2),
            ft.Container(expand=True),
            self._segment_dd, self._auth_strip, self._search_field, self._select_all_chk,
            self._clear_session_btn,
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

        def _col_header(text, width=None, expand=False, center=False):
            align = ft.Alignment.CENTER if center else ft.Alignment.CENTER_LEFT
            ctrl  = ft.Text(text, size=9, color=TEXT_DIM, font_family="IBM Plex Sans Bold",
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
            "Introduce el ID en el panel izquierdo y pulsa «Cargar».",
            size=12, color=TEXT_DIM, font_family="IBM Plex Sans", opacity=1.0, text_align=ft.TextAlign.CENTER,
        )
        self._empty_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(controls=[
                ft.Container(content=ft.Icon(ft.Icons.LIBRARY_MUSIC, size=52, color=TEXT_DIM),
                             bgcolor=CHIP_BG, border=ft.Border.all(0.8, BORDER_LIGHT),
                             border_radius=20, padding=ft.Padding.all(20)),
                ft.Text("Carga una playlist", size=20, color=TEXT_PRIMARY, font_family="IBM Plex Sans Bold",
                        opacity=1.0),
                ft.Text("Sin playlist cargada", size=14, color=TEXT_MUTED, font_family="IBM Plex Sans Medium",
                        opacity=1.0),
                self._empty_hint_text,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=True, **_sf,
        )

        self._error_text  = ft.Text("", size=13, color=ERROR_COL, font_family="IBM Plex Sans", opacity=1.0)
        self._error_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column([ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ERROR_COL), self._error_text],
                              horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=8),
            alignment=ft.Alignment.CENTER, visible=False, **_sf,
        )

        # Preview readonly (derecha del doble)
        self._preview_list_view = ft.ListView(item_extent=ITEM_H, spacing=0, expand=True,
                                              padding=ft.Padding.only(bottom=20))
        self._preview_count = ft.Text("", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans", opacity=1.0)
        self._preview_empty = self._make_cono_empty("Preview vacío", "Marca canciones y organiza para ver cómo se va a pasar.", visible=False)
        self._preview_empty_wrap = ft.Container(content=self._preview_empty, bgcolor=BG_LIST, visible=False, **_sf)
        self._preview_view_wrap = ft.Container(content=self._preview_list_view, bgcolor=BG_LIST, visible=False, **_sf)
        self._preview_stack = ft.Stack(controls=[self._preview_view_wrap, self._preview_empty_wrap], expand=True)
        self._preview_panel = ft.Container(
            expand=True, bgcolor=BG_LIST, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.VISIBILITY_OUTLINED, size=14, color=TEXT_MUTED),
                    ft.Text("Preview — cómo se va a pasar", size=11, color=TEXT_MUTED, font_family="IBM Plex Sans Bold"),
                    ft.Container(expand=True),
                    self._preview_count,
                ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                self._preview_stack,
            ], spacing=8, expand=True),
            padding=ft.Padding.all(10), visible=True,
        )

        # Lista panel (izq) envuelve lista editable + estados
        list_stack = ft.Stack(controls=[
            self._list_view_wrap, self._skeleton_view_wrap, self._empty_state, self._error_state,
        ], expand=True)
        self._lista_panel = ft.Container(
            expand=True, bgcolor=BG_LIST, border=ft.Border.all(0.5, BORDER_LIGHT), border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column([
                ft.Row([
                    ft.Icon(ft.Icons.LIST_ALT, size=14, color=TEXT_MUTED),
                    ft.Text("Lista editable", size=11, color=TEXT_MUTED, font_family="IBM Plex Sans Bold"),
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
        self._mode_bar = ft.Row([
            ft.Text("Vista:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
            self._mode_seg,
            ft.Container(expand=True),
            ft.IconButton(icon=ft.Icons.CLOSE, icon_size=14, icon_color=TEXT_DIM, tooltip="Cerrar doble vista",
                          on_click=lambda _: self.state.set_show_dual(False),
                          style=ft.ButtonStyle(padding=4, bgcolor={ft.ControlState.DEFAULT: ft.Colors.TRANSPARENT})),
        ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER, visible=False)

        # Doble fila responsive: Lista | Preview — evita duplicar controles (reparent dinámico)
        self._dual_row = ft.Row(spacing=10, expand=True, visible=False)
        # list_area es un Container cuyo content cambia entre _lista_panel (single) y _dual_row (doble)
        self._list_area = ft.Container(content=self._lista_panel, expand=True,
                                       animate_opacity=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
                                       animate_scale=ft.Animation(300, ft.AnimationCurve.EASE_OUT),
                                       opacity=1.0, scale=1.0)
        list_area = self._list_area

        self._content = ft.Container(
            expand=True, bgcolor=BG_LIST, padding=ft.Padding.all(24),
            content=ft.Column(controls=[self._content_progress, header_bar, col_headers, self._mode_bar, list_area],
                              spacing=10, expand=True),
        )


    # ── STATE REACTIONS ────────────────────────────────────────────────

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
        new_hint = _hint_map.get(s.source, "Introduce el ID en el panel izquierdo y pulsa \u00abCargar\u00bb.")
        if self._empty_hint_text.value != new_hint:
            self._empty_hint_text.value = new_hint
            self._empty_hint_text.update()
        dest_needs_confirm = is_local_src and not s.destination_confirmed
        new_dst_border = WARNING if dest_needs_confirm else BORDER_LIGHT
        if self._dst_dd.border_color != new_dst_border:
            self._dst_dd.border_color = new_dst_border
            self._dst_dd.focused_border_color = ACCENT if not dest_needs_confirm else WARNING
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
            self._dest_session_warn.value = "" if dest_ok else f"Sesi\u00f3n expirada en {s.destination}"
        if s.source == s.destination:
            self._status_badge.value = "\u26a0 Origen y destino iguales"
            self._status_badge.color = WARNING
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
            self._transfer_btn.content.value = "Exportar" if is_export else "Transferir"  # type: ignore
            self._transfer_btn.icon = ft.Icons.SAVE_ALT if is_export else ft.Icons.SWAP_HORIZ  # type: ignore
            self._transfer_btn.update()
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
            self._sync_list_view(s.display_tracks)
        has_tracks = len(s.tracks) > 0
        self._organize_btn.disabled = not has_tracks
        self._split_btn.disabled = not has_tracks
        if s.segments:
            current_options = [opt.key for opt in self._segment_dd.options] if self._segment_dd.options else []
            new_options = list(s.segments.keys())
            if current_options != new_options:
                self._segment_dd.options = [opt.key for opt in new_options]
            self._segment_dd.value = s.active_segment_key
            self._segment_dd.visible = True
        else:
            self._segment_dd.visible = False

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

    def _sync_telemetry(self, s, xfer_active: bool, is_loading: bool) -> None:
        if xfer_active:
            self._telemetry.update_counters(
                s.count_detected, s.count_candidates, s.count_processed,
                s.count_confirmed, len(s.api_rejected_tracks),
            )
        self._telemetry.update_log(s.log_lines)
        if s.transfer_state == TransferState.DONE:
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
        lv           = self._list_view
        existing_ids = {c.track.id for c in lv.controls if hasattr(c, "track")}
        incoming_ids = {t.id for t in tracks}
        if existing_ids != incoming_ids:
            lv.controls.clear()
            self._row_cache.clear()
            for i, track in enumerate(tracks, 1):
                row = SongRow(track, i, self.state.toggle_track)
                self._row_cache[track.id] = row
                lv.controls.append(row)
        else:
            track_map = {t.id: t for t in tracks}
            for tid, row in self._row_cache.items():
                current = track_map.get(tid)
                if current:
                    row.refresh(current)


    # ── EVENT HANDLERS ─────────────────────────────────────────────────

    def _on_load(self, _) -> None:
        src = self.state.source
        if src in AppState.LOCAL_SOURCES and not self.state.destination_confirmed:
            self._snack("⚠ Selecciona primero una plataforma de Destino", error=True)
            self._dst_dd.border_color         = WARNING
            self._dst_dd.focused_border_color = WARNING
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
            self._snack("Introduce un ID de playlist")
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
                    ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
                    order_seg
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Elige cómo está escrito cada línea (TuneMyMusic usa Artista - Título).",
                        size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
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
                        size=11, color=TEXT_MUTED, font_family="IBM Plex Sans"),
                name_field,
                ft.Row([
                    ft.Text("Orden:", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
                    order_seg2
                ], spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Confirma el orden de cada línea antes de importar.",
                        size=9, color=TEXT_DIM, font_family="IBM Plex Sans"),
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
        if self.state.source == self.state.destination:
            self._snack("Origen y destino no pueden ser iguales", error=True)
            return
        if self.state.source in AppState.LOCAL_SOURCES and not self.state.destination_confirmed:
            self._snack("⚠ Selecciona una plataforma de destino antes de transferir", error=True)
            self._dst_dd.border_color         = WARNING
            self._dst_dd.focused_border_color = WARNING
            self._dst_dd.update()
            return
        if self.state.selected_count == 0:
            self._snack("Selecciona al menos una canción", error=True)
            return
        from ui.playlist_meta_dialog import PlaylistMetaDialog
        meta = await PlaylistMetaDialog(
            self.page,
            default_title=self.state.playlist_name,
            default_description=self.state.playlist_description,
        ).show()
        if not meta.confirmed:
            return
        if not meta.title.strip():
            self._snack("El nombre de la playlist no puede estar vacío", error=True)
            return
        await self.state.transfer_playlist(
            title_override=meta.title,
            description_override=meta.description,
        )

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
            bgcolor=BG_INPUT, border_color=BORDER_LIGHT, focused_border_color=ACCENT,
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family="IBM Plex Sans"),
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
        info_txt = ft.Text(f"{len(src_tracks)} canciones · {self.state.playlist_name[:30]}", size=10, color=TEXT_MUTED, font_family="IBM Plex Sans")

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
                        size=10, color=TEXT_MUTED, font_family="IBM Plex Sans"),
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

    def _stop_skeleton_pulse(self) -> None:
        for task in self._skeleton_tasks:
            task.cancel()
        self._skeleton_tasks.clear()
        for sk in self._skeletons:
            sk.stop_pulse()

    def stop(self) -> None:
        self._stop_skeleton_pulse()
        if self._search_task and not self._search_task.done():
            self._search_task.cancel()

    def start_auth_poll(self, task: asyncio.Task) -> None:
        self._auth_poll_task = task

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
