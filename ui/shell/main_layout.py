"""ui/shell/main_layout.py — Controlador de layout principal de Gestión y ensamblaje raíz."""

from __future__ import annotations

import asyncio

import flet as ft

from core.models import LoadState, TransferState
from ui.components.skeletons import PreviewSkeletonRow, SkeletonRow
from ui.components.song_row import ITEM_H
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    app_text_field,
    input_border,
)
from ui.shell.nav_rail import build_nav_rail
from ui.shell.sidebar import build_sidebar
from ui.theme.fonts import FONT_HEADLINE, FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_INPUT,
    BG_LIST,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    ERROR_COL,
    MOTION_PANEL_MS,
    RADIUS_INPUT,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def compute_contextual_actions_state(
    state, *, net_blocked: bool = False
) -> dict[str, dict[str, object]]:
    """Calcula visibilidad, estado habilitado y tooltip de la barra contextual.

    Mantiene la regla de 2.10: mostrar/habilitar solo acciones aplicables a la
    fuente cargada (por ejemplo, ocultar ``Descargar`` si todas las pistas ya
    son archivos de audio locales en disco, y ocultar acciones de pista cuando
    no hay playlist cargada salvo ``Fusionar``).
    """
    tracks = list(getattr(state, "tracks", []) or [])
    load_state = getattr(state, "load_state", LoadState.IDLE)
    is_ready = load_state == LoadState.READY and len(tracks) > 0
    is_transferring = getattr(state, "transfer_state", TransferState.IDLE) == TransferState.RUNNING
    download_busy = getattr(state, "download_state", TransferState.IDLE) == TransferState.RUNNING
    all_local_audio = bool(tracks) and all(bool(getattr(t, "source_path", "")) for t in tracks)

    return {
        "transfer": {
            "visible": is_ready,
            "disabled": (not is_ready) or is_transferring or net_blocked,
            "tooltip": (
                "Transferencia en curso"
                if is_transferring
                else (
                    "Sin conexión (rate limit activo)"
                    if net_blocked
                    else "Elegir destino y transferir la selección"
                )
            ),
        },
        "organize": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Ordenar o agrupar las pistas cargadas",
        },
        "split": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Dividir la playlist por criterio o grupos manuales",
        },
        "merge": {
            "visible": True,
            "disabled": is_transferring,
            "tooltip": "Fusionar varias playlists o fuentes locales sin duplicados",
        },
        "download": {
            "visible": is_ready and not all_local_audio,
            "disabled": (not is_ready) or all_local_audio or download_busy,
            "tooltip": (
                "Descarga en curso"
                if download_busy
                else "Descargar pistas seleccionadas con yt-dlp"
            ),
        },
        "export": {
            "visible": is_ready,
            "disabled": not is_ready,
            "tooltip": "Exportar a archivo o añadir a una playlist destino",
        },
    }


def _make_segmented_control(
    current: str,
    options: list[tuple[str, str]],
    *,
    dd_width: int,
    on_change=None,
) -> ft.Control:
    """Construye un ``SegmentedButton`` con fallback a ``Dropdown`` si falta en runtime."""
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
                ft.Segment(value=val, label=ft.Text(label, size=11, font_family=FONT_HEADLINE))
                for val, label in options
            ],
            on_change=on_change,
        )
    except Exception:
        return ft.Dropdown(
            value=current,
            width=dd_width,
            height=38,
            bgcolor=BG_INPUT,
            border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            options=[ft.dropdown.Option(val, label) for val, label in options],
            on_select=on_change,
        )


