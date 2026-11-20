"""ui/player/bar_layout.py — Construcción visual de secciones de PlayerBar."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Callable

import flet as ft

from ui.components.song_row import _thumb_content_for
from ui.components.widgets import app_text, safe_update
from ui.theme.fonts import FONT_HEADLINE_BOLD, FONT_TEXT, mono_family
from ui.theme.icons import PIcons, app_icon, app_icon_button
from ui.theme.tokens import (
    ACCENT,
    BG_DEEP,
    BG_HOVER,
    BG_SURFACE,
    BORDER_LIGHT,
    BORDER_MUTED,
    CHIP_BG,
    FS_META,
    MOTION_HOVER_MS,
    OVERLAY_08,
    PLAYER_PROGRESS_ANIM_MS,
    PLAYER_PROGRESS_H,
    PLAYER_THUMB_SIZE,
    RADIUS_NONE,
    RADIUS_PILL,
    SPACE_0,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    TEXT_MUTED,
    TEXT_PRIMARY,
)


def build_player_bar_layout(bar, *, default_track_width: float = 240.0) -> None:
    """Construye y asigna los subcontroles visuales en ``bar``."""
    thumb_wrap, meta_col = _build_now_playing_section(bar)
    _build_transport_and_progress_section(bar, default_track_width=default_track_width)
    right_row = _build_volume_and_queue_section(bar)
    _assemble_bar_row(bar, thumb_wrap, meta_col, right_row)


def _build_now_playing_section(bar) -> tuple[ft.Stack, ft.Container]:
    # [funda + disco asomando]
    disc_peek = ft.Container(
        width=36,
        height=36,
        right=0,
        top=4,
        border_radius=RADIUS_PILL,
        bgcolor=BG_DEEP,
        border=ft.Border.all(1, BORDER_LIGHT),
        alignment=ft.Alignment.CENTER,
        content=ft.Container(
            width=10,
            height=10,
            border_radius=RADIUS_PILL,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(1, BORDER_MUTED),
        ),
    )

    def _on_thumb_hover(e: ft.HoverEvent) -> None:
        is_h = e.data == "true" or e.data is True
        bar._thumb_box.border = ft.Border.all(1, ACCENT if is_h else BORDER_MUTED)
        safe_update(bar._thumb_box)

    bar._thumb_box = ft.Container(
        width=PLAYER_THUMB_SIZE,
        height=PLAYER_THUMB_SIZE,
        left=0,
        top=0,
        bgcolor=BG_SURFACE,
        border=ft.Border.all(1, BORDER_MUTED),
        border_radius=RADIUS_NONE,
        alignment=ft.Alignment.CENTER,
        ink=True,
        tooltip="Abrir escena Reproduciendo",
        animate=ft.Animation(MOTION_HOVER_MS, ft.AnimationCurve.EASE_OUT),
        on_click=lambda _e: bar.open_now_playing_scene(),
        on_hover=_on_thumb_hover,
        content=_thumb_content_for("", box=PLAYER_THUMB_SIZE, icon_size=18),
    )
    bar._thumb_wrap = ft.Stack(
        width=PLAYER_THUMB_SIZE + 14,
        height=PLAYER_THUMB_SIZE,
        controls=[disc_peek, bar._thumb_box],
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
            spacing=SPACE_0,
            alignment=ft.MainAxisAlignment.CENTER,
        ),
    )

    # [badge formato]
    bar._badge_txt = app_text(
        "—",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=mono_family("medium"),
    )
    bar._badge_box = ft.Container(
        bgcolor=CHIP_BG,
        border=ft.Border.all(1, BORDER_MUTED),
        border_radius=RADIUS_NONE,
        padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=SPACE_1),
        content=bar._badge_txt,
        tooltip="Formato real del archivo",
        visible=False,
    )

    return bar._thumb_wrap, meta_col


def _build_transport_and_progress_section(bar, *, default_track_width: float = 240.0) -> None:
    # [🔀 ⏮ ▶/⏸ ⏭ 🔁]
    bar._shuffle_btn = app_icon_button(
        glyph=PIcons.SHUFFLE,
        size=16,
        icon_color=TEXT_MUTED,
        tooltip="Aleatorio",
        on_click=bar._on_shuffle_click,
        width=28,
        height=28,
    )
    bar._prev_btn = app_icon_button(
        glyph=PIcons.SKIP_BACK,
        size=16,
        icon_color=TEXT_PRIMARY,
        tooltip="Anterior (o reiniciar si > 3 s)",
        on_click=bar._on_prev_click,
        width=28,
        height=28,
    )
    bar._play_btn = app_icon_button(
        glyph=PIcons.PLAY,
        fill=True,
        size=22,
        icon_color=ACCENT,
        hover_color_icon=ACCENT,
        hover_scale=1.12,
        tooltip="Reproducir / Pausar",
        on_click=bar._on_play_pause_click,
        width=32,
        height=32,
    )
    bar._next_btn = app_icon_button(
        glyph=PIcons.SKIP_FORWARD,
        size=16,
        icon_color=TEXT_PRIMARY,
        tooltip="Siguiente",
        on_click=bar._on_next_click,
        width=28,
        height=28,
    )
    bar._repeat_btn = app_icon_button(
        glyph=PIcons.REPEAT,
        size=16,
        icon_color=TEXT_MUTED,
        tooltip="Repetir: desactivado",
        on_click=bar._on_repeat_click,
        width=28,
        height=28,
    )
    bar._transport_row = ft.Row(
        controls=[
            bar._shuffle_btn,
            bar._prev_btn,
            bar._play_btn,
            bar._next_btn,
            bar._repeat_btn,
        ],
        spacing=SPACE_1,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        alignment=ft.MainAxisAlignment.CENTER,
        tight=True,
    )

    # [tiempos en mono a cada lado de la barra]
    bar._time_pos_txt = app_text(
        "0:00",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=mono_family("light"),
    )
    bar._time_dur_txt = app_text(
        "0:00",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=mono_family("light"),
    )

    # [barra]
    bar._progress_fill = ft.Container(
        width=0,
        height=PLAYER_PROGRESS_H,
        bgcolor=ACCENT,
        border_radius=RADIUS_NONE,
        animate=ft.Animation(PLAYER_PROGRESS_ANIM_MS, ft.AnimationCurve.EASE_IN_OUT),
    )
    bar._progress_track = ft.Container(
        height=PLAYER_PROGRESS_H,
        bgcolor=BORDER_MUTED,
        border_radius=RADIUS_NONE,
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
            padding=ft.Padding.symmetric(vertical=SPACE_1),
            content=bar._progress_track,
            expand=True,
        ),
        expand=True,
    )
    bar._progress_row = ft.Row(
        controls=[bar._time_pos_txt, bar._progress_gesture, bar._time_dur_txt],
        spacing=SPACE_2,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    bar._center_col = ft.Column(
        controls=[bar._transport_row, bar._progress_row],
        spacing=SPACE_0,
        alignment=ft.MainAxisAlignment.CENTER,
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )


_FADER_KNOB_W = 4
_FADER_KNOB_H = 12
_FADER_TRACK_H = 4
_FADER_HIT_H = 24


class NeoVolumeFader(ft.Container):
    """Fader de volumen rectangular afilado (Neo-Ukiyo) compatible con la API de Slider."""

    def __init__(
        self,
        *,
        value: float = 1.0,
        width: float | None = 96,
        expand: bool = False,
        on_change: Callable | None = None,
        on_change_end: Callable | None = None,
    ) -> None:
        self._value = self._clamp(value)
        self._track_width = float(width) if width else 120.0
        self._on_change_cb = on_change
        self._on_change_end_cb = on_change_end

        self._fill = ft.Container(
            width=0,
            height=_FADER_TRACK_H,
            bgcolor=ACCENT,
            border_radius=RADIUS_NONE,
        )
        self._knob = ft.Container(
            width=_FADER_KNOB_W,
            height=_FADER_KNOB_H,
            bgcolor=TEXT_PRIMARY,
            border=ft.Border.all(0.8, ACCENT),
            border_radius=RADIUS_NONE,
            left=0,
            top=(_FADER_HIT_H - _FADER_KNOB_H) / 2,
        )
        self._rail = ft.Container(
            height=_FADER_TRACK_H,
            bgcolor=BG_SURFACE,
            border=ft.Border.all(0.5, BORDER_MUTED),
            border_radius=RADIUS_NONE,
            alignment=ft.Alignment.CENTER_LEFT,
            content=self._fill,
            left=0,
            right=0,
            top=(_FADER_HIT_H - _FADER_TRACK_H) / 2,
        )
        self._stack = ft.Stack(
            controls=[self._rail, self._knob],
            height=_FADER_HIT_H,
            width=width,
            expand=expand,
        )
        self._gesture = ft.GestureDetector(
            mouse_cursor=ft.MouseCursor.CLICK,
            on_tap_down=self._on_tap,
            on_horizontal_drag_update=self._on_drag_update,
            on_horizontal_drag_end=self._on_drag_end,
            content=self._stack,
        )
        super().__init__(
            content=self._gesture,
            width=width,
            height=_FADER_HIT_H,
            expand=expand,
            tooltip=self._fmt_tooltip(self._value),
            on_size_change=self._on_size_change,
        )
        self._sync_geometry()

    @staticmethod
    def _clamp(val: float) -> float:
        try:
            return max(0.0, min(1.0, float(val)))
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _fmt_tooltip(val: float) -> str:
        return f"Volumen: {int(round(val * 100))}%"

    @property
    def value(self) -> float:
        return self._value

    @value.setter
    def value(self, val: float) -> None:
        self._value = self._clamp(val)
        self.tooltip = self._fmt_tooltip(self._value)
        self._sync_geometry()

    def _sync_geometry(self) -> None:
        w = max(float(_FADER_KNOB_W), self._track_width)
        fill_w = w * self._value
        self._fill.width = fill_w
        self._knob.left = max(0.0, min(w - _FADER_KNOB_W, fill_w - (_FADER_KNOB_W / 2)))

    def _on_size_change(self, e) -> None:
        w = getattr(e, "width", None)
        if w is not None and float(w) > 0:
            self._track_width = float(w)
            self._sync_geometry()
            safe_update(self)

    def _value_from_event(self, e) -> float | None:
        loc = getattr(e, "local_position", None)
        x = getattr(loc, "x", None) if loc is not None else getattr(e, "local_x", None)
        if x is None:
            return None
        w = max(1.0, self._track_width)
        return self._clamp(float(x) / w)

    def _emit_change(self, val: float, *, end: bool = False) -> None:
        self.value = val
        safe_update(self)
        ev = SimpleNamespace(control=self)
        if callable(self._on_change_cb):
            self._on_change_cb(ev)
        if end and callable(self._on_change_end_cb):
            self._on_change_end_cb(ev)

    def _on_tap(self, e) -> None:
        val = self._value_from_event(e)
        if val is not None:
            self._emit_change(val, end=True)

    def _on_drag_update(self, e) -> None:
        val = self._value_from_event(e)
        if val is not None:
            self._emit_change(val, end=False)

    def _on_drag_end(self, _e) -> None:
        if callable(self._on_change_end_cb):
            self._on_change_end_cb(SimpleNamespace(control=self))


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
    bar._vol_slider = NeoVolumeFader(
        value=0.0 if bar._muted else bar._volume,
        width=96,
        on_change=bar._on_volume_change,
        on_change_end=bar._on_volume_change_end,
    )
    vol_row = ft.Row(
        controls=[bar._mute_btn, bar._vol_slider],
        spacing=SPACE_0,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        tight=True,
    )

    # [letra] [escena] [cola]
    bar._lyrics_btn = app_icon_button(
        glyph=PIcons.MUSIC_NOTES,
        size=18,
        icon_color=TEXT_MUTED,
        tooltip="Letra",
        on_click=lambda _e: bar._open_lyrics_panel(),
        width=32,
        height=32,
    )
    bar._queue_icon = app_icon(PIcons.QUEUE, size=16, color=TEXT_PRIMARY)
    bar._queue_count_txt = app_text(
        "0",
        size=FS_META,
        color=TEXT_MUTED,
        font_family=mono_family("medium"),
    )

    def _on_queue_hover(e: ft.HoverEvent) -> None:
        is_h = e.data == "true" or e.data is True
        bar._queue_btn.bgcolor = BG_HOVER if is_h else OVERLAY_08
        bar._queue_btn.border = ft.Border.all(1, ACCENT if is_h else BORDER_MUTED)
        safe_update(bar._queue_btn)

    bar._queue_btn = ft.Container(
        bgcolor=OVERLAY_08,
        border=ft.Border.all(1, BORDER_MUTED),
        border_radius=RADIUS_NONE,
        padding=ft.Padding.symmetric(horizontal=SPACE_2, vertical=SPACE_0),
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
            spacing=SPACE_2,
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
    )

    return ft.Row(
        controls=[vol_row, bar._lyrics_btn, bar._queue_btn],
        spacing=SPACE_1,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        tight=True,
    )


def _assemble_bar_row(
    bar,
    thumb_wrap: ft.Stack,
    meta_col: ft.Container,
    right_row: ft.Row,
) -> None:
    left_row = ft.Row(
        controls=[thumb_wrap, meta_col, bar._badge_box],
        spacing=SPACE_3,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        tight=True,
    )
    bar.content = ft.Row(
        controls=[
            left_row,
            bar._center_col,
            right_row,
        ],
        spacing=SPACE_4,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
        expand=True,
    )

