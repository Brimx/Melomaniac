"""
╔══════════════════════════════════════════════════════════════════════╗
║                    Melomaniac v4.9.0                             ║
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
from core.config import APP_VERSION, EXPORT_DEST_LABEL
from ui.song_row import SongRow, SkeletonRow, PreviewSkeletonRow, build_preview_row, ITEM_H
from ui.fonts import (
    FONT_HEADLINE,
    FONT_HEADLINE_MEDIUM,
    FONT_PHOSPHOR,
    FONT_PHOSPHOR_FILL,
    brand_family,
    mono_family,
)
from ui.icons import PIcons, app_icon, app_icon_button
from ui.transfer_report import TransferReportDialog
from ui.widgets import app_text_field, DialogMixin, notify, input_border

from ui.tokens import (
    BG_DEEP,
    BG_HOVER,
    SIDEBAR_BG,
    BG_LIST,
    BORDER_LIGHT,
    BORDER_MUTED,
    ACCENT,
    ACCENT_HALO,
    SUCCESS,
    WARNING,
    ERROR_COL,
    TEXT_PRIMARY,
    TEXT_MUTED,
    TEXT_DIM,
    RADIUS_RAIL,
    RADIUS_RAIL_ITEM,
    RAIL_W_COLLAPSED,
    RAIL_W_EXPANDED,
    RAIL_MARGIN,
    RAIL_PAD_H,
    RAIL_PAD_V,
    RAIL_ITEM_SPACING,
    RAIL_ITEM_PAD_V,
    RAIL_ITEM_H,
    RAIL_HEADER_H,
    RAIL_LABEL_W,
    RAIL_BRAND_W,
    RAIL_VERSION_H,
    RAIL_VERSION_H_COLLAPSED,
    RAIL_ANIM_MS,
    MOTION_HOVER_MS,
)
from ui.division_dialogs import DivisionMixin
from ui.main_actions import ActionsMixin
from ui.main_layout import LayoutMixin

# Por encima de este número conviene un único update del ListView en vez de
# publicar una actualización por fila (transferencias masivas, reordenamientos).
_ROW_UPDATE_BATCH_THRESHOLD = 16


class PlaylistManagerUI(DialogMixin, DivisionMixin, ActionsMixin, LayoutMixin):
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
        self._failed_dialog_shown:    bool  = False
        self._report_dialog_shown:    bool  = False
        self._transfer_start:         float = 0.0
        self._completion_snack_shown: bool  = False
        self._pm_cleared_for_load:    bool  = False
        self.auth_manager                   = None
        self.on_show_welcome                = None  # inyectado por app.py
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
            ("Gestión", PIcons.ARROWS_LEFT_RIGHT, PIcons.ARROWS_LEFT_RIGHT),
            ("Biblioteca", PIcons.BOOKS, PIcons.BOOKS),
            ("Descargas", PIcons.DOWNLOAD_SIMPLE, PIcons.DOWNLOAD_SIMPLE),
            ("Config", PIcons.GEAR, PIcons.GEAR),
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

        # builder de items (Container con animate + hover reactivo) — Headline para labels, Notch para brand
        def _rail_item(idx: int, label: str, icon_out: str, icon_sel: str) -> ft.Container:
            is_sel = (idx == self._current_module)
            # Stack Sans Headline para labels (§ Headline: labels/botones)
            txt = ft.Text(
                label, size=13, color=TEXT_PRIMARY if is_sel else TEXT_MUTED,
                font_family=FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE,
                expand=True, no_wrap=True, max_lines=1,
                overflow=ft.TextOverflow.CLIP,
            )
            # Solo animan ancho/margen/opacidad de la caja: el texto permanece
            # montado y recortado (clip), nunca visible/invisible, para que el
            # colapso sea un recorte progresivo y no un salto.
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
            icon = app_icon(icon_sel if is_sel else icon_out, size=20, color=ACCENT if is_sel else TEXT_DIM, fill=is_sel)
            # Row fija: el icono nunca se recentra; solo se recorta el label.
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
                border=ft.Border.all(0.8, ACCENT if is_sel else ft.Colors.TRANSPARENT),
                border_radius=RADIUS_RAIL_ITEM,
                padding=ft.Padding.symmetric(
                    horizontal=12,
                    vertical=RAIL_ITEM_PAD_V,
                ),
                height=RAIL_ITEM_H,
                data=str(idx),
                tooltip=label if self._rail_collapsed else None,
                on_click=_on_rail_item_click,
                animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
                alignment=ft.Alignment.CENTER_LEFT,
            )

            def _on_hover(e: ft.HoverEvent) -> None:
                is_currently_sel = (idx == self._current_module)
                if is_currently_sel:
                    return
                is_h = e.data == "true" or e.data is True
                cont.bgcolor = BG_HOVER if is_h else ft.Colors.TRANSPARENT
                cont.border = ft.Border.all(0.8, ACCENT if is_h else ft.Colors.TRANSPARENT)
                icon.color = TEXT_PRIMARY if is_h else TEXT_DIM
                txt.color = TEXT_PRIMARY if is_h else TEXT_MUTED
                if cont.page:
                    cont.update()

            cont.on_hover = _on_hover
            self._rail_item_refs.append(cont)
            return cont

        # toggle colapso: ancho del shell + ancho/opacidad de labels en paralelo
        def _toggle_rail(_e=None):
            self._rail_collapsed = not self._rail_collapsed
            expanded = not self._rail_collapsed
            try:
                self._nav_rail.width = self._rail_w_expanded if expanded else self._rail_w_collapsed
                self._rail_toggle_btn.tooltip = "Colapsar" if expanded else "Expandir"
                # header brand: caja que crece/decrece y se desvanece
                if self._rail_header_brand_box is not None:
                    self._rail_header_brand_box.width = RAIL_BRAND_W if expanded else 0
                    self._rail_header_brand_box.margin = ft.Margin.only(left=8) if expanded else ft.Margin.only(left=0)
                    self._rail_header_brand_box.opacity = 1.0 if expanded else 0.0
                if self._rail_header_box is not None:
                    self._rail_header_box.alignment = ft.Alignment.CENTER_LEFT
                if self._rail_version_text is not None:
                    self._rail_version_text.opacity = 0.7 if expanded else 0.0
                if self._rail_version_box is not None:
                    # En compacto no deja espacio muerto bajo Config.
                    self._rail_version_box.height = RAIL_VERSION_H if expanded else RAIL_VERSION_H_COLLAPSED
                # items: solo cambian ancho/margen/opacidad del label (animados).
                # Sin invertir visible/alineación/padding en el mismo parche:
                # esas propiedades no se animan y provocaban el salto brusco.
                for i, label_box in enumerate(self._rail_label_boxes):
                    label_box.width = RAIL_LABEL_W if expanded else 0
                    label_box.margin = ft.Margin.only(left=12) if expanded else ft.Margin.only(left=0)
                    label_box.opacity = 1.0 if expanded else 0.0
                    try:
                        cont = self._rail_item_refs[i]
                        cont.tooltip = None if expanded else self._rail_dests[i][0]
                    except Exception:
                        pass
                self._nav_rail.update()
            except Exception:
                pass

        self._rail_toggle_btn = app_icon_button(
            PIcons.SIDEBAR_SIMPLE,
            size=18,
            icon_color=TEXT_MUTED,
            hover_color_icon=TEXT_PRIMARY,
            tooltip="Expandir" if self._rail_collapsed else "Colapsar",
            on_click=_toggle_rail,
            width=32,
            height=32,
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
            alignment=ft.Alignment.CENTER_LEFT,
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
                    cont.border = ft.Border.all(0.8, ACCENT if is_sel else ft.Colors.TRANSPARENT)
                    row = cont.content  # Row([icon, label_box])
                    if row and len(row.controls) >= 2:
                        icon_ctrl = row.controls[0]
                        label_box = row.controls[1]
                        lbl_ctrl = label_box.content if isinstance(label_box, ft.Container) else None
                        glyph = self._rail_dests[i][2] if is_sel else self._rail_dests[i][1]
                        icon_ctrl.value = glyph
                        icon_ctrl.font_family = FONT_PHOSPHOR_FILL if is_sel else FONT_PHOSPHOR
                        icon_ctrl.color = ACCENT if is_sel else TEXT_DIM
                        if lbl_ctrl:
                            lbl_ctrl.color = TEXT_PRIMARY if is_sel else TEXT_MUTED
                            lbl_ctrl.font_family = FONT_HEADLINE_MEDIUM if is_sel else FONT_HEADLINE
                except Exception:
                    pass
        self._refresh_rail_selection = _refresh_rail_selection  # type: ignore

        # versión — IBM Plex Mono Light 300 (datos técnicos § Mono)
        self._rail_version_text = ft.Text(
            f"v{APP_VERSION}", size=8, color=TEXT_DIM,
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
        from services.mpris_service import MPRISService
        from ui.player_bar import PlayerBar
        from ui.player_queue import PlayerQueuePanel

        self.player_bar = PlayerBar(
            page,
            state,
            on_toggle_queue=lambda: self.player_queue.toggle(),
        )
        self.player_bar.attach_mpris(MPRISService())
        self.player_queue = PlayerQueuePanel(page, self.player_bar)
        self.player_bar.log_audio_services_count("init")
        if hasattr(self, "_panel_biblioteca") and self._panel_biblioteca is not None:
            self._panel_biblioteca.player_bar = self.player_bar

        # El selector de playlists de Gestión se rellena cuando Biblioteca
        # termina de sincronizar, sin acoplar ambas vistas.
        lib_for_picker = getattr(self, "library_state", None)
        if lib_for_picker is not None and hasattr(lib_for_picker, "subscribe"):
            def _on_library_change() -> None:
                try:
                    self._refresh_playlist_picker(self.state.source)
                except Exception:
                    pass

            lib_for_picker.subscribe(_on_library_change)
            # Primer relleno con lo que ya haya en caché para el origen actual.
            try:
                self._refresh_playlist_picker(self.state.source)
            except Exception:
                pass

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
            self._panel_config = ConfigView(
                page, on_change=self._on_config_changed,
                on_welcome=lambda: self.on_show_welcome and self.on_show_welcome(),
            )
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
        # Layout principal: rail flotante + módulo, barra de reproducción abajo.
        # La cola es overlay posicionado (derecha) sobre el área de módulos:
        # abrirla no cambia el ancho de la vista activa, así que listas y
        # grilla no se redimensionan durante su animación.
        self.root = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Stack(
                        controls=[
                            ft.Row(
                                controls=[
                                    self._nav_rail,
                                    self._module_stack,
                                ],
                                spacing=0, expand=True,
                                vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                            ),
                            self.player_queue,
                        ],
                        expand=True,
                        fit=ft.StackFit.EXPAND,
                        clip_behavior=ft.ClipBehavior.HARD_EDGE,
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

    # ── MÉTODOS DE ORGANIZACIÓN Y DIVISIÓN ────────────────────────────

    # ── BUILD CONTENT ──────────────────────────────────────────────────

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
        if s.destination == EXPORT_DEST_LABEL:
            dest_ok = True
            self._dest_session_warn.visible = False
            self._dest_session_warn.value = ""
        else:
            session = s.auth_session_ok.get(s.destination)
            # ``None`` no bloquea el botón: se confirma en _on_transfer. Solo
            # un fallo explícito (False) deshabilita la transferencia.
            dest_ok = session is not False
            self._dest_session_warn.visible = session is not True
            self._dest_session_warn.value = "" if session is True else (
                f"Sesión de {s.destination} sin validar · pulsa el icono para comprobarla"
                if session is None
                else f"No se confirmó la sesión en {s.destination}"
            )
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
            glyph = PIcons.EXPORT if is_export else PIcons.ARROWS_LEFT_RIGHT
            if isinstance(self._transfer_btn.content, ft.Row) and len(self._transfer_btn.content.controls) >= 2:
                self._transfer_btn.content.controls[0].value = glyph
                self._transfer_btn.content.controls[1].value = label
            elif hasattr(self._transfer_btn.content, "value"):
                self._transfer_btn.content.value = label
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
        elif s.auth_session_ok.get(s.destination) is None:
            self._transfer_btn.tooltip = (
                f"Sesi\u00f3n de {s.destination} sin validar \u00b7 se comprobar\u00e1 al transferir"
            )
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
