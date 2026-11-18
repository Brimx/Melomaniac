"""ui/shell/sidebar.py — Panel de fuente y carga de Gestión (estilo Sala de escucha)."""

from __future__ import annotations

import asyncio

import flet as ft

from core.config import EXPORT_DEST_LABEL
from core.models import LoadState, TransferState
from core.state import AppState
from ui.components.widgets import (
    _ghost_btn,
    _primary_btn,
    app_text,
    app_text_field,
    input_border,
    kicker,
    platform_select,
    safe_update,
    set_button_text,
)
from ui.theme.fonts import FONT_HEADLINE, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_INSET,
    BG_INPUT,
    BG_PANEL,
    BG_SURFACE,
    BORDER_CONTROL,
    BORDER_ROW,
    DECOR_LINE,
    ERROR_COL,
    FS_META,
    FS_SM,
    RADIUS_NONE,
    RADIUS_SHARP,
    SPACE_0,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    SUCCESS,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WARNING,
)

SOURCE_PLATFORM_OPTIONS: list[tuple[str, str, str]] = [
    ("YouTube Music", "YouTube Music", PIcons.YOUTUBE_LOGO),
    ("Apple Music", "Apple Music", PIcons.APPLE_LOGO),
    ("Spotify", "Spotify", PIcons.SPOTIFY_LOGO),
    ("Archivo Local", "Archivo Local", PIcons.FOLDER),
    ("Pegar Texto", "Pegar Texto", PIcons.LIST_BULLETS),
]


def platform_icon(name: str) -> str:
    """Devuelve el icono Phosphor correspondiente a la plataforma o fuente."""
    low = (name or "").lower()
    if "youtube" in low:
        return PIcons.YOUTUBE_LOGO
    if "apple" in low:
        return PIcons.APPLE_LOGO
    if "spotify" in low:
        return PIcons.SPOTIFY_LOGO
    if "export" in low:
        return PIcons.EXPORT
    if "pegar" in low:
        return PIcons.LIST_BULLETS
    if "local" in low or "archivo" in low:
        return PIcons.FOLDER
    return PIcons.MUSIC_NOTES


def _make_source_selector(ui, current: str, on_select) -> ft.Container:
    """Construye el selector de plataforma origen con ``platform_select``."""
    holder = ft.Container()

    def _rebuild(val: str) -> None:
        holder.content = platform_select(
            SOURCE_PLATFORM_OPTIONS,
            val,
            on_select,
            label="ORIGEN",
        )

    _rebuild(current)
    ui._rebuild_src_selector = _rebuild
    return holder


