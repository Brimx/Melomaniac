"""ui/player/bar_layout.py — Construcción visual de secciones de PlayerBar."""

from __future__ import annotations

import flet as ft

from ui.components.widgets import app_text
from ui.theme.fonts import FONT_HEADLINE_BOLD, FONT_PHOSPHOR, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_HOVER,
    BG_SURFACE,
    BORDER_MUTED,
    CHIP_BG,
    MOTION_HOVER_MS,
    OVERLAY_08,
    PLAYER_PROGRESS_ANIM_MS,
    PLAYER_PROGRESS_H,
    PLAYER_THUMB_SIZE,
    RADIUS_BUTTON,
    RADIUS_INPUT,
    TEXT_DIM,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def build_player_bar_layout(bar, *, default_track_width: float = 240.0) -> None:
    """Construye y asigna los subcontroles visuales en ``bar``."""
    meta_col = _build_now_playing_section(bar)
    _build_transport_and_progress_section(bar, default_track_width=default_track_width)
    vol_row = _build_volume_and_queue_section(bar)
    _assemble_bar_row(bar, meta_col, vol_row)


def _build_now_playing_section(bar) -> ft.Container:
    # [thumb]
    bar._thumb_icon = app_icon(PIcons.DISC, size=18, color=TEXT_DIM)
    bar._thumb_img = ft.Image(
        src="",
        width=PLAYER_THUMB_SIZE,
        height=PLAYER_THUMB_SIZE,
        fit=ft.BoxFit.COVER,
        border_radius=RADIUS_INPUT,
        visible=False,
    )
    bar._thumb_box = ft.Container(
        width=PLAYER_THUMB_SIZE,
        height=PLAYER_THUMB_SIZE,
        bgcolor=BG_SURFACE,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=RADIUS_INPUT,
        alignment=ft.Alignment.CENTER,
        content=ft.Stack(controls=[bar._thumb_icon, bar._thumb_img]),
    )

    # [título/artista]
    bar._title_txt = app_text(
        "Sin reproducción",
        size=12,
        color=TEXT_PRIMARY,
        font_family=FONT_HEADLINE_BOLD,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    bar._artist_txt = app_text(
        "Selecciona una pista descargada",
        size=11,
        color=TEXT_MUTED,
        font_family=FONT_TEXT,
        max_lines=1,
        overflow=ft.TextOverflow.ELLIPSIS,
    )
    meta_col = ft.Container(
        width=190,
        content=ft.Column(
            controls=[bar._title_txt, bar._artist_txt],
            spacing=1,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
    )

    # [badge formato]
    bar._badge_txt = app_text(
        "—",
        size=10,
        color=TEXT_MUTED,
        font_family=mono_family("medium"),
    )
    bar._badge_box = ft.Container(
        bgcolor=CHIP_BG,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=6,
        padding=ft.Padding.symmetric(horizontal=8, vertical=4),
        content=bar._badge_txt,
        tooltip="Formato real del archivo",
        visible=False,
    )

    return meta_col

def _build_transport_and_progress_section(bar, *, default_track_width: float = 240.0) -> None:
    # [⏮ ▶/⏸ ⏭]
    bar._prev_btn = app_icon_button(
        glyph=PIcons.SKIP_BACK,
        size=18,
        icon_color=TEXT_PRIMARY,
        tooltip="Anterior (o reiniciar si > 3 s)",
        on_click=bar._on_prev_click,
        width=32,
        height=32,
    )
    bar._play_btn = app_icon_button(
        glyph=PIcons.PLAY,
        fill=True,
        size=28,
        icon_color=ACCENT,
        hover_color_icon=ACCENT,
        hover_scale=1.12,
        tooltip="Reproducir / Pausar",
        on_click=bar._on_play_pause_click,
        width=36,
        height=36,
    )
    bar._next_btn = app_icon_button(
        glyph=PIcons.SKIP_FORWARD,
        size=18,
        icon_color=TEXT_PRIMARY,
        tooltip="Siguiente",
        on_click=bar._on_next_click,
        width=32,
        height=32,
    )
    bar._transport_row = ft.Row(
        controls=[bar._prev_btn, bar._play_btn, bar._next_btn],
        spacing=4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        tight=True,
    )

    # [tiempo]
    bar._time_txt = app_text(
        "0:00 / 0:00",
        size=11,
        color=TEXT_MUTED,
        font_family=mono_family("light"),
    )

    # [barra]
    bar._progress_fill = ft.Container(
        width=0,
        height=PLAYER_PROGRESS_H,
        bgcolor=ACCENT,
        border_radius=PLAYER_PROGRESS_H / 2,
        animate=ft.Animation(PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
    )
    bar._progress_track = ft.Container(
        height=PLAYER_PROGRESS_H,
        bgcolor=BORDER_MUTED,
        border_radius=PLAYER_PROGRESS_H / 2,
        alignment=ft.Alignment.CENTER_LEFT,
        on_size_change=bar._on_track_size_change,
        content=bar._progress_fill,
        expand=True,
    )
    bar._progress_gesture = ft.GestureDetector(
        mouse_cursor=ft.MouseCursor.CLICK,
        on_tap_down=bar._on_progress_tap,
        on_horizontal_drag_start=bar._on_scrub_start,
        on_horizontal_drag_update=bar._on_scrub_update,
        on_horizontal_drag_end=bar._on_scrub_end,
        content=ft.Container(
            padding=ft.Padding.symmetric(vertical=8),
            content=bar._progress_track,
            expand=True,
        ),
        expand=True,
    )

def _build_volume_and_queue_section(bar) -> ft.Row:
    # [volumen]
    bar._mute_btn = app_icon_button(
        glyph=PIcons.SPEAKER_HIGH,
        size=18,
        icon_color=TEXT_MUTED,
        tooltip="Silenciar / Activar sonido",
        on_click=bar._on_mute_click,
        width=32,
        height=32,
    )
    bar._vol_slider = ft.Slider(
        min=0.0,
        max=1.0,
        value=0.0 if bar._muted else bar._volume,
        width=100,
        active_color=ACCENT,
        inactive_color=BG_SURFACE,
        on_change=bar._on_volume_change,
        on_change_end=bar._on_volume_change_end,
    )
    vol_row = ft.Row(
        controls=[bar._mute_btn, bar._vol_slider],
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        tight=True,
    )

    # [aleatorio] [repetir] [cola]
    bar._shuffle_btn = app_icon_button(
        glyph=PIcons.SHUFFLE,
        size=18,
        icon_color=TEXT_MUTED,
        tooltip="Aleatorio",
        on_click=bar._on_shuffle_click,
        width=32,
        height=32,
    )
    bar._repeat_btn = app_icon_button(
        glyph=PIcons.REPEAT,
        size=18,
        icon_color=TEXT_MUTED,
        tooltip="Repetir: desactivado",
        on_click=bar._on_repeat_click,
        width=32,
        height=32,
    )
    bar._queue_icon = app_icon(PIcons.QUEUE, size=16, color=TEXT_PRIMARY)
    bar._queue_count_txt = app_text(
        "0",
        size=10,
        color=TEXT_MUTED,
        font_family=mono_family("medium"),
    )
    def _on_queue_hover(e: ft.HoverEvent) -> None:
        is_h = e.data == "true" or e.data is True
        bar._queue_btn.bgcolor = BG_HOVER if is_h else OVERLAY_08
        bar._queue_btn.border = ft.Border.all(0.8, ACCENT if is_h else BORDER_MUTED)
        if bar._queue_btn.page:
            bar._queue_btn.update()

    bar._queue_btn = ft.Container(
        bgcolor=OVERLAY_08,
        border=ft.Border.all(0.8, BORDER_MUTED),
        border_radius=RADIUS_BUTTON,
        padding=ft.Padding.symmetric(horizontal=10, vertical=0),
        height=32,
        alignment=ft.Alignment.CENTER,
        tooltip="Mostrar / ocultar cola de reproducción",
        animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
        on_click=lambda _e: bar._on_toggle_queue() if bar._on_toggle_queue else None,
        on_hover=_on_queue_hover,
        content=ft.Row(
            controls=[
                bar._queue_icon,
                bar._queue_count_txt,
            ],
            spacing=6,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
    )

    return vol_row

def _assemble_bar_row(bar, meta_col: ft.Container, vol_row: ft.Row) -> None:
    bar.content = ft.Row(
        controls=[
            bar._thumb_box,
            meta_col,
            bar._badge_box,
            bar._transport_row,
            bar._time_txt,
            bar._progress_gesture,
            vol_row,
            bar._shuffle_btn,
            bar._repeat_btn,
            bar._queue_btn,
        ],
        spacing=10,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )

