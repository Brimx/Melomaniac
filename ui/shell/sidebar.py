"""ui/shell/sidebar.py — Barra lateral de origen, selector de playlist y telemetría."""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import APP_VERSION, EXPORT_DEST_LABEL
from core.state import AppState
from ui.components.telemetry import TelemetryDrawer
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    _section_label,
    app_text,
    app_text_field,
    input_border,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT, brand_family, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    ACCENT_HALO,
    BG_INPUT,
    BG_PANEL,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    ERROR_COL,
    SIDEBAR_BG,
    SUCCESS,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)


def build_sidebar(ui) -> None:
    """Construye la barra lateral izquierda de Gestión sobre ``ui``."""
    s = ui.state

    ui.btn_wizard = app_icon_button(
        PIcons.GEAR,
        icon_color=TEXT_DIM,
        hover_color_icon=TEXT_PRIMARY,
        size=16,
        tooltip="Configurar credenciales",
        on_click=ui._on_open_wizard,
        width=28,
        height=28,
    )
    logo = ft.Column(
        [
            ft.Row(
                [
                    ft.Container(
                        content=app_icon(PIcons.MUSIC_NOTES, color=ACCENT, size=20, fill=True),
                        bgcolor=ACCENT_HALO,
                        border_radius=8,
                        padding=ft.Padding.all(6),
                    ),
                    ft.Column(
                        [
                            ft.Text(
                                spans=[
                                    ft.TextSpan(
                                        "Melomaniac",
                                        ft.TextStyle(
                                            size=20,
                                            color=TEXT_PRIMARY,
                                            font_family=brand_family("bold"),
                                        ),
                                    ),
                                ],
                                opacity=1.0,
                            ),
                            ft.Text(
                                f"v{APP_VERSION}",
                                size=9,
                                color=TEXT_DIM,
                                font_family=FONT_TEXT,
                                style=ft.TextStyle(letter_spacing=0.8),
                                opacity=1.0,
                            ),
                        ],
                        spacing=0,
                        tight=True,
                        expand=True,
                    ),
                    ui.btn_wizard,
                ],
                spacing=10,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
        ],
        spacing=0,
    )

    _dd_style = dict(
        bgcolor=BG_INPUT,
        border=input_border(),
        label_style=ft.TextStyle(color=TEXT_MUTED, size=10, font_family=FONT_HEADLINE),
        text_style=ft.TextStyle(color=TEXT_PRIMARY, size=11, font_family=FONT_TEXT),
        content_padding=ft.Padding.symmetric(horizontal=8, vertical=0),
        expand=True,
    )

    def _on_src_select(e) -> None:
        val = e.control.value
        s.set_source(val)
        if val == "Archivo Local":
            asyncio.create_task(ui._do_local_pick())
        elif val == "Pegar Texto":
            ui._open_paste_dialog()
        elif val in AppState.PLATFORMS:
            asyncio.create_task(ui._refresh_auth_live(val))
            ui._refresh_playlist_picker(val)

    def _on_dst_select(e) -> None:
        val = e.control.value
        s.set_destination(val)
        if val == EXPORT_DEST_LABEL:
            if isinstance(ui._transfer_btn.content, ft.Row) and len(ui._transfer_btn.content.controls) >= 2:
                ui._transfer_btn.content.controls[0].value = PIcons.EXPORT
                ui._transfer_btn.content.controls[1].value = "Exportar"
            elif hasattr(ui._transfer_btn.content, "value"):
                ui._transfer_btn.content.value = "Exportar"
            ui._transfer_btn.update()
            return
        try:
            if isinstance(ui._transfer_btn.content, ft.Row) and len(ui._transfer_btn.content.controls) >= 2:
                ui._transfer_btn.content.controls[0].value = PIcons.ARROWS_LEFT_RIGHT
                ui._transfer_btn.content.controls[1].value = "Transferir"
            elif hasattr(ui._transfer_btn.content, "value"):
                ui._transfer_btn.content.value = "Transferir"
        except Exception:
            pass
        if val in AppState.PLATFORMS:
            asyncio.create_task(ui._refresh_auth_live(val))

    ui._src_dd = ft.Dropdown(
        label="Origen",
        value=s.source,
        options=[ft.dropdown.Option(key=p, text=p) for p in AppState.SOURCE_OPTIONS],
        on_select=_on_src_select,
        **_dd_style,
    )
    _dst_opts = [*AppState.PLATFORMS, EXPORT_DEST_LABEL]
    ui._dst_dd = ft.Dropdown(
        label="Destino",
        value=s.destination,
        options=[ft.dropdown.Option(key=p, text=p) for p in _dst_opts],
        on_select=_on_dst_select,
        visible=False,
        **_dd_style,
    )
    ui._status_badge = ft.Text("", size=10, color=SUCCESS, font_family=mono_family(), opacity=1.0)
    ui._dest_session_warn = ft.Text("", size=9, color=ERROR_COL, font_family=FONT_TEXT, visible=False)

    platform_section = ft.Column(
        [
            _section_label("PLATAFORMAS"),
            ft.Row([ui._src_dd, ui._dst_dd], spacing=8),
            ui._status_badge,
            ui._dest_session_warn,
        ],
        spacing=8,
    )

    ui._id_clear_btn = app_icon_button(
        PIcons.X,
        icon_color=TEXT_DIM,
        hover_color_icon=TEXT_PRIMARY,
        size=12,
        width=24,
        height=24,
        tooltip="Limpiar ID",
        visible=False,
        on_click=ui._on_clear_id,
    )

    def _on_id_change(_):
        ui._id_clear_btn.visible = bool(ui._id_field.value)
        ui._id_clear_btn.update()

    ui._id_field = app_text_field(
        label="ID o URL de la Playlist",
        hint_text="pl.xxxx  /  PLxxxx  /  URL",
        suffix=ui._id_clear_btn,
        on_change=_on_id_change,
        on_submit=lambda e: asyncio.create_task(ui._do_cloud_load(e)),
    )

    _dd_col_style = {k: v for k, v in _dd_style.items() if k != "expand"}
    ui._playlist_pick = ft.Dropdown(
        label="Playlist de la biblioteca",
        value="",
        options=[],
        on_select=ui._on_playlist_pick,
        **_dd_col_style,
    )
    ui._playlist_pick.visible = False
    ui._playlist_help = app_text(
        "",
        size=9,
        color=TEXT_DIM,
        font_family=FONT_TEXT,
        visible=False,
    )
    ui._url_toggle_label = ft.Text(
        "Usar URL o ID",
        size=10,
        color=TEXT_MUTED,
        font_family=FONT_HEADLINE,
    )
    ui._use_url_btn = ft.TextButton(
        content=ft.Row(
            [app_icon(PIcons.LINK, size=13, color=TEXT_MUTED), ui._url_toggle_label],
            spacing=6,
            tight=True,
        ),
        on_click=ui._on_toggle_url_field,
    )
    ui._url_mode = False

    ui._playlist_section = ft.Column(
        [
            _section_label("PLAYLIST"),
            ui._playlist_pick,
            ui._playlist_help,
            ui._id_field,
            ui._use_url_btn,
        ],
        spacing=8,
        visible=(s.source not in AppState.LOCAL_SOURCES),
    )
    ui._id_field.visible = False
    ui._playlist_help.value = (
        "Sin playlists sincronizadas: usa «Usar URL o ID» o sincroniza "
        "la plataforma en Biblioteca."
    )
    ui._playlist_help.visible = True
    ui._playlist_divider = ft.Divider(
        height=1,
        color=BORDER_MUTED,
        thickness=0.5,
        visible=(s.source not in AppState.LOCAL_SOURCES),
    )

    _btn_w, _btn_h = 129, 44
    ui._load_btn = _primary_btn(
        "Cargar", PIcons.DOWNLOAD_SIMPLE, ui._on_load, width=_btn_w, height=_btn_h
    )
    ui._merge_sidebar_btn = _ghost_btn(
        "Fusionar",
        PIcons.QUEUE,
        lambda _: ui._open_merge_dialog(),
        width=_btn_w,
        height=_btn_h,
    )
    actions = ft.Column(
        [ft.Row([ui._load_btn, ui._merge_sidebar_btn], spacing=6)],
        spacing=6,
    )

    ui._rl_banner = ft.Container(
        content=ft.Row(
            [
                app_icon(PIcons.CLOCK, color=WARNING, size=14),
                ft.Text("", size=10, color=WARNING, font_family=mono_family("light"), opacity=1.0),
            ],
            spacing=6,
        ),
        bgcolor=BG_PANEL,
        border=ft.Border.all(0.8, WARNING),
        border_radius=8,
        padding=ft.Padding.symmetric(horizontal=10, vertical=6),
        visible=False,
    )

    ui._progress_bar = ft.ProgressBar(value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=4)
    ui._progress_row = ft.Container(
        content=ft.Column(
            [
                ui._progress_bar,
                ft.Row(
                    [ft.Text("", size=10, color=TEXT_MUTED, font_family=mono_family("light"), opacity=1.0)],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=4,
        ),
        visible=False,
        border=ft.Border.all(0.8, BORDER_LIGHT),
        border_radius=8,
        padding=ft.Padding.symmetric(horizontal=8, vertical=6),
    )

    ui._telemetry = TelemetryDrawer(ui.page, sidebar_width=300)

    fixed_top = ft.Column(
        controls=[
            logo,
            ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
            platform_section,
            ft.Divider(height=1, color=BORDER_MUTED, thickness=0.5),
            ui._playlist_section,
            ui._playlist_divider,
            _section_label("ACCIONES"),
            actions,
        ],
        spacing=12,
    )

    scrollable_bottom = ft.Column(
        controls=[ui._rl_banner, ui._telemetry.container],
        spacing=12,
        scroll=ft.ScrollMode.ADAPTIVE,
        expand=True,
    )

    sidebar_col = ft.Column(controls=[fixed_top, scrollable_bottom], spacing=12, expand=True)
    sidebar_stack = ft.Stack(controls=[sidebar_col, ui._telemetry.handle], expand=True)

    ui._sidebar = ft.Container(
        width=300,
        padding=ft.Padding.all(18),
        bgcolor=SIDEBAR_BG,
        clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
        border_radius=ft.BorderRadius.only(top_right=14, bottom_right=14),
        border=ft.Border.only(right=ft.BorderSide(1, BORDER_LIGHT)),
        content=sidebar_stack,
    )