def build_sidebar(ui) -> None:
    """Construye el panel izquierdo de fuente y carga sobre ``ui``."""
    s = ui.state

    ui.btn_wizard = app_icon_button(
        PIcons.GEAR,
        icon_color=TEXT_MUTED,
        hover_color_icon=TEXT_PRIMARY,
        size=16,
        tooltip="Configurar credenciales y sesiones",
        on_click=ui._on_open_wizard,
        width=28,
        height=28,
    )

    _dd_style = dict(
        bgcolor=BG_INPUT,
        border=input_border(),
        label_style=ft.TextStyle(color=TEXT_MUTED, size=FS_META, font_family=FONT_HEADLINE),
        text_style=ft.TextStyle(color=TEXT_PRIMARY, size=FS_SM, font_family=FONT_TEXT),
        content_padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=0),
        expand=True,
    )

    def _apply_source_change(val: str) -> None:
        s.set_source(val)
        ui._src_dd.value = val
        if hasattr(ui, "_rebuild_src_selector"):
            ui._rebuild_src_selector(val)
            safe_update(ui._src_selector_wrap)
        if val == "Archivo Local":
            asyncio.create_task(ui._do_local_pick())
        elif val == "Pegar Texto":
            ui._open_paste_dialog()
        elif val in AppState.PLATFORMS:
            asyncio.create_task(ui._refresh_auth_live(val))
            ui._refresh_playlist_picker(val)

    def _on_src_select(e) -> None:
        _apply_source_change(e.control.value)

    def _on_dst_select(e) -> None:
        if getattr(s, "transfer_state", TransferState.IDLE) == TransferState.RUNNING:
            e.control.value = s.destination
            safe_update(e.control)
            return
        val = e.control.value
        s.set_destination(val)
        if val == EXPORT_DEST_LABEL:
            set_button_text(ui._transfer_btn, "Exportar", icon=PIcons.EXPORT)
            safe_update(ui._transfer_btn)
            return
        set_button_text(ui._transfer_btn, "Transferir", icon=PIcons.ARROWS_LEFT_RIGHT)
        safe_update(ui._transfer_btn)
        if val in AppState.PLATFORMS:
            asyncio.create_task(ui._refresh_auth_live(val))

    ui._src_dd = ft.Dropdown(
        label="Origen",
        value=s.source,
        options=[ft.dropdown.Option(key=p, text=p) for p in AppState.SOURCE_OPTIONS],
        on_select=_on_src_select,
        visible=False,
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
    ui._src_selector_wrap = _make_source_selector(ui, s.source, _apply_source_change)
    ui._set_source_display = lambda val: (
        setattr(ui._src_dd, "value", val),
        ui._rebuild_src_selector(val) if hasattr(ui, "_rebuild_src_selector") else None,
        safe_update(ui._src_selector_wrap),
    )

    ui._status_badge = ft.Text(
        "",
        size=FS_META,
        color=SUCCESS,
        font_family=mono_family(),
        opacity=1.0,
    )
    ui._dest_session_warn = ft.Text(
        "",
        size=FS_META,
        color=ERROR_COL,
        font_family=FONT_TEXT,
        visible=False,
    )

    header_row = ft.Row(
        controls=[
            kicker("FUENTE"),
            ft.Container(expand=True),
            ui.btn_wizard,
        ],
        spacing=SPACE_2,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    platform_section = ft.Column(
        [
            header_row,
            ui._src_selector_wrap,
            ui._src_dd,
            ui._dst_dd,
            ui._status_badge,
            ui._dest_session_warn,
        ],
        spacing=SPACE_2,
    )

    ui._id_clear_btn = app_icon_button(
        PIcons.X,
        icon_color=DECOR_LINE,
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
        safe_update(ui._id_clear_btn)

    ui._id_field = app_text_field(
        label="ID o URL de la playlist",
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
        size=FS_META,
        color=TEXT_MUTED,
        font_family=FONT_TEXT,
        visible=False,
    )
    ui._url_toggle_label = ft.Text(
        "Avanzado",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=FONT_HEADLINE,
    )
    ui._use_url_btn = ft.TextButton(
        content=ft.Row(
            [app_icon(PIcons.LINK, size=13, color=TEXT_MUTED), ui._url_toggle_label],
            spacing=SPACE_2,
            tight=True,
        ),
        on_click=ui._on_toggle_url_field,
    )
    ui._url_mode = False

    ui._load_btn = _primary_btn(
        "Cargar", PIcons.DOWNLOAD_SIMPLE, ui._on_load, width=236, height=40
    )
    ui._load_btn.visible = False

    ui._playlist_section = ft.Column(
        [
            kicker("PLAYLIST"),
            ui._playlist_pick,
            ui._playlist_help,
            ui._id_field,
            ui._use_url_btn,
            ui._load_btn,
        ],
        spacing=SPACE_2,
        visible=(s.source not in AppState.LOCAL_SOURCES),
    )
    ui._id_field.visible = False
    ui._playlist_help.value = (
        "Sin playlists sincronizadas: usa «Avanzado» o sincroniza en Biblioteca."
    )
    ui._playlist_help.visible = True
    ui._playlist_divider = ft.Divider(
        height=1,
        color=BORDER_ROW,
        thickness=0.5,
        visible=(s.source not in AppState.LOCAL_SOURCES),
    )

    ui._rl_banner = ft.Container(
        content=ft.Row(
            [
                app_icon(PIcons.CLOCK, color=WARNING, size=14),
                ft.Text(
                    "",
                    size=FS_META,
                    color=WARNING,
                    font_family=mono_family("light"),
                    opacity=1.0,
                ),
            ],
            spacing=SPACE_1,
        ),
        bgcolor=BG_INSET,
        border=ft.Border.all(0.8, WARNING),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=SPACE_1),
        visible=False,
    )

    ui._progress_bar = ft.ProgressBar(
        value=0, bgcolor=BG_SURFACE, color=ACCENT, border_radius=RADIUS_SHARP
    )
    ui._progress_row = ft.Container(
        content=ft.Column(
            [
                ui._progress_bar,
                ft.Row(
                    [
                        ft.Text(
                            "",
                            size=FS_META,
                            color=TEXT_MUTED,
                            font_family=mono_family("light"),
                            opacity=1.0,
                        )
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
            ],
            spacing=SPACE_1,
        ),
        visible=False,
        bgcolor=BG_INSET,
        border=ft.Border.all(0.8, BORDER_CONTROL),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=SPACE_1),
    )

    def _on_sidebar_dest_click(_e=None) -> None:
        if getattr(s, "load_state", LoadState.IDLE) != LoadState.READY or not getattr(s, "tracks", None):
            return
        if getattr(s, "transfer_state", TransferState.IDLE) == TransferState.RUNNING:
            return
        ui._on_transfer(None)

    ui._route_dst_icon = app_icon(
        platform_icon(s.destination), size=16, color=TEXT_MUTED
    )
    ui._route_dst_text = ft.Text(
        s.destination or "Elegir al transferir",
        size=FS_SM,
        color=TEXT_PRIMARY,
        font_family=FONT_HEADLINE,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    ui._route_dst_btn = ft.Container(
        content=ft.Row(
            controls=[
                ui._route_dst_icon,
                ft.Column(
                    controls=[kicker("DESTINO"), ui._route_dst_text],
                    spacing=SPACE_0,
                    tight=True,
                    expand=True,
                ),
                app_icon(PIcons.CARET_DOWN, size=14, color=TEXT_MUTED),
            ],
            spacing=SPACE_2,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
        bgcolor=BG_SURFACE,
        border=ft.Border.all(0.8, BORDER_CONTROL),
        border_radius=RADIUS_SHARP,
        padding=ft.Padding.symmetric(horizontal=SPACE_3, vertical=SPACE_2),
        tooltip="Elegir plataforma destino y transferir",
        on_click=_on_sidebar_dest_click,
        ink=True,
    )

    ui._transfer_btn = _primary_btn(
        "Transferir",
        PIcons.ARROWS_LEFT_RIGHT,
        ui._on_transfer,
        width=236,
        height=40,
        disabled=True,
    )
    ui._transfer_btn.tooltip = "Elegir destino y transferir la selección"
    ui._footer_transfer_btn = ui._transfer_btn
    ui._ctx_transfer_btn = ui._transfer_btn

    ui._ctx_export_btn = _ghost_btn(
        "Exportar",
        PIcons.EXPORT,
        lambda _: asyncio.create_task(ui._open_export_dialog()),
        height=36,
    )
    ui._ctx_export_btn.expand = True
    ui._ctx_download_btn = _ghost_btn(
        "Descargar",
        PIcons.DOWNLOAD_SIMPLE,
        lambda _: ui._on_batch_download(),
        height=36,
    )
    ui._ctx_download_btn.expand = True

    ui._sidebar_actions_divider = ft.Divider(
        height=1,
        color=BORDER_ROW,
        thickness=0.5,
        visible=False,
    )
    ui._sidebar_actions_section = ft.Column(
        controls=[
            kicker("ACCIONES"),
            ui._route_dst_btn,
            ui._transfer_btn,
            ft.Row(
                controls=[ui._ctx_export_btn, ui._ctx_download_btn],
                spacing=SPACE_2,
            ),
        ],
        spacing=SPACE_2,
        visible=False,
    )

    sidebar_col = ft.Column(
        controls=[
            platform_section,
            ui._playlist_divider,
            ui._playlist_section,
            ui._rl_banner,
            ft.Container(expand=True),
            ui._sidebar_actions_divider,
            ui._sidebar_actions_section,
        ],
        spacing=SPACE_3,
        scroll=ft.ScrollMode.ADAPTIVE,
        expand=True,
    )

    ui._sidebar = ft.Container(
        width=268,
        padding=ft.Padding.all(SPACE_4),
        bgcolor=BG_PANEL,
        border_radius=RADIUS_NONE,
        border=ft.Border.only(right=ft.BorderSide(1, BORDER_ROW)),
        content=sidebar_col,
    )