class LayoutController:
    """Controlador de construcción y sincronización de layout para ``PlaylistManagerUI``."""

    def __init__(self, ui) -> None:
        self._ui = ui

    def _make_order_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton para elegir Artista-Título vs Título-Artista."""
        return _make_segmented_control(
            current,
            [("artist-title", "Artista - Título"), ("title-artist", "Título - Artista")],
            dd_width=200,
            on_change=on_change,
        )

    def _make_mode_segmented(self, current: str, on_change=None) -> ft.Control:
        """SegmentedButton Lista|Doble|Preview."""
        return _make_segmented_control(
            current,
            [("lista", "Lista"), ("doble", "Doble"), ("preview", "Preview")],
            dd_width=180,
            on_change=on_change,
        )

    def _make_cono_empty(self, title: str, subtitle: str, visible: bool = True) -> ft.Container:
        """Empty con icono para placeholders de módulo y preview."""
        return ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=app_icon(PIcons.WRENCH, size=52, color=TEXT_DIM),
                        bgcolor=CHIP_BG,
                        border=ft.Border.all(0.8, BORDER_LIGHT),
                        border_radius=20,
                        padding=ft.Padding.all(20),
                    ),
                    ft.Text(
                        title,
                        size=18,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                        opacity=1.0,
                    ),
                    ft.Text(
                        subtitle,
                        size=12,
                        color=TEXT_MUTED,
                        font_family=FONT_TEXT,
                        opacity=1.0,
                        text_align=ft.TextAlign.CENTER,
                    ),
                    ft.Text(
                        "Función en desarrollo",
                        size=11,
                        color=TEXT_DIM,
                        font_family=FONT_TEXT,
                        opacity=0.8,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment.CENTER,
            visible=visible,
            expand=True,
        )

    def _build_sidebar(self) -> None:
        build_sidebar(self._ui)

    def _build_content(self) -> None:
        ui = self._ui
        header_bar = self._build_content_header()
        ui._contextual_bar = self._build_contextual_bar()
        ui._catalog_panel = ui._catalog.build_catalog_panel()
        ui._col_headers = self._build_col_headers()
        self._build_list_and_preview_panels()
        self._build_mode_bar()
        self._assemble_content_area(header_bar, ui._col_headers)

    def _build_contextual_bar(self) -> ft.Container:
        """Barra de acciones contextuales encima de la tabla (Bloque 2.10)."""
        ui = self._ui
        ui._ctx_transfer_btn = _primary_btn(
            "Transferir", PIcons.ARROWS_LEFT_RIGHT, ui._on_transfer, height=34
        )
        ui._ctx_organize_btn = _ghost_btn(
            "Organizar", PIcons.SLIDERS_HORIZONTAL, ui._on_organize, height=34
        )
        ui._ctx_split_btn = _ghost_btn(
            "Dividir", PIcons.GIT_BRANCH, ui._on_split, height=34
        )
        ui._ctx_reorganize_btn = _ghost_btn(
            "Reorganizar", PIcons.ARROWS_CLOCKWISE, ui._on_reorganize, height=34, disabled=True
        )
        ui._ctx_download_btn = _ghost_btn(
            "Descargar", PIcons.DOWNLOAD_SIMPLE, lambda _: ui._on_batch_download(), height=34
        )
        ui._ctx_export_btn = _ghost_btn(
            "Exportar",
            PIcons.EXPORT,
            lambda _: asyncio.create_task(ui._open_export_dialog()),
            height=34,
        )
        ui._transfer_btn = ui._ctx_transfer_btn
        ui._organize_btn = ui._ctx_organize_btn
        ui._split_btn = ui._ctx_split_btn
        ui._reorganize_btn = ui._ctx_reorganize_btn
        ui._ctx_action_buttons = {
            "transfer": ui._ctx_transfer_btn,
            "organize": ui._ctx_organize_btn,
            "split": ui._ctx_split_btn,
            "download": ui._ctx_download_btn,
            "export": ui._ctx_export_btn,
        }
        row = ft.Row(
            controls=[
                ui._ctx_transfer_btn,
                ui._ctx_organize_btn,
                ui._ctx_split_btn,
                ui._ctx_reorganize_btn,
                ui._ctx_download_btn,
                ui._ctx_export_btn,
            ],
            spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )
        bar = ft.Container(
            content=row,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.8, BORDER_MUTED),
            border_radius=RADIUS_INPUT,
            padding=ft.Padding.symmetric(horizontal=12, vertical=8),
        )
        self._apply_contextual_actions_state(compute_contextual_actions_state(ui.state))
        return bar

    def _apply_contextual_actions_state(
        self, states: dict[str, dict[str, object]]
    ) -> None:
        """Aplica visibilidad, habilitación y tooltips a la barra contextual."""
        buttons = getattr(self._ui, "_ctx_action_buttons", {})
        for key, btn in buttons.items():
            spec = states.get(key)
            if not spec:
                continue
            btn.visible = bool(spec.get("visible", True))
            btn.disabled = bool(spec.get("disabled", False))
            tip = str(spec.get("tooltip", "") or "")
            if tip:
                btn.tooltip = tip

    def _build_content_header(self) -> ft.Row:
        ui = self._ui
        ui._playlist_title = ft.Text(
            "Cargar una playlist",
            size=22,
            color=TEXT_PRIMARY,
            font_family=FONT_HEADLINE_BOLD,
            opacity=1.0,
        )
        ui._track_count = ft.Text(
            "", size=12, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0
        )
        ui._search_field = app_text_field(
            hint_text="Buscar título, artista…",
            prefix_icon=PIcons.MAGNIFYING_GLASS,
            width=240,
            height=36,
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            on_change=ui._on_search_change,
        )
        ui._catalog_search_btn = _ghost_btn(
            "Catálogo",
            PIcons.GLOBE,
            lambda _: ui._on_toggle_catalog_search(),
            height=36,
        )
        ui._catalog_search_btn.tooltip = "Buscar canciones, álbumes o artistas en plataformas"
        ui._segment_dd = ft.Dropdown(
            width=160,
            height=38,
            bgcolor=BG_INPUT,
            border=input_border(),
            text_style=ft.TextStyle(color=TEXT_PRIMARY, size=12, font_family=FONT_TEXT),
            content_padding=ft.Padding.symmetric(horizontal=10, vertical=0),
            on_select=lambda e: ui.state.set_active_segment(e.control.value),
            visible=False,
            hint_text="Segmento...",
        )

        ui._auth_yt = app_icon_button(
            glyph=PIcons.YOUTUBE_LOGO,
            icon_color=TEXT_DIM,
            size=17,
            tooltip="YouTube Music · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(ui._on_auth_probe("YouTube Music")),
            width=28,
            height=28,
        )
        ui._auth_am = app_icon_button(
            glyph=PIcons.APPLE_LOGO,
            icon_color=TEXT_DIM,
            size=17,
            tooltip="Apple Music · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(ui._on_auth_probe("Apple Music")),
            width=28,
            height=28,
        )
        ui._auth_sp = app_icon_button(
            glyph=PIcons.SPOTIFY_LOGO,
            icon_color=TEXT_DIM,
            size=17,
            tooltip="Spotify · clic = validar sesión ahora",
            on_click=lambda _: asyncio.create_task(ui._on_auth_probe("Spotify")),
            width=28,
            height=28,
        )
        ui._auth_strip = ft.Row(
            controls=[ui._auth_yt, ui._auth_am, ui._auth_sp],
            spacing=2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        ui._select_all_chk = ft.Checkbox(
            label="Todo",
            label_style=ft.TextStyle(color=TEXT_MUTED, size=11, font_family=FONT_HEADLINE),
            fill_color={ft.ControlState.SELECTED: ACCENT},
            check_color=TEXT_PRIMARY,
            border_side=ft.BorderSide(1.5, TEXT_DIM),
            on_change=lambda _: ui.state.toggle_select_all(),
        )

        ui._content_progress_bar = ft.ProgressBar(
            value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4
        )
        ui._content_prog_label = ft.Text(
            "", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=0.6
        )
        ui._content_eta_label = ft.Text(
            "", size=10, color=TEXT_DIM, font_family=mono_family("light"), opacity=0.45
        )
        ui._content_progress = ft.Container(
            content=ft.Column(
                [
                    ui._content_progress_bar,
                    ft.Row(
                        [ui._content_prog_label, ft.Container(expand=True), ui._content_eta_label],
                        spacing=0,
                    ),
                ],
                spacing=4,
            ),
            visible=False,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=6),
        )

        ui._clear_session_btn = app_icon_button(
            glyph=PIcons.TRASH,
            icon_color=TEXT_DIM,
            size=16,
            tooltip="Limpiar playlist cargada",
            on_click=ui._on_clear_session,
            width=28,
            height=28,
        )

        return ft.Row(
            controls=[
                ft.Column([ui._playlist_title, ui._track_count], spacing=2),
                ft.Container(expand=True),
                ui._auth_strip,
                ui._search_field,
                ui._catalog_search_btn,
                ui._select_all_chk,
                ui._clear_session_btn,
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    def _build_col_headers(self) -> ft.Container:
        def _col_header(text, width=None, expand=False, center=False):
            align = ft.Alignment.CENTER if center else ft.Alignment.CENTER_LEFT
            ctrl = ft.Text(
                text,
                size=9,
                color=TEXT_DIM,
                font_family=FONT_HEADLINE_BOLD,
                style=ft.TextStyle(letter_spacing=0.8),
                text_align=ft.TextAlign.CENTER if center else ft.TextAlign.LEFT,
                opacity=1.0,
            )
            return ft.Container(content=ctrl, width=width, expand=expand, alignment=align)

        ui = self._ui
        ui._album_col_header = _col_header("ÁLBUM", expand=2)
        return ft.Container(
            content=ft.Row(
                controls=[
                    _col_header("#", width=32, center=True),
                    _col_header("PORTADA", width=55, center=True),
                    _col_header("TÍTULO / ARTISTA", expand=3),
                    ui._album_col_header,
                    _col_header("DUR.", width=48, center=True),
                    _col_header("", width=26, center=True),
                    _col_header("SEL.", width=32, center=True),
                ],
                spacing=16,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=16, vertical=8),
            bgcolor=CHIP_BG,
            border_radius=12,
            border=ft.Border.all(0.8, BORDER_LIGHT),
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        )

    def _sync_queue_album_column(self, queue_open: bool, *, update: bool = True) -> None:
        """Oculta la columna Álbum en Gestión cuando el panel de cola está abierto (D26)."""
        ui = self._ui
        show_album = not bool(queue_open)
        album_hdr = getattr(ui, "_album_col_header", None)
        if album_hdr is not None and album_hdr.visible != show_album:
            album_hdr.visible = show_album
            if update:
                try:
                    album_hdr.update()
                except Exception:
                    pass
        lv = getattr(ui, "_list_view", None)
        if lv is None:
            return
        for row in getattr(lv, "controls", ()):
            setter = getattr(row, "set_album_visible", None)
            if callable(setter):
                setter(show_album, update=update)

    def _build_list_and_preview_panels(self) -> None:
        ui = self._ui
        ui._list_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=0,
            expand=True,
            padding=ft.Padding.only(bottom=20),
        )
        _sf = dict(left=0, top=0, right=0, bottom=0)
        ui._list_view_wrap = ft.Container(content=ui._list_view, bgcolor=BG_LIST, visible=False, **_sf)
        init_cnt = ui._calc_skeleton_count() if hasattr(ui, "_calc_skeleton_count") else ui.SKELETON_COUNT
        ui._skeletons = [SkeletonRow(i) for i in range(init_cnt)]
        ui._skeleton_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=0,
            expand=True,
            controls=ui._skeletons,
            visible=True,
        )
        ui._skeleton_view_wrap = ft.Container(
            content=ui._skeleton_view, bgcolor=BG_LIST, visible=False, **_sf
        )

        ui._empty_hint_text = ft.Text(
            "Introduce el ID o la URL en el panel izquierdo y pulsa «Cargar».",
            size=12,
            color=TEXT_DIM,
            font_family=FONT_TEXT,
            opacity=1.0,
            text_align=ft.TextAlign.CENTER,
        )
        ui._empty_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(
                controls=[
                    ft.Container(
                        content=app_icon(PIcons.PLAYLIST, size=52, color=TEXT_DIM),
                        bgcolor=CHIP_BG,
                        border=ft.Border.all(0.8, BORDER_LIGHT),
                        border_radius=20,
                        padding=ft.Padding.all(20),
                    ),
                    ft.Text(
                        "Carga una playlist",
                        size=20,
                        color=TEXT_PRIMARY,
                        font_family=FONT_HEADLINE_BOLD,
                        opacity=1.0,
                    ),
                    ft.Text(
                        "Sin playlist cargada",
                        size=14,
                        color=TEXT_MUTED,
                        font_family=FONT_HEADLINE,
                        opacity=1.0,
                    ),
                    ui._empty_hint_text,
                    _ghost_btn(
                        "Buscar en catálogo",
                        PIcons.MAGNIFYING_GLASS,
                        lambda _: ui._on_toggle_catalog_search(),
                        height=34,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment.CENTER,
            visible=True,
            **_sf,
        )

        ui._error_text = ft.Text("", size=13, color=ERROR_COL, font_family=FONT_TEXT, opacity=1.0)
        ui._error_state = ft.Container(
            bgcolor=BG_LIST,
            content=ft.Column(
                [
                    app_icon(PIcons.WARNING_CIRCLE, size=48, color=ERROR_COL, fill=True),
                    ui._error_text,
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            alignment=ft.Alignment.CENTER,
            visible=False,
            **_sf,
        )

        ui._preview_list_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=0,
            expand=True,
            padding=ft.Padding.only(bottom=20),
        )
        ui._preview_count = ft.Text(
            "", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0
        )
        ui._preview_empty = self._make_cono_empty(
            "Preview vacío", "Marca canciones y organiza para ver cómo se va a pasar.", visible=False
        )
        ui._preview_empty_wrap = ft.Container(
            content=ui._preview_empty, bgcolor=BG_LIST, visible=False, **_sf
        )
        ui._preview_view_wrap = ft.Container(
            content=ui._preview_list_view, bgcolor=BG_LIST, visible=False, **_sf
        )
        ui._preview_skeletons = [PreviewSkeletonRow(i) for i in range(len(ui._skeletons))]
        ui._preview_skeleton_view = ft.ListView(
            item_extent=ITEM_H,
            cache_extent=ITEM_H * 8,
            spacing=0,
            expand=True,
            controls=ui._preview_skeletons,
            visible=True,
        )
        ui._preview_skeleton_wrap = ft.Container(
            content=ui._preview_skeleton_view, bgcolor=BG_LIST, visible=False, **_sf
        )
        ui._preview_stack = ft.Stack(
            controls=[ui._preview_view_wrap, ui._preview_skeleton_wrap, ui._preview_empty_wrap],
            expand=True,
        )
        ui._preview_header_text = ft.Text(
            "Preview — cómo se va a pasar",
            size=11,
            color=TEXT_MUTED,
            font_family=FONT_HEADLINE_BOLD,
        )
        ui._preview_header_icon = app_icon(PIcons.EYE, size=14, color=TEXT_MUTED)
        ui._preview_panel = ft.Container(
            expand=True,
            bgcolor=BG_LIST,
            border=ft.Border.all(0.5, BORDER_LIGHT),
            border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ui._preview_header_icon,
                            ui._preview_header_text,
                            ft.Container(expand=True),
                            ui._preview_count,
                        ],
                        spacing=6,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    ui._preview_stack,
                ],
                spacing=8,
                expand=True,
            ),
            padding=ft.Padding.all(10),
            visible=True,
        )

        list_stack = ft.Stack(
            controls=[ui._list_view_wrap, ui._skeleton_view_wrap, ui._empty_state, ui._error_state],
            expand=True,
        )
        ui._lista_header_text = ft.Text(
            "Lista editable", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE_BOLD
        )
        ui._lista_header_icon = app_icon(PIcons.LIST_BULLETS, size=14, color=TEXT_MUTED)
        ui._lista_panel = ft.Container(
            expand=True,
            bgcolor=BG_LIST,
            border=ft.Border.all(0.5, BORDER_LIGHT),
            border_radius=10,
            clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
            content=ft.Column(
                [
                    ft.Row([ui._lista_header_icon, ui._lista_header_text], spacing=6),
                    ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
                    list_stack,
                ],
                spacing=8,
                expand=True,
            ),
            padding=ft.Padding.all(10),
            visible=True,
        )

    def _build_mode_bar(self) -> None:
        ui = self._ui

        def _on_mode_change(e):
            try:
                v = (
                    list(e.control.selected)[0]
                    if hasattr(e.control, "selected") and e.control.selected
                    else e.control.value
                )
            except Exception:
                v = getattr(e.control, "value", "lista")
            ui.state.set_dual_mode(v)

        ui._mode_seg = self._make_mode_segmented(
            getattr(ui.state, "dual_mode", "lista"), on_change=_on_mode_change
        )
        ui._division_btn_text = ft.Text("División 1", size=11, color=TEXT_MUTED, font_family=FONT_HEADLINE)
        ui._division_label = ft.Text("División", size=9, color=TEXT_DIM, font_family=FONT_HEADLINE_BOLD)
        ui._division_popup = ft.PopupMenuButton(
            content=ft.Row(
                [
                    app_icon(PIcons.FUNNEL, size=14, color=TEXT_DIM),
                    ui._division_btn_text,
                    app_icon(PIcons.CARET_DOWN, size=14, color=TEXT_DIM),
                ],
                spacing=4,
                tight=True,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
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
        ui._division_col = ft.Column([ui._division_label, ui._division_popup], spacing=0, tight=True)
        ui._partition_dd = None
        ui._partition_btn = _ghost_btn(
            "Todos", PIcons.FUNNEL, lambda _: ui._open_partition_picker()
        )
        ui._partition_btn.visible = False
        ui._partition_btn.tooltip = "Seleccionar divisiones para transferir — multi §7"
        ui._edit_division_btn = _ghost_btn(
            "Editar", PIcons.PENCIL_SIMPLE, lambda _: ui._open_edit_division_dialog()
        )
        ui._edit_division_btn.visible = False
        ui._edit_division_btn.tooltip = (
            "Editar división — quitar/agregar entre ORIGINAL y División activa §4"
        )
        ui._mode_bar = ft.Row(
            [
                ft.Text("Vista:", size=10, color=TEXT_MUTED, font_family=FONT_HEADLINE),
                ui._mode_seg,
                ft.VerticalDivider(width=1, color=BORDER_MUTED),
                app_icon(PIcons.GIT_BRANCH, size=14, color=TEXT_DIM),
                ui._division_col,
                ui._edit_division_btn,
                ft.Container(expand=True),
                app_icon_button(
                    glyph=PIcons.X,
                    size=14,
                    icon_color=TEXT_DIM,
                    tooltip="Cerrar doble vista",
                    on_click=lambda _: ui.state.set_show_dual(False),
                    width=24,
                    height=24,
                ),
            ],
            spacing=8,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            visible=False,
        )

    def _assemble_content_area(self, header_bar: ft.Row, col_headers: ft.Container) -> None:
        ui = self._ui
        ui._dual_row = ft.Row(
            controls=[ui._lista_panel, ui._preview_panel],
            spacing=10,
            expand=True,
            visible=True,
        )
        ui._preview_panel.visible = False
        ui._lista_panel.visible = True
        ui._list_area = ft.Container(
            content=ui._dual_row,
            expand=True,
            animate_opacity=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            animate_scale=ft.Animation(MOTION_PANEL_MS, ft.AnimationCurve.EASE_IN_OUT),
            opacity=1.0,
            scale=1.0,
        )
        ui._content = ft.Container(
            expand=True,
            bgcolor=BG_LIST,
            padding=ft.Padding.all(24),
            content=ft.Column(
                controls=[
                    ui._content_progress,
                    header_bar,
                    ui._contextual_bar,
                    ui._catalog_panel,
                    col_headers,
                    ui._mode_bar,
                    ui._list_area,
                ],
                spacing=10,
                expand=True,
            ),
        )

    def _build_nav_rail(self) -> None:
        build_nav_rail(self._ui)

    def _build_modules_and_root(self) -> None:
        ui = self._ui
        ui._panel_gestion = ft.Row(
            controls=[ui._sidebar, ui._content],
            spacing=0,
            expand=True,
            vertical_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        try:
            from services.library_state import LibraryState
            from ui.views.library_view import LibraryView

            lib_state = LibraryState(service=getattr(ui.state, "service", None))
            ui.library_state = lib_state  # type: ignore[attr-defined]
            ui._panel_biblioteca = LibraryView(
                ui.page,
                lib_state,
                getattr(ui.state, "service", None),
                app_state=ui.state,
                on_open_downloads=lambda: ui._select_module(2),
                on_open_gestion=ui._open_folder_in_gestion,
            )
        except Exception as exc:
            try:
                ui.state.log(f"[WARN] Biblioteca fallback: {exc}")
            except Exception:
                pass
            ui._panel_biblioteca = ft.Container(
                expand=True,
                bgcolor=BG_LIST,
                alignment=ft.Alignment.CENTER,
                content=self._make_cono_empty(
                    "Biblioteca", "Colección local, historial 20 y portadas — pronto.", visible=True
                ),
            )

        ui._auto_play_download_ids = set()
        from services.mpris_service import MPRISService
        from ui.player.player_bar import PlayerBar
        from ui.player.player_queue import PlayerQueuePanel

        ui.player_bar = PlayerBar(
            ui.page,
            ui.state,
            on_toggle_queue=lambda: ui.player_queue.toggle(),
        )
        ui.player_bar.attach_mpris(MPRISService())
        ui.player_queue = PlayerQueuePanel(
            ui.page,
            ui.player_bar,
            on_open_change=self._sync_queue_album_column,
        )
        self._sync_queue_album_column(ui.player_queue.is_open, update=False)
        ui.player_bar.log_audio_services_count("init")
        if hasattr(ui, "_panel_biblioteca") and ui._panel_biblioteca is not None:
            ui._panel_biblioteca.player_bar = ui.player_bar

        lib_for_picker = getattr(ui, "library_state", None)
        if lib_for_picker is not None and hasattr(lib_for_picker, "subscribe"):
            def _on_library_change() -> None:
                try:
                    ui._refresh_playlist_picker(ui.state.source)
                except Exception:
                    pass

            lib_for_picker.subscribe(_on_library_change)
            try:
                ui._refresh_playlist_picker(ui.state.source)
            except Exception:
                pass

        try:
            from ui.views.downloads_view import DownloadsView

            ui._panel_descargas = DownloadsView(
                ui.page, ui.state, library_state=getattr(ui, "library_state", None)
            )
            ui._panel_descargas.player_bar = ui.player_bar  # type: ignore[attr-defined]
        except Exception as exc:
            try:
                ui.state.log(f"[WARN] Descargas fallback: {exc}")
            except Exception:
                pass
            ui._panel_descargas = ft.Container(
                expand=True,
                bgcolor=BG_LIST,
                alignment=ft.Alignment.CENTER,
                content=self._make_cono_empty(
                    "Descargas", "yt-dlp + progreso — no disponible.", visible=True
                ),
            )

        try:
            from ui.views.config_view import ConfigView

            ui._panel_config = ConfigView(
                ui.page,
                on_change=ui._on_config_changed,
                on_welcome=lambda: ui.on_show_welcome and ui.on_show_welcome(),
            )
        except Exception as exc:
            try:
                ui.state.log(f"[WARN] Config fallback: {exc}")
            except Exception:
                pass
            ui._panel_config = ft.Container(
                expand=True,
                bgcolor=BG_LIST,
                alignment=ft.Alignment.CENTER,
                content=self._make_cono_empty(
                    "Configuración", "Preferencias no disponibles.", visible=True
                ),
            )

        ui._module_panels = [
            ui._panel_gestion,
            ui._panel_biblioteca,
            ui._panel_descargas,
            ui._panel_config,
        ]
        for i, p in enumerate(ui._module_panels):
            p.visible = (i == 0)
        ui._module_stack = ft.Stack(
            controls=ui._module_panels,
            expand=True,
            fit=ft.StackFit.EXPAND,
        )

        # Layout principal: rail flotante + módulo + cola lateral por empuje, y reproductor abajo.
        ui.root = ft.Container(
            content=ft.Column(
                controls=[
                    ft.Row(
                        controls=[
                            ui._nav_rail,
                            ui._module_stack,
                            ui.player_queue,
                        ],
                        spacing=0,
                        expand=True,
                        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
                    ),
                    ui.player_bar,
                ],
                spacing=0,
                expand=True,
            ),
            bgcolor=BG_LIST,
            expand=True,
        )
